"""Turn probabilities into a verdict. Pure functions, no I/O, every threshold here.

Two readings that would be wrong, and that the tests pin down:

A noul is the probability that the answer is yes. A noul of 0.5 means yes and no
are roughly equally likely — it is not "medium severity". So a safety noul at 0.5
sits well above every default threshold in this module and stops the agent.

Choice and Score confidence describe how concentrated the returned distribution
is. Concentration is not correctness and it is not permission to act, which is
why confidence is consulted only after every safety gate has passed. It also
falls as the option list grows, purely because the distribution is normalized
across whatever options were offered — see docs/DEVIATIONS.md D17.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

BLOCKERS = (
    "none",
    "loading",
    "login_required",
    "human_verification",
    "permission_dialog",
    "error",
    "paywall",
)


class Verdict(StrEnum):
    ACT = "act"
    RETRY = "retry"
    SCROLL = "scroll"
    DONE = "done"
    ASK_HUMAN = "ask_human"
    ABORT = "abort"


_BLOCKER_VERDICTS: dict[str, Verdict] = {
    "loading": Verdict.RETRY,
    "error": Verdict.RETRY,
    "login_required": Verdict.ASK_HUMAN,
    "human_verification": Verdict.ASK_HUMAN,
    "permission_dialog": Verdict.ASK_HUMAN,
    "paywall": Verdict.ASK_HUMAN,
}


@dataclass
class Thresholds:
    """Shipped values are guesses. See docs/CALIBRATION.md before trusting them.

    Every safety comparison is inclusive: a judgment exactly at its threshold
    stops the agent. The asymmetry is deliberate — a needless stop costs a
    prompt, a missed veto costs whatever the action did.
    """

    complete: float = 0.80
    min_confidence: float = 0.35
    injection: float = 0.40
    irreversible: float = 0.35
    rule_breach: float = 0.30
    blocker_confidence: float = 0.55
    stall_window: int = 4
    stall_delta: float = 0.3
    repeat_limit: int = 2


@dataclass
class Judgments:
    """Everything one step's requests returned, already unpacked from the SDK types."""

    has_action: bool
    confidence: float
    complete: float
    progress: float
    injection: float = 0.0
    rule_breach: float = 0.0
    irreversible: float = 0.0
    blocker: str = "none"
    blocker_confidence: float = 0.0
    selected_signature: str = "-"


@dataclass
class History:
    """What has happened so far in this run."""

    recent_signatures: list[str] = field(default_factory=list)
    progress_history: list[float] = field(default_factory=list)
    step: int = 0


def decide(judgments: Judgments, history: History, t: Thresholds) -> tuple[Verdict, str]:
    """Return the verdict and a human-readable reason.

    The order below is the policy, and each position is deliberate:

    1. injection — screen text addressing the agent taints every other judgment
       on this step, because they were all made from that same screen.
    2. rule breach — a user-supplied rule outranks the agent's own sense of
       progress; breaking it to finish faster is still breaking it.
    3. irreversibility — spending, sending, or deleting cannot be undone by a
       later step, so it is gated before any reversible consideration.
    4. blocker — a login wall or permission dialog means the screen cannot be
       acted on at all; acting anyway wastes a step or triggers something worse.
    5. complete — only once the screen is trustworthy and actionable is
       finishing a meaningful claim.
    6. confidence — a diffuse distribution means the model could not tell the
       candidates apart, so look at more of the screen instead of guessing.
    7. repeat — the same action twice has not worked; ban it and re-enumerate.
    8. stall — flat progress across the window means the run is going nowhere.
    9. act.
    """
    if judgments.injection >= t.injection:
        return Verdict.ASK_HUMAN, (
            f"injection risk {judgments.injection:.2f} >= {t.injection:.2f}: "
            "the screen appears to address the agent directly"
        )
    if judgments.rule_breach >= t.rule_breach:
        return Verdict.ASK_HUMAN, (
            f"rule breach risk {judgments.rule_breach:.2f} >= {t.rule_breach:.2f}"
        )
    if judgments.irreversible >= t.irreversible:
        return Verdict.ASK_HUMAN, (
            f"irreversible risk {judgments.irreversible:.2f} >= {t.irreversible:.2f}: "
            "the selected action may spend, send, delete, or change permissions"
        )
    if judgments.blocker != "none" and judgments.blocker_confidence >= t.blocker_confidence:
        mapped = _BLOCKER_VERDICTS.get(judgments.blocker)
        if mapped is not None:
            return mapped, f"blocker: {judgments.blocker}"
    if judgments.complete >= t.complete:
        return Verdict.DONE, f"completion {judgments.complete:.2f} >= {t.complete:.2f}"
    if not judgments.has_action:
        return Verdict.SCROLL, "no legal action was available on this screen"
    if judgments.confidence < t.min_confidence:
        return Verdict.SCROLL, (
            f"confidence {judgments.confidence:.2f} < {t.min_confidence:.2f}: "
            "the candidates could not be told apart"
        )
    repeats = history.recent_signatures.count(judgments.selected_signature)
    if repeats >= t.repeat_limit:
        return Verdict.RETRY, (
            f"action {judgments.selected_signature} already tried {repeats} times; banning it"
        )
    window = history.progress_history[-t.stall_window :]
    if len(window) >= t.stall_window and (max(window) - min(window)) < t.stall_delta:
        return Verdict.ABORT, (
            f"progress moved less than {t.stall_delta} across {t.stall_window} steps"
        )
    return Verdict.ACT, "clear"
