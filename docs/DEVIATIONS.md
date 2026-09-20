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

---

# Revision 3 — Task 2 capture results, measured 2026-09-21

## D12 — Chrome's web accessibility tree is enabled progressively

A first capture of Chrome returned 172 nodes with zero `AXWebArea` and no page
content at all: toolbars, tab buttons, and the address bar only. The social media page
that was open was entirely invisible.

Setting the two attributes normally used to request it returned errors:

```
AXUIElementSetAttributeValue(app, "AXManualAccessibility", True)   -> -25205
AXUIElementSetAttributeValue(app, "AXEnhancedUserInterface", True) -> -25208
```

Despite both failing, the tree populated anyway over the following captures:

| Capture | Nodes | AXWebArea | AXStaticText | AXLink |
| --- | --- | --- | --- | --- |
| first | 172 | 0 | 0 | 0 |
| after set attempts | 179 | 1 | 0 | 0 |
| after depth raise | 691 | 1 | 51 | 26 |

Chrome appears to enable its renderer accessibility tree when it observes
sustained AX API use, and then builds it lazily. The practical consequence is
that **the first capture of a Chrome window is not representative**, and an
agent's first few steps against Chrome may see a screen with no page content on
it. `MacComputer` should warm the tree — read it once and discard — before the
loop's first real observation, and the README must state this.

The supported alternative is launching Chrome with
`--force-renderer-accessibility`. Safari exposes web content natively and is
untested here; it is the better first target for a browser task.

## D13 — Depth cap of 14 truncates page content

Page content sits below the depth at which browser chrome ends. Sweeping the cap
against the same three Chrome windows:

| Depth cap | Nodes | Deepest reached | Labelled | Web-content nodes | Walk |
| --- | --- | --- | --- | --- | --- |
| 14 | 182 | 14 | 104 | 4 | 32ms |
| 20 | 190 | 20 | 104 | 4 | 17ms |
| 30 | 226 | 28 | 117 | 12 | 19ms |
| 45 | 226 | 28 | 117 | 12 | 19ms |

The tree fully resolves at 28. `MAX_DEPTH` is therefore 30, not 14, and the
extra depth costs nothing measurable. SPEC.md's traversal assumed shallow
application trees; a browser is not one.

## D14 — With page content present, both caps bind

The fully populated Chrome capture, filtered the way the agent filters:

| Measure | Count |
| --- | --- |
| Total nodes | 691 |
| Labelled | 252 |
| Labelled with a bounding box | 251 |
| On screen | 251 |
| Clickable and on screen | 165 |

So `max_elements=150` truncates 251 elements, and `max_candidates=150` truncates
165 candidates. Revision 2 raised the candidate cap from 80 to 150 based on a
capture that did not include page content; that measurement was not
representative, and 150 is still too low for a browser.

This makes the rerank escape hatch described in Task 4 no longer optional for
browser tasks. One social media page produced 165 candidates; a denser page will
produce more, and tree-order truncation will silently drop whichever ones happen
to come last. Resolve before Task 12 ships a browser example: either raise the
cap toward the 218 demonstrated in the semantic-find cookbook and measure
quality at that size, or build the two-stage shortlist.

State size is unchanged at roughly 1,200 tokens for 150 elements, so cost is not
the constraint — selection quality at large option counts is.

## D15 — Captured fixtures must be sanitized before they are committed

SPEC.md §5 M0 says to commit real captured trees. The repository is public, and
a real capture contains whatever was on screen: in these captures, a social media
page including a physical address, and desktop filenames.

`scripts/sanitize_tree.py` replaces every label with a generic stand-in while
preserving role, bounding box, depth, child ordering, and the long-label cases
that exercise truncation. The parser tests care about structure only, so nothing
of test value is lost. Raw captures stay local and are gitignored as
`*_PRIVATE.json`.

---

# Revision 4 — the candidate cap is an API limit, measured 2026-09-21

