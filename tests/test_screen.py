import json

import pytest

from jev_use.screen import ROLE_VOCABULARY, Element, is_on_screen, parse_tree


def load(fixtures_dir, name):
    return json.loads((fixtures_dir / "trees" / f"{name}.json").read_text())


def test_element_as_line_is_model_readable() -> None:
    element = Element(index="e14", role="button", label="Continue")
    assert element.as_line() == '[e14] button "Continue"'


def test_element_center_from_bbox() -> None:
    assert Element(index="e0", role="button", label="Go", bbox=(100, 200, 80, 30)).center == (
        140,
        215,
    )
    assert Element(index="e1", role="button", label="Go").center is None


@pytest.mark.parametrize("name", ["mac_chrome", "mac_finder"])
def test_parses_every_fixture_without_raising(fixtures_dir, name) -> None:
    elements = parse_tree(load(fixtures_dir, name))
    assert elements
    assert all(e.role in ROLE_VOCABULARY for e in elements)


def test_real_capture_exposes_expected_roles(fixtures_dir) -> None:
    roles = {e.role for e in parse_tree(load(fixtures_dir, "mac_chrome"))}
    assert {"button", "link", "text"} <= roles


def test_unlabelled_nodes_are_dropped() -> None:
    tree = {"role": "window", "label": "", "children": [{"role": "button", "label": "Go"}]}
    assert [e.label for e in parse_tree(tree)] == ["Go"]


def test_ordering_is_deterministic(fixtures_dir) -> None:
    tree = load(fixtures_dir, "mac_finder")
    assert parse_tree(tree) == parse_tree(tree)


def test_labels_are_truncated() -> None:
    tree = {"role": "button", "name": "x" * 500, "children": []}
    assert len(parse_tree(tree, max_label=20)[0].label) == 20


def test_max_elements_is_respected() -> None:
    tree = {
        "role": "window",
        "name": "Big",
        "children": [{"role": "button", "name": f"b{i}", "children": []} for i in range(300)],
    }
    assert len(parse_tree(tree, max_elements=150)) == 150


def test_macos_ax_role_names_are_normalized() -> None:
    tree = {
        "AXRole": "AXWindow",
        "AXTitle": "W",
        "children": [
            {"AXRole": "AXButton", "AXTitle": "Continue", "children": []},
            {"AXRole": "AXTextField", "AXTitle": "Email", "children": []},
            {"AXRole": "AXStaticText", "AXTitle": "Hello", "children": []},
        ],
    }
    assert [(e.role, e.label) for e in parse_tree(tree)] == [
        ("window", "W"),
        ("button", "Continue"),
        ("textfield", "Email"),
        ("text", "Hello"),
    ]


def test_windows_and_atspi_key_names_are_tolerated() -> None:
    uia = {"ControlType": "Button", "Name": "Save", "BoundingRectangle": [10, 20, 60, 24]}
    atspi = {
        "role_name": "push button",
        "name": "Open",
        "bounds": {"x": 5, "y": 7, "w": 40, "h": 20},
    }
    assert parse_tree(uia)[0] == Element(
        index="e0", role="button", label="Save", bbox=(10, 20, 60, 24)
    )
    assert parse_tree(atspi)[0].role == "button"
    assert parse_tree(atspi)[0].bbox == (5, 7, 40, 20)


@pytest.mark.parametrize(
    "bbox,expected",
    [
        ((10, 10, 100, 40), True),
        ((0, 0, 0, 0), False),
        ((-500, 10, 100, 40), False),
        ((1900, 10, 100, 40), True),
        ((3000, 10, 100, 40), False),
        ((10, 2000, 100, 40), False),
    ],
)
def test_is_on_screen(bbox, expected) -> None:
    assert is_on_screen(bbox, (1920, 1080)) is expected


def test_offscreen_elements_are_dropped_when_bounds_are_known() -> None:
    tree = {
        "role": "window",
        "label": "W",
        "children": [
            {"role": "button", "label": "Visible", "bbox": [10, 10, 40, 20], "children": []},
            {"role": "button", "label": "Closed menu", "bbox": [0, 0, 0, 0], "children": []},
            {
                "role": "button",
                "label": "Scrolled away",
                "bbox": [10, 5000, 40, 20],
                "children": [],
            },
        ],
    }
    assert [e.label for e in parse_tree(tree, screen_bounds=(1920, 1080))] == ["W", "Visible"]


def test_without_bounds_nothing_is_filtered() -> None:
    tree = {
        "role": "window",
        "label": "W",
        "children": [
            {"role": "button", "label": "Scrolled away", "bbox": [10, 5000, 40, 20], "children": []}
        ],
    }
    assert len(parse_tree(tree)) == 2


def test_disabled_state_is_carried_through() -> None:
    tree = {"role": "button", "label": "Pay", "enabled": False, "children": []}
    assert parse_tree(tree)[0].enabled is False
