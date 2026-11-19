"""Processing times: how long each step of a run and a report took.

    t = Timings()
    with t.step("Read", "Point cloud", "cloud.laz"):
        ...
    t.note("93.6 M points")  # adds to the last step's detail
    print(t.table())

The run (pipeline.run) and the report (report.build_html, write_pdf) record
into one Timings, so the report can list every step and the total.
Steps are grouped: "Read" (files), "Compute" (checks and maths), "Draw"
(maps, charts, snapshots, tables) and "Write" (exports).
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass, field

GROUPS = ("Read", "Compute", "Draw", "Write")


@dataclass
class Step:
    group: str  # one of GROUPS
    name: str
    seconds: float
    detail: str = ""
    phase: str = "run"  # "run", "report" or "export": a rebuilt report replaces its phase's steps


@dataclass
class Timings:
    steps: list[Step] = field(default_factory=list)
    phase: str = "run"  # given to the steps recorded from now on

    @contextmanager
    def step(self, group: str, name: str, detail: str = ""):
        start = time.perf_counter()
        s = Step(group, name, 0.0, detail, self.phase)
        self.steps.append(s)
        try:
            yield s
        finally:
            s.seconds = time.perf_counter() - start

    def add(self, group: str, name: str, seconds: float, detail: str = "") -> None:
        """A step timed elsewhere."""
        self.steps.append(Step(group, name, seconds, detail, self.phase))

    def note(self, detail: str) -> None:
        if self.steps:
            last = self.steps[-1]
            last.detail = f"{last.detail} · {detail}" if last.detail else detail

    def start_phase(self, phase: str) -> None:
        """Record the next steps as phase, forgetting that phase's earlier steps (a rebuilt report or a
        second export replaces the first)."""
        self.steps = [s for s in self.steps if s.phase != phase]
        self.phase = phase

    @property
    def total(self) -> float:
        return sum(s.seconds for s in self.steps)

    def by_group(self) -> dict[str, float]:
        out = dict.fromkeys(GROUPS, 0.0)
        for s in self.steps:
            out[s.group] = out.get(s.group, 0.0) + s.seconds
        return {g: v for g, v in out.items() if v > 0}

    def table(self) -> str:
        """Plain text, for the command line and the log."""
        if not self.steps:
            return ""
        width = max(len(s.name) for s in self.steps)
        lines = [f"{s.group:8} {s.name:{width}}  {fmt(s.seconds):>8}  {s.detail}".rstrip() for s in self.steps]
        lines.append(f"{'Total':8} {'':{width}}  {fmt(self.total):>8}")
        return "\n".join(lines)


def fmt(seconds: float) -> str:
    if seconds < 1:
        return f"{seconds * 1000:.0f} ms"
    if seconds < 60:
        return f"{seconds:.1f} s"
    m, s = divmod(round(seconds), 60)
    return f"{m} min {s:02d} s"
