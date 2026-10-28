import contextlib
import os
import signal
import subprocess
import sys
import threading
import time

import pytest

from basal_ui.jobs import Jobs, LogBuffer, terminate


def wait_done(jobs, kind, timeout=3):
    deadline = time.monotonic() + timeout
    while jobs.states()[kind]["running"] and time.monotonic() < deadline:
        time.sleep(0.01)
    return jobs.states()[kind]


def test_one_job_per_kind_cancel_error_and_result():
    logs, events = {}, []
    jobs = Jobs(logs, lambda level, message: events.append((level, message)))
    release = threading.Event()

    def waits(job):
        job.progress("waiting", 1, 2)
        while not release.is_set():
            job.check()
            time.sleep(0.01)
        job.log.append("finished")
        return {"value": 1}

    assert jobs.start("bench", "m", "label", waits)["running"] is True
    with pytest.raises(RuntimeError, match="a bench job is already running"):
        jobs.start("bench", "m", "label", waits)
    jobs.start("other", None, "other", lambda job: {"ok": True})  # another kind runs alongside
    assert wait_done(jobs, "other")["result"] == {"ok": True}
    release.set()
    state = wait_done(jobs, "bench")
    assert (state["phase"], state["result"], state["error"]) == ("done", {"value": 1}, None)
    assert logs["job:bench"].last() == "finished"

    release.clear()
    jobs.start("bench", "m", "again", waits)
    assert jobs.cancel("bench")["running"] is True
    assert wait_done(jobs, "bench")["phase"] == "cancelled"
    assert jobs.cancel("missing") is None

    def fails(job):
        raise ValueError("bad input")

    jobs.start("bench", "m", "fails", fails)
    state = wait_done(jobs, "bench")
    assert (state["phase"], state["error"], state["running"]) == ("error", "bad input", False)
    assert logs["job:bench"].last() == "error: bad input" and ("error", "bench failed: fails: bad input") in events


def test_job_phase_codes_preserve_english_phase_and_capture_parameters():
    from basal_ui.jobs import PHASE_TEMPLATES, Job, phase_fields

    assert phase_fields("restoring model/example") == ("phase_restoring", {"model": "model/example"})
    job = Job("backend-test", "m", "label", LogBuffer())
    job.progress("checking selected models")
    state = job.state()
    assert state["phase"] == "checking selected models"
    assert state["phase_code"] == "phase_checking"
    assert state["phase_params"] == {}
    assert PHASE_TEMPLATES[state["phase_code"]].format(**state["phase_params"]) == state["phase"]


def test_log_buffer_generation_changes_when_sequence_restarts():
    old = LogBuffer()
    old.append("old")
    new = LogBuffer()
    new.append("new")
    assert old.generation != new.generation
    assert [line["seq"] for line in new.since(0)] == [1]


def test_shutdown_join_waits_for_a_cancelled_job_and_reports_the_stuck_one():
    jobs = Jobs({})
    stuck_release = threading.Event()

    def polite(job):
        while True:
            job.check()
            time.sleep(0.01)

    def stuck(job):  # ignores cancel, like an in-process calibration
        stuck_release.wait(5)

    jobs.start("bench", "m", "polite", polite)
    jobs.start("calibrate", "m", "stuck", stuck)
    jobs.cancel_all()
    try:
        assert jobs.join(0.5) == ["calibrate"]
        assert jobs.states()["bench"]["phase"] == "cancelled"
    finally:
        stuck_release.set()
    assert jobs.join(2) == []


@pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX-only")
def test_terminate_kills_a_group_member_that_survives_sigterm():
    grandchild = (
        "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('ready', flush=True); time.sleep(60)"
    )
    leader = (
        "import subprocess, sys, time\n"
        f"p = subprocess.Popen([sys.executable, '-c', {grandchild!r}], stdout=subprocess.PIPE, text=True)\n"
        "p.stdout.readline()\n"
        "print(p.pid, flush=True)\n"
        "time.sleep(60)\n"
    )
    proc = subprocess.Popen([sys.executable, "-c", leader], stdout=subprocess.PIPE, text=True, start_new_session=True)
    pid = int(proc.stdout.readline())
    try:
        terminate(proc)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            pytest.fail("the SIGTERM-ignoring group member survived terminate()")
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)
        proc.stdout.close()
