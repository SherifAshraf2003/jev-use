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
import subprocess
import time
from pathlib import Path
from typing import Any, Literal, Self

import AppKit
import ApplicationServices as AX
import Quartz

logger = logging.getLogger(__name__)

# Page content sits below the depth at which browser chrome ends; the tree fully
# resolved at 28 on the captured browser window. See docs/DEVIATIONS.md D13.
MAX_DEPTH = 30
SCROLL_LINES = 3
# Placeholder comes before value: a field's placeholder says what the field is for
# ("Search"), its value is whatever happens to be typed in it. YouTube's search
# box has no title or description at all, only a placeholder, so without it the
# box was dropped as unlabelled and the agent could never type a search.
LABEL_ATTRS = (
    AX.kAXTitleAttribute,
    AX.kAXDescriptionAttribute,
    "AXPlaceholderValue",
    AX.kAXValueAttribute,
    # The hover tooltip: last resort, but it is the only name many icon-only
    # toolbar buttons have. Measured on WhatsApp, it named one of four unnamed buttons.
    "AXHelp",
)

SETTINGS_PATH = "System Settings > Privacy & Security > Accessibility"

KEYCODES = {"Enter": 36, "Return": 36, "Escape": 53, "Tab": 48, "Space": 49}

# Time for the window server to finish raising an application before an event is
# posted to it. Without a pause the first event can still reach the old front app.
ACTIVATE_SETTLE_SECONDS = 0.25


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


WINDOW_ATTRS = (AX.kAXWindowsAttribute, "AXFocusedWindow", "AXMainWindow")


def _windows_for(pid: int) -> list[Any]:
    """Every window of an application, by whichever attribute actually answers.

    `AXWindows` is the documented way and is what most applications answer. Chrome
    returns an empty list from it while `AXFocusedWindow` and `AXMainWindow` both
    resolve to a full window tree — the same lazy-accessibility behaviour as D12.
    Asking only `AXWindows` made a visibly open browser look like it had none.
    """
    app = AX.AXUIElementCreateApplication(pid)
    windows: list[Any] = []
    seen: set[int] = set()
    for name in WINDOW_ATTRS:
        value = _attribute(app, name)
        if value is None:
            continue
        candidates = list(value) if isinstance(value, (list, tuple)) else [value]
        for window in candidates:
            # A window with no role is the placeholder Chrome hands back from
            # AXWindows; it has no children and is not worth walking.
            if _attribute(window, AX.kAXRoleAttribute) is None:
                continue
            if id(window) in seen:
                continue
            seen.add(id(window))
            windows.append(window)
        if windows:
            break
    return windows


APP_DIRECTORIES = (
    Path("/Applications"),
    Path("/System/Applications"),
    Path("/System/Applications/Utilities"),
    Path.home() / "Applications",
)
LAUNCH_TIMEOUT_SECONDS = 15.0


def installed_apps() -> list[str]:
    """Names of installed applications plus anything running, sorted, no duplicates.

    Running applications are included because some live outside the standard
    folders. The names are what Jev chooses between for `jev-use do`.
    """
    names = {
        path.stem
        for directory in APP_DIRECTORIES
        if directory.is_dir()
        for path in directory.glob("*.app")
    }
    for app in AppKit.NSWorkspace.sharedWorkspace().runningApplications():
        if app.activationPolicy() == 0 and app.localizedName():
            names.add(str(app.localizedName()).strip("\u200e"))
    return sorted(names, key=str.lower)


def _running_app(pid: int) -> Any:
    return AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)


def _is_frontmost(pid: int) -> bool:
    """Ask the application itself, live, whether it is frontmost.

    Not NSWorkspace.frontmostApplication: that value is only refreshed by a run
    loop, which a command-line process does not have. Measured: after switching
    to Finder, NSWorkspace still reported Chrome while AX correctly said Finder.
    Trusting it let keystrokes meant for the browser land in the terminal.
    """
    app = AX.AXUIElementCreateApplication(pid)
    return _attribute(app, "AXFrontmost") is True


