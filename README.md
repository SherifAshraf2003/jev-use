# jev-use

A computer-use agent whose per-step decision is a single [TypeSafe](https://typesafe.ai)
System One request instead of a frontier-LLM call. Code reads the macOS
accessibility tree and enumerates every legal action on the screen; the model
selects one; code executes it. No text is generated anywhere in the loop.

**Status: in development.** There is no working release yet. The build is
happening against [`SPEC.md`](SPEC.md) and the plan in
[`docs/superpowers/plans/`](docs/superpowers/plans/), with verified API reality
recorded in [`docs/DEVIATIONS.md`](docs/DEVIATIONS.md).

## Read these limits before using it

- **It drives your real Mac.** It clicks and types on the machine you are using,
  in whatever applications are open, signed in as you.
- **Accuracy is bounded by the model.** TypeSafe reports roughly 68% on their
  own four-workflow benchmark, and those workflows were built by their team.
  Run it on reversible tasks.
- **It needs Accessibility permission**, which macOS grants to the terminal or
  editor that launches it, not to this package. Without the grant, every read
  returns empty and the agent sees a blank screen.
- **Icon-only buttons are invisible to it.** So is anything without an
  accessibility label, without a bounding box, or scrolled off the display.
- **It cannot write text.** Composed text is supplied as input; the model only
  selects.

## Licence

MIT. Not affiliated with, endorsed by, or sponsored by TypeSafe AI.
