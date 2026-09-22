import pytest

pytest.importorskip("Speech")

from jev_use.voice import SegmentStitcher  # noqa: E402


def feed(partials):
    s = SegmentStitcher()
    for p in partials:
        s.update(p)
    return s.text


def test_a_restart_after_a_pause_keeps_the_earlier_words() -> None:
    """Measured sequence from the on-device recognizer with a pause mid-sentence."""
    partials = [
        "Search",
        "Search for",
        "Search for Claud",
        "Search for Claude",
        "Search for Claude code",
        "Search for Claude code skills",
        "In",
        "In two",
        "In 2000",
        "In 2020",
        "In 2026",
        "In 2026 on",
        "In 2026 on YouTube",
    ]
    assert feed(partials) == "Search for Claude code skills In 2026 on YouTube"


def test_a_trailing_empty_transcription_does_not_erase_the_command() -> None:
    """The live failure: every word heard, then an empty restart, and '' returned."""
    assert feed(["Search for", "Search for cats", ""]) == "Search for cats"


def test_an_ordinary_revision_is_not_a_restart() -> None:
    assert feed(["Sofa", "Sofa for", "Search for", "Search for lofi music"]) == (
        "Search for lofi music"
    )


def test_words_growing_and_being_corrected_stay_one_segment() -> None:
    assert feed(["In two", "In 2000", "In 2020", "In 2026"]) == "In 2026"


def test_update_reports_whether_new_speech_arrived() -> None:
    s = SegmentStitcher()
    assert s.update("open") is True
    assert s.update("open") is False
    assert s.update("") is False
    assert s.update("chrome") is True
    assert s.text == "open chrome"


def test_nothing_heard_is_empty() -> None:
    assert feed([]) == ""
    assert feed(["", ""]) == ""
