"""The loaded models: one basal-serve child per Runner, owned by the Fleet with their downloads, logs, jobs, statistics and history."""

import asyncio
import dataclasses
import hashlib
import os
import secrets
import shlex
import socket
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import httpx

from . import catalog, hardware, settings
from . import options as load_options
from .compat import child_request
from .history import make_record
from .jobs import Jobs, LogBuffer, terminate

START_TIMEOUT_S = 1800  # --mode fast compiles for 4-17 minutes on the first start (README, Serving modes)
MODEL_LOGS_KEPT = 10  # log buffers of unloaded models kept for the Logs view
RSS_INTERVAL_S = 2  # `ps` runs at most this often for the resident memory of the children
JOB_SHUTDOWN_S = 30  # shutdown waits this long for cancelled jobs before unloading the models


def _lan_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1", 80))
            return sock.getsockname()[0]
    except OSError:
        return socket.gethostname()


def _fmt(n):
    return f"{n / 1024**3:.1f} GiB"


def rss_by_pgid() -> dict[int, int]:
    """Resident memory (bytes) per process group: a basal-serve child leads its own group (start_new_session), which
    also holds its engine's workers. Empty where ps is unavailable (Windows)."""
    if os.name != "posix":
        return {}
    try:
        out = subprocess.run(["ps", "-eo", "pgid=,rss="], capture_output=True, text=True, timeout=5, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    totals: dict[int, int] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            totals[int(parts[0])] = totals.get(int(parts[0]), 0) + int(parts[1]) * 1024
    return totals


class Downloads:
    """Model downloads by repository, shared by the runners and the Download button. A download outlives the load that
    started it (an unload leaves it finishing into the cache); a failed one stays reported for ERROR_KEEP_S."""

    ERROR_KEEP_S = 60

    def __init__(self, event=None):
        self.lock = threading.Lock()
        self.records: dict[str, dict] = {}
        self.event = event or (lambda level, message: None)

    def start(self, variant, cache_dir, revision=None) -> dict:
        with self.lock:
            record = self.records.get(variant.repo)
            if record and record["thread"].is_alive():
                return record
            record = {
                "bar": _byte_counter(),
                "total": variant.weights_bytes or 0,
                "path": None,
                "error": None,
                "ended": None,
                "revision": revision,
            }
            record["thread"] = threading.Thread(target=self._run, args=(record, variant, cache_dir), daemon=True)
            self.records[variant.repo] = record
            record["thread"].start()
        self.event("info", f"download started: {variant.repo}")
        return record

    def _run(self, record, variant, cache_dir):
        try:
            record["path"] = catalog.download(variant, cache_dir, tqdm_class=record["bar"], revision=record["revision"])
            self.event("info", f"download finished: {variant.repo}")
        except Exception as exc:  # reported in the UI and re-raised by a waiting load
            record["error"] = exc
            self.event("error", f"download failed: {variant.repo}: {exc}")
        finally:
            record["ended"] = time.monotonic()

    def alive(self, repo) -> bool:
        with self.lock:
            record = self.records.get(repo)
            return bool(record and record["thread"].is_alive())

    def any_alive(self) -> bool:
        with self.lock:
            return any(record["thread"].is_alive() for record in self.records.values())

    def status(self) -> dict:
        now, out = time.monotonic(), {}
        with self.lock:
            for repo, record in list(self.records.items()):
                failed = record["error"] is not None and now - record["ended"] < self.ERROR_KEEP_S
                if record["thread"].is_alive() or failed:
                    done = record["bar"].done
                    out[repo] = {
                        "done_bytes": done,
                        "total_bytes": max(record["total"], done),
                        "error": str(record["error"]) if record["error"] is not None else None,
                    }
                else:
                    del self.records[repo]
        return out


def _child_settings(serve, calibration=None):
    """The settings a basal-serve child is started with (the rest belongs to the API server): the decision log, the
    vLLM / SGLang context and the calibration file selected for the model (None: the shipped one)."""
    return {"log_decisions": serve["log_decisions"], "max_len": serve["max_len"], "calibration": calibration}


class Runner:
    """One model: idle -> downloading -> starting -> ready, or error. Every load or unload starts a new generation; a
    background load that finds its generation outdated stops instead of touching the state."""

    def __init__(
        self,
        models_dir_fn=settings.models_dir,
        platform_fn=lambda: hardware.detect().platform,
        settings_mod=settings,
        downloads=None,
        log=None,
        event=None,
    ):
        self.models_dir_fn = models_dir_fn
        self.platform_fn = platform_fn
        self.settings_mod = settings_mod
        self.downloads = downloads if downloads is not None else Downloads()
        self.log = log if log is not None else LogBuffer()
        self.event = event or (lambda level, message: None)
        self.lock = threading.RLock()
        self.gen = 0
        self.process = None
        self.rss_bytes = None
        self._reset()

    def _reset(self):
        self.state, self.model, self.model_repo, self.mode = "idle", None, None, None
        self.command, self.progress, self.error, self.base_url = None, None, None, None
        self.media_requested, self.media = False, []
        self.est_memory_bytes, self.models_entry, self.ready_at = None, None, None
        self.loaded_path = None
        self.serve_snapshot = None  # the child settings the running basal-serve was started with
        self.extra_args, self.temporary, self.options = (), False, {}
        self.local_only, self.remember, self.snapshot_override = False, True, None
        self.generation = secrets.token_hex(16)
        self.health = {"reachable": None, "running": None, "queued": None}
        self.health_checked = 0.0
        self.health_polling = False

    @property
    def pid(self):
        return self.process.pid if self.process else None

    def _poll_health(self, gen, url):
        health = {"reachable": False, "running": None, "queued": None}
        try:
            response = httpx.get(url + "/health", timeout=0.5)
            response.raise_for_status()
            data = response.json()
            health["reachable"] = isinstance(data, dict) and data.get("status") == "ok"
            if health["reachable"]:
                for key in ("running", "queued"):
                    value = data.get(key)
                    if type(value) is int and value >= 0:
                        health[key] = value
        except (httpx.HTTPError, ValueError):
            pass
        finally:
            with self.lock:
                if gen == self.gen:
                    self.health, self.health_polling = health, False

    def status(self):
        with self.lock:
            if self.state == "ready" and self.process and self.process.poll() is not None:
                self.state = "error"
                self.error = f"basal-serve exited ({self.process.returncode}): {self.log.last() or ''}"
                self.event("error", f"{self.model}: {self.error}")
            if (
                self.state == "ready"
                and self.base_url
                and not self.health_polling
                and time.monotonic() - self.health_checked >= 1
            ):
                self.health_checked, self.health_polling = time.monotonic(), True
                threading.Thread(target=self._poll_health, args=(self.gen, self.base_url), daemon=True).start()
            restart_required = (
                self.state == "ready"
                and self.serve_snapshot is not None
                and not self.temporary
                and self.serve_snapshot
                != _child_settings(
                    self.settings_mod.serve_settings(), self.settings_mod.calibration_selection().get(self.model)
                )
            )
            return {
                "id": self.model,
                "model": self.model,
                "repo": self.model_repo,
                "state": self.state,
                "mode": self.mode,
                "command": self.command,
                "progress": self.progress,
                "error": self.error,
                "media_requested": self.media_requested,
                "media": self.media,
                "restart_required": restart_required,
                "est_memory_bytes": self.est_memory_bytes,
                "rss_bytes": self.rss_bytes,
                "base_url": self.base_url,
                "entry": self.models_entry,
                "generation": self.generation,
                "options": dict(self.options),
                "health": dict(self.health)
                if self.state == "ready"
                else {"reachable": None, "running": None, "queued": None},
            }

    def load(
        self,
        assessment,
        media=False,
        options=None,
        extra_args=(),
        temporary=False,
        local_only=False,
        snapshot=None,
        remember=True,
    ):
        """options: load options (basal_ui/options.py; their mode replaces the assessment's, except with media);
        temporary: a private child for a job (basal-calibrate): no API key (it is reached on loopback without one),
        no calibration selected in basal-ui, not remembered for auto-load; extra_args go to basal-serve last."""
        options = load_options.non_default(load_options.validate(options, assessment.mode))
        if media:  # --media on: eager, or vLLM where chosen (prepare checks it can run)
            assessment = dataclasses.replace(assessment, mode="vllm" if options.get("mode") == "vllm" else "eager")
        elif options.get("mode"):
            assessment = dataclasses.replace(assessment, mode=options["mode"])
        with self.lock:
            if self.state in ("downloading", "starting"):
                raise RuntimeError("the model is already loading; unload it first")
            old, _ = self.detach()
            self.state, self.model, self.model_repo = "downloading", assessment.variant.id, assessment.variant.repo
            self.mode = assessment.mode
            self.media_requested, self.media = media, []
            self.est_memory_bytes = assessment.est_memory_bytes
            self.extra_args, self.temporary, self.options = tuple(extra_args), temporary, options
            self.local_only, self.snapshot_override, self.remember = local_only, snapshot, remember
            gen = self.gen
            cache_dir = self.models_dir_fn()
            serve = {**self.settings_mod.serve_settings(), **(snapshot or {})}
        self._terminate(old)
        threading.Thread(target=self._start, args=(assessment, gen, media, cache_dir, serve), daemon=True).start()
        return self.status()

    def _update(self, gen, **fields):
        """Set fields if gen is still the current load; False when it was unloaded or replaced meanwhile."""
        with self.lock:
            if gen != self.gen:
                return False
            for k, v in fields.items():
                setattr(self, k, v)
            return True

    def _download(self, v, gen, cache_dir, revision=None):
        record = self.downloads.start(v, cache_dir, revision=revision)
        while record["thread"].is_alive():
            done = record["bar"].done
            if not self._update(gen, progress={"done_bytes": done, "total_bytes": max(v.weights_bytes or 0, done)}):
                return None  # unloaded while downloading: the download finishes in the background, into the cache
            record["thread"].join(0.5)
        if record["error"] is not None:
            raise record["error"]
        return Path(record["path"])

    def _calibration(self, v):
        if self.snapshot_override is not None:
            return self.snapshot_override.get("calibration")
        return None if self.temporary else self.settings_mod.calibration_selection().get(v.id)

    def _build_command(self, v, path, assessment, media, port, serve):
        """The child is private: loopback and a free port only basal-ui knows."""
        command = [
            sys.executable,
            "-m",
            "basal.server",
            *catalog.serve_args(
                v, path, assessment.mode, self.platform_fn() if media else None, platform=self.platform_fn()
            ),
            "--name",
            v.id,
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ]
        if self._calibration(v):
            command += ["--calibration", self._calibration(v)]
        if serve["log_decisions"] and not self.temporary:
            command += ["--log-decisions", serve["log_decisions"]]
        if assessment.mode in ("vllm", "sglang") and self.options.get("max_len") is None:
            command += ["--max-len", str(serve["max_len"])]
        if not media and assessment.mode == "mps" and self.platform_fn() != "apple" and not self.options.get("device"):
            command += ["--device", "cpu"]
        return command + load_options.args(self.options) + list(self.extra_args)

    def _models_entry(self, media):
        """The child's GET /v1/models entry and, with media, its media kinds (required then, best effort else)."""
        attempts, error = (5 if media else 3), None
        for attempt in range(attempts):
            try:
                response = httpx.get(self.base_url + "/v1/models", timeout=1)
                response.raise_for_status()
                models = response.json().get("models", [])
                entry = models[0] if models else None
                return entry, (entry.get("media", []) if media and entry else [])
            except Exception as exc:  # noqa: BLE001
                error = exc
                if attempt < attempts - 1:
                    time.sleep(0.5)
        if media:
            raise RuntimeError(f"could not read media capabilities from basal-serve: {error}")
        return None, []

    def _start(self, assessment, gen, media=False, cache_dir=None, serve=None):
        v = assessment.variant
        serve = serve or self.settings_mod.serve_settings()
        try:
            revision = self.options.get("revision")
            path = catalog.is_downloaded(v, cache_dir, revision=revision)
            if path is None and self.local_only:
                raise RuntimeError("downloaded model files are missing; this operation never downloads")
            path = path or self._download(v, gen, cache_dir, revision)
            if path is None:
                return
            with socket.socket() as s:
                s.bind(("127.0.0.1", 0))
                port = s.getsockname()[1]
            command = self._build_command(v, path, assessment, media, port, serve)
            options = (
                {"start_new_session": True}
                if os.name == "posix"
                else {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
            )
            with self.lock:
                if gen != self.gen:
                    return
                self.loaded_path = str(path)
                self.state, self.progress, self.command = "starting", None, command
                self.base_url = f"http://127.0.0.1:{port}"
                self.serve_snapshot = _child_settings(serve, self._calibration(v))
                self.process = proc = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    env={
                        **os.environ,
                        **({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"} if self.local_only else {}),
                    },
                    **options,
                )
            self.event("info", f"{v.id}: basal-serve starting on 127.0.0.1:{port}: {shlex.join(map(str, command))}")
            threading.Thread(target=self._read_logs, args=(proc,), daemon=True).start()
            deadline = time.monotonic() + START_TIMEOUT_S
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    time.sleep(0.2)  # let the log reader drain the last lines
                    raise RuntimeError(f"basal-serve exited ({proc.returncode}): {self.log.last() or 'no output'}")
                try:
                    if httpx.get(self.base_url + "/health", timeout=1).status_code == 200:
                        entry, media_kinds = self._models_entry(media)
                        if entry is not None and "certified" not in entry:  # basal-serve 2.4.1 does not report them
                            from .calibration import served_certified

                            entry = {
                                **entry,
                                "certified": served_certified(
                                    entry, path, assessment.mode, self.options, self._calibration(v)
                                ),
                            }
                        with self.lock:
                            if gen != self.gen:
                                return
                            self.state, self.media = "ready", media_kinds
                            self.models_entry, self.ready_at = entry, time.time()
                            if not self.temporary and self.remember:
                                self.settings_mod.remember_loaded(v.id, media, self.options)
                        self.event("info", f"{v.id}: ready")
                        return
                except httpx.HTTPError:
                    pass
                if gen != self.gen:
                    return
                time.sleep(0.5)
            raise RuntimeError(f"basal-serve did not become healthy within {START_TIMEOUT_S} s")
        except Exception as exc:  # any failure of this load is shown in the UI
            proc = None
            with self.lock:
                if gen == self.gen:
                    proc = self.process
                    self.process = None
                    self.gen += 1
                    self.state, self.error = "error", str(exc)
            if proc is not None or self.state == "error":
                self.event("error", f"{v.id}: {exc}")
            self._terminate(proc)

    def _read_logs(self, proc):
        for line in proc.stdout:
            self.log.append(line.rstrip())

    def unload(self):
        proc, _ = self.detach()
        self._terminate(proc)
        return self.status()

    def detach(self):
        """Start a new generation and forget the model; -> (the child to terminate, the memory estimate it held)."""
        with self.lock:
            self.gen += 1
            proc, self.process = self.process, None
            est = self.est_memory_bytes if self.state not in ("idle", "error") else None
            self._reset()
            return proc, est

    _terminate = staticmethod(terminate)

    def _url(self, path):
        with self.lock:
            if self.state != "ready":
                raise RuntimeError(f"model {self.model} is not ready ({self.state})")
            return self.base_url + path

    def decide(self, body, path="/v1/systemone"):
        """basal-serve's answer to a System One or OpenAI Decisions request (path), in the path's shape."""
        child_body, reply = child_request(path, body)
        if child_body is None:
            return reply
        return reply(httpx.post(self._url("/v1/systemone"), json=child_body, timeout=120))

    async def adecide(self, client, body, path="/v1/systemone"):
        child_body, reply = child_request(path, body)
        if child_body is None:
            return reply
        return reply(await client.post(self._url("/v1/systemone"), json=child_body))


class Fleet:
    """The loaded models (one Runner each, in load order), their shared downloads, logs, statistics and history."""

    def __init__(self, models_dir_fn, platform_fn, settings_mod, hardware_fn, stats=None, history=None):
        self.models_dir_fn, self.platform_fn = models_dir_fn, platform_fn
        self.settings_mod, self.hardware_fn = settings_mod, hardware_fn
        self.stats, self.history = stats, history
        self.internal_secret = secrets.token_urlsafe(32)
        self.lock = threading.RLock()
        self.runners: dict[str, Runner] = {}
        self.logs: dict[str, LogBuffer] = {"basal-ui": LogBuffer()}
        self.downloads = Downloads(self.event)
        self.jobs = Jobs(self.logs, self.event)
        self.draining: set[str] = set()
        self.leases: dict[Runner, int] = {}
        self.inflight: dict[object, tuple[str, str, float]] = {}  # each request being answered: (model, source, start)
        self.stopping = False
        self.temporary: dict[str, Runner] = {}  # private children of jobs (basal-calibrate's uncalibrated server)
        self.reserved: dict[str, int] = {}  # memory of models a job loads into basal-ui's own process
        self._lan_ip = None
        self.retiring: dict[Runner, int | None] = {}  # unloaded runners whose child is still being terminated
        self._rss_cache = (0.0, {})

    def event(self, level, message):
        self.logs["basal-ui"].append(f"{level} {message}")

    def internal_sha256(self):
        return hashlib.sha256(self.internal_secret.encode()).hexdigest()

    def lan_ip(self):
        if self._lan_ip is None:
            self._lan_ip = _lan_ip()
        return self._lan_ip

    def _memory(self, rss_total=0):
        config = self.settings_mod.memory_settings()
        hw = self.hardware_fn()
        budget = config["budget_bytes"] or hw.memory_bytes
        committed = (
            sum(r.est_memory_bytes or 0 for r in self.runners.values() if r.state not in ("idle", "error"))
            + sum(r.est_memory_bytes or 0 for r in self.temporary.values() if r.state not in ("idle", "error"))
            + sum(est or 0 for est in self.retiring.values())
            + sum(self.reserved.values())
        )
        return {
            "budget_bytes": budget,
            "reserve_bytes": config["reserve_bytes"],
            "hardware_budget_bytes": hw.memory_bytes,
            "available_bytes": hw.available_bytes,
            "committed_bytes": committed,
            "rss_bytes": rss_total,
            "over": committed > budget - config["reserve_bytes"],
        }

    def check_budget(self, needed):
        """RuntimeError when `needed` more bytes would go over the memory budget (call under the fleet lock)."""
        memory = self._memory()  # an errored load being replaced has no process and does not count
        limit = memory["budget_bytes"] - memory["reserve_bytes"]
        if memory["committed_bytes"] + (needed or 0) > limit:
            raise RuntimeError(
                f"over memory budget: {_fmt(memory['committed_bytes'])} loaded + {_fmt(needed or 0)} needed > "
                f"{_fmt(limit)} ({_fmt(memory['budget_bytes'])} budget − {_fmt(memory['reserve_bytes'])} kept "
                "free); unload a model or raise the budget"
            )

    def _runner(self, log):
        return Runner(
            models_dir_fn=self.models_dir_fn,
            platform_fn=self.platform_fn,
            settings_mod=self.settings_mod,
            downloads=self.downloads,
            log=log,
            event=self.event,
        )

    def start_temporary(
        self, kind, assessment, log, options=None, extra_args=(), local_only=False, reservation=None
    ) -> Runner:
        """A private, unremembered child for a job (its memory counted in the budget); stop_temporary ends it."""
        with self.lock:
            if kind in self.temporary:
                raise RuntimeError(f"a {kind} child is already running")
            if self.stopping:
                raise RuntimeError("app is shutting down")
            if reservation is not None:
                if reservation not in self.reserved:
                    raise RuntimeError("test memory reservation was lost")
                self.reserved.pop(reservation)
            else:
                self.check_budget(assessment.est_memory_bytes)
            runner = self.temporary[kind] = self._runner(log)
            runner.load(
                assessment,
                options=options,
                extra_args=extra_args,
                temporary=True,
                **({"local_only": True} if local_only else {}),
            )
        self.event("info", f"{kind}: temporary basal-serve for {assessment.variant.id}")
        return runner

    def stop_temporary(self, kind):
        with self.lock:
            runner = self.temporary.pop(kind, None)
            if runner is not None:
                self.retiring[runner] = runner.est_memory_bytes
        if runner is not None:
            runner.unload()
            with self.lock:
                self.retiring.pop(runner, None)

    def reserve(self, kind, needed):
        """Count `needed` bytes for a job that loads a model into basal-ui's own process (RuntimeError over the
        budget); release(kind) frees them."""
        with self.lock:
            if kind in self.reserved:
                raise RuntimeError(f"a {kind} job already holds memory")
            self.check_budget(needed)
            self.reserved[kind] = needed or 0

    def release(self, kind):
        with self.lock:
            self.reserved.pop(kind, None)

    def load(self, assessment, media=False, options=None, _reservation=None, **runner_options):
        """_reservation: the memory restart() reserved; that restart also holds the model in maintenance."""
        model_id = assessment.variant.id
        with self.lock:
            if self.stopping or (model_id in self.draining and _reservation is None):
                raise RuntimeError("model is unavailable during maintenance")
            existing = self.runners.get(model_id)
            state = existing.status()["state"] if existing is not None else "idle"
            if state not in ("idle", "error"):
                raise RuntimeError(f"model {model_id} is already {'loaded' if state == 'ready' else 'loading'}")
            if _reservation is None:
                self.check_budget(assessment.est_memory_bytes)
            else:
                reserved = self.reserved.get(_reservation)
                if reserved is None:
                    raise RuntimeError("restart memory reservation was lost")
                self.reserved.pop(_reservation)
            self.logs.pop(model_id, None)
            self.logs[model_id] = log = LogBuffer()  # most recent last; a reload starts a fresh log
            runner = self._runner(log)
            self.runners[model_id] = runner
            self._prune_logs()
            runner.load(assessment, media=media, options=options, **runner_options)
        if existing is not None:
            existing.unload()
        self.event("info", f"load requested: {model_id}{' with media' if media else ''}")
        return self.status()

    def _prune_logs(self):
        unloaded = [
            key for key in self.logs if key != "basal-ui" and not key.startswith("job:") and key not in self.runners
        ]
        for key in unloaded[: max(0, len(self.logs) - 1 - MODEL_LOGS_KEPT)]:
            del self.logs[key]

    def unload(self, model_id=None, forget=True):
        """Unload one model (KeyError when it is not loaded) or every model; forget=False keeps it for auto-load."""
        with self.lock:
            if model_id is not None and model_id not in self.runners:
                raise KeyError(model_id)
            ids = [model_id] if model_id is not None else list(self.runners)
            if not self.stopping and any(i in self.draining for i in ids):
                raise RuntimeError("model is unavailable during maintenance")
            retiring = []
            for i in ids:
                runner = self.runners.pop(i)
                # detach under the fleet lock: the old load can no longer reach ready and remember itself after
                # forget_loaded; its memory stays committed until the child is gone (a new load must wait for it)
                proc, est = runner.detach()
                retiring.append((runner, proc))
                self.retiring[runner] = est
                if forget:
                    self.settings_mod.forget_loaded(i)
            self._prune_logs()
        for i, (runner, proc) in zip(ids, retiring, strict=True):
            Runner._terminate(proc)
            with self.lock:
                self.retiring.pop(runner, None)
            self.event("info", f"unloaded: {i}")
        if model_id is None:  # every model: the private children of jobs too (their job then fails)
            with self.lock:
                temporary = list(self.temporary)
            for kind in temporary:
                self.stop_temporary(kind)
        return self.status()

    def restart(self, assessment, media=False, options=None):
        """Unload and load again; options None keeps the running child's load options. The model stays in maintenance
        (no concurrent load) until the replacement load starts, and a failed load releases the memory it reserved."""
        model_id = assessment.variant.id
        with self.lock:
            if model_id in self.draining:
                raise RuntimeError("model is unavailable during maintenance")
            current = self.runners.get(model_id)
            if current is None:
                raise KeyError(model_id)
            if options is None:
                options = current.options
            proc, est = current.detach()
            self.runners.pop(model_id)
            self.retiring[current] = est
            self.draining.add(model_id)
            # The existing remembered model remains available for auto-load after the restart.
        self.event("info", f"restart: {model_id}")
        reservation = f"restart:{model_id}"
        try:
            Runner._terminate(proc)
            with self.lock:
                self.retiring.pop(current, None)
                self.reserved[reservation] = assessment.est_memory_bytes or 0
            return self.load(assessment, media=media, options=options, _reservation=reservation)
        finally:
            with self.lock:
                self.retiring.pop(current, None)
                self.draining.discard(model_id)
                self.reserved.pop(reservation, None)

    def download(self, variant) -> bool:
        """Start a runner-less download into the models directory; False when the model is on disk already.
        Under the fleet lock, so the directory cannot change (while_idle) or the repository be deleted
        (while_not_busy) between the check and the start."""
        with self.lock:
            cache_dir = self.models_dir_fn()
            if catalog.is_downloaded(variant, cache_dir):
                return False
            self.downloads.start(variant, cache_dir)
            return True

    def status(self):
        with self.lock:
            runners = list(self.runners.values())
        rss = self._rss() if any(r.process for r in runners) else {}
        models = []
        for runner in runners:
            runner.rss_bytes = rss.get(runner.pid) if runner.pid else None
            models.append(runner.status())
        now = time.monotonic()
        with self.lock:
            memory = self._memory(sum(m["rss_bytes"] or 0 for m in models))
            inflight = list(self.inflight.values())
        for m in models:  # what basal-ui is waiting on (Playground, API server, MCP, benchmark), oldest first
            m["inflight"] = [
                {"source": source, "seconds": round(now - t0, 1)}
                for model, source, t0 in sorted(inflight, key=lambda x: x[2])
                if model == m["id"]
            ]
        return {"models": models, "downloads": self.downloads.status(), "memory": memory, "jobs": self.jobs.states()}

    def _rss(self):
        """rss_by_pgid() at most every RSS_INTERVAL_S: the UI polls every second while a model loads."""
        now = time.monotonic()
        if now - self._rss_cache[0] > RSS_INTERVAL_S:
            self._rss_cache = (now, rss_by_pgid())
        return self._rss_cache[1]

    def ready(self, model_id) -> Runner:
        with self.lock:
            if self.stopping or model_id in self.draining:
                raise RuntimeError(f"model {model_id} is not ready (maintenance)")
            runner = self.runners.get(model_id)
        if runner is None:
            raise KeyError(model_id)
        state = runner.status()["state"]
        if state != "ready":
            raise RuntimeError(f"model {model_id} is not ready ({state})")
        return runner

    def loaded_ids(self):
        with self.lock:
            runners = list(self.runners.items())
        return [model_id for model_id, runner in runners if runner.status()["state"] == "ready"]

    def model_entries(self):
        """The ready models for GET /v1/models: id, the child's own entry, the time it became ready."""
        with self.lock:
            runners = list(self.runners.items())
        return [
            {"id": model_id, "entry": runner.models_entry, "created": int(runner.ready_at or 0)}
            for model_id, runner in runners
            if runner.status()["state"] == "ready"
        ]

    def busy(self, repo) -> bool:
        """A download of the repository is running (also one left in the background by an unload), or a loaded or
        loading model comes from it."""
        with self.lock:
            return self.downloads.alive(repo) or any(
                r.model_repo == repo and r.state != "idle" for r in [*self.runners.values(), *self.temporary.values()]
            )

    def while_idle(self, fn):
        """fn() with no model, job child and download running, atomically with load()."""
        with self.lock:
            if self.runners or self.temporary or self.downloads.any_alive():
                raise RuntimeError("unload every model before changing the models directory")
            return fn()

    def while_not_busy(self, repo, fn):
        """fn() while nothing uses the repository, atomically with load()."""
        with self.lock:
            if self.busy(repo):
                raise RuntimeError("unload the model first")
            return fn()

    def _record(self, model_id, body, path, source, status, data, latency_ms):
        """Count and keep the decision; a failed write is logged, never turned into a failed answer."""
        if source == "benchmark":
            return
        try:
            if self.stats is not None:
                self.stats.record(model_id, data, latency_ms, ok=200 <= status < 300)
            if self.history is not None:
                request = {"model": model_id, **body} if path == "/v1/systemone" else body
                self.history.append(make_record(model_id, source, request, status, data, latency_ms))
        except OSError as exc:
            self.event("error", f"decision not recorded: {exc}")

    @staticmethod
    def _json(response):
        try:
            return response.json()
        except ValueError:
            return {"error": response.text}

    @contextmanager
    def lease(self, model_id):
        with self.lock:
            runner = self.ready(model_id)
            self.leases[runner] = self.leases.get(runner, 0) + 1
        try:
            yield runner
        finally:
            with self.lock:
                count = self.leases.get(runner, 1) - 1
                if count:
                    self.leases[runner] = count
                else:
                    self.leases.pop(runner, None)

    def start_model_job(self, kind, model_id, label, target, *args):
        # Acquire before scheduling: a queued job already owns its model.
        lease = self.lease(model_id)
        lease.__enter__()

        def run(job, *values):
            try:
                return target(job, *values)
            finally:
                lease.__exit__(None, None, None)

        try:
            return self.jobs.start(kind, model_id, label, run, *args)
        except BaseException:
            lease.__exit__(None, None, None)
            raise

    @contextmanager
    def answering(self, model_id, source):
        token = object()
        with self.lock:
            self.inflight[token] = (model_id, source, time.monotonic())
        try:
            yield
        finally:
            with self.lock:
                self.inflight.pop(token, None)

    def decide(self, model_id, body, path="/v1/systemone", source="playground"):
        with self.lease(model_id) as runner, self.answering(model_id, source):
            t0 = time.perf_counter()
            try:
                response = runner.decide(body, path)
            except httpx.HTTPError as exc:
                self._record(
                    model_id,
                    body,
                    path,
                    source,
                    502,
                    {"error": f"basal-serve: {exc}"},
                    (time.perf_counter() - t0) * 1000,
                )
                raise
            self._record(
                model_id,
                body,
                path,
                source,
                response.status_code,
                self._json(response),
                (time.perf_counter() - t0) * 1000,
            )
            return response

    async def adecide(self, model_id, body, path="/v1/systemone", source="api", client=None):
        with self.lease(model_id) as runner, self.answering(model_id, source):
            t0 = time.perf_counter()
            try:
                response = await runner.adecide(client, body, path)
            except httpx.HTTPError as exc:
                await asyncio.to_thread(
                    self._record,
                    model_id,
                    body,
                    path,
                    source,
                    502,
                    {"error": f"basal-serve: {exc}"},
                    (time.perf_counter() - t0) * 1000,
                )
                raise
            await asyncio.to_thread(
                self._record,
                model_id,
                body,
                path,
                source,
                response.status_code,
                self._json(response),
                (time.perf_counter() - t0) * 1000,
            )
            return response

    def shutdown(self):
        """Cancel the jobs, end their private children (a job reading from one fails through job.check()), wait up
        to JOB_SHUTDOWN_S for them, then unload every model. A calibration fitted inside basal-ui's own process cannot
        be interrupted: it is bounded by that wait, and its daemon thread ends with the process."""
        with self.lock:
            self.stopping = True
            temporary = list(self.temporary)
        self.jobs.cancel_all()
        for kind in temporary:
            self.stop_temporary(kind)
        left = self.jobs.join(JOB_SHUTDOWN_S)
        if left:
            self.event("warn", f"shutdown: jobs still running: {', '.join(left)}")
        self.unload(forget=False)


class _Sink:
    def write(self, _text):
        pass

    def flush(self):
        pass


def _byte_counter():
    """A tqdm class for snapshot_download that prints nothing and keeps, as the class attribute `done`, the larger of
    its two aggregated byte bars: bytes received ("Downloading bytes", which Xet reports while chunks arrive) and
    bytes written ("Reconstructing", fed by every file, Xet or plain HTTP)."""
    from tqdm.auto import tqdm

    class Counter(tqdm):
        done = 0

        def __init__(self, *args, **kwargs):
            kwargs["file"] = _Sink()
            super().__init__(*args, **kwargs)

        def update(self, n=1):
            out = super().update(n)
            if self.unit == "B" and str(self.desc).startswith(("Reconstructing", "Downloading bytes")):
                Counter.done = max(Counter.done, int(self.n))
            return out

    return Counter
