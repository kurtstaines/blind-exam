"""Run a blind exam: one fresh contestant call per item, resumable, scored against a hidden key.

An exam is a folder:

    exam.json   name, instruction, required answer keys, scorer, and the items contestants see
    key.json    the ground truth per item id; never shown to a contestant

A contestant is any callable ``(prompt, item) -> str``. The harness calls it once per item with
no shared state, extracts ONE JSON object from the reply, and stores it. Failures are stored with
a reason so a later run can retry just those.
"""

from __future__ import annotations

import importlib
import json
import random
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

Contestant = Callable[[str, dict[str, Any]], str]
Scorer = Callable[[dict[str, Any] | None, dict[str, Any]], dict[str, float]]


def load_exam(exam_dir: str | Path) -> dict[str, Any]:
    """Load ``exam.json`` and resolve each item's image to an absolute path."""
    root = Path(exam_dir).resolve()
    exam = json.loads((root / "exam.json").read_text(encoding="utf-8"))
    for item in exam["items"]:
        if item.get("image"):
            item["image"] = str(root / item["image"])
    exam["dir"] = str(root)
    return exam


def build_prompt(exam: dict[str, Any], item: dict[str, Any]) -> str:
    """The exact text a contestant receives for one item."""
    parts = [exam["instruction"].strip(), f"Item {item['id']}"]
    if item.get("text"):
        parts.append(item["text"].strip())
    if item.get("image"):
        parts.append(f"Image file: {item['image']}")
    return "\n\n".join(parts)


def extract_json(text: str) -> dict[str, Any]:
    """Return the last JSON object found in a model reply, ignoring prose and code fences."""
    decoder = json.JSONDecoder()
    found = None
    start = text.find("{")
    while start != -1:
        try:
            value, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            start = text.find("{", start + 1)
            continue
        if isinstance(value, dict):
            found = value
        start = text.find("{", end)
    if found is None:
        raise ValueError("no JSON object in reply")
    return found


def _decide(contestant: Contestant, exam: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    start = time.time()
    record: dict[str, Any] = {"status": "failed"}
    try:
        raw = contestant(build_prompt(exam, item), item)
        record["raw"] = raw[-4000:]
        answer = extract_json(raw)
        missing = [k for k in exam.get("required", []) if k not in answer]
        if missing:
            raise ValueError(f"answer is missing {missing}")
        record.update(status="ok", answer=answer)
    except Exception as exc:  # a contestant may fail in any way; record it and move on
        record["reason"] = f"{type(exc).__name__}: {exc}"[:300]
    record["seconds"] = round(time.time() - start, 2)
    return record


def run_exam(
    exam_dir: str | Path,
    contestant: Contestant,
    out_dir: str | Path,
    *,
    limit: int | None = None,
    retry_failed: bool = False,
    workers: int = 1,
) -> dict[str, Any]:
    """Sit a contestant on the exam, writing ``decisions.json`` after every item.

    Items already answered are skipped, so an interrupted run resumes where it stopped. Failed
    items (timeout, crash, unparseable or incomplete answer) stay failed unless ``retry_failed``.
    """
    exam = load_exam(exam_dir)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "decisions.json"
    decisions = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    todo = [
        item
        for item in exam["items"][:limit]
        if item["id"] not in decisions
        or (retry_failed and decisions[item["id"]]["status"] == "failed")
    ]
    lock = threading.Lock()

    def sit(item: dict[str, Any]) -> None:
        record = _decide(contestant, exam, item)
        with lock:
            decisions[item["id"]] = record
            path.write_text(json.dumps(decisions, indent=1), encoding="utf-8")

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        list(pool.map(sit, todo))
    done = [d for d in decisions.values() if d["status"] == "ok"]
    return {"attempted": len(todo), "ok": len(done), "failed": len(decisions) - len(done)}


def load_scorer(spec: str) -> Scorer:
    """Import a scorer given as ``package.module:function``."""
    module, _, name = spec.partition(":")
    return getattr(importlib.import_module(module), name)


def bootstrap_ci(values: list[float], *, rounds: int = 2000, seed: int = 0) -> tuple[float, float]:
    """95% bootstrap confidence interval for the mean of ``values``."""
    if not values:
        return (0.0, 0.0)
    rng = random.Random(seed)
    n = len(values)
    means = sorted(sum(rng.choices(values, k=n)) / n for _ in range(rounds))
    return (means[int(0.025 * rounds)], means[int(0.975 * rounds) - 1])


def score_run(exam_dir: str | Path, out_dir: str | Path) -> dict[str, Any]:
    """Score a run against the hidden key and write ``result.json``.

    Every item in the exam counts. An item with no usable answer is scored by passing ``None``
    to the scorer, so skipping hard items cannot raise a contestant's score.
    """
    exam = load_exam(exam_dir)
    key = json.loads((Path(exam["dir"]) / "key.json").read_text(encoding="utf-8"))
    scorer = load_scorer(exam["scorer"])
    out = Path(out_dir)
    path = out / "decisions.json"
    decisions = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    rows: dict[str, dict[str, float]] = {}
    for item in exam["items"]:
        record = decisions.get(item["id"], {})
        answer = record.get("answer") if record.get("status") == "ok" else None
        try:
            rows[item["id"]] = scorer(answer, key[item["id"]])
        except (KeyError, TypeError, ValueError):  # well-formed JSON with unusable values
            rows[item["id"]] = scorer(None, key[item["id"]])
    scores = [row["score"] for row in rows.values()]
    answered = sum(1 for d in decisions.values() if d["status"] == "ok")
    low, high = bootstrap_ci(scores)
    metrics = sorted({name for row in rows.values() for name in row})
    result = {
        "exam": exam["name"],
        "items": len(scores),
        "answered": answered,
        "failed": len(decisions) - answered,
        "not_attempted": len(scores) - len(decisions),
        "ci95": [round(low, 4), round(high, 4)],
        "seconds": round(sum(d.get("seconds", 0) for d in decisions.values()), 1),
        **{m: round(sum(row.get(m, 0.0) for row in rows.values()) / len(rows), 4) for m in metrics},
    }
    (out / "result.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    return result
