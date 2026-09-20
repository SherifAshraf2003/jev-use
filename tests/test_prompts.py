import pathlib

from jev_use import prompts
from jev_use.policy import BLOCKERS


def test_progress_has_five_ordered_levels() -> None:
    assert len(prompts.PROGRESS_LEVELS) == 5
    assert all(level and level[0].isupper() for level in prompts.PROGRESS_LEVELS)


def test_blocker_criteria_cover_the_policy_vocabulary() -> None:
    assert tuple(prompts.BLOCKER_CRITERIA) == BLOCKERS


def test_prompt_version_is_set() -> None:
    assert prompts.PROMPT_VERSION


def test_no_other_module_hardcodes_instructions() -> None:
    """SPEC.md section 9: model-facing strings live only in prompts.py."""
    src = pathlib.Path(__file__).parent.parent / "src/jev_use"
    offenders = [
        path.name
        for path in src.rglob("*.py")
        if path.name != "prompts.py" and "instructions=" in path.read_text()
    ]
    assert offenders == []


def test_every_prompt_is_non_empty() -> None:
    for name in dir(prompts):
        if name.isupper() and name != "PROMPT_VERSION":
            assert getattr(prompts, name), f"{name} is empty"
