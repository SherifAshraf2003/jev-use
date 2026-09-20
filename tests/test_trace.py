import json

from jev_use.trace import TraceWriter, format_trace, read_trace, top_n


def test_top_n_returns_the_highest_probabilities_in_order() -> None:
    probabilities = {"a": 0.1, "b": 0.5, "c": 0.3, "d": 0.05, "e": 0.03, "f": 0.02}
    assert top_n(probabilities, 3) == [("b", 0.5), ("c", 0.3), ("a", 0.1)]


def test_top_n_handles_fewer_than_n() -> None:
    assert top_n({"a": 1.0}, 5) == [("a", 1.0)]
    assert top_n({}, 5) == []


def test_writer_emits_one_json_object_per_line(tmp_path) -> None:
    path = tmp_path / "run.jsonl"
    with TraceWriter(path) as writer:
        writer.write_step(step=1, verdict="act", action="click e0", runners_up=[["click e1", 0.2]])
        writer.write_step(step=2, verdict="done", action=None, runners_up=[])
        writer.write_summary(steps=2, cost_usd=0.0001)
    lines = path.read_text().strip().split("\n")
    assert len(lines) == 3
    assert json.loads(lines[0])["kind"] == "step"
    assert json.loads(lines[2])["kind"] == "summary"


def test_writer_creates_missing_directories(tmp_path) -> None:
    path = tmp_path / "deep" / "nested" / "run.jsonl"
    with TraceWriter(path) as writer:
        writer.write_step(step=1)
    assert path.exists()


def test_each_record_is_flushed_immediately(tmp_path) -> None:
    """A crash mid-action must still leave the decision that led to it on disk."""
    path = tmp_path / "run.jsonl"
    writer = TraceWriter(path)
    writer.write_step(step=1, verdict="act")
    assert path.read_text().strip()
    writer.close()


def test_read_trace_round_trips(tmp_path) -> None:
    path = tmp_path / "run.jsonl"
    with TraceWriter(path) as writer:
        writer.write_step(step=1, verdict="act")
    records = read_trace(path)
    assert records[0]["step"] == 1
    assert "ts" in records[0]


def test_non_serializable_values_do_not_crash_the_run(tmp_path) -> None:
    path = tmp_path / "run.jsonl"
    with TraceWriter(path) as writer:
        writer.write_step(step=1, verdict=object())
    assert read_trace(path)[0]["step"] == 1


def test_format_trace_mentions_every_step(tmp_path) -> None:
    path = tmp_path / "run.jsonl"
    with TraceWriter(path) as writer:
        writer.write_step(step=1, verdict="act", action='Click the button labelled "Go"')
        writer.write_summary(steps=1, cost_usd=0.0)
    rendered = format_trace(read_trace(path))
    assert "step 1" in rendered
    assert "Go" in rendered
    assert "summary" in rendered.lower()


def test_format_trace_shows_runners_up_and_reason(tmp_path) -> None:
    path = tmp_path / "run.jsonl"
    with TraceWriter(path) as writer:
        writer.write_step(
            step=1, verdict="act", action="Click Go", reason="clear", runners_up=[["Click No", 0.2]]
        )
    rendered = format_trace(read_trace(path))
    assert "clear" in rendered
    assert "Click No" in rendered
    assert "0.200" in rendered


def test_reading_a_trace_with_blank_lines(tmp_path) -> None:
    path = tmp_path / "run.jsonl"
    path.write_text('{"kind":"step","step":1}\n\n{"kind":"step","step":2}\n')
    assert [r["step"] for r in read_trace(path)] == [1, 2]