## D16 — A Choice question accepts at most 255 options

This closes SPEC.md §11.2, which asked for the practical ceiling on Choice
options before quality degrades. There is no quality cliff to find, because the
API refuses the request first:

```
POST https://api.typesafe.ai/v1/systemone: 400 Too many choices.
Must have at most 255 choices.
```

Binary searched between 250 and 400: 255 accepted, 256 rejected. The limit is
exact and is not documented on the models page.

## D17 — No accuracy or latency degradation up to the limit

Measured against the real Chrome capture, one unambiguous goal
("Send a message to this page"), correct option placed last in every list so
that tree-order truncation would be maximally punishing, three runs per size:

| Options | Median latency | Confidence | Correct | Input tokens |
| --- | --- | --- | --- | --- |
| 25 | 351ms | 0.970 | 3/3 | 991 |
| 50 | 324ms | 0.990 | 3/3 | 1,743 |
| 100 | 382ms | 0.860 | 3/3 | 3,114 |
| 166 | 346ms | 0.880 | 3/3 | 4,157 |
| 210 | 371ms | 0.470 | 1/3 | 5,301 |
| 255 | 364ms | 0.720 | 3/3 | 6,471 |

An earlier sweep across three goals at 20, 50, 100 and 166 options was 12/12
correct with confidence unchanged by list size.

Two readings of this table:

- **Latency is flat.** It does not grow with option count. An earlier run in
  this session appeared to show 2–5 second responses at 250 options; resampling
  showed that was noise, not a size effect.
- **The 210 row is not a degradation.** Resampled with eight runs at the same
  size and option set: 8/8 correct. The 1/3 does not reproduce. It does show
  that the same request can return different answers across calls, which is
  worth remembering when reading any single trace.

Falling confidence as the list grows is expected and is not a quality signal:
a Choice distribution is normalized across the options given, so the same
correct answer necessarily holds less probability mass in a larger field. See
the note in `policy.py` about confidence measuring concentration.

## D18 — `max_candidates` is 250, and sharding is required above it

250 element actions plus the five fallbacks fits under the 255 ceiling. The
Chrome capture produced 166 clickable elements, so an ordinary page has
headroom today.

A denser page will not. Above 255 candidates the request is **rejected**, not
degraded, so this is a hard correctness boundary rather than a tuning choice,
and v0.1 must handle it. Two designs, both costing a second sequential round
trip (~700ms per step instead of ~350ms, since latency is flat):

- **Choice bracket.** Shard the candidates, run the shards in parallel, then a
  final Choice over the shard winners. Sound, because the final round makes the
  winners commensurable again — necessary since Choice probabilities are
  normalized within their own option set and cannot be compared across shards.
  Its weakness is that elimination is unrecoverable: if the correct action loses
  inside its shard, the final round can never see it.
- **Score shortlist.** Score every candidate for relevance, take the top N by
  raw score, then one Choice over the shortlist. A Score is absolute rather than
  normalized within a batch, so scores merge across shards without a
  reconciliation round. This is the pattern TypeSafe's rerank cookbook uses.

Score shortlisting is preferred for the same round-trip budget. Neither is built
in v0.1; `enumerate_actions` truncates at 250 and the loop must log when it does,
so the condition is visible rather than silent.

## D19 — Descriptions collide, and a collision silently drops candidates

`Choice.criteria` is a dict keyed by the option description, and `brain.py` maps
the returned string back to its `Action` through that same key. Two identical
descriptions therefore collapse to one option, and the answer can resolve to the
wrong element.

This is not an edge case. The captured browser window held 166 clickable
elements but only **91 distinct role-and-label pairs**: 24 separate buttons
labelled "Close", four comboboxes labelled "Tab Search", and so on. Undisambiguated,
**75 of 166 candidates — 45% — would vanish from the option list**, including
23 of the 24 close buttons, with nothing in the trace to show it happened.

`enumerate_actions` therefore appends an ordinal to colliding descriptions only:

