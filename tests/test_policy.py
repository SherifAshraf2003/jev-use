import pytest

from jev_use.policy import BLOCKERS, History, Judgments, Thresholds, Verdict, decide

T = Thresholds()


def judgments(**overrides) -> Judgments:
    base = dict(
        has_action=True,
        confidence=0.9,
        complete=0.02,
        progress=2.0,
        injection=0.01,
        rule_breach=0.01,
        irreversible=0.01,
        blocker="none",
        blocker_confidence=0.9,
        selected_signature="click:e0:-",
    )
    return Judgments(**{**base, **overrides})


def history(**overrides) -> History:
    base = dict(recent_signatures=[], progress_history=[], step=1)
    return History(**{**base, **overrides})


def test_clean_state_acts() -> None:
    assert decide(judgments(), history(), T)[0] is Verdict.ACT


@pytest.mark.parametrize(
    "field,threshold",
    [
        ("injection", T.injection),
        ("rule_breach", T.rule_breach),
        ("irreversible", T.irreversible),
    ],
)
def test_safety_judgments_stop_the_agent(field, threshold) -> None:
    verdict, reason = decide(judgments(**{field: threshold + 0.01}), history(), T)
    assert verdict is Verdict.ASK_HUMAN
    assert field.split("_")[0] in reason


@pytest.mark.parametrize(
    "field,threshold",
    [
        ("injection", T.injection),
        ("rule_breach", T.rule_breach),
        ("irreversible", T.irreversible),
    ],
)
def test_thresholds_are_inclusive(field, threshold) -> None:
    """Exactly at the threshold must stop, not act."""
    assert decide(judgments(**{field: threshold}), history(), T)[0] is Verdict.ASK_HUMAN


def test_injection_outranks_completion() -> None:
    verdict, _ = decide(judgments(injection=0.99, complete=0.99), history(), T)
    assert verdict is Verdict.ASK_HUMAN


def test_injection_outranks_every_other_safety_judgment() -> None:
    verdict, reason = decide(
        judgments(injection=0.99, rule_breach=0.99, irreversible=0.99), history(), T
    )
    assert verdict is Verdict.ASK_HUMAN
    assert "injection" in reason


def test_loading_blocker_retries_but_login_asks_human() -> None:
    assert decide(judgments(blocker="loading"), history(), T)[0] is Verdict.RETRY
    assert decide(judgments(blocker="login_required"), history(), T)[0] is Verdict.ASK_HUMAN


@pytest.mark.parametrize(
    "blocker,expected",
    [
        ("none", Verdict.ACT),
        ("loading", Verdict.RETRY),
        ("error", Verdict.RETRY),
        ("login_required", Verdict.ASK_HUMAN),
        ("human_verification", Verdict.ASK_HUMAN),
        ("permission_dialog", Verdict.ASK_HUMAN),
        ("paywall", Verdict.ASK_HUMAN),
    ],
)
def test_every_blocker_maps_to_a_verdict(blocker, expected) -> None:
    assert decide(judgments(blocker=blocker), history(), T)[0] is expected


def test_blocker_vocabulary_is_complete() -> None:
    for blocker in BLOCKERS:
        decide(judgments(blocker=blocker), history(), T)


def test_low_confidence_blocker_is_ignored() -> None:
    low = judgments(blocker="login_required", blocker_confidence=T.blocker_confidence - 0.01)
    assert decide(low, history(), T)[0] is Verdict.ACT


def test_completion_is_reported_before_confidence() -> None:
    assert decide(judgments(complete=0.95, confidence=0.01), history(), T)[0] is Verdict.DONE


def test_no_action_or_low_confidence_scrolls() -> None:
    assert decide(judgments(has_action=False), history(), T)[0] is Verdict.SCROLL
    assert decide(judgments(confidence=0.1), history(), T)[0] is Verdict.SCROLL


def test_repeating_the_same_action_triggers_a_ban() -> None:
    repeated = history(recent_signatures=["click:e0:-"] * T.repeat_limit)
    assert decide(judgments(), repeated, T)[0] is Verdict.RETRY


def test_a_different_action_is_not_a_repeat() -> None:
    other = history(recent_signatures=["click:e9:-"] * 5)
    assert decide(judgments(), other, T)[0] is Verdict.ACT


def test_flat_progress_over_the_window_aborts() -> None:
    stalled = history(progress_history=[2.0, 2.05, 2.1, 2.05])
    assert decide(judgments(), stalled, T)[0] is Verdict.ABORT


def test_rising_progress_does_not_abort() -> None:
    rising = history(progress_history=[0.0, 1.0, 2.0, 3.0])
    assert decide(judgments(), rising, T)[0] is Verdict.ACT


def test_short_history_never_aborts_for_stalling() -> None:
    assert decide(judgments(), history(progress_history=[2.0, 2.0, 2.0]), T)[0] is Verdict.ACT


def test_noul_at_half_is_not_a_middling_severity() -> None:
    """A noul of 0.5 means yes and no are equally likely, so it must trip the veto.

    Reading it as 'medium severity, probably fine' is the mistake this guards against.
    """
    assert decide(judgments(irreversible=0.5), history(), T)[0] is Verdict.ASK_HUMAN
    assert decide(judgments(injection=0.5), history(), T)[0] is Verdict.ASK_HUMAN
    assert decide(judgments(rule_breach=0.5), history(), T)[0] is Verdict.ASK_HUMAN


def test_choice_confidence_is_not_permission_to_act() -> None:
    """High confidence must not override a safety veto: it measures distribution
    concentration, not correctness."""
    verdict, _ = decide(judgments(confidence=0.999, rule_breach=0.9), history(), T)
    assert verdict is Verdict.ASK_HUMAN


def test_every_verdict_carries_a_reason() -> None:
    cases = [
        judgments(),
        judgments(injection=0.9),
        judgments(complete=0.99),
        judgments(has_action=False),
        judgments(confidence=0.0),
        judgments(blocker="loading"),
    ]
    for case in cases:
        verdict, reason = decide(case, history(), T)
        assert reason, f"{verdict} came back with no reason"


def test_thresholds_are_overridable() -> None:
    lax = Thresholds(irreversible=0.99)
    assert decide(judgments(irreversible=0.5), history(), lax)[0] is Verdict.ACT
