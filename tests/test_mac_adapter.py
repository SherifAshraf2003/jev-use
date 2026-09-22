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


async def test_protocol_methods_reach_the_event_layer(monkeypatch) -> None:
    import jev_use.computers.mac as mac

    # Acting now raises the target first; that path has its own tests.
    monkeypatch.setattr(mac, "_activate", lambda pid: None)
    monkeypatch.setattr(mac, "_is_frontmost", lambda pid: True)
    monkeypatch.setattr(mac, "ACTIVATE_SETTLE_SECONDS", 0)
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
    """Attach to whichever application actually has a usable window right now.

    Hardcoding one application made this depend on machine state: it passed
    while Finder had a desktop window and failed once that window went stale.
    """
    import AppKit

    from jev_use.computers.mac import _is_trusted, _windows_for
    from jev_use.screen import parse_tree

    if not _is_trusted():
        pytest.skip("this process is not trusted for Accessibility")

    target = None
    for app in AppKit.NSWorkspace.sharedWorkspace().runningApplications():
        if app.activationPolicy() != 0:
            continue
        if _windows_for(int(app.processIdentifier())):
            target = app.localizedName()
            break
    if target is None:
        pytest.skip("no running application currently has a usable window")

    computer = await MacComputer.attach(target)
    try:
        elements = parse_tree(await computer.tree(), screen_bounds=await computer.screen_size())
    finally:
        await computer.close()
    assert elements, f"{target} returned no elements despite reporting a window"


async def test_input_is_refused_when_the_target_cannot_be_raised(monkeypatch) -> None:
    """A live run typed a URL into the terminal because the browser was never raised."""
    import jev_use.computers.mac as mac

    monkeypatch.setattr(mac, "_activate", lambda pid: None)
    monkeypatch.setattr(mac, "_is_frontmost", lambda pid: False)
    monkeypatch.setattr(mac, "ACTIVATE_SETTLE_SECONDS", 0)
    quartz = FakeQuartz()
    computer = MacComputer(pid=999, screen=(1920, 1080), events=quartz)
    with pytest.raises(RuntimeError, match="could not be brought to the front"):
        await computer.type_text("amazon.com")
    assert quartz.events == [], "no input may be synthesized when the target is not in front"


async def test_every_input_method_raises_the_target_first(monkeypatch) -> None:
    import jev_use.computers.mac as mac

    activated: list[int] = []
    monkeypatch.setattr(mac, "_activate", lambda pid: activated.append(pid))
    monkeypatch.setattr(mac, "_is_frontmost", lambda pid: bool(activated))
    monkeypatch.setattr(mac, "ACTIVATE_SETTLE_SECONDS", 0)
    quartz = FakeQuartz()
    computer = MacComputer(pid=42, screen=(1920, 1080), events=quartz)
    await computer.click(1, 2)
    assert activated == [42]
    await computer.type_text("x")
    await computer.scroll("down")
    await computer.press("Enter")
    assert len(quartz.events) == 4


async def test_reading_the_tree_does_not_steal_focus(monkeypatch) -> None:
    """Observation must never raise anything; only acting does."""
    import jev_use.computers.mac as mac

    activated: list[int] = []
    monkeypatch.setattr(mac, "_activate", lambda pid: activated.append(pid))
    monkeypatch.setattr(mac, "_windows_for", lambda pid: [])
    computer = MacComputer(pid=7, screen=(1920, 1080), events=FakeQuartz())
    await computer.tree()
    await computer.screen_size()
    assert activated == []


async def test_focus_is_rechecked_before_every_event_not_cached(monkeypatch) -> None:
    """Focus moved back to the terminal mid-run and a cached answer let typing follow it."""
    import jev_use.computers.mac as mac

    front = {"value": True}
    activations: list[int] = []

    def activate(pid):
        activations.append(pid)
        front["value"] = True

    monkeypatch.setattr(mac, "_activate", activate)
    monkeypatch.setattr(mac, "_is_frontmost", lambda pid: front["value"])
    monkeypatch.setattr(mac, "ACTIVATE_SETTLE_SECONDS", 0)
    computer = MacComputer(pid=5, screen=(1920, 1080), events=FakeQuartz())
    await computer.click(1, 1)
    assert activations == []
    front["value"] = False  # the user, or anything else, brings another app forward
    await computer.type_text("youtube")
    assert activations == [5]