def _frontmost_pid() -> int | None:
    """The pid of the frontmost application, read live through AX."""
    for app in AppKit.NSWorkspace.sharedWorkspace().runningApplications():
        if app.activationPolicy() != 0:
            continue
        pid = int(app.processIdentifier())
        if _is_frontmost(pid):
            return pid
    return None


def _activate(pid: int) -> None:
    AX.AXUIElementSetAttributeValue(AX.AXUIElementCreateApplication(pid), "AXFrontmost", True)
    if not _is_frontmost(pid):
        app = _running_app(pid)
        if app is not None:
            app.activateWithOptions_(AppKit.NSApplicationActivateIgnoringOtherApps)


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

    async def _ensure_frontmost(self) -> None:
        """Raise the target application before synthesizing any input.

        Reading and acting use different addressing. The tree is read from one
        application by pid, but CGEventPost posts to the system: the event goes
        to whatever is frontmost and to whatever window owns that screen point.
        If the target is not in front, clicks land on the window above it and
        keystrokes go to the wrong application entirely — in one live run a URL
        meant for the browser was typed into the terminal that launched the run.
        """
        # Checked live before every event, never cached: focus can move between
        # steps, and a cached answer is how input reached the wrong application.
        if _is_frontmost(self._pid):
            return
        _activate(self._pid)
        await asyncio.sleep(ACTIVATE_SETTLE_SECONDS)
        if not _is_frontmost(self._pid):
            logger.warning(
                "could not bring pid=%s to the front; input may reach another "
                "application. Refusing to act.",
                self._pid,
            )
            raise RuntimeError(
                f"the target application (pid {self._pid}) could not be brought to the "
                "front, so synthesized input would reach whatever is in front instead"
            )

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
        if not _windows_for(pid):
            raise LookupError(
                f"{app_name} is running but has no open windows, so there is nothing "
                "to read. Open a window in it and try again. (A minimized or fully "
                "closed window reports the same way.)"
            )
        bounds = Quartz.CGDisplayBounds(Quartz.CGMainDisplayID())
        return cls(pid, (int(bounds.size.width), int(bounds.size.height)))

    @classmethod
    async def open(cls, app_name: str, timeout: float = LAUNCH_TIMEOUT_SECONDS) -> Self:
        """Attach to an application, launching it or opening a window if needed.

        `open -a` launches an application that is not running, and sends a
        running one the reopen event, which gives a window to most applications
        that have none — Chrome included.
        """
        try:
            return await cls.attach(app_name)
        except LookupError:
            pass
        subprocess.run(["open", "-a", app_name], check=False, capture_output=True)
        deadline = time.monotonic() + timeout
        last_error: LookupError | None = None
        while time.monotonic() < deadline:
            await asyncio.sleep(0.25)
            try:
                return await cls.attach(app_name)
            except LookupError as exc:
                last_error = exc
        raise LookupError(
            f"opened {app_name} but it showed no window within {timeout:.0f}s"
        ) from last_error

    async def tree(self) -> Any:
        """Walk every window of the target application."""
        windows = _windows_for(self._pid)
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
        await self._ensure_frontmost()
        self._events.click(x, y)
        await asyncio.sleep(0.05)

    async def type_text(self, text: str) -> None:
        await self._ensure_frontmost()
        self._events.type_text(text)
        await asyncio.sleep(0.05)

    async def scroll(self, direction: Literal["up", "down"]) -> None:
        await self._ensure_frontmost()
        self._events.scroll(SCROLL_LINES if direction == "up" else -SCROLL_LINES)
        await asyncio.sleep(0.05)

    async def press(self, key: str) -> None:
        await self._ensure_frontmost()
        self._events.key(key)
        await asyncio.sleep(0.05)

    async def close(self) -> None:
        """Nothing to tear down: this process never owned the application."""
        return None
