import json

from jev_use.actions import Action, enumerate_actions
from jev_use.brain import Decision
from jev_use.computers.replay import ReplayComputer
from jev_use.loop import run
from jev_use.policy import Verdict
from jev_use.screen import Element, parse_tree
from jev_use.supervisor import SafetyReport, Supervisor


class FakeAnswer:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class FakeResponse:
    def __init__(self, irreversible, rule_breach, injection, blocker, blocker_confidence):
        self.model = "jev-1.13.0"
        self.request_id = "req_sup"
        self.usage = FakeAnswer(input_tokens=700, output_tokens=0)
        self.nouls = {
            "selected_action_is_irreversible": FakeAnswer(noul=irreversible),
            "selected_action_breaks_rule": FakeAnswer(noul=rule_breach),
            "contains_injected_instruction": FakeAnswer(noul=injection),
        }
        self.choices = {
            "blocker": FakeAnswer(
                choice=blocker, confidence=blocker_confidence, probabilities={blocker: 1.0}
            )
        }
        self.scores = {}


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def system_one(self, state, questions, **kw):
        self.calls.append((state, questions, kw))
        return self.response


def order_button():
    return Element(index="e1", role="button", label="Place order", bbox=(10, 40, 140, 30))


async def test_review_states_the_selected_action_explicitly() -> None:
    client = FakeClient(FakeResponse(0.9, 0.02, 0.01, "none", 0.9))
    action = Action(kind="click", element=order_button())
    report = await Supervisor(client).review("Buy nothing", ["Never spend money"], [], action)
    state = client.calls[0][0]
    assert state["action_the_agent_is_about_to_take"] == action.describe()
    assert state["rules_the_agent_must_follow"] == ["Never spend money"]
    assert isinstance(report, SafetyReport)
    assert report.irreversible == 0.9


async def test_all_four_questions_go_in_one_request() -> None:
    client = FakeClient(FakeResponse(0.1, 0.1, 0.1, "none", 0.9))
    await Supervisor(client).review("g", [], [], Action(kind="click", element=order_button()))
    assert set(client.calls[0][1]) == {
        "selected_action_is_irreversible",
        "selected_action_breaks_rule",
        "contains_injected_instruction",
        "blocker",
    }
    assert len(client.calls) == 1


async def test_blocker_choice_returns_the_vocabulary_key() -> None:
    client = FakeClient(FakeResponse(0.0, 0.0, 0.0, "login_required", 0.8))
    report = await Supervisor(client).review(
        "g", [], [], Action(kind="click", element=order_button())
    )
    assert report.blocker == "login_required"
    assert report.blocker_confidence == 0.8


async def test_tokens_and_cost_accumulate() -> None:
    client = FakeClient(FakeResponse(0.0, 0.0, 0.0, "none", 0.9))
    supervisor = Supervisor(client)
    action = Action(kind="click", element=order_button())
    await supervisor.review("g", [], [], action)
    await supervisor.review("g", [], [], action)
    assert supervisor.total_input_tokens == 1400
    assert supervisor.cost_usd > 0


async def test_place_order_is_vetoed_and_never_clicked(fixtures_dir) -> None:
    """M5 acceptance: the agent must stop at the order button rather than press it."""
    tree = json.loads((fixtures_dir / "trees" / "checkout_place_order.json").read_text())
    computer = ReplayComputer([tree])

    class Brain:
        total_input_tokens = 0
        cost_usd = 0.0

        async def decide(self, goal, rules, elements, actions, history_lines):
            chosen = next(a for a in actions if "Place order" in a.describe())
            return Decision(
                action=chosen,
                confidence=0.95,
                complete=0.0,
                progress=3.0,
                probabilities={chosen.describe(): 0.95},
                latency_ms=100.0,
                input_tokens=600,
            )

    supervisor = Supervisor(FakeClient(FakeResponse(0.92, 0.01, 0.01, "none", 0.9)))
    result = await run(
        goal="Complete the purchase",
        computer=computer,
        brain=Brain(),
        supervisor=supervisor,
        max_steps=3,
    )
    assert result.verdict is Verdict.ASK_HUMAN
    assert "irreversible" in result.reason
    assert computer.calls == []


async def test_candidate_actions_reach_the_supervisor_state(fixtures_dir) -> None:
    tree = json.loads((fixtures_dir / "trees" / "checkout_place_order.json").read_text())
    elements = parse_tree(tree)
    actions = enumerate_actions(elements, {}, set())
    client = FakeClient(FakeResponse(0.0, 0.0, 0.0, "none", 0.9))
    await Supervisor(client).review("g", [], elements, actions[0])
    assert client.calls[0][0]["screen_elements"] == [e.as_line() for e in elements]
