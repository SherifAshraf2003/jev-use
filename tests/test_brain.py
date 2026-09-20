import pytest

from jev_use.actions import enumerate_actions
from jev_use.brain import USD_PER_MTOK_INPUT, Decision, JevBrain
from jev_use.screen import Element


class FakeAnswer:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class FakeResponse:
    def __init__(self, choice, probabilities, confidence, noul, score, tokens=1000):
        self.model = "jev-1.13.0"
        self.request_id = "req_test"
        self.usage = FakeAnswer(input_tokens=tokens, output_tokens=0)
        self.choices = {
            "next_action": FakeAnswer(
                choice=choice, probabilities=probabilities, confidence=confidence
            )
        }
        self.nouls = {"task_complete": FakeAnswer(noul=noul)}
        self.scores = {"progress": FakeAnswer(score=score, confidence=0.7, probabilities={})}


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def system_one(self, state, questions, **kw):
        self.calls.append((state, questions, kw))
        return self.response


def elements():
    return [
        Element(index="e0", role="button", label="Continue", bbox=(0, 0, 10, 10)),
        Element(index="e1", role="button", label="Cancel", bbox=(20, 0, 10, 10)),
    ]


def test_state_uses_named_fields_not_one_blob() -> None:
    brain = JevBrain(FakeClient(None))
    state = brain.build_state("Buy milk", ["Never pay"], elements(), ["clicked Continue"])
    assert set(state) == {
        "task_goal",
        "rules_the_agent_must_follow",
        "screen_elements",
        "actions_already_taken",
    }
    assert state["screen_elements"] == ['[e0] button "Continue"', '[e1] button "Cancel"']


def test_questions_are_keyed_by_description() -> None:
    brain = JevBrain(FakeClient(None))
    actions = enumerate_actions(elements(), {}, set())
    questions = brain.build_questions(actions)
    assert set(questions) == {"next_action", "task_complete", "progress"}
    assert 'Click the button labelled "Continue"' in questions["next_action"].criteria


def test_questions_never_exceed_the_api_ceiling() -> None:
    brain = JevBrain(FakeClient(None))
    many = [
        Element(index=f"e{i}", role="button", label=f"b{i}", bbox=(0, 0, 4, 4))
        for i in range(1000)
    ]
    questions = brain.build_questions(enumerate_actions(many, {}, set()))
    assert len(questions["next_action"].criteria) <= 255


async def test_decide_maps_the_chosen_description_back_to_its_action() -> None:
    actions = enumerate_actions(elements(), {}, set())
    chosen = 'Click the button labelled "Cancel"'
    client = FakeClient(
        FakeResponse(
            chosen, {chosen: 0.8, "Wait for the screen to finish changing": 0.2}, 0.8, 0.05, 2.0
        )
    )
    decision = await JevBrain(client).decide("Goal", [], elements(), actions, [])
    assert isinstance(decision, Decision)
    assert decision.action is not None
    assert decision.action.element is not None
    assert decision.action.element.label == "Cancel"
    assert decision.confidence == 0.8
    assert decision.complete == 0.05
    assert decision.progress == 2.0
    assert decision.latency_ms >= 0
    assert decision.model == "jev-1.13.0"


async def test_colliding_labels_resolve_to_the_right_element() -> None:
    """D19: two elements sharing a label must stay distinguishable end to end."""
    same = [
        Element(index="e0", role="button", label="Close", bbox=(0, 0, 10, 10)),
        Element(index="e1", role="button", label="Close", bbox=(20, 0, 10, 10)),
    ]
    actions = enumerate_actions(same, {}, set())
    second = next(a for a in actions if a.ordinal == 2)
    client = FakeClient(FakeResponse(second.describe(), {second.describe(): 1.0}, 0.9, 0.0, 1.0))
    decision = await JevBrain(client).decide("Goal", [], same, actions, [])
    assert decision.action is not None
    assert decision.action.element is not None
    assert decision.action.element.index == "e1"


async def test_unrecognized_choice_yields_no_action_rather_than_guessing() -> None:
    actions = enumerate_actions(elements(), {}, set())
    client = FakeClient(FakeResponse('Click the button labelled "Nonexistent"', {}, 0.9, 0.0, 1.0))
    decision = await JevBrain(client).decide("Goal", [], elements(), actions, [])
    assert decision.action is None


async def test_tokens_and_cost_accumulate() -> None:
    actions = enumerate_actions(elements(), {}, set())
    chosen = 'Click the button labelled "Continue"'
    brain = JevBrain(FakeClient(FakeResponse(chosen, {chosen: 1.0}, 0.9, 0.0, 1.0)))
    await brain.decide("Goal", [], elements(), actions, [])
    await brain.decide("Goal", [], elements(), actions, [])
    assert brain.total_input_tokens == 2000
    assert brain.cost_usd == pytest.approx(2000 / 1_000_000 * USD_PER_MTOK_INPUT)


async def test_empty_candidate_list_skips_the_request_entirely() -> None:
    client = FakeClient(None)
    decision = await JevBrain(client).decide("Goal", [], [], [], [])
    assert decision.action is None
    assert client.calls == []


async def test_model_is_passed_through() -> None:
    actions = enumerate_actions(elements(), {}, set())
    chosen = 'Click the button labelled "Continue"'
    client = FakeClient(FakeResponse(chosen, {chosen: 1.0}, 0.9, 0.0, 1.0))
    await JevBrain(client, model="jev-1.13.0").decide("G", [], elements(), actions, [])
    assert client.calls[0][2]["model"] == "jev-1.13.0"


async def test_missing_usage_does_not_crash() -> None:
    actions = enumerate_actions(elements(), {}, set())
    chosen = 'Click the button labelled "Continue"'
    response = FakeResponse(chosen, {chosen: 1.0}, 0.9, 0.0, 1.0)
    response.usage = FakeAnswer(input_tokens=None, output_tokens=None)
    decision = await JevBrain(FakeClient(response)).decide("G", [], elements(), actions, [])
    assert decision.input_tokens == 0
