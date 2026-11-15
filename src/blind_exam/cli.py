"""Command line for blind-exam.

blind-exam make-sample exam/                      write the synthetic sample exam
blind-exam run exam/ baseline:momentum            sit a contestant (resumes if interrupted)
blind-exam run exam/ "cmd:ollama run llama3.2" --slug llama3.2 --retry-failed
blind-exam status exam/                           answered / failed per contestant
blind-exam board exam/                            score every run and write the leaderboard
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from blind_exam import board, contestants, core, sample


def _runs(args: argparse.Namespace) -> Path:
    return Path(args.runs) if args.runs else Path(args.exam) / "runs"


def main(argv: list[str] | None = None) -> int:
    """Entry point; returns a process exit code."""
    parser = argparse.ArgumentParser(prog="blind-exam", description="Run blind LLM exams.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("make-sample", help="write the synthetic sample exam")
    p.add_argument("out")
    p.add_argument("--n", type=int, default=40, help="number of items")
    p.add_argument("--seed", type=int, default=7)

    p = sub.add_parser("run", help="sit one contestant on an exam")
    p.add_argument("exam")
    p.add_argument(
        "contestant", help="baseline:<name> or cmd:<command reading the prompt on stdin>"
    )
    p.add_argument("--slug", help="folder name for this contestant's run")
    p.add_argument("--limit", type=int, help="only the first N items")
    p.add_argument("--retry-failed", action="store_true", help="re-ask items that failed before")
    p.add_argument("--workers", type=int, default=1, help="items to run at once")
    p.add_argument("--timeout", type=int, default=300, help="seconds per item for cmd: contestants")

    p.add_argument("--runs", help="runs folder (default: <exam>/runs)")

    for name, text in (("status", "show progress per contestant"), ("board", "score and rank")):
        p = sub.add_parser(name, help=text)
        p.add_argument("exam")
        p.add_argument("--runs", help="runs folder (default: <exam>/runs)")

    args = parser.parse_args(argv)
    if args.command == "make-sample":
        print(f"wrote {sample.make_sample(args.out, n=args.n, seed=args.seed)}")
        return 0

    runs = _runs(args)
    if args.command == "run":
        slug = args.slug or re.sub(r"[^A-Za-z0-9._-]+", "-", args.contestant).strip("-")
        contestant = contestants.resolve(args.contestant, timeout=args.timeout)
        summary = core.run_exam(
            args.exam,
            contestant,
            runs / slug,
            limit=args.limit,
            retry_failed=args.retry_failed,
            workers=args.workers,
        )
        print(f"{slug}: {json.dumps(summary)}")
        return 0

    slugs = sorted(p.parent.name for p in runs.glob("*/decisions.json"))
    if args.command == "status":
        for slug in slugs:
            decisions = json.loads((runs / slug / "decisions.json").read_text(encoding="utf-8"))
            failed = [k for k, d in decisions.items() if d["status"] == "failed"]
            print(f"{slug}: {len(decisions) - len(failed)} ok, {len(failed)} failed")
            for item_id in failed[:5]:
                print(f"  {item_id}: {decisions[item_id].get('reason', '')}")
        return 0

    for slug in slugs:
        core.score_run(args.exam, runs / slug)
    sys.stdout.write(board.to_markdown(board.write_board(runs)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
