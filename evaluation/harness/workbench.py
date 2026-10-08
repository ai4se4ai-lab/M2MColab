"""Benchmark adapter that AutoM2M validators execute against (vlib.Workbench)."""
from __future__ import annotations

from typing import Callable

from evaluation.benchmarks import tasks as T


class TaskWorkbench:
    def __init__(self, task: T.Task, bodies: Callable[[], dict[str, str]] | None = None) -> None:
        self.task = task
        self._bodies = bodies or (lambda: {})

    def bind(self, bodies: Callable[[], dict[str, str]]) -> None:
        self._bodies = bodies

    def method_names(self) -> list[str]:
        return [m.name for m in self.task.methods]

    def current_bodies(self) -> dict[str, str]:
        return self._bodies()

    def assemble(self, bodies: dict[str, str]) -> str:
        return T.assemble(self.task, bodies)

    def run_examples(self, code: str, only: list[str]) -> dict:
        return T.run_examples(self.task, code, only)

    def run_tests(self, code: str, tests: str) -> dict:
        return T.run_tests(code, tests)

    def extract_function(self, text: str, name: str) -> str | None:
        return T.extract_function(text, name)

    def extract_code(self, text: str) -> str:
        return T.extract_code(text)
