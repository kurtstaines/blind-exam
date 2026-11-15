"""Contestants: anything that turns one prompt into one reply.

``cmd:`` runs an external command once per item, with the prompt on stdin, so every item gets a
fresh process and no memory of earlier items. That is what makes the exam blind. The
``baseline:`` contestants need no model and give the scores a real model has to beat.
"""

from __future__ import annotations

import json
import random
import shlex
import subprocess
from typing import Any

from blind_exam.core import Contestant


def command(template: str, *, timeout: int = 300) -> Contestant:
    """A contestant that runs ``template`` per item; ``{image}`` becomes the item's image path."""

    def ask(prompt: str, item: dict[str, Any]) -> str:
        argv = [part.replace("{image}", item.get("image", "")) for part in shlex.split(template)]
        proc = subprocess.run(
            argv,
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"exit {proc.returncode}: {proc.stderr.strip()[-200:]}")
        return proc.stdout

    return ask


def _reply(direction: str, confidence: float) -> str:
    return json.dumps({"direction": direction, "confidence": confidence})


def coinflip(seed: int = 0) -> Contestant:
    """Guess at random with no stated edge. The floor every contestant should clear."""
    rng = random.Random(seed)
    return lambda prompt, item: _reply(rng.choice(["up", "down"]), 0.5)


def always_up(prompt: str, item: dict[str, Any]) -> str:
    """Answer ``up`` every time. Exposes an unbalanced answer key."""
    return _reply("up", 0.6)


def momentum(prompt: str, item: dict[str, Any]) -> str:
    """Follow the visible trend: a simple rule a capable model should match or beat."""
    series = item["series"]
    return _reply("up" if series[-1] >= series[0] else "down", 0.65)


BASELINES = {"coinflip": coinflip(), "always-up": always_up, "momentum": momentum}


def resolve(spec: str, *, timeout: int = 300) -> Contestant:
    """Turn ``baseline:<name>`` or ``cmd:<command>`` into a contestant."""
    kind, _, rest = spec.partition(":")
    if kind == "baseline" and rest in BASELINES:
        return BASELINES[rest]
    if kind == "cmd" and rest.strip():
        return command(rest, timeout=timeout)
    raise ValueError(f"unknown contestant {spec!r}; use baseline:<{'|'.join(BASELINES)}> or cmd:")
