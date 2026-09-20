"""One System One request per step: which action, are we done, how far along.

The three questions run in parallel over the same state, so the state is ingested
once and the extra two questions cost only their own tokens. See the TypeSafe
speculative fan-out pattern.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from typesafe_sdk import Choice, Noul, Score

from jev_use import prompts
from jev_use.actions import Action
from jev_use.screen import Element

logger = logging.getLogger(__name__)

# Verified against https://docs.typesafe.ai/models on 2026-09-20: $0.042 per million
# input tokens, output tokens free. Re-check before publishing any cost claim.
USD_PER_MTOK_INPUT = 0.042

DEFAULT_MODEL = "jev-latest"


class SystemOneClient(Protocol):
    """The slice of AsyncTypeSafeClient this module uses."""

    async def system_one(self, state: Any, questions: Any, **kwargs: Any) -> Any: ...


@dataclass
class Decision:
    """What one step's request produced, already unpacked from the SDK types."""

    action: Action | None
    confidence: float
    complete: float
    progress: float
    probabilities: dict[str, float] = field(default_factory=dict)
    latency_ms: float = 0.0
    input_tokens: int = 0
    model: str = ""
    request_id: str = ""


class JevBrain:
    """Builds the request, unpacks the response, and keeps the running bill."""

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
        self,
        goal: str,
        rules: list[str],
        elements: list[Element],
        history_lines: list[str],
    ) -> dict[str, Any]:
        """Named JSON fields, never one blob — each field is referable from a question."""
        return {
            "task_goal": goal,
            "rules_the_agent_must_follow": rules,
            "screen_elements": [element.as_line() for element in elements],
            "actions_already_taken": history_lines,
        }

    def build_questions(self, actions: list[Action]) -> dict[str, Choice | Noul | Score]:
        """Choice criteria are keyed by description, because the key is not sent.

        `enumerate_actions` guarantees the descriptions are distinct, which this
        dict depends on: duplicate keys would silently drop options and could
        resolve the answer to the wrong element. See docs/DEVIATIONS.md D19.
        """
        return {
            "next_action": Choice(
                instructions=prompts.NEXT_ACTION_INSTRUCTIONS,
                criteria={action.describe(): None for action in actions},
            ),
            "task_complete": Noul(instructions=prompts.TASK_COMPLETE_INSTRUCTIONS),
            "progress": Score(
                instructions=prompts.PROGRESS_INSTRUCTIONS,
                criteria=prompts.PROGRESS_LEVELS,
            ),
        }

    async def decide(
        self,
        goal: str,
        rules: list[str],
        elements: list[Element],
        actions: list[Action],
        history_lines: list[str],
    ) -> Decision:
        """Send one request and return the unpacked decision."""
        if not actions:
            logger.warning("no candidate actions; skipping the request")
            return Decision(action=None, confidence=0.0, complete=0.0, progress=0.0)

        state = self.build_state(goal, rules, elements, history_lines)
        questions = self.build_questions(actions)

        started = time.perf_counter()
        response = await self._client.system_one(state, questions, model=self._model)
        latency_ms = (time.perf_counter() - started) * 1000

        input_tokens = int(getattr(response.usage, "input_tokens", 0) or 0)
        self._total_input_tokens += input_tokens

        choice_answer = response.choices["next_action"]
        by_description = {action.describe(): action for action in actions}
        selected = by_description.get(choice_answer.choice)
        if selected is None:
            logger.warning("model returned an unknown option: %r", choice_answer.choice)

        return Decision(
            action=selected,
            confidence=float(choice_answer.confidence),
            complete=float(response.nouls["task_complete"].noul),
            progress=float(response.scores["progress"].score),
            probabilities=dict(choice_answer.probabilities),
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            model=str(getattr(response, "model", "")),
            request_id=str(getattr(response, "request_id", "") or ""),
        )
