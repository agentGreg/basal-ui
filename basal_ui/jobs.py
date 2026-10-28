"""Background jobs of basal-ui (benchmark, calibration, batch, ...): one running job per kind, each in its own thread,
its progress in /api/status and its output in the Logs view (source job:<kind>)."""

import json
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from pathlib import Path

import httpx


class LogBuffer:
    """The last lines of one source (a basal-serve child, a job or basal-ui's own events), numbered for incremental
    reads."""

    def __init__(self, maxlen=1000):
        self.lines = deque(maxlen=maxlen)
        self.seq = 0
        self.generation = uuid.uuid4().hex
        self.lock = threading.Lock()

    def append(self, text):
        with self.lock:
            self.seq += 1
            self.lines.append({"seq": self.seq, "ts": time.time(), "text": text})

    def since(self, seq=0):
        with self.lock:
            return [line for line in self.lines if line["seq"] > seq]

    def last(self):
        with self.lock:
            return self.lines[-1]["text"] if self.lines else None

    def text(self):
        with self.lock:
            return "\n".join(line["text"] for line in self.lines)


PHASE_TEMPLATES = {
    "phase_starting": "starting",
    "phase_running": "running",
    "phase_checking": "checking selected models",
    "phase_unloading": "unloading selected models",
    "phase_starting_model": "starting model",
    "phase_testing_inference": "testing inference",
    "phase_restoring": "restoring {model}",
    "phase_calibrating_engine": "calibrating (engine in basal-ui's process)",
    "phase_calibrating": "calibrating",
    "phase_loading_model": "loading model",
    "phase_warm_up": "warm-up",
    "phase_sequential": "sequential",
    "phase_concurrent": "concurrent",
    "phase_done": "done",
    "phase_done_warnings": "done_with_warnings",
    "phase_cancelled": "cancelled",
    "phase_error": "error",
}


def phase_fields(phase):
    for code, template in PHASE_TEMPLATES.items():
        keys = re.findall(r"\{(\w+)\}", template)
        pattern = re.escape(template)
        for key in keys:
            pattern = pattern.replace(re.escape("{" + key + "}"), f"(?P<{key}>.*?)", 1)
        match = re.fullmatch(pattern, phase)
        if match:
            return code, match.groupdict()
    return None, None


def terminate(proc):
    """Stop a child process and everything it started (its own process group / session), then reap it."""
    if not proc:
        return

    def signal_group(kill):
        try:
            if os.name == "posix":
                os.killpg(proc.pid, signal.SIGKILL if kill else signal.SIGTERM)
            else:
                proc.kill() if kill else proc.terminate()
        except ProcessLookupError:
            pass

    signal_group(kill=False)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        signal_group(kill=True)
        proc.wait()
        return
    if os.name == "posix":  # the leader is reaped; a member that ignored SIGTERM keeps the group alive
        deadline = time.monotonic() + 5
        while _group_alive(proc.pid):
            if time.monotonic() >= deadline:
                signal_group(kill=True)
                break
            time.sleep(0.1)


def _group_alive(pgid) -> bool:
    """Whether any process of the group is left (signal 0 only probes)."""
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # a member exists but cannot be signalled by this user
        pass
    return True


def run_tool(job, module, argv, out_path=None, on_json=None):
    """`python -m basal.<module> argv` (an engine tool: basal-loadtest, basal-bench, basal-run) for a job: its output
    lines go to the job's log (each JSON object line also to on_json), a cancel stops it and everything it started.
    -> the JSON it wrote to out_path, else the last JSON line it printed (None without one); RuntimeError with its last
    output line when it fails."""
    command = [sys.executable, "-m", f"basal.{module}", *map(str, argv)]
    job.log.append(f"$ {shlex.join(command)}")
    options = (
        {"start_new_session": True}
        if os.name == "posix"
        else {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    )
    proc = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        **options,
    )
    finished = threading.Event()

    def stop_on_cancel():
        while not finished.wait(0.2):
            if job.cancel.is_set():
                terminate(proc)
                return

    threading.Thread(target=stop_on_cancel, name=f"basal-ui-{module}-cancel", daemon=True).start()
    last_json = None
    try:
        for line in proc.stdout:
            line = line.rstrip("\n")
            job.log.append(line)
            if line.startswith("{"):
                try:
                    last_json = json.loads(line)
                except ValueError:
                    continue
                if on_json is not None:
                    on_json(last_json)
        proc.wait()
    finally:
        finished.set()
    if job.cancel.is_set():
        raise Cancelled()
    if proc.returncode:
        raise RuntimeError(f"basal-{module} exited ({proc.returncode}): {job.log.last() or 'no output'}")
    if out_path is not None:
        return json.loads(Path(out_path).read_text(encoding="utf-8"))
    return last_json


