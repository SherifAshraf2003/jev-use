"""A fake machine that plays back captured trees. Used by tests and the demo."""

from __future__ import annotations

from typing import Any, Literal


class ReplayComputer:
    """Advances to the next captured tree on any action; holds on the last one."""

    def __init__(self, trees: list[Any], *, screen: tuple[int, int] = (1920, 1080)) -> None:
        if not trees:
            raise ValueError("ReplayComputer needs at least one tree")
        self._trees = trees
        self._screen = screen
        self._position = 0
        self.calls: list[tuple[str, Any]] = []

    async def tree(self) -> Any:
        return self._trees[self._position]

    async def screen_size(self) -> tuple[int, int]:
        return self._screen

    def _advance(self) -> None:
        self._position = min(self._position + 1, len(self._trees) - 1)

    async def click(self, x: int, y: int) -> None:
        self.calls.append(("click", (x, y)))
        self._advance()

    async def type_text(self, text: str) -> None:
        self.calls.append(("type_text", text))

    async def scroll(self, direction: Literal["up", "down"]) -> None:
        self.calls.append(("scroll", direction))
        self._advance()

    async def press(self, key: str) -> None:
        self.calls.append(("press", key))
        self._advance()

    async def wait(self) -> None:
        self.calls.append(("wait", None))

    async def close(self) -> None:
        self.calls.append(("close", None))
