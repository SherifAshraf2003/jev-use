"""Observe, enumerate, decide, execute. The whole agent is in `run`."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from jev_use.actions import enumerate_actions
from jev_use.brain import Decision
from jev_use.computers.base import Computer, execute_action
from jev_use.policy import History, Judgments, Thresholds, Verdict, decide
from jev_use.screen import parse_tree
from jev_use.trace import TraceWriter, top_n

logger = logging.getLogger(__name__)

TERMINAL_VERDICTS = frozenset({Verdict.DONE, Verdict.ASK_HUMAN, Verdict.ABORT})


class Brain(Protocol):
    total_input_tokens: int
    cost_usd: float

    async def decide(
        self,
        goal: str,
        rules: list[str],
        elements: Any,
        actions: Any,
        history_lines: list[str],
    ) -> Decision: ...


class SupervisorLike(Protocol):
    async def review(self, goal: str, rules: list[str], elements: Any, action: Any) -> Any: ...


@dataclass
class RunResult:
    verdict: Verdict
    steps: int
    calls: int
    input_tokens: int
    cost_usd: float
    mean_latency_ms: float
    reason: str = ""
    trace_path: Path | None = None
    latencies: list[float] = field(default_factory=list)


async def run(
    *,
    goal: str,
    computer: Computer,
    brain: Brain,
    inputs: dict[str, str] | None = None,
    rules: list[str] | None = None,
    thresholds: Thresholds | None = None,
    supervisor: SupervisorLike | None = None,
    max_steps: int = 25,
    trace_path: Path | str | None = None,
    dry_run: bool = False,
) -> RunResult:
    """Drive the machine until the task is done, blocked, or out of steps."""
    inputs = inputs or {}
    rules = rules or []
    thresholds = thresholds or Thresholds()
    history = History()
    banned: set[str] = set()
    history_lines: list[str] = []
    latencies: list[float] = []
    calls = 0
    verdict = Verdict.ABORT
    reason = "no steps were taken"
    writer = TraceWriter(trace_path) if trace_path is not None else None

    try:
        screen_bounds = await computer.screen_size()
        for step in range(1, max_steps + 1):
            history.step = step
            elements = parse_tree(await computer.tree(), screen_bounds=screen_bounds)
            actions = enumerate_actions(elements, inputs, banned)
            decision = await brain.decide(goal, rules, elements, actions, history_lines)
            calls += 1
            if decision.latency_ms:
                latencies.append(decision.latency_ms)

            judgments = Judgments(
                has_action=decision.action is not None,
                confidence=decision.confidence,
                complete=decision.complete,
                progress=decision.progress,
                selected_signature=(
                    decision.action.signature() if decision.action is not None else "-"
                ),
            )

            if supervisor is not None and decision.action is not None:
                report = await supervisor.review(goal, rules, elements, decision.action)
                calls += 1
                if report.latency_ms:
                    latencies.append(report.latency_ms)
                judgments.injection = report.injection
                judgments.rule_breach = report.rule_breach
                judgments.irreversible = report.irreversible
                judgments.blocker = report.blocker
                judgments.blocker_confidence = report.blocker_confidence

            verdict, reason = decide(judgments, history, thresholds)

            if writer is not None:
                writer.write_step(
                    step=step,
                    verdict=str(verdict),
                    reason=reason,
                    action=decision.action.describe() if decision.action else None,
                    signature=judgments.selected_signature,
                    confidence=decision.confidence,
                    complete=decision.complete,
                    progress=decision.progress,
                    injection=judgments.injection,
                    rule_breach=judgments.rule_breach,
                    irreversible=judgments.irreversible,
                    blocker=judgments.blocker,
                    input_tokens=decision.input_tokens,
                    latency_ms=decision.latency_ms,
                    elements=len(elements),
                    candidates=len(actions),
                    runners_up=top_n(decision.probabilities, 5),
                )

            history.progress_history.append(decision.progress)

            if verdict in TERMINAL_VERDICTS:
                break
            if verdict is Verdict.RETRY:
                banned.add(judgments.selected_signature)
                continue
            if verdict is Verdict.SCROLL:
                if not dry_run:
                    await computer.scroll("down")
                continue

            if decision.action is None:
                continue
            history.recent_signatures.append(judgments.selected_signature)
            history_lines.append(decision.action.describe())
            if not dry_run:
                await execute_action(computer, decision.action)
        else:
            verdict, reason = Verdict.ABORT, f"reached the step limit of {max_steps}"
    finally:
        mean_latency = (sum(latencies) / len(latencies)) if latencies else 0.0
        if writer is not None:
            writer.write_summary(
                verdict=str(verdict),
                reason=reason,
                steps=history.step,
                calls=calls,
                input_tokens=brain.total_input_tokens,
                cost_usd=brain.cost_usd,
                mean_latency_ms=mean_latency,
            )
            writer.close()

    return RunResult(
        verdict=verdict,
        steps=history.step,
        calls=calls,
        input_tokens=brain.total_input_tokens,
        cost_usd=brain.cost_usd,
        mean_latency_ms=mean_latency,
        reason=reason,
        trace_path=Path(trace_path) if trace_path is not None else None,
        latencies=latencies,
    )