class Cancelled(Exception):
    """A job's target stops early: Job.cancel was set."""


class Job:
    """What /api/status shows of one job; the target reports through progress() and polls cancel / check()."""

    def __init__(self, kind, model, label, log):
        self.kind, self.model, self.label, self.log = kind, model, label, log
        self.phase, self.done, self.total, self.error = "starting", 0, 0, None
        self.running, self.started, self.result = True, time.time(), None
        self.cancel = threading.Event()
        self.lock = threading.Lock()
        self.thread = None

    def progress(self, phase, done=0, total=0):
        with self.lock:
            self.phase, self.done, self.total = phase, done, total

    def check(self):
        if self.cancel.is_set():
            raise Cancelled()

    def finish(self, phase, result=None, error=None):
        with self.lock:
            self.phase, self.result, self.error, self.running = phase, result, error, False

    def state(self) -> dict:
        with self.lock:
            code, params = phase_fields(self.phase)
            return {
                "kind": self.kind,
                "model": self.model,
                "label": self.label,
                "phase": self.phase,
                "phase_code": code,
                "phase_params": params,
                "done": self.done,
                "total": self.total,
                "error": self.error,
                "running": self.running,
                "started": self.started,
                "result": self.result,
            }


class Jobs:
    """The last job of each kind; `logs` is the fleet's log buffers (a job's output goes to logs["job:<kind>"])."""

    def __init__(self, logs: dict, event=None):
        self.logs = logs
        self.event = event or (lambda level, message: None)
        self.lock = threading.Lock()
        self.jobs: dict[str, Job] = {}

    def start(self, kind, model, label, target, *args) -> dict:
        """Run target(job, *args) in a daemon thread; its return value is the job's result. RuntimeError when a job of
        the kind is running."""
        with self.lock:
            current = self.jobs.get(kind)
            if current is not None and current.running:
                raise RuntimeError(f"a {kind} job is already running")
            log = LogBuffer()
            self.logs[f"job:{kind}"] = log
            job = self.jobs[kind] = Job(kind, model, label, log)

        def run():
            try:
                result = target(job, *args)
                job.finish(
                    "done_with_warnings" if isinstance(result, dict) and result.get("warnings") else "done",
                    result=result,
                )
                self.event("info", f"{kind} finished: {label}")
            except Cancelled:
                job.finish("cancelled")
                self.event("info", f"{kind} cancelled: {label}")
            except (Exception, SystemExit) as exc:  # noqa: BLE001 — shown in the UI and the job's log (basal's
                # engines and CLIs refuse with SystemExit)
                message = str(exc)
                if isinstance(exc, httpx.HTTPStatusError):
                    response = exc.response
                    message = f"HTTP {response.status_code}: {response.text}"
                    if response.headers.get("x-request-id"):
                        message += f" [request {response.headers['x-request-id']}]"
                job.log.append(f"error: {message}")
                job.finish("error", error=message)
                self.event("error", f"{kind} failed: {label}: {exc}")

        self.event("info", f"{kind} started: {label}")
        job.thread = threading.Thread(target=run, name=f"basal-ui-{kind}", daemon=True)
        job.thread.start()
        return job.state()

    def get(self, kind) -> Job | None:
        with self.lock:
            return self.jobs.get(kind)

    def cancel(self, kind) -> dict | None:
        job = self.get(kind)
        if job is None:
            return None
        job.cancel.set()
        return job.state()

    def cancel_all(self):
        with self.lock:
            jobs = list(self.jobs.values())
        for job in jobs:
            job.cancel.set()

    def join(self, timeout: float) -> list[str]:
        """Wait up to timeout seconds for running jobs; -> the kinds still running."""
        deadline = time.monotonic() + timeout
        with self.lock:
            jobs = list(self.jobs.values())
        left = []
        for job in jobs:
            if job.thread is None:
                continue
            job.thread.join(max(0.0, deadline - time.monotonic()))
            if job.thread.is_alive():
                left.append(job.kind)
        return left

    def states(self) -> dict[str, dict]:
        with self.lock:
            jobs = dict(self.jobs)
        return {kind: job.state() for kind, job in jobs.items()}

    def running(self) -> bool:
        with self.lock:
            return any(job.running for job in self.jobs.values())
