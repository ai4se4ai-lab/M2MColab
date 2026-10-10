"""Benchmark adapter that AutoM2M validators execute against (vlib.Workbench)."""
from __future__ import annotations

from collections.abc import Callable

from autom2m.pywork import PyWorkbench
from evaluation.benchmarks import tasks as T


class TaskWorkbench(PyWorkbench):
    """`PyWorkbench` with the benchmark's assembler (ClassEval frames)."""

    def __init__(self, task: T.Task, bodies: Callable[[], dict[str, str]] | None = None) -> None:
        super().__init__(task, bodies, assembler=T.assemble)
