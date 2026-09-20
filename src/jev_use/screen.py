"""Turn a platform accessibility tree into a flat list of addressable elements.

The macOS adapter normalizes to role/label/enabled/bbox/children, but raw AX
dicts, Windows UIA, Linux AT-SPI, and the optional Cua adapter all spell these
differently. Every candidate key lives in a module-level constant so a new
source is a one-line change, not a hunt through the walker.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

INDEX_KEYS = ("index", "id", "node_id", "element_id", "ref", "AXIdentifier")
ROLE_KEYS = ("role", "AXRole", "role_name", "ControlType", "control_type", "type")
LABEL_KEYS = (
    "label",
    "name",
    "Name",
    "title",
    "AXTitle",
    "AXDescription",
    "AXValue",
    "description",
    "text",
    "value",
)
CHILDREN_KEYS = ("children", "child_nodes", "childNodes", "elements", "nodes")
ENABLED_KEYS = ("enabled", "is_enabled", "IsEnabled", "AXEnabled")
FOCUSED_KEYS = ("focused", "is_focused", "HasKeyboardFocus", "AXFocused")
SELECTED_KEYS = ("selected", "is_selected", "IsSelected", "AXSelected")
BBOX_KEYS = ("bbox", "bounds", "frame", "rect", "BoundingRectangle", "position_size")

ROLE_VOCABULARY = frozenset(
    {
        "button",
        "link",
        "textfield",
        "checkbox",
        "radio",
        "combobox",
        "menuitem",
        "tab",
        "list",
        "listitem",
        "table",
        "row",
        "cell",
        "image",
        "heading",
        "text",
        "window",
        "group",
        "dialog",
        "toolbar",
        "scrollbar",
        "slider",
        "unknown",
    }
)

ROLE_ALIASES = {
    "axapplication": "window",
    "application": "window",
    "axbutton": "button",
    "pushbutton": "button",
    "push button": "button",
    "axlink": "link",
    "hyperlink": "link",
    "axtextfield": "textfield",
    "axtextarea": "textfield",
    "edit": "textfield",
    "entry": "textfield",
    "searchfield": "textfield",
    "axsearchfield": "textfield",
    "axcheckbox": "checkbox",
    "check box": "checkbox",
    "axradiobutton": "radio",
    "radiobutton": "radio",
    "radio button": "radio",
    "axcombobox": "combobox",
    "combo box": "combobox",
    "axpopupbutton": "combobox",
    "axmenuitem": "menuitem",
    "menu item": "menuitem",
    "axmenubaritem": "menuitem",
    "axmenu": "list",
    "axtab": "tab",
    "axtabgroup": "tab",
    "tabitem": "tab",
    "axlist": "list",
    "axoutline": "list",
    "axstatictext": "text",
    "statictext": "text",
    "static text": "text",
    "axheading": "heading",
    "aximage": "image",
    "axwindow": "window",
    "axwebarea": "group",
    "frame": "window",
    "axgroup": "group",
    "axsplitgroup": "group",
    "axscrollarea": "group",
    "axtoolbar": "toolbar",
    "axscrollbar": "scrollbar",
    "axsplitter": "scrollbar",
    "axslider": "slider",
    "axdialog": "dialog",
    "axsheet": "dialog",
    "axtable": "table",
    "axrow": "row",
    "axcell": "cell",
}


@dataclass(frozen=True)
class Element:
    """One addressable thing on the screen."""

    index: str
    role: str
    label: str
    enabled: bool = True
    bbox: tuple[int, int, int, int] | None = None
    focused: bool = False
    selected: bool = False

    def as_line(self) -> str:
        """The single line the model reads.

        The bbox is excluded as noise, but focus and selection are included:
        without them a screen with twenty-four identically labelled tab buttons
        gives the model no way to tell which one belongs to the current tab, and
        it can only guess. See docs/DEVIATIONS.md D22.
        """
        marks = []
        if self.focused:
            marks.append("focused")
        if self.selected:
            marks.append("selected")
        suffix = f" ({', '.join(marks)})" if marks else ""
        return f'[{self.index}] {self.role} "{self.label}"{suffix}'

    @property
    def center(self) -> tuple[int, int] | None:
        """Click point in the tree's own coordinate space, or None if unclickable."""
        if self.bbox is None:
            return None
        x, y, width, height = self.bbox
        return (x + width // 2, y + height // 2)


def _first(node: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in node and node[key] not in (None, ""):
            return node[key]
    return None


def _normalize_role(raw: Any) -> str:
    if not isinstance(raw, str) or not raw:
        return "unknown"
    lowered = raw.strip().lower()
    if lowered in ROLE_ALIASES:
        return ROLE_ALIASES[lowered]
    if lowered in ROLE_VOCABULARY:
        return lowered
    squashed = lowered.replace(" ", "").replace("_", "")
    if squashed in ROLE_ALIASES:
        return ROLE_ALIASES[squashed]
    return squashed if squashed in ROLE_VOCABULARY else "unknown"


def _normalize_bbox(raw: Any) -> tuple[int, int, int, int] | None:
    if isinstance(raw, dict):
        width = raw.get("width", raw.get("w"))
        height = raw.get("height", raw.get("h"))
        try:
            return (
                int(raw["x"]),
                int(raw["y"]),
                int(width if width is not None else 0),
                int(height if height is not None else 0),
            )
        except (KeyError, TypeError, ValueError):
            return None
    if isinstance(raw, (list, tuple)) and len(raw) == 4:
        try:
            return (int(raw[0]), int(raw[1]), int(raw[2]), int(raw[3]))
        except (TypeError, ValueError):
            return None
    return None


def _walk(node: Any) -> Iterator[dict[str, Any]]:
    """Depth-first, tree order. Stable across runs for a stable input."""
    if isinstance(node, dict):
        yield node
        children = _first(node, CHILDREN_KEYS)
        if isinstance(children, list):
            for child in children:
                yield from _walk(child)
    elif isinstance(node, list):
        for child in node:
            yield from _walk(child)


def is_on_screen(bbox: tuple[int, int, int, int], screen_bounds: tuple[int, int]) -> bool:
    """Whether a synthesized click at this rectangle would land on the display.

    An element with zero area (a closed menu item) or one lying entirely outside
    the display (scrolled out of view, or on another screen) cannot be clicked:
    the event would land somewhere else. Dropping these is a correctness filter,
    not a relevance guess. See docs/DEVIATIONS.md D11.
    """
    x, y, width, height = bbox
    screen_width, screen_height = screen_bounds
    if width <= 0 or height <= 0:
        return False
    return x + width > 0 and y + height > 0 and x < screen_width and y < screen_height


def parse_tree(
    root: Any,
    *,
    screen_bounds: tuple[int, int] | None = None,
    max_elements: int = 250,
    max_label: int = 70,
) -> list[Element]:
    """Flatten an accessibility tree into at most `max_elements` labelled elements.

    When `screen_bounds` is given, elements that cannot be clicked are dropped.
    When it is None — parsing a stored fixture, say — nothing is filtered.
    """
    elements: list[Element] = []
    for position, node in enumerate(_walk(root)):
        raw_label = _first(node, LABEL_KEYS)
        label = str(raw_label).strip()[:max_label] if raw_label is not None else ""
        if not label:
            continue
        bbox = _normalize_bbox(_first(node, BBOX_KEYS))
        if screen_bounds is not None and bbox is not None and not is_on_screen(bbox, screen_bounds):
            continue
        raw_index = _first(node, INDEX_KEYS)
        enabled = _first(node, ENABLED_KEYS)
        elements.append(
            Element(
                index=str(raw_index) if raw_index is not None else f"e{position}",
                role=_normalize_role(_first(node, ROLE_KEYS)),
                label=label,
                enabled=True if enabled is None else bool(enabled),
                bbox=bbox,
                focused=bool(_first(node, FOCUSED_KEYS)),
                selected=bool(_first(node, SELECTED_KEYS)),
            )
        )
        if len(elements) >= max_elements:
            break
    return elements
