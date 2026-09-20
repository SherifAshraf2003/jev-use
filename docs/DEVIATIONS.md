# Deviations from SPEC.md

Reality wins over the spec (SPEC.md §0.1). Every row below is something verified
against an installed package or the live documentation on 2026-09-20, which
differs from what SPEC.md assumed.

## Verified versions

| Component | Version | How verified |
| --- | --- | --- |
| Python (local) | 3.14.7 | `python3 --version` |
| `typesafe-sdk` | 0.7.0 | `pip show typesafe-sdk` |
| `cua-computer` | 0.4.17 | `pip show cua-computer` |

Note: PyPI publishes `cua-computer` 0.5.19, but pip resolved 0.4.17 on Python
3.14. Record the resolved version again when the project pins a Python floor of
3.11, since resolution may differ there.

## D1 — Cua dispatches clicks by coordinate, not by element index

SPEC.md §4.6 assumes `click(element_index)` and `type_text(element_index, text)`.
The installed interface (`computer.interface.base.BaseComputerInterface`) exposes:

```
get_accessibility_tree(self) -> Dict
left_click(self, x: int | None = None, y: int | None = None, delay: float | None = None) -> None
type_text(self, text: str, delay: float | None = None) -> None
scroll_down(self, clicks: int = 1, delay: float | None = None) -> None
scroll_up(self, clicks: int = 1, delay: float | None = None) -> None
press_key(self, key: str, delay: float | None = None) -> None
```

There is no element-indexed dispatch anywhere on the interface. Consequences,
all of which SPEC.md §11.4 anticipated as the fallback case:

1. `Element` gains a `bbox: tuple[int, int, int, int] | None` field (x, y, width,
   height) and a `center` property. Elements without a bbox cannot be clicked and
   are dropped during action enumeration.
2. The `Computer` protocol becomes coordinate-based: `click(x, y)` and
   `type_text(text)`. Typing at an element is a two-call sequence — click the
   element to focus it, then type — and the adapter owns that sequence.
3. `Element.index` remains, but it is now an identifier the trace and the
   banned-action signatures use, not something the driver consumes.

## D2 — A Noul answer carries no confidence

SPEC.md §4.5 warns against misreading noul confidence. The SDK does not return
one at all:

```
NoulAnswer:  type, noul (float)
ChoiceAnswer: type, choice (str), confidence (float), probabilities (dict[str, float])
ScoreAnswer:  type, score (float), confidence (float), legend (dict[int, str|...]), probabilities (dict[int, float])
```

`policy.py` must therefore read only `.noul` for noul judgments. There is no
confidence axis available for the supervisor's safety questions.

## D3 — Score answers are continuous, with integer-keyed probabilities

`ScoreAnswer.score` is a `float`, not a level index, and `probabilities` is keyed
by `int` level index rather than by level description. `legend` maps the integer
index back to the level description that was sent. SPEC.md §4.3 described
`progress` as "5 ordered levels" without specifying the return shape; the
`Decision.progress` field holds the float score.

## D4 — Verified TypeSafe client surface

```
AsyncTypeSafeClient(*, api_key=None, model=None, retry=None, timeout=None,
                    headers=None, transport=None, http_client=None, base_url=None)

await client.system_one(state, questions, *, model=None, retry=None,
                        timeout=None, extra_headers=None, extra_body=None,
                        response_model=None) -> SystemOneResponse
```

Questions are a mapping of id to `Noul | Choice | Score`:

```
Choice(instructions=..., criteria: Mapping[str, JSONContent | None])
Noul(instructions=..., criteria: NoulCriteria | None = None)
Score(instructions=..., criteria: Sequence[JSONContent])
```

`SystemOneResponse` exposes `model`, `usage`, `answers`, `request_id` and the
convenience views `choices`, `nouls`, `scores`. `Usage` carries `input_tokens`
and `output_tokens`, both `int | None`.

SPEC.md §4.3 asked whether plain dicts or typed helpers are preferable. Use the
typed helpers: they are the documented path and they validate the criteria shape
before the request leaves the process.

## D5 — Retries are built in

`RetryPolicy(max_retries=2, backoff_initial=0.5, backoff_max=5.0,
backoff_jitter=0.25, http_statuses=<factory>, respect_retry_after=True,
api_connection_error=True, api_timeout_error=True, exceptions=<factory>,
predicate=None, timeout=30.0)`.

The SDK retries and honors `retry-after`. SPEC.md §4.3 is satisfied with no
hand-rolled retry logic.

## D6 — Verified pricing, model names, and limits

- Price: $0.042 per million input tokens. Output tokens are free.
- Default model: `jev-latest`, currently resolving to `jev-1.13.0`.
- Context: 64k tokens per request total; 32k for `state` plus the longest
  single question.
- Rate limits: 250,000 tokens per second, 1,200 requests per minute.
- Environment variables: `TYPESAFE_API_KEY`, `TYPESAFE_BASE_URL`,
  `TYPESAFE_DEFAULT_MODEL`, `TYPESAFE_LOG_LEVEL`. Default base URL is
  `https://api.typesafe.ai`; default per-operation timeout is 10.0 seconds.
- Input is text only: string, JSON object, or array of text values. This answers
  SPEC.md §11.1 — there is no image input to build on.

## Still unverified

These need live credentials and a booted machine. They are M0 acceptance items
and must be filled in before the milestones that depend on them.

- The concrete shape of the dict returned by `get_accessibility_tree()`. It is
  produced by the remote computer-server, so it cannot be read from the local
  package source. `parse_tree`'s key-candidate constants stay provisional until
  a real tree is captured.
- Whether tree nodes carry bounding boxes, and in which coordinate space
  (screen or screenshot). `to_screen_coordinates` / `to_screenshot_coordinates`
  exist on the interface, which implies the two spaces differ.
- The practical ceiling on Choice options before quality degrades (SPEC.md §11.2).
- Whether two-phase supervision costs measurably more latency than speculative
  supervision (SPEC.md §11.3).
