"""Benchmark one loaded model from basal-ui: latency (sequential), throughput (concurrent) and accuracy on the items
with a `gold` answer, over the bundled example questions or a custom JSONL set (docs/BENCHMARK_SETS.md)."""
import asyncio
import json
import statistics
import time
from pathlib import Path

import httpx

from basal.bench import DEFAULT_QUESTIONS
from basal.loadtest import body

from .jobs import Cancelled

MIN_ITEMS = 10
WARMUP = 5


def load_items(paths) -> list[dict]:
    """Simple-format items {"state", "question", "options", "gold"?} of each file, in file order, tagged with the
    file's stem; ValueError names a malformed line."""
    items = []
    for path in paths:
        path = Path(path)
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except ValueError as exc:
                raise ValueError(f"{path}:{number}: not JSON ({exc})") from exc
            if isinstance(item, dict) and isinstance(item.get("options"), list) and len(item["options"]) >= 2 \
                    and isinstance(item.get("state"), (str, dict)) and isinstance(item.get("question"), str):
                items.append({**item, "_file": path.stem})
    return items


def resolve_set(spec) -> tuple[str, list[str]]:
    """"bundled" or a path to a .jsonl file or a directory of them -> (label, files); ValueError says why not."""
    if spec in (None, "", "bundled"):
        return "bundled", list(DEFAULT_QUESTIONS)
    if not isinstance(spec, str):
        raise ValueError("set must be \"bundled\" or a path")
    path = Path(spec).expanduser()
    if not path.exists():
        raise ValueError(f"{path} does not exist")
    files = sorted(path.glob("*.jsonl")) if path.is_dir() else [path]
    if not files:
        raise ValueError(f"{path} contains no .jsonl files")
    try:
        items = load_items(files)
    except OSError as exc:
        raise ValueError(f"cannot read {exc.filename}: {exc.strerror}") from exc
    if len(items) < MIN_ITEMS:
        raise ValueError(f"{path}: {len(items)} items with state, question and options; a benchmark set needs at "
                         f"least {MIN_ITEMS} (docs/BENCHMARK_SETS.md)")
    return str(path), [str(f) for f in files]


def _correct(item, response_json):
    """None without gold or an answer, else whether the most probable option is the gold one."""
    gold = item.get("gold")
    if not isinstance(gold, int) or isinstance(gold, bool):
        return None
    try:
        probabilities = response_json["answers"]["q0"]["probabilities"]
        return max(probabilities, key=probabilities.get) == f"option_{gold + 1}"
    except (KeyError, TypeError, ValueError):
        return None


async def run_benchmark(fleet, model_id, paths, label, job, n_seq=30, n_conc=120, concurrency=8) -> dict:
    """Warm-up, n_seq sequential requests (latency), n_conc at `concurrency` (throughput); every first answer to an
    item with `gold` is scored. Requests go through fleet.adecide with source "benchmark": not counted in the
    statistics or the history. Progress and cancellation through the job (basal/ui/jobs.py)."""
    items = load_items(paths)
    if not items:
        raise ValueError(f"{label}: no items")
    n = WARMUP + n_seq + n_conc
    order = [i % len(items) for i in range(n)]  # small sets are reused
    scored: dict[int, bool] = {}
    progress = job.progress

    def check():
        if job.cancel.is_set():
            raise Cancelled()

    def score(index, response):
        if index in scored or response.status_code != 200:
            return
        result = _correct(items[index], response.json())
        if result is not None:
            scored[index] = result

    async with httpx.AsyncClient(timeout=300) as client:
        async def ask(index):
            check()
            t0 = time.perf_counter()
            response = await fleet.adecide(model_id, body(items[index]), source="benchmark", client=client)
            return time.perf_counter() - t0, response

        for done, index in enumerate(order[:WARMUP]):
            progress("warm-up", done, WARMUP)
            _, response = await ask(index)
            response.raise_for_status()
            score(index, response)
        latencies = []
        for done, index in enumerate(order[WARMUP:WARMUP + n_seq]):
            progress("sequential", done, n_seq)
            seconds, response = await ask(index)
            response.raise_for_status()
            latencies.append(seconds * 1000)
            score(index, response)
        semaphore = asyncio.Semaphore(concurrency)
        finished = 0

        async def one(index):
            nonlocal finished
            async with semaphore:
                try:
                    _, response = await ask(index)
                except httpx.HTTPError:
                    return None
                finished += 1
                progress("concurrent", finished, n_conc)
                return index, response

        progress("concurrent", 0, n_conc)
        t0 = time.perf_counter()
        results = await asyncio.gather(*[one(i) for i in order[WARMUP + n_seq:]])
        wall = time.perf_counter() - t0
    check()  # a cancel during the last in-flight requests must not store a result
    ok, tokens = 0, 0
    for result in results:
        if result is None or result[1].status_code != 200:
            continue
        index, response = result
        ok += 1
        usage = response.json().get("usage") or {}
        tokens += usage.get("input_tokens", 0) or 0
        score(index, response)
    latencies.sort()
    by_file: dict[str, list[bool]] = {}
    for index, correct in scored.items():
        by_file.setdefault(items[index]["_file"], []).append(correct)
    values = list(scored.values())
    return {"model": model_id, "set": label, "items": len(items), "when": time.time(),
            "p50_ms": statistics.median(latencies) if latencies else None,
            "p95_ms": latencies[min(len(latencies) - 1, int(0.95 * len(latencies)))] if latencies else None,
            "mean_ms": statistics.fmean(latencies) if latencies else None,
            "decisions_per_s": ok / wall if wall else None, "questions_per_s": ok / wall if wall else None,
            "input_tokens_per_s": tokens / wall if wall else None, "errors": len(results) - ok,
            "accuracy": {"overall": sum(values) / len(values) if values else None,
                         "by_file": {stem: sum(v) / len(v) for stem, v in sorted(by_file.items())}}}


def benchmark_job(job, fleet, model_id, paths, label):
    """The benchmark job's target: run it, keep the result in the statistics file (last BENCHMARKS per model)."""
    result = asyncio.run(run_benchmark(fleet, model_id, paths, label, job))
    if fleet.stats is not None:
        fleet.stats.add_benchmark(model_id, result)
    return result
