import pathlib
import re

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
    """SPEC.md section 9: model-facing strings live only in prompts.py.

    Referencing a prompt constant is the point, so what is banned is a string
    *literal* passed as instructions or criteria, not the keyword itself.
    """
    src = pathlib.Path(__file__).parent.parent / "src/jev_use"
    literal = re.compile(r"""(instructions|criteria)\s*=\s*["']""")
    offenders = [
        path.name
        for path in src.rglob("*.py")
        if path.name != "prompts.py" and literal.search(path.read_text())
    ]
    assert offenders == []


def test_the_literal_check_would_catch_a_real_offender(tmp_path) -> None:
    """Guard the guard: a test that cannot fail is not a test."""
    literal = re.compile(r"""(instructions|criteria)\s*=\s*["']""")
    assert literal.search('Noul(instructions="Is this urgent?")')
    assert literal.search("Choice(criteria='x')")
    assert not literal.search("Noul(instructions=prompts.TASK_COMPLETE_INSTRUCTIONS)")


def test_every_prompt_is_non_empty() -> None:
    for name in dir(prompts):
        if name.isupper() and name != "PROMPT_VERSION":
            assert getattr(prompts, name), f"{name} is empty"
