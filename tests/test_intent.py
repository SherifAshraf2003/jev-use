from jev_use import prompts
from jev_use.intent import Intent, goal_spans, parse_intent


class FakeAnswer:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class FakeClient:
    def __init__(self, choices, tokens=700):
        self.choices = choices
        self.tokens = tokens
        self.calls = []

    async def system_one(self, state, questions, **kw):
        self.calls.append((state, questions, kw))
        return FakeAnswer(
            usage=FakeAnswer(input_tokens=self.tokens),
            choices={
                q: FakeAnswer(choice=c, confidence=conf, probabilities={c: conf})
                for q, (c, conf) in self.choices.items()
                if q in questions
            },
        )


def test_spans_cover_every_run_of_words() -> None:
    spans = goal_spans("open chrome and go to youtube")
    assert "youtube" in spans
    assert "open chrome" in spans
    assert "go to youtube" in spans
    assert len(spans) == len(set(spans))


def test_spans_strip_trailing_punctuation_but_keep_domains() -> None:
    spans = goal_spans("Go to youtube.com, then search for cats!")
    assert "youtube.com" in spans
    assert "cats" in spans
    assert "cats!" not in spans
    assert "youtube.com," not in spans


def test_spans_respect_the_word_limit() -> None:
    spans = goal_spans("a b c d e f g h", max_words=3)
    assert all(len(s.split()) <= 3 for s in spans)


def test_spans_never_exceed_the_choice_ceiling() -> None:
    long = " ".join(f"w{i}" for i in range(200))
    assert len(goal_spans(long)) < 255


async def test_one_request_answers_app_and_inputs() -> None:
    client = FakeClient(
        {
            "app": ("Google Chrome", 0.97),
            "address_bar": ("youtube", 0.98),
            "search_box": (prompts.NO_TEXT_OPTION, 0.9),
            "text_field": (prompts.NO_TEXT_OPTION, 0.9),
        }
    )
    intent = await parse_intent(client, "open chrome and go to youtube", ["Google Chrome", "Notes"])
    assert isinstance(intent, Intent)
    assert intent.app == "Google Chrome"
    assert intent.app_confidence == 0.97
    assert intent.inputs == {"address_bar": "youtube"}
    assert len(client.calls) == 1


async def test_the_same_text_picked_twice_becomes_one_input() -> None:
    """Measured: the search-box question also picked 'youtube' for a URL-only goal."""
    client = FakeClient(
        {
            "app": ("Google Chrome", 0.97),
            "address_bar": ("youtube", 0.98),
            "search_box": ("youtube", 0.92),
            "text_field": (prompts.NO_TEXT_OPTION, 0.9),
        }
    )
    intent = await parse_intent(client, "go to youtube", ["Google Chrome"])
    assert list(intent.inputs.values()) == ["youtube"]


async def test_no_app_list_skips_the_app_question() -> None:
    client = FakeClient({"address_bar": ("youtube", 0.98)})
    intent = await parse_intent(client, "go to youtube", None)
    assert intent.app is None
    assert "app" not in client.calls[0][1]


async def test_app_options_are_capped() -> None:
    client = FakeClient({"app": ("App 1", 0.9)})
    await parse_intent(client, "open app 1", [f"App {i}" for i in range(400)])
    assert len(client.calls[0][1]["app"].criteria) <= 255
