"""Custom calibration from basal-ui: basal-calibrate fits a calibration file on the user's labelled requests
(docs/CALIBRATION.md, input format) against a temporary, uncalibrated basal-serve of the model, in basal-ui's process.
The files live in settings.calibrations_dir(model); one per model can be selected (basal-serve --calibration)."""
import contextlib
import io
import json
import time
from pathlib import Path

from basal.server import MODES, calibration_file, certified_table

READY_POLL_S = 0.5
TARGETS = ("0.01", "0.05")


class _Lines(io.TextIOBase):
    """basal-calibrate's stdout and stderr, line by line into the job's log; `lines` keeps them for the report."""

    def __init__(self, log):
        self.log, self.lines, self.buffer = log, [], ""

    def writable(self):
        return True

    def write(self, text):
        self.buffer += text
        *done, self.buffer = self.buffer.split("\n")
        for line in done:
            self.log.append(line)
            self.lines.append(line)
        return len(text)

    def flush(self):
        if self.buffer:
            self.write("\n")


def summary(path) -> dict:
    """What the Calibration table shows of a calibration file."""
    cal = json.loads(Path(path).read_text())
    return {"fitted_on": cal.get("fitted_on"), "gates": cal.get("gates"), "thresholds": certified_table(cal),
            "temperature_per_prim": cal.get("temperature_per_prim"), "engine": cal.get("engine")}


def shipped_file(local_dir, mode, preset=None) -> Path:
    """The calibration file basal-serve loads for the model in `mode` without --calibration."""
    kind, quant = MODES[mode][:2]
    return calibration_file(Path(local_dir), kind, None, quant, preset)


def listing(model_id, settings_mod) -> list[dict]:
    """The calibration files basal-ui fitted for the model, newest first; unreadable files are listed with the error."""
    files = []
    for path in sorted(settings_mod.calibrations_dir(model_id).glob("*.json"), reverse=True):
        entry = {"file": str(path), "when": path.stat().st_mtime}
        try:
            entry.update(summary(path))
        except (OSError, ValueError) as exc:
            entry["error"] = str(exc)
        files.append(entry)
    return files


def run_calibration(job, fleet, assessment, local_dir, input_path, out_path, targets=TARGETS, options=None):
    """The calibrate job: a temporary basal-serve --no-calibration child, basal-calibrate --url on it, the child
    stopped; -> the file, the report (basal-calibrate's output, also written to <file>.report.txt) and its summary.
    basal-calibrate's output is redirected for the whole process while it runs (one calibration at a time)."""
    job.progress("starting model")
    runner = fleet.start_temporary("calibrate", assessment, job.log, options, extra_args=["--no-calibration"])
    writer = _Lines(job.log)
    try:
        while (state := runner.status())["state"] != "ready":
            job.check()
            if state["state"] == "error":
                raise RuntimeError(state["error"])
            if state["state"] == "idle":
                raise RuntimeError("the temporary basal-serve was stopped")
            time.sleep(READY_POLL_S)
        job.progress("calibrating")
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        from basal import calibrate
        argv = [str(input_path), "--url", runner.base_url, "--model", str(local_dir), "--out", str(out_path),
                "--targets", ",".join(targets)]
        try:
            with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
                calibrate.main(argv)
        except SystemExit as exc:
            if exc.code not in (None, 0):
                job.check()  # cancelled: the child was stopped under basal-calibrate
                raise RuntimeError(exc.code if isinstance(exc.code, str) else
                                   writer.lines[-1] if writer.lines else f"basal-calibrate exited ({exc.code})") from None
        finally:
            writer.flush()
    finally:
        fleet.stop_temporary("calibrate")
    job.check()
    if not Path(out_path).exists():
        raise RuntimeError("basal-calibrate wrote no file")
    report = "\n".join(writer.lines)
    Path(out_path).with_suffix(".report.txt").write_text(report + "\n")
    return {"file": str(out_path), "report": report, **summary(out_path)}
