"""Capture one application's accessibility tree as a test fixture.

Requires Accessibility permission for the host process. Usage:

    python scripts/capture_mac_tree.py Chrome
"""

import json
import pathlib
import sys

import AppKit
import ApplicationServices as AX

OUT_DIR = pathlib.Path(__file__).parent.parent / "tests/fixtures/trees"
MAX_DEPTH = 30

LABEL_ATTRS = (AX.kAXTitleAttribute, AX.kAXDescriptionAttribute, AX.kAXValueAttribute)


def attribute(element, name):
    err, value = AX.AXUIElementCopyAttributeValue(element, name, None)
    return value if err == 0 else None


def bbox(element):
    position = attribute(element, AX.kAXPositionAttribute)
    size = attribute(element, AX.kAXSizeAttribute)
    if position is None or size is None:
        return None
    ok_position, point = AX.AXValueGetValue(position, AX.kAXValueCGPointType, None)
    ok_size, extent = AX.AXValueGetValue(size, AX.kAXValueCGSizeType, None)
    if not (ok_position and ok_size):
        return None
    return [int(point.x), int(point.y), int(extent.width), int(extent.height)]


def node(element, depth=0):
    label = next((attribute(element, a) for a in LABEL_ATTRS if attribute(element, a)), None)
    enabled = attribute(element, AX.kAXEnabledAttribute)
    children = attribute(element, AX.kAXChildrenAttribute) or []
    return {
        "role": str(attribute(element, AX.kAXRoleAttribute) or ""),
        "label": str(label).strip() if label else "",
        "enabled": True if enabled is None else bool(enabled),
        "bbox": bbox(element),
        "children": [node(c, depth + 1) for c in children] if depth < MAX_DEPTH else [],
    }


def pid_for(name):
    for app in AppKit.NSWorkspace.sharedWorkspace().runningApplications():
        if app.activationPolicy() == 0 and name.lower() in (app.localizedName() or "").lower():
            return int(app.processIdentifier())
    raise SystemExit(f"no running application matching {name!r}")


def main(name):
    if not AX.AXIsProcessTrusted():
        raise SystemExit(
            "this process is not trusted for Accessibility. Grant the terminal "
            "application in System Settings > Privacy & Security > Accessibility."
        )
    app = AX.AXUIElementCreateApplication(pid_for(name))
    windows = attribute(app, AX.kAXWindowsAttribute) or []
    if not windows:
        raise SystemExit(f"{name} has no open windows; open one and retry")
    tree = {
        "role": "application",
        "label": name,
        "enabled": True,
        "bbox": None,
        "children": [node(w) for w in windows],
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"mac_{name.lower()}.json"
    out.write_text(json.dumps(tree, indent=2) + "\n")

    def count(n):
        return 1 + sum(count(c) for c in n["children"])

    print(f"{count(tree)} nodes from {len(windows)} windows -> {out}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "Finder")
