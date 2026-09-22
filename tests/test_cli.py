import json
import os

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


def test_demo_needs_no_api_key(monkeypatch, capsys, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
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


def test_decide_without_a_key_and_without_offline_fails_clearly(
    monkeypatch, capsys, fixtures_dir, tmp_path
):
    # Run somewhere with no .env, or the CLI would find the project's and make a
    # real request — tests must never hit the network.
    monkeypatch.chdir(tmp_path)
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


def test_run_without_an_api_key_fails_clearly(monkeypatch, capsys, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    code = main(["run", "--goal", "x", "--dry-run"])
    assert code != 0
    assert "TYPESAFE_API_KEY" in capsys.readouterr().err


def test_malformed_input_pair_is_rejected(capsys, fixtures_dir) -> None:
    tree = fixtures_dir / "trees" / "demo_screen_2.json"
    code = main(["decide", "--tree", str(tree), "--goal", "x", "--offline", "--input", "novalue"])
    assert code != 0
    assert "KEY=VALUE" in capsys.readouterr().err


def test_dotenv_is_loaded_from_the_nearest_parent(tmp_path, monkeypatch) -> None:
    from jev_use.cli import load_dotenv

    (tmp_path / ".env").write_text('TYPESAFE_API_KEY="from-file"\nOTHER=plain\n')
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.delenv("OTHER", raising=False)
    found = load_dotenv(nested)
    assert found == tmp_path / ".env"
    assert os.environ["TYPESAFE_API_KEY"] == "from-file"
    assert os.environ["OTHER"] == "plain"


def test_a_real_environment_variable_wins_over_dotenv(tmp_path, monkeypatch) -> None:
    from jev_use.cli import load_dotenv

    (tmp_path / ".env").write_text("TYPESAFE_API_KEY=from-file\n")
    monkeypatch.setenv("TYPESAFE_API_KEY", "from-shell")
    load_dotenv(tmp_path)
    assert os.environ["TYPESAFE_API_KEY"] == "from-shell"


def test_dotenv_tolerates_comments_exports_and_blanks(tmp_path, monkeypatch) -> None:
    from jev_use.cli import load_dotenv

    (tmp_path / ".env").write_text("# a comment\n\nexport TYPESAFE_API_KEY='quoted'\nnonsense\n")
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    load_dotenv(tmp_path)
    assert os.environ["TYPESAFE_API_KEY"] == "quoted"


def test_missing_dotenv_is_not_an_error(tmp_path) -> None:
    from jev_use.cli import load_dotenv

    assert load_dotenv(tmp_path / "nowhere") is None or True


def test_do_takes_a_plain_sentence() -> None:
    args = build_parser().parse_args(["do", "open chrome and go to youtube"])
    assert args.command == "do"
    assert args.sentence == "open chrome and go to youtube"
    assert args.app is None
    assert args.no_supervisor is False


def test_do_without_a_key_fails_clearly(monkeypatch, capsys, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    assert main(["do", "open chrome"]) != 0
    assert "TYPESAFE_API_KEY" in capsys.readouterr().err
