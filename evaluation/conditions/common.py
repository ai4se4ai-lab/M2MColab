"""Shared pieces of the baseline conditions: the execution tool every
condition gets (public examples + agent-written tests, never hidden tests),
code extraction, and the run record."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from autom2m.pywork import _FENCE  # noqa: F401
from evaluation.benchmarks import tasks as T

MAX_TRANSCRIPT_CHARS = 14000


@dataclass
class RunRecord:
    code: str = ""
    status: str = "done"
    transcript: list[dict] = field(default_factory=list)  # [{"agent", "content", "kind"}]
    team: dict | None = None
    extra: dict = field(default_factory=dict)
    declared_done: bool = False  # the condition's own completion signal


def task_text(task: T.Task) -> str:
    if task.kind == "class":
        return task.prompt
    return task.prompt


def answer_format(task: T.Task) -> str:
    if task.kind == "class":
        return (f"The final answer is the complete class `{task.entry}` (imports, __init__ and every method) "
                "in ONE ```python block.")
    return f"The final answer is the complete function `{task.entry}` (with its imports) in ONE ```python block."


def find_solution(task: T.Task, text: str) -> str | None:
    """A python block that defines the class / function under test."""
    for block in reversed(_FENCE.findall(text or "")):
        if task.kind == "class" and re.search(rf"^\s*class\s+{re.escape(task.entry)}\b", block, re.M):
            if T._parses(block):
                return block
        if task.kind == "function" and re.search(rf"^\s*def\s+{re.escape(task.entry)}\s*\(", block, re.M):
            if T._parses(block):
                return block
    return None


def find_tests(text: str) -> str | None:
    for block in reversed(_FENCE.findall(text or "")):
        if ("unittest" in block or re.search(r"^\s*def test_", block, re.M)) and T._parses(block):
            return block
    return None


def with_imports(task: T.Task, code: str) -> str:
    if not code:
        return ""
    missing = [l for l in task.imports.splitlines() if l.strip() and l.strip() not in code]
    return ("\n".join(missing) + "\n\n" + code) if missing else code


def execute(task: T.Task, code: str | None, tests: str | None = None) -> str:
    """The execution tool: run the public docstring examples and, if given,
    agent-written tests against `code`; return a terminal-style report."""
    if not code:
        return "No complete solution code block found yet."
    full = with_imports(task, code)
    lines = []
    ex = T.run_examples(task, full, [m.name for m in task.methods])
    if ex.get("error"):
        lines.append(f"Loading the code failed: {ex['error'][:400]}")
        return "\n".join(lines)
    lines.append(f"Public examples: {ex['n'] - len(ex['failed'])}/{ex['n']} as documented.")
    for f in ex["failed"][:4]:
        lines.append(f"  example `{f['example']}` expected {f['expected']!r} got {f['got']!r}")
    if tests:
        tr = T.run_tests(full, tests)
        if tr.get("error"):
            lines.append(f"Tests could not run: {tr['error'][:300]}")
        else:
            lines.append(f"Tests: {tr['passed']}/{tr['n']} passed.")
            for e in tr["errors"][:4]:
                lines.append(f"  {e}")
    return "\n".join(lines)


def clip(transcript: list[dict]) -> list[dict]:
    out, total = [], 0
    for m in reversed(transcript):
        total += len(m["content"])
        if total > MAX_TRANSCRIPT_CHARS and out:
            break
        out.append(m)
    return list(reversed(out))


def json_from(text: str) -> dict | None:
    t = (text or "").strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", t, re.S)
    if m:
        t = m.group(1)
    try:
        v = json.loads(t)
        return v if isinstance(v, dict) else None
    except json.JSONDecodeError:
        i, j = t.find("{"), t.rfind("}")
        if 0 <= i < j:
            try:
                v = json.loads(t[i:j + 1])
                return v if isinstance(v, dict) else None
            except json.JSONDecodeError:
                return None
        return None
