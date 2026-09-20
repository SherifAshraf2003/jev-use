import pytest

from jev_use.actions import Action
from jev_use.computers.base import execute_action
from jev_use.computers.replay import ReplayComputer
from jev_use.screen import Element


def field(label="Email"):
    return Element(index="e1", role="textfield", label=label, bbox=(100, 200, 80, 20))


async def test_replay_advances_through_trees_and_repeats_the_last() -> None:
    computer = ReplayComputer([{"n": 1}, {"n": 2}])
    assert await computer.tree() == {"n": 1}
    await computer.press("Enter")
    assert await computer.tree() == {"n": 2}
    await computer.press("Enter")
    assert await computer.tree() == {"n": 2}


async def test_replay_reports_a_screen_size() -> None:
    assert await ReplayComputer([{}]).screen_size() == (1920, 1080)
    assert await ReplayComputer([{}], screen=(2560, 1440)).screen_size() == (2560, 1440)


async def test_replay_rejects_an_empty_script() -> None:
    with pytest.raises(ValueError, match="at least one tree"):
        ReplayComputer([])


async def test_click_dispatches_the_element_center() -> None:
    computer = ReplayComputer([{}])
    element = Element(index="e0", role="button", label="Go", bbox=(100, 200, 80, 30))
    await execute_action(computer, Action(kind="click", element=element))
    assert computer.calls == [("click", (140, 215))]


async def test_typing_focuses_the_field_first() -> None:
    computer = ReplayComputer([{}])
    action = Action(kind="type", element=field(), text="a@b.com", input_key="email")
    await execute_action(computer, action)
    assert computer.calls == [("click", (140, 210)), ("type_text", "a@b.com")]


async def test_fallback_actions_dispatch_without_an_element() -> None:
    computer = ReplayComputer([{}])
    await execute_action(computer, Action(kind="scroll", text="down"))
    await execute_action(computer, Action(kind="scroll", text="up"))
    await execute_action(computer, Action(kind="key", text="Enter"))
    await execute_action(computer, Action(kind="wait"))
    assert computer.calls == [
        ("scroll", "down"),
        ("scroll", "up"),
        ("press", "Enter"),
        ("wait", None),
    ]


async def test_clicking_an_element_without_a_bbox_raises() -> None:
    computer = ReplayComputer([{}])
    element = Element(index="e0", role="button", label="Go")
    with pytest.raises(ValueError, match="no bounding box"):
        await execute_action(computer, Action(kind="click", element=element))


async def test_click_action_without_an_element_raises() -> None:
    with pytest.raises(ValueError, match="carries no element"):
        await execute_action(ReplayComputer([{}]), Action(kind="click"))


async def test_close_is_recorded() -> None:
    computer = ReplayComputer([{}])
    await computer.close()
    assert computer.calls == [("close", None)]
