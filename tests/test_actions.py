import logging

from jev_use.actions import FALLBACK_ACTIONS, MAX_CHOICE_OPTIONS, Action, enumerate_actions
from jev_use.screen import Element


def button(label: str, index: str = "e0") -> Element:
    return Element(index=index, role="button", label=label, bbox=(0, 0, 10, 10))


def field(label: str, index: str = "e1") -> Element:
    return Element(index=index, role="textfield", label=label, bbox=(0, 20, 100, 10))


def test_describe_is_self_contained() -> None:
    assert (
        Action(kind="click", element=button("Continue")).describe()
        == 'Click the button labelled "Continue"'
    )
    typing = Action(kind="type", element=field("Email"), text="a@b.com", input_key="email")
    assert typing.describe() == 'Type "a@b.com" into the textfield labelled "Email"'
    assert Action(kind="scroll", text="down").describe() == (
        "Scroll down to reveal more of the screen"
    )
    assert Action(kind="key", text="Enter").describe() == "Press the Enter key"
    assert Action(kind="wait").describe() == "Wait for the screen to finish changing"


def test_signature_is_stable_and_distinguishing() -> None:
    first = Action(kind="click", element=button("Continue", "e0"))
    assert first.signature() == Action(kind="click", element=button("Continue", "e0")).signature()
    assert first.signature() != Action(kind="click", element=button("Continue", "e7")).signature()
    assert first.signature() != Action(kind="type", element=button("Continue", "e0")).signature()


def test_banned_signatures_are_omitted_entirely() -> None:
    element = button("Continue")
    banned = {Action(kind="click", element=element).signature()}
    actions = enumerate_actions([element], {}, banned)
    assert all(a.signature() not in banned for a in actions)


def test_a_banned_fallback_is_also_omitted() -> None:
    banned = {Action(kind="scroll", text="down").signature()}
    actions = enumerate_actions([], {}, banned)
    assert all(a.signature() not in banned for a in actions)


def test_disabled_and_unclickable_elements_are_skipped() -> None:
    disabled = Element(index="e0", role="button", label="Pay", enabled=False, bbox=(0, 0, 4, 4))
    no_bbox = Element(index="e1", role="button", label="Help")
    actions = enumerate_actions([disabled, no_bbox], {}, set())
    assert [a for a in actions if a.kind == "click"] == []


def test_one_type_action_per_input_per_field() -> None:
    actions = enumerate_actions([field("Email")], {"email": "a@b.com", "name": "Ada"}, set())
    typed = [a for a in actions if a.kind == "type"]
    assert {a.text for a in typed} == {"a@b.com", "Ada"}
    assert {a.input_key for a in typed} == {"email", "name"}


def test_fallbacks_survive_truncation() -> None:
    elements = [button(f"b{i}", f"e{i}") for i in range(400)]
    actions = enumerate_actions(elements, {}, set(), max_candidates=10)
    assert len(actions) == 10
    for fallback in FALLBACK_ACTIONS:
        assert fallback.signature() in {a.signature() for a in actions}


def test_truncation_logs_a_warning(caplog) -> None:
    """D18: truncation is a silent correctness failure unless it is visible."""
    elements = [button(f"b{i}", f"e{i}") for i in range(400)]
    with caplog.at_level(logging.WARNING, logger="jev_use.actions"):
        enumerate_actions(elements, {}, set(), max_candidates=50)
    assert any("truncat" in r.message.lower() for r in caplog.records)
    assert any("400" in r.message or "395" in r.message for r in caplog.records)


def test_no_warning_when_everything_fits(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="jev_use.actions"):
        enumerate_actions([button("Go")], {}, set())
    assert caplog.records == []


def test_never_exceeds_the_api_ceiling() -> None:
    """A Choice question accepts at most 255 options (D16)."""
    elements = [button(f"b{i}", f"e{i}") for i in range(1000)]
    actions = enumerate_actions(elements, {"a": "1", "b": "2"}, set())
    assert len(actions) <= MAX_CHOICE_OPTIONS


def test_default_cap_leaves_room_for_fallbacks() -> None:
    elements = [button(f"b{i}", f"e{i}") for i in range(1000)]
    actions = enumerate_actions(elements, {}, set())
    assert len(actions) == 250
    assert len([a for a in actions if a.element is None]) == len(FALLBACK_ACTIONS)


def test_ordering_is_deterministic() -> None:
    elements = [button("a", "e0"), field("b", "e1")]
    assert enumerate_actions(elements, {"k": "v"}, set()) == enumerate_actions(
        elements, {"k": "v"}, set()
    )


def test_descriptions_are_unique_within_a_step() -> None:
    """brain.py maps the returned choice string back to its Action by description."""
    elements = [button("Save", "e0"), button("Save", "e1"), field("Note", "e2")]
    actions = enumerate_actions(elements, {"x": "1"}, set())
    described = [a.describe() for a in actions]
    assert len(described) == len(set(described))


def test_typing_actions_are_never_truncated() -> None:
    """They exist only because the caller supplied input; cutting them loses the intent."""
    clicks = [button(f"b{i}", f"e{i}") for i in range(400)]
    address = Element(index="e999", role="textfield", label="Address", bbox=(0, 0, 100, 10))
    actions = enumerate_actions(clicks + [address], {"url": "x.com"}, set(), max_candidates=50)
    assert any(a.kind == "type" for a in actions)
    assert len(actions) == 50
