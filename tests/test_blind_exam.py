"""Tests for the harness. No model is called: contestants are functions or tiny subprocesses."""

import json
import sys
from pathlib import Path

import pytest

from blind_exam import board, cli, contestants, core, sample, scorers


@pytest.fixture
def exam(tmp_path: Path) -> Path:
    return sample.make_sample(tmp_path / "exam", n=12, seed=3)


def reply(direction: str = "up", confidence: float = 0.7) -> str:
    return json.dumps({"direction": direction, "confidence": confidence})


# --- parsing -----------------------------------------------------------------


def test_extract_json_takes_the_last_object_and_ignores_prose():
    text = (
        'Draft: {"direction": "up"}\n```json\n{"direction": "down", "confidence": 0.6}\n```\nDone.'
    )
    assert core.extract_json(text) == {"direction": "down", "confidence": 0.6}


def test_extract_json_rejects_replies_without_an_object():
    with pytest.raises(ValueError):
        core.extract_json("I think it goes up, about 70% sure. [1, 2]")


# --- blindness and prompts ---------------------------------------------------


def test_contestant_never_sees_the_key_or_other_items(exam: Path):
    seen = []
    core.run_exam(exam, lambda prompt, item: seen.append((prompt, item)) or reply(), exam / "r")
    key = json.loads((exam / "key.json").read_text())
    assert len(seen) == 12
    for prompt, item in seen:
        assert "direction" not in item and set(item) == {"id", "image", "text", "series"}
        assert prompt.count("Item s") == 1 and Path(item["image"]).is_file()
    assert set(key) == {item["id"] for _, item in seen}


# --- resume and retry --------------------------------------------------------


def test_run_resumes_and_only_retries_failures_on_request(exam: Path):
    calls = []

    def flaky(prompt, item):
        calls.append(item["id"])
        if item["id"] == "s002" and calls.count("s002") == 1:
            raise TimeoutError("model timed out")
        return "not json" if item["id"] == "s003" else reply()

    out = exam / "runs" / "flaky"
    assert core.run_exam(exam, flaky, out, limit=5) == {"attempted": 5, "ok": 3, "failed": 2}
    decisions = json.loads((out / "decisions.json").read_text())
    assert decisions["s002"]["reason"].startswith("TimeoutError")
    assert "no JSON object" in decisions["s003"]["reason"]

    assert core.run_exam(exam, flaky, out, limit=5)["attempted"] == 0  # nothing new to do
    again = core.run_exam(exam, flaky, out, retry_failed=True)
    assert again == {"attempted": 9, "ok": 11, "failed": 1}  # s002 recovered, s003 still bad
    assert calls.count("s001") == 1


def test_incomplete_answers_are_failures(exam: Path):
    summary = core.run_exam(exam, lambda p, i: '{"direction": "up"}', exam / "r", limit=2)
    assert summary["failed"] == 2
    record = json.loads((exam / "r" / "decisions.json").read_text())["s001"]
    assert "confidence" in record["reason"]


def test_parallel_run_answers_every_item_once(exam: Path):
    summary = core.run_exam(exam, lambda p, i: reply(), exam / "r", workers=4)
    assert summary == {"attempted": 12, "ok": 12, "failed": 0}


# --- scoring -----------------------------------------------------------------


def test_brier_direction_rewards_calibration():
    up = {"direction": "up"}
    score = scorers.brier_direction
    assert score({"direction": "up", "confidence": 1.0}, up) == {"score": 1.0, "accuracy": 1.0}
    assert score({"direction": "down", "confidence": 1.0}, up) == {"score": 0.0, "accuracy": 0.0}
    assert score({"direction": "down", "confidence": 0.5}, up)["score"] == 0.75
    assert score(None, up)["score"] == 0.75  # no answer scores like an honest "no idea"
    assert score({"direction": "UP ", "confidence": 9}, up)["score"] == 1.0  # clamped
    with pytest.raises(ValueError):
        score({"direction": "sideways", "confidence": 0.9}, up)


