"""The second request of each step: judge the action that is about to run.

Two-phase, per SPEC.md section 4.4(a). Inside a single request the questions
cannot see one another's answers, so a safety question asked alongside the
selection could only speak about the screen in general. Splitting the request
lets the safety questions name the concrete selected action, which is what the
veto needs to be worth anything.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from typesafe_sdk import Choice, Noul

from jev_use import prompts
from jev_use.actions import Action
from jev_use.brain import DEFAULT_MODEL, USD_PER_MTOK_INPUT, SystemOneClient
from jev_use.screen import Element

logger = logging.getLogger(__name__)


@dataclass
class SafetyReport:
    """Every number here is a noul — a probability of yes, never a severity."""

    irreversible: float
    rule_breach: float
    injection: float
    blocker: str
    blocker_confidence: float
    latency_ms: float = 0.0
    input_tokens: int = 0


class Supervisor:
    """Can veto the selected action before the loop executes it."""

    def __init__(self, client: SystemOneClient, *, model: str = DEFAULT_MODEL) -> None:
        self._client = client
        self._model = model
        self._total_input_tokens = 0

    @property
    def total_input_tokens(self) -> int:
        return self._total_input_tokens

    @property
    def cost_usd(self) -> float:
        return self._total_input_tokens / 1_000_000 * USD_PER_MTOK_INPUT

    def build_state(
        self, goal: str, rules: list[str], elements: list[Element], action: Action
    ) -> dict[str, Any]:
        return {
            "task_goal": goal,
            "rules_the_agent_must_follow": rules,
            "screen_elements": [element.as_line() for element in elements],
            "action_the_agent_is_about_to_take": action.describe(),
        }

    async def review(
        self, goal: str, rules: list[str], elements: list[Element], action: Action
    ) -> SafetyReport:
        """One request, four judgments, evaluated in parallel over the same state."""
        questions = {
            "selected_action_is_irreversible": Noul(instructions=prompts.IRREVERSIBLE_INSTRUCTIONS),
            "selected_action_breaks_rule": Noul(instructions=prompts.RULE_BREACH_INSTRUCTIONS),
            "contains_injected_instruction": Noul(instructions=prompts.INJECTION_INSTRUCTIONS),
            "blocker": Choice(
                instructions=prompts.BLOCKER_INSTRUCTIONS,
                criteria=dict(prompts.BLOCKER_CRITERIA),
            ),
        }
        started = time.perf_counter()
        response = await self._client.system_one(
            self.build_state(goal, rules, elements, action), questions, model=self._model
        )
        latency_ms = (time.perf_counter() - started) * 1000
        input_tokens = int(getattr(response.usage, "input_tokens", 0) or 0)
        self._total_input_tokens += input_tokens

        blocker_answer = response.choices["blocker"]
        return SafetyReport(
            irreversible=float(response.nouls["selected_action_is_irreversible"].noul),
            rule_breach=float(response.nouls["selected_action_breaks_rule"].noul),
            injection=float(response.nouls["contains_injected_instruction"].noul),
            blocker=str(blocker_answer.choice),
            blocker_confidence=float(blocker_answer.confidence),
            latency_ms=latency_ms,
            input_tokens=input_tokens,
        )