```
Click the button labelled "Message"                             (unique, untouched)
Click the 24th of 24 button labelled "Close", in screen order   (collided)
```

Ordinals are assigned in tree order, which is the same order the elements appear
in the `screen_elements` state field, so "in screen order" is something the model
can actually resolve against what it was shown. Verified on the real capture:
161 candidates, 96 requiring an ordinal, 190 descriptions all distinct once
typing actions are included.

SPEC.md §4.2 required `describe()` to be self-contained but did not anticipate
that self-contained is not the same as unique.

---

# Revision 5 — first end-to-end decisions, measured 2026-09-21

## D20 — Latency scales with total tokens, correcting D17

D17 reported latency as flat at ~350ms and attributed an earlier 2–5 second
reading to noise. That conclusion was drawn from sweeping the **option count**
while the state stayed small. Sweeping the whole payload tells a different
story. Seven runs per row, same goal, real capture:

| Elements | Candidates | Input tokens | p50 | p90 | min | max |
| --- | --- | --- | --- | --- | --- | --- |
| 40 | 28 | 1,762 | 345ms | 807ms | 304ms | 807ms |
| 100 | 51 | 3,038 | 365ms | 436ms | 337ms | 436ms |
| 250 | 161 | 8,380 | 1,041ms | 2,152ms | 625ms | 2,152ms |

So the flat region is real but it ends. Up to roughly 3k tokens a request
returns in about 350ms; at 8.4k tokens the median triples and the tail reaches
2.2 seconds. D17's option-count conclusion stands — options are cheap — but the
claim that latency does not grow was too broad.

**This bears directly on SPEC.md §1**, which cites "roughly 70–500ms" for a
System One request. That holds for small states. At the 250-element cap this
project ships, a step costs about 1 second, and about 2 seconds with the
supervisor's second request. The cost claim survives; the latency claim needs
the state size stated next to it, and `docs/BENCHMARK.md` must report it that
way rather than quoting the docs figure.

There is a real tradeoff here that calibration should settle: 100 elements
answers in 365ms, 250 elements in 1,041ms. Almost 3x latency for elements that
are, on the measured page, mostly not the target.

## D21 — Cost per decision is 5x the earlier estimate, and still negligible

Measured across four goals on the full 250-element state: 9,151 input tokens per
decision, $0.000384 each. The earlier D14 estimate of $0.00008 counted only the
`screen_elements` lines and omitted the candidate descriptions, which are longer
than the element lines because they carry the action verb and, where labels
collide, an ordinal clause.

A 25-step task therefore costs roughly $0.01 with the supervisor on. Cost is
still not the constraint.

## D22 — Ordinals are selected by the model, but cannot express recency

First end-to-end decisions against the real capture, 250 elements and 190
candidates:

| Goal | Selected | Confidence |
| --- | --- | --- |
| Open the Marketplace section | `Click the link labelled "Marketplace"` | 0.990 |
| Send a message to this page | `Click the button labelled "Message"` | 0.970 |
| Close the current browser tab | `Click the 5th of 24 button labelled "Close"` | 0.580 |
| Search for something on this page | `Click the combobox labelled "Search Facebook"` | 0.380 |

The disambiguation from D19 works end to end: the model selects ordinal-tagged
options and resolves them to distinct elements.

The third row exposes a limit the ordinal cannot fix. Twenty-four tabs each
carry a "Close" button, and nothing in the state says which tab is *current*.
The model picked the 5th with 0.58 confidence and the 1st as runner-up at 0.21;
neither is knowably right, because the information is not in the state. The
spread is the honest signal here — that is what low confidence is supposed to
look like, and `min_confidence` of 0.35 would let this act anyway.

Fixing it means putting the missing fact into the state, for example a focused
or selected flag per element, rather than asking the model to guess. That is an
`AXFocused` / `AXSelected` attribute read in `mac.py` and a field on `Element`.
Logged for Task 12; not built yet.
