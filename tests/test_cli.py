import json

from jev_use.cli import build_parser, main


def test_parser_exposes_every_subcommand() -> None:
    parser = build_parser()
    args = parser.parse_args(["run", "--goal", "x", "--input", "k=v", "--dry-run"])
    assert args.command == "run"
    assert args.goal == "x"
    assert args.input == ["k=v"]
    assert args.dry_run is True


def test_unsafe_is_an_alias_for_no_supervisor() -> None:
    parser = build_parser()
    assert parser.parse_args(["run", "--goal", "x", "--unsafe"]).no_supervisor is True
    assert parser.parse_args(["run", "--goal", "x", "--no-supervisor"]).no_supervisor is True
    assert parser.parse_args(["run", "--goal", "x"]).no_supervisor is False


def test_demo_runs_offline_and_reports_a_summary(capsys, tmp_path) -> None:
    code = main(["demo", "--trace", str(tmp_path / "demo.jsonl")])
    out = capsys.readouterr().out
    assert code == 0
    assert "steps" in out
    assert "cost" in out.lower()
    records = [json.loads(line) for line in (tmp_path / "demo.jsonl").read_text().splitlines()]
    assert records[-1]["kind"] == "summary"
    assert records[-1]["verdict"] == "done"


def test_demo_needs_no_api_key(monkeypatch, capsys) -> None:
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    assert main(["demo"]) == 0


def test_decide_offline_prints_ranked_candidates(capsys, fixtures_dir) -> None:
    tree = fixtures_dir / "trees" / "demo_screen_2.json"
    code = main(
        [
            "decide",
            "--tree",
            str(tree),
            "--goal",
            "Search for opening hours",
            "--offline",
            "--input",
            "query=opening hours",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "candidate" in out.lower()
    # A textfield only yields an action when there is an input to type into it.
    assert 'Type "opening hours" into the textfield labelled "Search"' in out


def test_decide_without_a_key_and_without_offline_fails_clearly(monkeypatch, capsys, fixtures_dir):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    tree = fixtures_dir / "trees" / "demo_screen_2.json"
    code = main(["decide", "--tree", str(tree), "--goal", "x"])
    assert code != 0
    assert "TYPESAFE_API_KEY" in capsys.readouterr().err


def test_replay_pretty_prints_a_trace(capsys, tmp_path) -> None:
    trace = tmp_path / "t.jsonl"
    trace.write_text(
        json.dumps({"kind": "step", "step": 1, "verdict": "act", "action": "Click Go"}) + "\n"
    )
    assert main(["replay", str(trace)]) == 0
    assert "step 1" in capsys.readouterr().out


def test_run_without_an_api_key_fails_clearly(monkeypatch, capsys) -> None:
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    code = main(["run", "--goal", "x", "--dry-run"])
    assert code != 0
    assert "TYPESAFE_API_KEY" in capsys.readouterr().err


def test_malformed_input_pair_is_rejected(capsys, fixtures_dir) -> None:
    tree = fixtures_dir / "trees" / "demo_screen_2.json"
    code = main(["decide", "--tree", str(tree), "--goal", "x", "--offline", "--input", "novalue"])
    assert code != 0
    assert "KEY=VALUE" in capsys.readouterr().err
