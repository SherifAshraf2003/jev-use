import pytest

pytest.importorskip("ApplicationServices")

from jev_use.computers.mac import MacComputer  # noqa: E402


class FakeQuartz:
    def __init__(self):
        self.events = []

    def click(self, x, y):
        self.events.append(("click", x, y))

    def type_text(self, text):
        self.events.append(("type", text))

    def scroll(self, amount):
        self.events.append(("scroll", amount))

    def key(self, name):
        self.events.append(("key", name))


async def test_protocol_methods_reach_the_event_layer() -> None:
    quartz = FakeQuartz()
    computer = MacComputer(pid=123, screen=(1920, 1080), events=quartz)
    await computer.click(10, 20)
    await computer.type_text("hello")
    await computer.scroll("down")
    await computer.scroll("up")
    await computer.press("Enter")
    assert quartz.events == [
        ("click", 10, 20),
        ("type", "hello"),
        ("scroll", -3),
        ("scroll", 3),
        ("key", "Enter"),
    ]


async def test_screen_size_is_reported() -> None:
    computer = MacComputer(pid=1, screen=(2560, 1440), events=FakeQuartz())
    assert await computer.screen_size() == (2560, 1440)


async def test_untrusted_process_fails_loudly(monkeypatch) -> None:
    """An untrusted process returns empty trees with no error; that must not reach the loop."""
    import jev_use.computers.mac as mac

    monkeypatch.setattr(mac, "_is_trusted", lambda: False)
    with pytest.raises(PermissionError, match="Privacy & Security"):
        await MacComputer.attach("Finder")


async def test_missing_application_names_itself(monkeypatch) -> None:
    import jev_use.computers.mac as mac

    monkeypatch.setattr(mac, "_is_trusted", lambda: True)

    def boom(name):
        raise LookupError(f"no running application matching {name!r}")

    monkeypatch.setattr(mac, "find_pid", boom)
    with pytest.raises(LookupError, match="NoSuchApp"):
        await MacComputer.attach("NoSuchApp")


async def test_close_does_not_touch_the_application() -> None:
    computer = MacComputer(pid=1, screen=(1920, 1080), events=FakeQuartz())
    await computer.close()


def test_unmapped_key_raises_rather_than_guessing_a_keycode() -> None:
    from jev_use.computers.mac import QuartzEvents

    with pytest.raises(ValueError, match="unmapped key"):
        QuartzEvents().key("F13")


@pytest.mark.live
async def test_real_application_tree_parses() -> None:
    from jev_use.screen import parse_tree

    computer = await MacComputer.attach("Finder")
    try:
        elements = parse_tree(await computer.tree(), screen_bounds=await computer.screen_size())
    finally:
        await computer.close()
    assert elements, "Finder returned no elements; is Accessibility permission granted?"
