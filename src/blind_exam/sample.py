"""Generate the sample exam: unlabeled synthetic price charts with a hidden continuation.

Each item shows a price series as an image (axes and labels removed) and as a list of numbers.
The contestant says whether the series ends higher or lower after the hidden part, and how
confident it is. The series are synthetic: a drifting random walk whose drift persists into the
hidden part 70% of the time. So the visible trend carries real but imperfect information, and
"follow the trend" is a beatable baseline rather than the ceiling.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from PIL import Image, ImageDraw

INSTRUCTION = """You are sitting a blind exam. Each item is one price series with labels removed.
The item shows the visible part of the series. A hidden part follows it.

Decide whether the series will be HIGHER or LOWER at the end of the hidden part than at the last
visible point, and how confident you are.

Reply with one JSON object and nothing else:
{"direction": "up" or "down", "confidence": a number from 0.5 (no idea) to 1.0 (certain)}

You are scored on calibration as well as direction: a confident wrong answer costs more than an
unsure one."""

VISIBLE, HIDDEN = 60, 20


def _series(rng: random.Random) -> tuple[list[float], str]:
    drift = rng.choice([-1, 1]) * rng.uniform(0.15, 0.45)
    price, visible = 100.0, []
    for _ in range(VISIBLE):
        price += drift + rng.gauss(0, 1.0)
        visible.append(round(price, 2))
    if rng.random() > 0.7:  # the trend breaks in 30% of items
        drift = -drift
    for _ in range(HIDDEN):
        price += drift + rng.gauss(0, 1.0)
    return visible, "up" if price >= visible[-1] else "down"


def _draw(series: list[float], path: Path) -> None:
    width, height, pad = 640, 360, 24
    low, high = min(series), max(series)
    span = (high - low) or 1.0
    points = [
        (
            pad + i * (width - 2 * pad) / (len(series) - 1),
            height - pad - (v - low) / span * (height - 2 * pad),
        )
        for i, v in enumerate(series)
    ]
    image = Image.new("RGB", (width, height), "#14161a")
    draw = ImageDraw.Draw(image)
    draw.line(points, fill="#4fc3f7", width=2)
    image.save(path)


def make_sample(out_dir: str | Path, *, n: int = 40, seed: int = 7) -> Path:
    """Write a reproducible sample exam (``exam.json``, ``key.json``, ``items/``) to ``out_dir``."""
    out = Path(out_dir)
    (out / "items").mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    items, key = [], {}
    for i in range(1, n + 1):
        item_id = f"s{i:03d}"
        series, direction = _series(rng)
        _draw(series, out / "items" / f"{item_id}.png")
        text = "Visible series, oldest first:\n" + ", ".join(f"{v:.2f}" for v in series)
        items.append(
            {"id": item_id, "image": f"items/{item_id}.png", "text": text, "series": series}
        )
        key[item_id] = {"direction": direction}
    exam = {
        "name": f"synthetic-trend-{n}",
        "instruction": INSTRUCTION,
        "required": ["direction", "confidence"],
        "scorer": "blind_exam.scorers:brier_direction",
        "items": items,
    }
    (out / "exam.json").write_text(json.dumps(exam, indent=1), encoding="utf-8")
    (out / "key.json").write_text(json.dumps(key, indent=1), encoding="utf-8")
    return out