def test_exact_match():
    assert scorers.exact_match({"answer": " Paris "}, {"answer": "paris"}) == {"score": 1.0}
    assert scorers.exact_match({"answer": "Lyon"}, {"answer": "paris"}) == {"score": 0.0}
    assert scorers.exact_match(None, {"answer": "paris"}) == {"score": 0.0}


def test_score_counts_every_item_so_skipping_cannot_help(exam: Path):
    key = json.loads((exam / "key.json").read_text())

    def oracle(prompt, item):
        return reply(key[item["id"]]["direction"], 1.0)

    core.run_exam(exam, oracle, exam / "runs" / "half", limit=6)
    core.run_exam(exam, oracle, exam / "runs" / "full")
    half = core.score_run(exam, exam / "runs" / "half")
    full = core.score_run(exam, exam / "runs" / "full")
    assert full["score"] == 1.0 and full["accuracy"] == 1.0 and full["ci95"] == [1.0, 1.0]
    assert half["score"] == 0.875 and half["not_attempted"] == 6  # 6 perfect + 6 scored as 0.75


def test_unusable_values_are_scored_as_no_answer(exam: Path):
    core.run_exam(exam, lambda p, i: reply("sideways", 0.9), exam / "r")
    assert core.score_run(exam, exam / "r")["score"] == 0.75


def test_bootstrap_ci_brackets_the_mean_and_is_reproducible():
    values = [0.0, 1.0] * 50
    low, high = core.bootstrap_ci(values)
    assert low < 0.5 < high and 0.35 < low and high < 0.65
    assert core.bootstrap_ci(values) == (low, high)


# --- contestants -------------------------------------------------------------


def test_command_contestant_gets_prompt_on_stdin_and_image_path(exam: Path):
    code = (
        "import sys, json; p = sys.stdin.read(); "
        "print(json.dumps({'direction': 'up', 'confidence': 0.6, "
        "'saw': 'Item s001' in p, 'img': sys.argv[1][-8:]}))"
    )
    ask = contestants.command(f'"{sys.executable}" -c "{code}" {{image}}')
    item = core.load_exam(exam)["items"][0]
    answer = json.loads(ask(core.build_prompt(core.load_exam(exam), item), item))
    assert answer["saw"] is True and answer["img"] == "s001.png"


def test_command_contestant_failure_is_recorded_not_raised(exam: Path):
    bad = contestants.command(f'"{sys.executable}" -c "raise SystemExit(3)"')
    summary = core.run_exam(exam, bad, exam / "r", limit=1)
    assert summary["failed"] == 1
    assert "exit 3" in json.loads((exam / "r" / "decisions.json").read_text())["s001"]["reason"]


def test_resolve_rejects_unknown_contestants():
    assert contestants.resolve("baseline:momentum") is contestants.momentum
    with pytest.raises(ValueError):
        contestants.resolve("baseline:psychic")


# --- sample exam, board, CLI -------------------------------------------------


def test_sample_exam_is_reproducible(tmp_path: Path):
    a = sample.make_sample(tmp_path / "a", n=5, seed=1)
    b = sample.make_sample(tmp_path / "b", n=5, seed=1)
    assert (a / "exam.json").read_text() == (b / "exam.json").read_text()
    assert (a / "key.json").read_text() == (b / "key.json").read_text()
    assert (a / "items" / "s001.png").read_bytes() == (b / "items" / "s001.png").read_bytes()


def test_cli_end_to_end_ranks_contestants(exam: Path, capsys: pytest.CaptureFixture):
    for name in ("coinflip", "momentum"):
        assert cli.main(["run", str(exam), f"baseline:{name}"]) == 0
    assert cli.main(["status", str(exam)]) == 0
    assert "baseline-momentum: 12 ok, 0 failed" in capsys.readouterr().out
    assert cli.main(["board", str(exam)]) == 0
    table = capsys.readouterr().out
    assert "| 1 |" in table and "baseline-coinflip" in table
    rows = board.collect(exam / "runs")
    assert [r["rank"] for r in rows] == ["1", "2"]
    assert rows[0]["_sort"] >= rows[1]["_sort"]
    assert "<table>" in (exam / "runs" / "leaderboard.html").read_text()
