"""Scorers: ``(answer, key) -> {"score": ..., other metrics...}``.

``answer`` is ``None`` when the contestant gave nothing usable for the item. A scorer must still
return a score for it, and that score should be what an uninformed guess earns, so failing an
item is never better than answering it badly on purpose.
"""

from __future__ import annotations

from typing import Any


def exact_match(answer: dict[str, Any] | None, key: dict[str, Any]) -> dict[str, float]:
    """1.0 when ``answer["answer"]`` equals the key, ignoring case and surrounding space."""
    if answer is None:
        return {"score": 0.0}
    same = str(answer["answer"]).strip().casefold() == str(key["answer"]).strip().casefold()
    return {"score": float(same)}


def brier_direction(answer: dict[str, Any] | None, key: dict[str, Any]) -> dict[str, float]:
    """Brier skill for an up/down call with a stated confidence.

    The answer gives ``direction`` ("up" or "down") and ``confidence`` in that direction, from 0.5
    (no idea) to 1.0 (certain). The score is ``1 - (p_up - outcome)^2``: 0.75 for an honest
    "no idea", up to 1.0 for a confident correct call, down to 0.0 for a confident wrong one.
    Overconfidence is punished, which a plain accuracy number does not do.
    """
    outcome = 1.0 if key["direction"] == "up" else 0.0
    if answer is None:
        return {"score": 0.75, "accuracy": 0.5}
    direction = str(answer["direction"]).strip().casefold()
    if direction not in ("up", "down"):
        raise ValueError(f"direction must be up or down, got {direction!r}")
    confidence = min(1.0, max(0.5, float(answer["confidence"])))
    p_up = confidence if direction == "up" else 1.0 - confidence
    return {"score": 1.0 - (p_up - outcome) ** 2, "accuracy": float((p_up > 0.5) == bool(outcome))}
