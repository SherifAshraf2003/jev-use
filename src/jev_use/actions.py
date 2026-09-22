"""Enumerate every legal action on a screen.

The model can only pick from this list, so three properties matter.

Every description must stand on its own, because the option key the SDK uses is
not sent to the model.

Descriptions must be unique within a step. `Choice.criteria` is a dict keyed by
description, and `brain.py` maps the returned string back to its Action through
the same key, so two identical descriptions would silently drop one option and
could resolve the answer to the wrong element. Real screens collide constantly:
one captured browser window held 166 candidates but only 91 distinct
role-and-label pairs, including 24 separate buttons labelled "Close". Colliding
descriptions therefore get an ordinal.

A banned action must be absent rather than discouraged: omission is what breaks
a loop, since an option that is not in the list cannot be selected.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, replace
from typing import Literal

from jev_use.screen import Element

logger = logging.getLogger(__name__)

ActionKind = Literal["click", "type", "scroll", "key", "wait"]

# A Choice question accepts at most 255 options; above that the API returns
# "400 Too many choices". Binary searched 2026-09-21, see docs/DEVIATIONS.md D16.
MAX_CHOICE_OPTIONS = 255

CLICKABLE_ROLES = frozenset(
    {"button", "link", "checkbox", "radio", "menuitem", "tab", "listitem", "cell", "combobox"}
)
EDITABLE_ROLES = frozenset({"textfield", "combobox"})


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


@dataclass(frozen=True)
class Action:
    """One thing the agent could do next."""

    kind: ActionKind
    element: Element | None = None
    text: str | None = None
    input_key: str | None = None
    ordinal: int | None = None
    ordinal_of: int | None = None

    def describe(self) -> str:
        """The string the model actually reads. Must carry all of the meaning."""
        if self.kind == "click" and self.element is not None:
            return f"Click the {self._noun()}"
        if self.kind == "type" and self.element is not None:
            return f'Type "{self.text}" into the {self._noun()}'
        if self.kind == "scroll":
            return f"Scroll {self.text} to reveal more of the screen"
        if self.kind == "key":
            return f"Press the {self.text} key"
        return "Wait for the screen to finish changing"

    def _noun(self) -> str:
        """The element phrase, with an ordinal only when the label is not unique."""
        assert self.element is not None
        base = f'{self.element.role} labelled "{self.element.label}"'
        if self.ordinal is None:
            return base
        return f"{_ordinal(self.ordinal)} of {self.ordinal_of} {base}, in screen order"

    def signature(self) -> str:
        """Stable identity, used for banning and repeat detection."""
        index = self.element.index if self.element is not None else "-"
        return f"{self.kind}:{index}:{self.text or '-'}"


FALLBACK_ACTIONS: tuple[Action, ...] = (
    Action(kind="scroll", text="down"),
    Action(kind="scroll", text="up"),
    Action(kind="key", text="Enter"),
    Action(kind="key", text="Escape"),
    Action(kind="wait"),
)


def _disambiguate(actions: list[Action]) -> list[Action]:
    """Give colliding descriptions an ordinal, leave unique ones untouched."""
    counts = Counter(a.describe() for a in actions)
    seen: Counter[str] = Counter()
    out: list[Action] = []
    for action in actions:
        described = action.describe()
        total = counts[described]
        if total == 1:
            out.append(action)
            continue
        seen[described] += 1
        out.append(replace(action, ordinal=seen[described], ordinal_of=total))
    return out


def enumerate_actions(
    elements: list[Element],
    inputs: dict[str, str],
    banned: set[str],
    *,
    max_candidates: int = 250,
) -> list[Action]:
    """Build the candidate list in deterministic order: element actions, then fallbacks.

    Truncation cuts element actions first. The fallbacks are the escape hatches —
    without them a screen full of banned buttons would leave no legal move.

    Truncation is a silent correctness failure: it cuts in tree order, which is
    document order rather than relevance order, so the dropped option is
    arbitrary and the agent simply never sees it. It is logged for that reason.
    """
    element_actions: list[Action] = []
    for element in elements:
        if not element.enabled or element.center is None:
            continue
        if element.role in CLICKABLE_ROLES:
            element_actions.append(Action(kind="click", element=element))
        if element.role in EDITABLE_ROLES:
            for key, value in inputs.items():
                element_actions.append(
                    Action(kind="type", element=element, text=value, input_key=key)
                )

    keep = [a for a in element_actions if a.signature() not in banned]
    fallbacks = [a for a in FALLBACK_ACTIONS if a.signature() not in banned]
    room = max(min(max_candidates, MAX_CHOICE_OPTIONS) - len(fallbacks), 0)

    if len(keep) > room:
        logger.warning(
            "truncating candidates: %d element actions available, %d offered. "
            "Truncation is in tree order, so the dropped actions are arbitrary and "
            "the agent cannot select them. See docs/DEVIATIONS.md D18.",
            len(keep),
            room,
        )

    # Typing actions exist only because the caller supplied an input, so they are
    # the caller's stated intent and are never the ones cut. Clicks fill the rest,
    # and tree order is restored so ordinals still mean screen order.
    order = {id(a): i for i, a in enumerate(keep)}
    typing = [a for a in keep if a.kind == "type"][:room]
    clicks = [a for a in keep if a.kind != "type"][: room - len(typing)]
    offered = sorted(typing + clicks, key=lambda a: order[id(a)])
    return _disambiguate(offered) + fallbacks
