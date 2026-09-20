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

---

# Revision 2 — native macOS, measured 2026-09-21

The project now reads the macOS Accessibility API directly. Cua is no longer the
primary adapter. Everything below was measured on this machine, not assumed.

## D7 — The macOS Accessibility API is callable directly

`pyobjc-framework-ApplicationServices` exposes the whole surface this project
needs, verified present:

```
AXUIElementCreateSystemWide, AXUIElementCreateApplication,
AXUIElementCopyAttributeValue, AXUIElementCopyAttributeNames,
AXUIElementPerformAction, AXIsProcessTrusted, AXValueGetValue,
kAXChildrenAttribute, kAXRoleAttribute, kAXTitleAttribute,
kAXPositionAttribute, kAXSizeAttribute
```

Input synthesis comes from `pyobjc-framework-Quartz`: `CGEventCreateMouseEvent`,
`CGEventCreateKeyboardEvent`, `CGEventPost`. Screen bounds come from
`CGDisplayBounds(CGMainDisplayID())`.

This removes the Cua dependency, the VM, and the unverified tree shape that was
blocking the original Task 3. It replaces them with one requirement: the host
process needs Accessibility permission.

## D8 — Permission attaches to the host process, not the script

`AXIsProcessTrusted()` reports whether the **running binary** is trusted. A
Python script inherits the grant of whatever launched it — on this machine,
`Ghostty.app`. Granting Ghostty made every child process trusted; running the
same script from a different terminal or editor would report `False` again and
return empty trees with no error raised.

Consequences for the implementation:

- `MacComputer` must call `AXIsProcessTrusted()` at startup and fail loudly with
  the System Settings path, rather than returning an empty element list.
- The README must state that the grant is per-host-application.
- Shipping this to other people eventually needs a signed `.app` bundle so the
  grant attaches to a stable identity. Out of scope for v0.1.

## D9 — Language choice: measured, Swift advantage is ~1.16x

Identical traversal (five attributes plus children per node, depth cap 12,
median of three runs, same target pid), Python + pyobjc against compiled Swift:

| Target | Python | Swift | Ratio |
| --- | --- | --- | --- |
| Music (menu tree, 320 nodes) | 37.6ms | 31.5ms | 1.19x |
| Slack (menu tree, 283 nodes) | 33.2ms | 28.7ms | 1.16x |

Per attribute read: ~19.6µs Python, ~16.4µs Swift. The pyobjc bridge is only
~3µs of that. The remaining ~16µs is the AX API performing a cross-process call
into the target application, which costs the same from any language.

Set against a 200–500ms System One request, a full tree walk is roughly 10% of
step time, so the language difference is about 1.6% per step. **Python is the
decision.** The structural argument for Swift — permission attaching to a signed
binary identity — survives the measurement but only matters at distribution time.

## D10 — Real element counts, and a correction

An earlier measurement in this session reported ~320 nodes and ~230 clickable
elements for Music and Slack. That was wrong: both applications had zero open
windows, so the walk traversed their menu bar trees. `AXUIElementCopyAttributeValue`
with `kAXWindowsAttribute` returned an empty list for both.

Walking actual windows instead (depth cap 14, five runs, median):

| Application | Windows | Nodes | Labelled and on screen | Clickable | Walk |
| --- | --- | --- | --- | --- | --- |
| Finder | 1 | 20 | 20 | 0 | 2.3ms |
| Google Chrome | 3 | 166 | 98 | 90 | 23.3ms |

What this means for the caps in SPEC.md §4.1 and §4.2:

- `max_elements=150` is not binding on these applications.
- `max_candidates=80` is exceeded by Chrome's 90 clickable elements, so
  truncation is real but marginal. Raising it to 150 covers both, and the
  semantic-find cookbook scores 218 options in a single Choice, so 150 is within
  demonstrated range. SPEC.md §11.2 still asks for an empirical ceiling.
- State size at 150 elements is roughly 1,000 tokens, which is about $0.00005
  per decision at $0.042 per million input tokens.

Two caveats on these numbers. Chrome builds its accessibility tree lazily, so
166 nodes is likely browser chrome rather than page content; a content-heavy
page is untested and may be far larger. Finder's 20 nodes is low enough to
suggest lazily populated children that this traversal did not reach.

## D11 — Offscreen elements can be filtered correctly, not heuristically

Elements carry a position and size in screen coordinates. An element whose
rectangle has zero area, or lies entirely outside `CGDisplayBounds`, cannot be
clicked — the synthesized event would land somewhere else. Dropping these is a
correctness filter rather than a relevance guess, and it is what removed the
closed-menu items from the counts above.

`parse_tree` therefore needs the screen bounds, which means the `Computer`
protocol gains `screen_size()`.
