"""Replace the text in a captured tree while keeping its structure.

A real capture contains whatever was on screen: filenames, page content, account
names. That must not be committed to a public repository. This keeps every role,
bounding box, depth, and child ordering — which is all the parser tests care
about — and replaces each label with a generic stand-in of the same length class.

    python scripts/sanitize_tree.py raw.json tests/fixtures/trees/safe.json
"""

import json
import pathlib
import sys

WORDS = {
    "AXButton": "Button",
    "AXLink": "Link",
    "AXStaticText": "Text",
    "AXTextField": "Field",
    "AXHeading": "Heading",
    "AXImage": "Image",
    "AXMenuItem": "Menu item",
    "AXRadioButton": "Option",
    "AXPopUpButton": "Menu",
    "AXCheckBox": "Checkbox",
    "AXWindow": "Window",
    "AXWebArea": "Page",
}

counters: dict[str, int] = {}


def label_for(role: str, original: str) -> str:
    """A stable, meaningless label that preserves rough length."""
    if not original:
        return ""
    base = WORDS.get(role, role.removeprefix("AX") or "Node")
    counters[base] = counters.get(base, 0) + 1
    name = f"{base} {counters[base]}"
    # Preserve whether the original was short or long, since truncation is tested.
    if len(original) > 70:
        name = name + " " + "x" * (len(original) - len(name) - 1)
    return name


def sanitize(node: dict) -> dict:
    return {
        "role": node["role"],
        "label": label_for(node["role"], node["label"]),
        "enabled": node["enabled"],
        "bbox": node["bbox"],
        "children": [sanitize(c) for c in node["children"]],
    }


def main(src: str, dst: str) -> None:
    tree = json.loads(pathlib.Path(src).read_text())
    out = sanitize(tree)
    pathlib.Path(dst).write_text(json.dumps(out, indent=2) + "\n")

    def count(n: dict) -> int:
        return 1 + sum(count(c) for c in n["children"])

    print(f"{count(out)} nodes sanitized -> {dst}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
