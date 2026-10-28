"""Batch inference from basal-ui: basal-run over a JSONL file (simple items or full /v1/systemone requests) against a
loaded model's private basal-serve; the answers go to a JSONL file, one line per input line. The requests go straight
to the child: they are not counted in the statistics or the history (as benchmarks)."""
import json
from pathlib import Path

from .jobs import run_tool


def default_output(input_path) -> Path:
    path = Path(input_path)
    return path.with_name(f"{path.stem}.answers.jsonl")


def run_batch(job, fleet, model_id, input_path, output_path, concurrency=16, early_exit=None) -> dict:
    """The batch job: basal-run in its own process (it writes the output file at the end); a cancel stops it."""
    runner = fleet.ready(model_id)
    total = sum(1 for line in Path(input_path).read_text(encoding="utf-8").splitlines() if line.strip())
    job.progress("running", 0, total)
    args = ["--input", str(input_path), "--output", str(output_path), "--url", runner.base_url + "/v1/systemone",
            "--concurrency", str(concurrency)]
    if early_exit:
        args += ["--early-exit", str(early_exit)]
    summary = run_tool(job, "run", args)  # basal-run prints its summary as the last line
    if summary is None:
        raise RuntimeError("basal-run printed no summary")
    job.progress("running", total, total)
    return {**summary, "input": str(input_path), "output": str(output_path), "model": model_id}


def rows(output_path, offset=0, limit=100) -> dict:
    """A page of a batch output file: {"rows", "total"}."""
    lines = [line for line in Path(output_path).read_text(encoding="utf-8").splitlines() if line.strip()]
    return {"rows": [json.loads(line) for line in lines[offset:offset + limit]], "total": len(lines)}
