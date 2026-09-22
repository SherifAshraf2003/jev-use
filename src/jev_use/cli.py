"""Command line entry point. This is the only module allowed to print."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from jev_use import __version__
from jev_use.actions import enumerate_actions
from jev_use.brain import USD_PER_MTOK_INPUT, Decision, JevBrain
from jev_use.computers.replay import ReplayComputer
from jev_use.loop import run as run_loop
from jev_use.policy import Thresholds
from jev_use.screen import parse_tree
from jev_use.trace import format_trace, read_trace, top_n

logger = logging.getLogger(__name__)

FIXTURES = Path(__file__).resolve().parent.parent.parent / "tests/fixtures"
DEMO_SCREENS = [FIXTURES / "trees" / f"demo_screen_{i}.json" for i in range(1, 5)]
DEMO_SCRIPT: list[tuple[str | None, float, float, float]] = [
    ("Open Browser", 0.01, 0.0, 0.88),
    ("Search", 0.02, 1.0, 0.81),
    ("Opening hours", 0.04, 2.0, 0.77),
    (None, 0.96, 4.0, 0.90),
]


DEFAULT_DO_TRACE = Path("traces/last.jsonl")


class OfflineBrain:
    """Replays a fixed script. No network, no key, deterministic output."""

    def __init__(self, script: list[tuple[str | None, float, float, float]]) -> None:
        self._script = list(script)
        self.total_input_tokens = 0
        self.cost_usd = 0.0

    async def decide(
        self,
        goal: str,
        rules: list[str],
        elements: Any,
        actions: Any,
        history_lines: list[str],
    ) -> Decision:
        self.total_input_tokens += 480
        self.cost_usd = self.total_input_tokens / 1_000_000 * 0.042
        picker, complete, progress, confidence = (
            self._script.pop(0) if self._script else (None, 0.99, 4.0, 0.9)
        )
        chosen = None
        if picker is not None:
            chosen = next((a for a in actions if picker in a.describe()), None)
        spread = {a.describe(): round(1.0 / max(len(actions), 1), 4) for a in actions}
        if chosen is not None:
            spread[chosen.describe()] = confidence
        return Decision(
            action=chosen,
            confidence=confidence,
            complete=complete,
            progress=progress,
            probabilities=spread,
            latency_ms=180.0,
            input_tokens=480,
            model="offline",
        )


def load_dotenv(start: Path | None = None) -> Path | None:
    """Read KEY=VALUE lines from the nearest .env, walking up from `start`.

    Only fills variables that are not already set, so a real environment
    variable always wins. Written by hand rather than taking a dependency,
    since SPEC.md section 9 caps the runtime dependencies.
    """
    here = (start or Path.cwd()).resolve()
    for directory in [here, *here.parents]:
        candidate = directory / ".env"
        if not candidate.is_file():
            continue
        for raw in candidate.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip().removeprefix("export ").strip()
            value = value.strip().strip("\"'")
            if key and key not in os.environ:
                os.environ[key] = value
        return candidate
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jev-use", description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    demo = sub.add_parser("demo", help="run a scripted task offline, with no API key")
    demo.add_argument("--trace", type=Path, default=None)

    decide = sub.add_parser("decide", help="one decision against a saved tree")
    decide.add_argument("--tree", type=Path, required=True)
    decide.add_argument("--goal", required=True)
    decide.add_argument("--input", action="append", default=[], metavar="KEY=VALUE")
    decide.add_argument("--offline", action="store_true", help="use the scripted brain")

    run = sub.add_parser("run", help="the full loop on the local Mac")
    run.add_argument("--goal", required=True)
    run.add_argument("--app", default="Chrome", help="the running application to drive")
    run.add_argument("--input", action="append", default=[], metavar="KEY=VALUE")
    run.add_argument("--rule", action="append", default=[])
    run.add_argument("--max-steps", type=int, default=25)
    run.add_argument(
        "--unsafe",
        "--no-supervisor",
        dest="no_supervisor",
        action="store_true",
        help="disable the safety supervisor (not recommended)",
    )
    run.add_argument("--trace", type=Path, default=None)
    run.add_argument("--dry-run", action="store_true")

    do = sub.add_parser("do", help="say what you want in plain words")
    do.add_argument("sentence", help='e.g. "open chrome and go to youtube"')
    do.add_argument("--app", default=None, help="skip app detection and use this app")
    do.add_argument("--input", action="append", default=[], metavar="KEY=VALUE")
    do.add_argument("--rule", action="append", default=[], help="replaces the default rules")
    do.add_argument("--max-steps", type=int, default=12)
    do.add_argument(
        "--unsafe",
        "--no-supervisor",
        dest="no_supervisor",
        action="store_true",
        help="disable the safety supervisor (not recommended)",
    )
    do.add_argument("--trace", type=Path, default=DEFAULT_DO_TRACE)
    do.add_argument("--dry-run", action="store_true")

    listen = sub.add_parser("listen", help="speak commands; each one runs like `do`")
    listen.add_argument("--once", action="store_true", help="handle one command, then exit")
    listen.add_argument("--locale", default="en-US")
    listen.add_argument("--app", default=None)
    listen.add_argument("--input", action="append", default=[], metavar="KEY=VALUE")
    listen.add_argument("--rule", action="append", default=[])
    listen.add_argument("--max-steps", type=int, default=12)
    listen.add_argument("--unsafe", "--no-supervisor", dest="no_supervisor", action="store_true")
    listen.add_argument("--trace", type=Path, default=DEFAULT_DO_TRACE)
    listen.add_argument("--dry-run", action="store_true")

    replay = sub.add_parser("replay", help="pretty-print a saved trajectory")
    replay.add_argument("trace", type=Path)
    return parser


def _parse_inputs(pairs: list[str]) -> dict[str, str]:
    inputs: dict[str, str] = {}
    for pair in pairs:
        key, _, value = pair.partition("=")
        if not key or not value:
            raise ValueError(f"--input expects KEY=VALUE, got {pair!r}")
        inputs[key] = value
    return inputs


def _print_summary(result: Any) -> None:
    print()
    print(f"verdict: {result.verdict} ({result.reason})")
    print(f"steps: {result.steps}   model calls: {result.calls}")
    print(f"input tokens: {result.input_tokens}   cost: ${result.cost_usd:.6f}")
    print(f"mean latency: {result.mean_latency_ms:.0f}ms")
    if result.trace_path is not None:
        print(f"trace: {result.trace_path}")


def _cmd_demo(args: argparse.Namespace) -> int:
    trees = [json.loads(path.read_text()) for path in DEMO_SCREENS]
    result = asyncio.run(
        run_loop(
            goal="Find the library opening hours",
            computer=ReplayComputer(trees),
            brain=OfflineBrain(DEMO_SCRIPT),
            inputs={"query": "city library opening hours"},
            trace_path=args.trace,
        )
    )
    _print_summary(result)
    return 0


def _cmd_decide(args: argparse.Namespace) -> int:
    try:
        inputs = _parse_inputs(args.input)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    elements = parse_tree(json.loads(args.tree.read_text()))
    actions = enumerate_actions(elements, inputs, set())

    brain: Any
    if args.offline:
        brain = OfflineBrain([(None, 0.1, 1.0, 0.7)])
    else:
        key = os.environ.get("TYPESAFE_API_KEY")
        if not key:
            print("TYPESAFE_API_KEY is not set; use --offline for a dry decision", file=sys.stderr)
            return 2
        from typesafe_sdk import AsyncTypeSafeClient

        brain = JevBrain(AsyncTypeSafeClient(api_key=key))

    decision = asyncio.run(brain.decide(args.goal, [], elements, actions, []))
    print(f"{len(elements)} elements, {len(actions)} candidate actions")
    for option, probability in top_n(decision.probabilities, 10):
        marker = "->" if decision.action and option == decision.action.describe() else "  "
        print(f"{marker} {probability:6.3f}  {option}")
    print()
    print(
        f"confidence {decision.confidence:.3f}   complete {decision.complete:.3f}   "
        f"progress {decision.progress:.2f}"
    )
    print(f"{decision.input_tokens} input tokens   {decision.latency_ms:.0f}ms")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    key = os.environ.get("TYPESAFE_API_KEY")
    if not key:
        print(
            "TYPESAFE_API_KEY is not set.\n"
            "Put it in a .env file in the project root as TYPESAFE_API_KEY=... "
            "(this command reads the nearest .env automatically), or export it "
            "in your shell.",
            file=sys.stderr,
        )
        return 2
    try:
        inputs = _parse_inputs(args.input)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        from typesafe_sdk import AsyncTypeSafeClient

        from jev_use.computers.mac import MacComputer
        from jev_use.supervisor import Supervisor
    except ImportError as exc:
        print(f"pyobjc is required for `run`: {exc}", file=sys.stderr)
        return 2

    async def go() -> Any:
        client = AsyncTypeSafeClient(api_key=key)
        computer = await MacComputer.attach(args.app)
        try:
            return await run_loop(
                goal=args.goal,
                computer=computer,
                brain=JevBrain(client),
                inputs=inputs,
                rules=args.rule,
                thresholds=Thresholds(),
                supervisor=None if args.no_supervisor else Supervisor(client),
                max_steps=args.max_steps,
                trace_path=args.trace,
                dry_run=args.dry_run,
            )
        finally:
            await computer.close()

    try:
        result = asyncio.run(go())
    except (PermissionError, LookupError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    _print_summary(result)
    return 0


# Below this, the app guess is too unsure to act on: ask for --app instead of
# opening the wrong application.
APP_CONFIDENCE_FLOOR = 0.5


async def _do_sentence(
    key: str, sentence: str, args: argparse.Namespace, current_app: str | None = None
) -> tuple[Any, str | None]:
    """Understand one sentence and carry it out. Shared by `do` and `listen`.

    Returns the RunResult and the application used, or (None, None) if the
    application could not be determined.
    """
    from typesafe_sdk import AsyncTypeSafeClient

    from jev_use.computers.mac import MacComputer, installed_apps
    from jev_use.intent import parse_intent
    from jev_use.prompts import DEFAULT_RULES
    from jev_use.supervisor import Supervisor

    overrides = _parse_inputs(args.input)
    client = AsyncTypeSafeClient(api_key=key)
    intent = await parse_intent(
        client, sentence, None if args.app else installed_apps(), current_app=current_app
    )
    app = args.app or intent.app
    if not args.app and intent.app_confidence < APP_CONFIDENCE_FLOOR:
        print(
            f"not sure which app you mean (best guess {intent.app!r} at "
            f"{intent.app_confidence:.2f}). Say it again, or pass --app.",
            file=sys.stderr,
        )
        return None, None
    inputs = {**intent.inputs, **overrides}
    print(f"app:    {app}" + ("" if args.app else f"  ({intent.app_confidence:.2f})"))
    print(f"typing: {', '.join(repr(v) for v in inputs.values()) or 'nothing'}")
    print()

    if args.trace == DEFAULT_DO_TRACE:
        args.trace.unlink(missing_ok=True)
    computer = await MacComputer.open(str(app))
    try:
        result = await run_loop(
            goal=sentence,
            computer=computer,
            brain=JevBrain(client),
            inputs=inputs,
            rules=args.rule or list(DEFAULT_RULES),
            thresholds=Thresholds(),
            supervisor=None if args.no_supervisor else Supervisor(client),
            max_steps=args.max_steps,
            trace_path=args.trace,
            dry_run=args.dry_run,
        )
    finally:
        await computer.close()
    # The intent request is part of what this command cost.
    result.input_tokens += intent.input_tokens
    result.cost_usd += intent.input_tokens / 1_000_000 * USD_PER_MTOK_INPUT
    result.calls += 1
    return result, str(app)


def _require_key() -> str | None:
    key = os.environ.get("TYPESAFE_API_KEY")
    if not key:
        print("TYPESAFE_API_KEY is not set. Put it in .env in the project root.", file=sys.stderr)
    return key


def _cmd_do(args: argparse.Namespace) -> int:
    key = _require_key()
    if key is None:
        return 2
    try:
        result, _ = asyncio.run(_do_sentence(key, args.sentence, args))
    except ImportError as exc:
        print(f"pyobjc is required for `do`: {exc}", file=sys.stderr)
        return 2
    except (ValueError, PermissionError, LookupError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if result is None:
        return 2
    _print_summary(result)
    return 0


def _cmd_listen(args: argparse.Namespace) -> int:
    key = _require_key()
    if key is None:
        return 2
    try:
        from jev_use.computers.mac import _activate, _frontmost_pid
        from jev_use.voice import authorize, listen_once
    except ImportError as exc:
        print(f"pyobjc speech frameworks are required for `listen`: {exc}", file=sys.stderr)
        return 2
    try:
        authorize()
    except PermissionError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    # The terminal is frontmost right now. Remember it so it can be brought
    # back after each command, ready for the next Enter.
    terminal_pid = _frontmost_pid()
    # Session memory: the application the last command used, so a follow-up
    # like "now search for X" continues there instead of guessing afresh.
    current_app: str | None = None
    print("Press Enter, then say a command. Ctrl-C to quit.")
    try:
        while True:
            input()
            print("listening…", flush=True)
            try:
                heard = listen_once(
                    args.locale, on_partial=lambda t: print(f"\r  {t}", end="", flush=True)
                )
            except (PermissionError, RuntimeError) as exc:
                print(f"\n{exc}", file=sys.stderr)
                return 2
            print()
            if not heard:
                print("didn't catch that — press Enter and try again.")
                continue
            print(f"heard: {heard}\n")
            try:
                result, used = asyncio.run(_do_sentence(key, heard, args, current_app))
            except (ValueError, PermissionError, LookupError, RuntimeError) as exc:
                print(str(exc), file=sys.stderr)
                result, used = None, None
            if used is not None:
                current_app = used
            if result is not None:
                _print_summary(result)
            if terminal_pid is not None:
                _activate(terminal_pid)
            if args.once:
                return 0
            print("\nPress Enter for the next command.")
    except (KeyboardInterrupt, EOFError):
        print()
        return 0


def _cmd_replay(args: argparse.Namespace) -> int:
    print(format_trace(read_trace(args.trace)))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING)
    loaded = load_dotenv()
    if loaded is not None:
        logger.debug("loaded environment from %s", loaded)
    handlers = {
        "demo": _cmd_demo,
        "decide": _cmd_decide,
        "run": _cmd_run,
        "replay": _cmd_replay,
        "do": _cmd_do,
        "listen": _cmd_listen,
    }
    return int(handlers[args.command](args))


if __name__ == "__main__":
    raise SystemExit(main())
