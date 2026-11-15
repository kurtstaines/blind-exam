"""Build a leaderboard from every scored run under a runs folder."""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

COLUMNS = ["rank", "contestant", "score", "ci95", "accuracy", "answered", "failed", "seconds"]


def collect(runs_dir: str | Path) -> list[dict[str, Any]]:
    """Read each ``<runs>/<slug>/result.json`` and rank contestants by score, best first."""
    rows = []
    for path in sorted(Path(runs_dir).glob("*/result.json")):
        result = json.loads(path.read_text(encoding="utf-8"))
        low, high = result["ci95"]
        rows.append(
            {
                "contestant": path.parent.name,
                "score": f"{result['score']:.3f}",
                "ci95": f"{low:.3f} to {high:.3f}",
                "accuracy": f"{result['accuracy']:.0%}" if "accuracy" in result else "",
                "answered": f"{result['answered']}/{result['items']}",
                "failed": str(result["failed"]),
                "seconds": f"{result['seconds']:.0f}",
                "_sort": result["score"],
            }
        )
    rows.sort(key=lambda r: r["_sort"], reverse=True)
    for rank, row in enumerate(rows, 1):
        row["rank"] = str(rank)
    return rows


def to_markdown(rows: list[dict[str, Any]]) -> str:
    """Render the leaderboard as a Markdown table."""
    lines = ["| " + " | ".join(COLUMNS) + " |", "|" + "---|" * len(COLUMNS)]
    lines += ["| " + " | ".join(row[c] for c in COLUMNS) + " |" for row in rows]
    return "\n".join(lines) + "\n"


def to_html(rows: list[dict[str, Any]], title: str = "Leaderboard") -> str:
    """Render the leaderboard as one self-contained HTML page."""
    head = "".join(f"<th>{c}</th>" for c in COLUMNS)
    body = "".join(
        "<tr>" + "".join(f"<td>{html.escape(row[c])}</td>" for c in COLUMNS) + "</tr>"
        for row in rows
    )
    style = (
        "body{font:15px system-ui,sans-serif;margin:2rem;color:#1f2328}"
        "table{border-collapse:collapse}th,td{border:1px solid #d1d9e0;padding:6px 12px;"
        "text-align:left}th{background:#f6f8fa}"
    )
    return (
        f"<!doctype html><meta charset=utf-8><title>{html.escape(title)}</title>"
        f"<style>{style}</style><h1>{html.escape(title)}</h1>"
        f"<table><tr>{head}</tr>{body}</table>\n"
    )


def write_board(runs_dir: str | Path) -> list[dict[str, Any]]:
    """Write ``LEADERBOARD.md`` and ``leaderboard.html`` into the runs folder."""
    rows = collect(runs_dir)
    out = Path(runs_dir)
    (out / "LEADERBOARD.md").write_text(to_markdown(rows), encoding="utf-8")
    (out / "leaderboard.html").write_text(to_html(rows), encoding="utf-8")
    return rows
