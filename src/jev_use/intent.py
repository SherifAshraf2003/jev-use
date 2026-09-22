"""Turn one plain sentence into an application and the text to type.

The model never writes the text. Code splits the sentence into every run of
consecutive words, and the model selects which run belongs in each kind of
field — TypeSafe's select-instead-of-generate pattern. The application is picked
the same way, from the list of installed applications.

Everything is asked in one request: the questions run in parallel over the same
state, so the sentence is read once.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from typesafe_sdk import Choice

from jev_use import prompts
from jev_use.actions import MAX_CHOICE_OPTIONS
from jev_use.brain import DEFAULT_MODEL, SystemOneClient

logger = logging.getLogger(__name__)

MAX_SPAN_WORDS = 6
TRAILING_PUNCTUATION = ".,!?;:\"'"


@dataclass
class Intent:
    app: str | None
    app_confidence: float
    inputs: dict[str, str] = field(default_factory=dict)
    input_tokens: int = 0
    latency_ms: float = 0.0


def goal_spans(goal: str, max_words: int = MAX_SPAN_WORDS) -> list[str]:
    """Every run of up to `max_words` consecutive words, in order, deduplicated.

    Trailing punctuation is stripped from each run so "cats!" offers "cats", but
    interior punctuation is kept so "youtube.com" survives intact. Capped below
    the Choice ceiling, leaving room for the nothing option.
    """
    words = goal.split()
    spans: list[str] = []
    seen: set[str] = set()
    for start in range(len(words)):
        for end in range(start + 1, min(start + max_words, len(words)) + 1):
            span = " ".join(words[start:end]).rstrip(TRAILING_PUNCTUATION).strip()
            if span and span not in seen:
                seen.add(span)
                spans.append(span)
    return spans[: MAX_CHOICE_OPTIONS - 1]


async def parse_intent(
    client: SystemOneClient,
    goal: str,
    apps: list[str] | None,
    *,
    current_app: str | None = None,
    model: str = DEFAULT_MODEL,
) -> Intent:
    """One request: which application, and what text goes in which kind of field.

    Pass `apps=None` when the application is already known, to skip that question.
    Pass `current_app` in a spoken session so a follow-up such as "now search for
    X" resolves against the application already in use rather than guessing afresh.
    """
    options = [*goal_spans(goal), prompts.NO_TEXT_OPTION]
    questions: dict[str, Any] = {
        name: Choice(instructions=instructions, criteria={o: None for o in options})
        for name, instructions in prompts.INPUT_FIELD_INSTRUCTIONS.items()
    }
    if apps:
        questions["app"] = Choice(
            instructions=prompts.APP_CHOICE_INSTRUCTIONS,
            criteria={name: None for name in apps[:MAX_CHOICE_OPTIONS]},
        )

    started = time.perf_counter()
    state: dict[str, Any] = {"what_the_user_said": goal}
    if current_app:
        state["application_already_open_and_in_front"] = current_app
    response = await client.system_one(state, questions, model=model)
    latency_ms = (time.perf_counter() - started) * 1000

    inputs: dict[str, str] = {}
    for name in prompts.INPUT_FIELD_INSTRUCTIONS:
        answer = response.choices.get(name)
        if answer is None or answer.choice == prompts.NO_TEXT_OPTION:
            continue
        # The same text picked for two kinds of field is one input, not two:
        # otherwise every field gets it offered twice.
        if answer.choice not in inputs.values():
            inputs[name] = str(answer.choice)

    app_answer = response.choices.get("app") if apps else None
    return Intent(
        app=str(app_answer.choice) if app_answer is not None else None,
        app_confidence=float(app_answer.confidence) if app_answer is not None else 1.0,
        inputs=inputs,
        input_tokens=int(getattr(response.usage, "input_tokens", 0) or 0),
        latency_ms=latency_ms,
    )
