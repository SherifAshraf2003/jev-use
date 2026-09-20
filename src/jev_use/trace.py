"""Append-only JSONL trajectory log.

Every step is written before its action runs, so a crash mid-action still leaves
the decision that led to it on disk. That is also why each record is flushed
rather than buffered.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import TracebackType
from typing import Any, Self


def top_n(probabilities: dict[str, float], n: int = 5) -> list[tuple[str, float]]:
    """The n highest-probability options, highest first."""
    return sorted(probabilities.items(), key=lambda item: item[1], reverse=True)[:n]


class TraceWriter:
    """One JSON object per line: `kind` is `step` or `summary`."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("a", encoding="utf-8")

    def _write(self, kind: str, fields: dict[str, Any]) -> None:
        record = {"kind": kind, "ts": time.time(), **fields}
        self._handle.write(json.dumps(record, default=str) + "\n")
        self._handle.flush()

    def write_step(self, **fields: Any) -> None:
        self._write("step", fields)

    def write_summary(self, **fields: Any) -> None:
        self._write("summary", fields)

    def close(self) -> None:
        if not self._handle.closed:
            self._handle.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


def read_trace(path: Path | str) -> list[dict[str, Any]]:
    """Read a JSONL trace back into records, skipping blank lines."""
    return [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def format_trace(records: list[dict[str, Any]]) -> str:
    """Render a trace for a human reading the terminal."""
    lines: list[str] = []
    for record in records:
        if record.get("kind") == "summary":
            lines.append("")
            lines.append("Summary")
            for key, value in record.items():
                if key not in {"kind", "ts"}:
                    lines.append(f"  {key}: {value}")
            continue
        lines.append(
            f"step {record.get('step')}: {record.get('verdict')} — {record.get('action') or '-'}"
        )
        if record.get("reason"):
            lines.append(f"    reason: {record['reason']}")
        for option, probability in record.get("runners_up", []):
            lines.append(f"    {probability:.3f}  {option}")
    return "\n".join(lines)
