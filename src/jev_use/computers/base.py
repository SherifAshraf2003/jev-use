"""The machine interface, and the dispatcher that turns an Action into calls.

Coordinates rather than element indexes, because CGEventPost posts events at a
screen point and there is no element-indexed dispatch on macOS (D1, D11).
Focusing a field before typing lives here so that no caller has to remember the
two-call sequence.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal, Protocol, runtime_checkable

from jev_use.actions import Action

logger = logging.getLogger(__name__)

WAIT_SECONDS = 1.0


@runtime_checkable
class Computer(Protocol):
    async def tree(self) -> Any: ...
    async def screen_size(self) -> tuple[int, int]: ...
    async def click(self, x: int, y: int) -> None: ...
    async def type_text(self, text: str) -> None: ...
    async def scroll(self, direction: Literal["up", "down"]) -> None: ...
    async def press(self, key: str) -> None: ...
    async def close(self) -> None: ...


async def execute_action(computer: Computer, action: Action) -> None:
    """Carry out one action on the machine."""
    if action.kind in {"click", "type"}:
        if action.element is None:
            raise ValueError(f"{action.kind} action carries no element")
        point = action.element.center
        if point is None:
            raise ValueError(
                f"element {action.element.index} has no bounding box, so it cannot be clicked"
            )
        await computer.click(*point)
        if action.kind == "type":
            await computer.type_text(action.text or "")
        return
    if action.kind == "scroll":
        direction: Literal["up", "down"] = "up" if action.text == "up" else "down"
        await computer.scroll(direction)
        return
    if action.kind == "key":
        await computer.press(action.text or "Enter")
        return

    # wait: a fake machine records it so tests can see it; a real one just sleeps.
    waiter = getattr(computer, "wait", None)
    if waiter is not None:
        await waiter()
    else:
        await asyncio.sleep(WAIT_SECONDS)
