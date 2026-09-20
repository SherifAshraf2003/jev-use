import json

import pytest

from jev_use.brain import Decision
from jev_use.computers.replay import ReplayComputer
from jev_use.loop import run
from jev_use.policy import Thresholds, Verdict


class ScriptedBrain:
    """Returns a prepared Decision per step, picking a real action from the candidates."""

    def __init__(self, script):
        self.script = list(script)
        self.total_input_tokens = 0
        self.cost_usd = 0.0
        self.seen_candidate_counts = []

    async def decide(self, goal, rules, elements, actions, history_lines):
        self.seen_candidate_counts.append(len(actions))
        self.total_input_tokens += 500
        self.cost_usd = self.total_input_tokens / 1_000_000 * 0.042
        picker, complete, progress, confidence = (
            self.script.pop(0) if self.script else (None, 0.99, 4.0, 0.9)
        )
        chosen = None
        if picker is not None:
            chosen = next((a for a in actions if picker in a.describe()), None)
        return Decision(
            action=chosen,
            confidence=confidence,
            complete=complete,
            progress=progress,
            probabilities={a.describe(): 1.0 / len(actions) for a in actions} if actions else {},
            latency_ms=120.0,
            input_tokens=500,
        )


def trees(fixtures_dir):
    return [
        json.loads((fixtures_dir / "trees" / f"demo_screen_{i}.json").read_text())
        for i in range(1, 5)
    ]


async def test_demo_script_completes_and_writes_one_record_per_step(fixtures_dir, tmp_path) -> None:
    brain = ScriptedBrain(
        [
            ("Open Browser", 0.01, 0.0, 0.9),
            ("Search", 0.01, 1.0, 0.9),
            ("Opening hours", 0.02, 2.0, 0.9),
            (None, 0.99, 4.0, 0.9),
        ]
    )
    trace_path = tmp_path / "demo.jsonl"
    result = await run(
        goal="Find the library opening hours",
        computer=ReplayComputer(trees(fixtures_dir)),
        brain=brain,
        inputs={"query": "city library opening hours"},
        trace_path=trace_path,
    )
    assert result.verdict is Verdict.DONE
    assert result.steps == 4
    records = [json.loads(line) for line in trace_path.read_text().splitlines()]
    assert len([r for r in records if r["kind"] == "step"]) == 4
    assert records[-1]["kind"] == "summary"
    assert all("runners_up" in r for r in records if r["kind"] == "step")


async def test_repeated_action_is_banned_and_disappears_from_candidates(fixtures_dir) -> None:
    """A model stuck on one button must run out of that option within repeat_limit + 1 steps."""
    thresholds = Thresholds(repeat_limit=2)
    brain = ScriptedBrain([("Open Browser", 0.01, 1.0, 0.9)] * 10)
    one_screen = [json.loads((fixtures_dir / "trees" / "demo_screen_1.json").read_text())]
    result = await run(
        goal="Loop forever",
        computer=ReplayComputer(one_screen),
        brain=brain,
        thresholds=thresholds,
        max_steps=8,
    )
    assert result.steps <= 8
    assert min(brain.seen_candidate_counts) < max(brain.seen_candidate_counts)


async def test_max_steps_aborts(fixtures_dir) -> None:
    brain = ScriptedBrain([("Open Browser", 0.01, 1.0, 0.9)] * 20)
    result = await run(
        goal="Never finish",
        computer=ReplayComputer(trees(fixtures_dir)),
        brain=brain,
        max_steps=3,
    )
    assert result.verdict is Verdict.ABORT
    assert result.steps == 3


async def test_dry_run_executes_nothing(fixtures_dir) -> None:
    computer = ReplayComputer(trees(fixtures_dir))
    brain = ScriptedBrain([("Open Browser", 0.01, 1.0, 0.9), (None, 0.99, 4.0, 0.9)])
    await run(goal="Look only", computer=computer, brain=brain, dry_run=True)
    assert computer.calls == []


async def test_mean_latency_and_cost_are_reported(fixtures_dir) -> None:
    brain = ScriptedBrain([("Open Browser", 0.01, 1.0, 0.9), (None, 0.99, 4.0, 0.9)])
    result = await run(goal="Go", computer=ReplayComputer(trees(fixtures_dir)), brain=brain)
    assert result.mean_latency_ms == 120.0
    assert result.input_tokens == 1000
    assert result.cost_usd > 0


async def test_a_safety_veto_stops_before_executing(fixtures_dir) -> None:
    class Vetoer:
        total_input_tokens = 700
        cost_usd = 700 / 1_000_000 * 0.042

        async def review(self, goal, rules, elements, action):
            class Report:
                injection = 0.0
                rule_breach = 0.0
                irreversible = 0.95
                blocker = "none"
                blocker_confidence = 0.0
                latency_ms = 50.0
                input_tokens = 700

            return Report()

    computer = ReplayComputer(trees(fixtures_dir))
    brain = ScriptedBrain([("Open Browser", 0.01, 1.0, 0.95)])
    result = await run(
        goal="Do something irreversible",
        computer=computer,
        brain=brain,
        supervisor=Vetoer(),
        max_steps=3,
    )
    assert result.verdict is Verdict.ASK_HUMAN
    assert computer.calls == []
    assert result.calls == 2


async def test_scroll_verdict_scrolls_the_machine(fixtures_dir) -> None:
    brain = ScriptedBrain([(None, 0.0, 1.0, 0.9), (None, 0.99, 4.0, 0.9)])
    computer = ReplayComputer(trees(fixtures_dir))
    await run(goal="Find something offscreen", computer=computer, brain=brain, max_steps=2)
    assert ("scroll", "down") in computer.calls


async def test_history_is_passed_to_the_brain(fixtures_dir) -> None:
    seen = []

    class Recorder(ScriptedBrain):
        async def decide(self, goal, rules, elements, actions, history_lines):
            seen.append(list(history_lines))
            return await super().decide(goal, rules, elements, actions, history_lines)

    brain = Recorder(
        [("Open Browser", 0.01, 1.0, 0.9), ("Search", 0.01, 2.0, 0.9), (None, 0.99, 4.0, 0.9)]
    )
    await run(goal="Go", computer=ReplayComputer(trees(fixtures_dir)), brain=brain, max_steps=3)
    assert seen[0] == []
    assert "Open Browser" in seen[1][0]


async def test_supervisor_tokens_are_included_in_the_bill(fixtures_dir) -> None:
    """Both requests are part of one step's cost; reporting only the brain's halves it."""

    class Report:
        injection = 0.0
        rule_breach = 0.0
        irreversible = 0.0
        blocker = "none"
        blocker_confidence = 0.0
        latency_ms = 50.0
        input_tokens = 700

    class Counting:
        def __init__(self):
            self.total_input_tokens = 0
            self.cost_usd = 0.0

        async def review(self, goal, rules, elements, action):
            self.total_input_tokens += 700
            self.cost_usd = self.total_input_tokens / 1_000_000 * 0.042
            return Report()

    brain = ScriptedBrain([("Open Browser", 0.01, 1.0, 0.9), (None, 0.99, 4.0, 0.9)])
    result = await run(
        goal="Go",
        computer=ReplayComputer(trees(fixtures_dir)),
        brain=brain,
        supervisor=Counting(),
        max_steps=2,
    )
    # Two brain calls at 500, and one supervisor review at 700 — the second step
    # selects no action, so the supervisor is not consulted for it.
    assert result.input_tokens == 1700
    assert result.cost_usd == pytest.approx(1700 / 1_000_000 * 0.042)
    assert brain.total_input_tokens == 1000
