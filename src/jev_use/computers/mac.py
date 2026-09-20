"""Drive the local Mac through the Accessibility API and Quartz.

Reading is AXUIElementCopyAttributeValue against the target application. Acting
is CGEventPost, which takes a screen point — hence the coordinate-based protocol.
No screenshots are taken and no pixels are read.

Accessibility permission attaches to the process that launched this one, so the
trust check belongs at attach time and must be loud: an untrusted process gets
empty trees back with no error raised (docs/DEVIATIONS.md D8).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal, Self

import AppKit
import ApplicationServices as AX
import Quartz

logger = logging.getLogger(__name__)

# Page content sits below the depth at which browser chrome ends; the tree fully
# resolved at 28 on the captured browser window. See docs/DEVIATIONS.md D13.
MAX_DEPTH = 30
SCROLL_LINES = 3
LABEL_ATTRS = (AX.kAXTitleAttribute, AX.kAXDescriptionAttribute, AX.kAXValueAttribute)

SETTINGS_PATH = "System Settings > Privacy & Security > Accessibility"

KEYCODES = {"Enter": 36, "Return": 36, "Escape": 53, "Tab": 48, "Space": 49}


def _is_trusted() -> bool:
    """Separate function so tests can monkeypatch it."""
    return bool(AX.AXIsProcessTrusted())


def find_pid(app_name: str) -> int:
    """Resolve a running, user-facing application by name."""
    for app in AppKit.NSWorkspace.sharedWorkspace().runningApplications():
        if app.activationPolicy() != 0:
            continue
        name = app.localizedName() or ""
        if app_name.lower() in name.lower():
            return int(app.processIdentifier())
    raise LookupError(f"no running application matching {app_name!r}")


def _attribute(element: Any, name: Any) -> Any:
    err, value = AX.AXUIElementCopyAttributeValue(element, name, None)
    return value if err == 0 else None


def _bbox(element: Any) -> list[int] | None:
    position = _attribute(element, AX.kAXPositionAttribute)
    size = _attribute(element, AX.kAXSizeAttribute)
    if position is None or size is None:
        return None
    ok_position, point = AX.AXValueGetValue(position, AX.kAXValueCGPointType, None)
    ok_size, extent = AX.AXValueGetValue(size, AX.kAXValueCGSizeType, None)
    if not (ok_position and ok_size):
        return None
    return [int(point.x), int(point.y), int(extent.width), int(extent.height)]


def _node(element: Any, depth: int = 0) -> dict[str, Any]:
    label = next((_attribute(element, a) for a in LABEL_ATTRS if _attribute(element, a)), None)
    enabled = _attribute(element, AX.kAXEnabledAttribute)
    children = _attribute(element, AX.kAXChildrenAttribute) or []
    return {
        "role": str(_attribute(element, AX.kAXRoleAttribute) or ""),
        "label": str(label).strip() if label else "",
        "enabled": True if enabled is None else bool(enabled),
        "bbox": _bbox(element),
        "focused": _attribute(element, AX.kAXFocusedAttribute) is True,
        "selected": _attribute(element, "AXSelected") is True,
        "children": [_node(c, depth + 1) for c in children] if depth < MAX_DEPTH else [],
    }


class QuartzEvents:
    """Input synthesis. Isolated so the adapter can be tested without moving the cursor."""

    def click(self, x: int, y: int) -> None:
        point = Quartz.CGPointMake(x, y)
        for event_type in (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp):
            event = Quartz.CGEventCreateMouseEvent(
                None, event_type, point, Quartz.kCGMouseButtonLeft
            )
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)

    def type_text(self, text: str) -> None:
        for character in text:
            for pressed in (True, False):
                event = Quartz.CGEventCreateKeyboardEvent(None, 0, pressed)
                Quartz.CGEventKeyboardSetUnicodeString(event, 1, character)
                Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)

    def scroll(self, amount: int) -> None:
        event = Quartz.CGEventCreateScrollWheelEvent(None, Quartz.kCGScrollEventUnitLine, 1, amount)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)

    def key(self, name: str) -> None:
        code = KEYCODES.get(name)
        if code is None:
            raise ValueError(f"unmapped key {name!r}; add it to KEYCODES")
        for pressed in (True, False):
            Quartz.CGEventPost(
                Quartz.kCGHIDEventTap, Quartz.CGEventCreateKeyboardEvent(None, code, pressed)
            )


class MacComputer:
    """The local Mac, exposed through the Computer protocol."""

    def __init__(self, pid: int, screen: tuple[int, int], events: Any | None = None) -> None:
        self._pid = pid
        self._screen = screen
        self._events = events if events is not None else QuartzEvents()

    @classmethod
    async def attach(cls, app_name: str) -> Self:
        """Resolve a running application and verify this process may read it."""
        if not _is_trusted():
            raise PermissionError(
                "this process is not trusted for Accessibility. Grant the terminal or "
                f"editor that launched it in {SETTINGS_PATH}, then restart it. Without "
                "the grant every accessibility read returns empty with no error raised."
            )
        pid = find_pid(app_name)
        bounds = Quartz.CGDisplayBounds(Quartz.CGMainDisplayID())
        return cls(pid, (int(bounds.size.width), int(bounds.size.height)))

    async def tree(self) -> Any:
        """Walk every window of the target application."""
        app = AX.AXUIElementCreateApplication(self._pid)
        windows = _attribute(app, AX.kAXWindowsAttribute) or []
        if not windows:
            logger.warning("application pid=%s has no open windows", self._pid)
        return {
            "role": "application",
            "label": "",
            "enabled": True,
            "bbox": None,
            "children": [_node(window) for window in windows],
        }

    async def screen_size(self) -> tuple[int, int]:
        return self._screen

    async def click(self, x: int, y: int) -> None:
        self._events.click(x, y)
        await asyncio.sleep(0.05)

    async def type_text(self, text: str) -> None:
        self._events.type_text(text)
        await asyncio.sleep(0.05)

    async def scroll(self, direction: Literal["up", "down"]) -> None:
        self._events.scroll(SCROLL_LINES if direction == "up" else -SCROLL_LINES)
        await asyncio.sleep(0.05)

    async def press(self, key: str) -> None:
        self._events.key(key)
        await asyncio.sleep(0.05)

    async def close(self) -> None:
        """Nothing to tear down: this process never owned the application."""
        return None
