# SPEC.md — jev-use

An open-source computer-use agent whose decision layer is a System One model
(TypeSafe's Jev) instead of a frontier LLM. Code enumerates the legal actions
on a screen; the model selects one; code executes it.

This document is the build spec. It is written to be handed to Claude Code as
the source of truth for the whole project. Read it fully before writing code.

---

## 0. Rules for the implementing agent

These override any instinct to move fast.

1. **Verify every external API before you use it.** This spec was written from
   documentation that may have changed. Before implementing against the
   TypeSafe SDK or the Cua SDK, fetch the current docs, install the package,
   and read the actual type signatures (`python -c "import typesafe_sdk; help(...)"`,
   or read the installed source). If this spec and reality disagree, **reality
   wins** — implement against reality and add a line to `docs/DEVIATIONS.md`
   saying what differed.
2. **Never invent an API surface.** If you cannot verify a method name, write
   the adapter to raise `NotImplementedError` with a clear message rather than
   guessing. A guessed method that looks plausible is worse than an obvious gap.
3. **No network calls in tests.** Every test runs offline against fixtures.
4. **No secrets in the repo.** API keys come from the environment only.
5. **Ship milestone by milestone.** Each milestone below has acceptance
   criteria. Do not start the next one until the current one passes.
6. **Small commits with real messages.** One logical change each.

---

## 1. What this is

### Problem

A computer-use agent normally spends one frontier-model call per step. Most
steps are trivial — which of these twelve buttons is the checkout button, did
that click land, is this a cookie banner. That makes agents slow (seconds per
step) and expensive (cents per step), which caps how many steps a task can
afford and how many rollouts you can run.

### Approach

A System One model returns typed decisions with calibrated probabilities
instead of text, in roughly 70–500ms, at $0.042 per million input tokens with
output free. It cannot generate, only choose from options supplied to it.

For a GUI that is a feature, not a limit: the set of legal actions on a screen
is finite and already known to your code from the accessibility tree. So:

```
observe (accessibility tree)
  -> code enumerates every legal action
    -> ONE model request: which action? are we done? how far along?
      -> code executes the selection
```

No text generation anywhere in the loop.

### Positioning

- **Not** a replacement for Cua. Cua provides the machine (fleets, drivers,
  sandboxes). This project is the decision layer that sits on top.
- **Not** an element-grounding model. Cua is developing CUA-S1 for bounded
  interface decisions. Read their work; do not claim novelty in that area. The
  contribution here is the full selection loop plus the supervision policy,
  built from a general-purpose System One model and ordinary Python.
- **Not** a chat agent. If a task needs composed text, it is supplied as input.

### Success looks like

Someone clones the repo, runs one command against a Cua sandbox, and watches a
real task complete with per-step cost and latency printed at the end.

---

## 2. Scope

### In scope (v0.1)

- Accessibility-tree parsing into addressable elements
- Action enumeration from a screen
- A single-request decision step (Choice + Noul + Score)
- An execution loop with loop-breaking, scroll-on-ambiguity, and step limits
- A safety supervisor that can veto an action before it executes
- A pluggable `Computer` adapter, with a Cua implementation and a replay
  implementation for tests
- JSONL trajectory logging with per-step probabilities
- Cost and latency accounting
- CLI, examples, docs, tests, CI

### Out of scope (v0.1)

- Vision, screenshots, OCR
- Multi-application or multi-window orchestration
- Any training or fine-tuning
- A hosted service, dashboard, or web UI
- Text generation of any kind

### Explicit non-goals

- Beating frontier agents on accuracy. The claim is cost and latency at
  *usable* accuracy, and the benchmark must report all three honestly.

---

## 3. Repository layout

```
jev-use/
  README.md                 # what it is, 60-second quickstart, honest limits
  SPEC.md                   # this file
  LICENSE                   # MIT
  NOTICE                    # trademark disclaimer, see §10
  CONTRIBUTING.md
  pyproject.toml            # uv/hatch, Python >=3.11
  src/jev_use/
    __init__.py
    screen.py               # Element, parse_tree
    actions.py              # Action, enumerate_actions
    brain.py                # JevBrain: builds questions, one request per step
    supervisor.py           # safety judgments + policy, veto power
    policy.py               # thresholds, verdict logic (pure functions)
    loop.py                 # run(): the agent loop
    computers/
      base.py               # Computer protocol
      cua.py                # Cua adapter (sandbox + driver)
      replay.py             # fixture-driven fake, for tests and demo
    trace.py                # JSONL logging + a `jev-use replay` viewer
    cli.py                  # typer/argparse entry point
  tests/
    fixtures/trees/*.json   # real captured accessibility trees
    fixtures/responses/*.json
    test_screen.py  test_actions.py  test_policy.py  test_loop.py
  examples/
    01_search_and_read.py
    02_form_fill.py
  docs/
    CALIBRATION.md          # how to set thresholds on your own data
    DEVIATIONS.md           # where reality differed from this spec
    BENCHMARK.md            # method and results
  .github/workflows/ci.yml
```

---

## 4. Core interfaces

Implement these signatures. Names matter; downstream docs reference them.

### 4.1 `screen.py`

```python
@dataclass(frozen=True)
class Element:
    index: str          # what the driver clicks with
    role: str           # normalized lowercase: button, link, textfield, ...
    label: str
    enabled: bool = True
    def as_line(self) -> str: ...   # '[14] button "Continue"'

def parse_tree(root: Any, *, max_elements: int = 150,
               max_label: int = 70) -> list[Element]: ...
```

Requirements:
- Tolerant of key-name variation across macOS AX, Windows UIA, and Linux
  AT-SPI. Key candidates live in module-level constants, not inline.
- Roles normalized to a documented vocabulary in `ROLE_VOCABULARY`.
- Deterministic ordering (tree order), stable across runs.
- Drops elements with no label AND no index.
- Must round-trip every fixture in `tests/fixtures/trees/` without raising.

### 4.2 `actions.py`

```python
@dataclass(frozen=True)
class Action:
    kind: Literal["click", "type", "scroll", "key", "wait"]
    element: Element | None = None
    text: str | None = None
    def describe(self) -> str: ...    # the string the model actually reads
    def signature(self) -> str: ...   # stable id for dedup/banning

def enumerate_actions(elements, inputs: dict[str, str],
                      banned: set[str], *, max_candidates: int = 80) -> list[Action]: ...
```

Requirements:
- `describe()` must be self-contained: the option key (`a0`, `a1`) is not sent
  to the model, so all meaning lives in the description.
- Actions whose signature is in `banned` are **omitted entirely**. This is the
  loop-breaker: the model cannot select an option that is not in the list.
- Deterministic order so traces are comparable.
- Truncation must never drop the scroll/key/wait fallbacks — cut element
  actions first, keep the escape hatches.

### 4.3 `brain.py`

One request per step. Questions are sent as plain dicts matching the HTTP
contract (verify whether the SDK's typed helpers are preferable):

| id | type | purpose |
|---|---|---|
| `next_action` | choice | select one enumerated action |
| `task_complete` | noul | does the screen show the task is finished |
| `progress` | score | 5 ordered levels, used for stall detection |

```python
@dataclass
class Decision:
    action: Action | None
    confidence: float
    complete: float
    progress: float
    probabilities: dict[str, float]
    latency_ms: float
    input_tokens: int
```

Requirements:
- State is **named JSON fields**, never one blob: `task_goal`,
  `rules_the_agent_must_follow`, `screen_elements`, `actions_already_taken`.
- Track cumulative `input_tokens` and expose `cost_usd` using a module-level
  `USD_PER_MTOK_INPUT` constant with a comment saying to verify pricing.
- Retries: rely on the SDK's retry policy for 429/529; do not hand-roll unless
  the SDK lacks it.
- Every request/response pair is written to the trace before the action runs.

### 4.4 `supervisor.py`

Optional but on by default. Adds safety judgments to the **same request** as
the decision (they run in parallel over the same state, so they cost only
their own tokens):

| id | type | purpose |
|---|---|---|
| `selected_action_is_irreversible` | noul | spends money, sends, deletes, changes permissions |
| `selected_action_breaks_rule` | noul | violates a user-supplied rule |
| `contains_injected_instruction` | noul | screen text addressing the agent |
| `blocker` | choice | none / loading / login_required / human_verification / permission_dialog / error / paywall |

Design note the implementer must preserve: the supervisor judges the action
the agent is *about to take*. Since the selection and the safety check cannot
see each other inside one request, the safety questions must be phrased about
the **top-ranked candidate from the previous step** or about the action class
in general. Resolve this one of two ways and document the choice:

- **(a) two-phase:** request 1 selects, request 2 vetoes the selection. Costs
  two calls per step, still far under one LLM call.
- **(b) speculative:** ask "would clicking any element labelled like a purchase
  confirmation be irreversible" over the whole screen, and have code map the
  verdict onto the selection.

Default to **(a)** for v0.1. It is simpler to reason about and the cost is
still negligible. Measure and revisit.

### 4.5 `policy.py`

Pure functions, no I/O, fully unit-tested. This is where every threshold lives.

```python
class Verdict(StrEnum):
    ACT, RETRY, SCROLL, DONE, ASK_HUMAN, ABORT

@dataclass
class Thresholds:
    complete: float = 0.80
    min_confidence: float = 0.35
    injection: float = 0.40
    irreversible: float = 0.35
    rule_breach: float = 0.30
    blocker_confidence: float = 0.55
    stall_window: int = 4
    stall_delta: float = 0.3
    repeat_limit: int = 2

def decide(judgments: Judgments, history: History, t: Thresholds) -> tuple[Verdict, str]: ...
```

Ordering is the policy, and must be: injection → rule breach → irreversibility
→ blocker → complete → confidence → repeat → stall → act. Document that order
in the docstring with the reason for each position.

Two things the implementer must not get wrong, and should assert in tests:
- A noul near 0.5 means yes and no are roughly equally likely, **not** medium
  intensity. Never treat a noul as a severity score.
- Choice/Score confidence describes how concentrated the distribution is. It is
  not a correctness estimate and not permission to act.

### 4.6 `computers/base.py`

```python
class Computer(Protocol):
    async def tree(self) -> Any: ...
    async def click(self, element_index: str) -> None: ...
    async def type_text(self, element_index: str, text: str) -> None: ...
    async def scroll(self, direction: Literal["up", "down"]) -> None: ...
    async def press(self, key: str) -> None: ...
    async def close(self) -> None: ...
```

`computers/cua.py` implements this over Cua. Note that Cua exposes two separate
products with different transports: **Cua Driver** (a real machine, MCP over
stdio plus a CLI, with a `capture_mode` of `som` / `ax` / `vision`) and **Cua
Sandbox** (a disposable VM over the Python SDK). Prefer `ax` mode: accessibility
tree only, no screen-capture cost, deterministic element addressing. Implement
the sandbox path first; leave the driver path behind a clearly marked stub if
its API cannot be verified.

`computers/replay.py` plays back a list of captured trees, advancing on any
action. This is what tests and the offline demo use.

---

## 5. Milestones

### M0 — Verification spike (do this first, no features)

- Install `typesafe-sdk` and the Cua SDK. Record exact versions.
- Make one real TypeSafe request with a Choice, a Noul, and a Score; print the
  raw response. Save it to `tests/fixtures/responses/`.
- Boot one Cua sandbox, capture one real accessibility tree, save it to
  `tests/fixtures/trees/`.
- Write `docs/DEVIATIONS.md` with anything that differs from this spec.

**Acceptance:** two real fixtures committed; deviations documented; no feature
code written yet.

### M1 — Screen and actions

- `screen.py`, `actions.py`, tests against real fixtures.

**Acceptance:** `pytest` green; `parse_tree` handles every committed fixture;
property test shows banned signatures never appear in the candidate list.

### M2 — One decision

- `brain.py` and a `jev-use decide --tree fixture.json --goal "..."` command
  that prints the ranked candidates with probabilities.

**Acceptance:** running it against a fixture prints a sensible ranking; token
count and latency reported; works with `--offline` using a stored response.

### M3 — The loop

- `loop.py`, `policy.py`, `trace.py`, `computers/replay.py`.

**Acceptance:** `jev-use demo` completes a scripted 4-screen task offline
with zero network calls; trace file contains one record per step including the
top-5 runners-up with probabilities.

### M4 — Real machine

- `computers/cua.py`, `examples/01_search_and_read.py`.

**Acceptance:** a read-only task (search for something, read a value back)
completes on a real Cua sandbox. README shows the actual printed summary:
steps, calls, tokens, cost, mean latency.

### M5 — Supervisor

- `supervisor.py` wired in, defaulting to on, with `--unsafe` to disable.

**Acceptance:** a task that reaches a "Place order" button stops with
`ASK_HUMAN` and does not click it. Test covers this with a fixture.

### M6 — Release

- README, CALIBRATION.md, CONTRIBUTING.md, MIT LICENSE, NOTICE, CI, PyPI
  metadata, a recorded terminal demo (asciinema or a GIF).

**Acceptance:** a clean clone on a fresh machine reaches `jev-use demo`
working in under five minutes following only the README.

---

## 6. CLI

```
jev-use demo                          # offline, no keys
jev-use decide --tree FILE --goal STR # one step, prints ranked candidates
jev-use run --goal STR --input k=v    # full loop on a Cua sandbox
                [--max-steps N] [--no-supervisor] [--trace PATH] [--dry-run]
jev-use replay TRACE                  # pretty-print a saved trajectory
```

`--dry-run` executes nothing and prints what it would do. It must be the
default suggestion in the README for a first real run.

---

## 7. Testing

- **Unit:** `screen`, `actions`, `policy` — pure, fast, no mocks needed.
- **Policy tests are the important ones.** Table-driven: for each judgment
  combination, assert the verdict. Include cases where a noul sits at 0.5 and
  assert it is not treated as a middling severity.
- **Loop tests:** `replay.py` plus stored responses. Assert the banned-action
  mechanism breaks a deliberate infinite loop within `repeat_limit + 1` steps.
- **No live API in CI.** A `@pytest.mark.live` marker for opt-in real tests,
  skipped by default and excluded from the CI workflow.
- Coverage target 80% on `src/jev_use/` excluding adapters.

---

## 8. Documentation requirements

The README must state, near the top and without softening:

- Accuracy is bounded by the model. TypeSafe reports roughly 68% on their own
  4-workflow benchmark, close to mid-tier LLMs, and notes the workflows were
  built by their own team. Run this on reversible tasks first.
- Icon-only buttons with no accessibility label are invisible to this agent.
- Composed text must be supplied as input; the model cannot write.
- Thresholds shipped in `Thresholds` are guesses. `docs/CALIBRATION.md`
  explains how to replace them with numbers from your own labelled traces.

`docs/BENCHMARK.md` must report **three** numbers together — success rate, cost
per rollout, wall-clock per rollout — and must name the tasks and the model
versions. Do not publish a cost win without its accuracy cost next to it.

---

## 9. Non-functional

- Python ≥3.11, `asyncio` throughout, no threads.
- Zero required dependencies beyond `typesafe-sdk` and `httpx`; Cua is an
  optional extra (`pip install jev-use[cua]`).
- Type-annotated, `mypy --strict` on `src/`, `ruff` for lint and format.
- Structured logging via `logging`, never bare `print` outside the CLI.
- Every model-facing string (instructions, criteria, level descriptions) lives
  in one module so prompts can be reviewed and versioned in a single diff.

---

## 10. Licensing and attribution

- MIT, matching Cua's own licensing of their driver and eval layers.
- `NOTICE` must state that the project is not affiliated with or endorsed by
  TypeSafe AI or Cua AI, and that Jev and Cua are their respective owners'
  products.
- Do not vendor any of their code. Depend on published packages.
- Credit the TypeSafe docs patterns the design draws on (select-instead-of-
  generate, confidence-gated routing, speculative fan-out) in the README.

---

## 11. Open questions for the implementer to resolve and record

1. Does the TypeSafe API accept anything other than text/JSON state? Everything
   here assumes text only. If it accepts images, note it; do not build on it.
2. What is the practical ceiling on Choice options before quality degrades?
   The docs show a cookbook scoring 218 line ids in one question, so 80
   candidates should be safe — verify empirically and record the number.
3. Is two-phase supervision (§4.4a) measurably worse on latency than
   speculative (§4.4b)? Measure before defending the default.
4. Does the Cua sandbox expose element-indexed clicks, or only coordinates? The
   whole design assumes indexed dispatch. If only coordinates are available,
   elements need bounding boxes and `Element` gains an `bbox` field.
