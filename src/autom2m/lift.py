"""Component 1, the Lifter: a deterministic text-to-model injection of the task
into the goal model M0, conforming to the goal view MM0 (paper Sec. 3.1-3.2,
Listing 3).

    package Goal {
      class Task    { name, description, outline;
                      methods [1-*] container : Method;
                      examples [0-*] : Example }            -- in docstring order
      class Method  { name, signature, docstring;
                      task : Task; examples [0-*] container : Example }
      class Example { call, expected } }

Doctests are moved out of `docstring` into `Example` objects, so a rule that
reads a docstring does not see the examples (the footprint must name them).
`outline` is the class (or function) outline without any doctest, the frame
into which the runtime inserts deliverable values.

The builder cannot change MM0 or M0: `normalize` overwrites whatever goal
view a proposal declares with this one.
"""
from __future__ import annotations

import copy
import doctest
import re
from typing import Any, Protocol

GOAL_VIEW_NAME = "Goal"
GOAL_VIEW: dict = {
    "classes": {
        "Task": {
            "attributes": {"name": "string", "description": "string", "outline": "string"},
            "references": {"methods": {"type": "Goal.Method", "many": True, "required": True},
                           "examples": {"type": "Goal.Example", "many": True}},
        },
        "Method": {
            "attributes": {"name": "string", "signature": "string", "docstring": "string"},
            "references": {"task": {"type": "Goal.Task", "required": True},
                           "examples": {"type": "Goal.Example", "many": True}},
        },
        "Example": {"attributes": {"call": "string", "expected": "string"}},
    }
}


class TaskLike(Protocol):
    entry: str
    description: str
    prompt: str
    methods: list


# --------------------------------------------------------------------------
# docstrings
# --------------------------------------------------------------------------

_PARSER = doctest.DocTestParser()


def split_doctests(docstring: str) -> tuple[str, list[tuple[str, str]]]:
    """(docstring without doctest examples, [(call, expected), ...])."""
    text = docstring or ""
    try:
        parts = _PARSER.parse(text)
    except ValueError:
        return text.strip(), []
    prose, examples = [], []
    for p in parts:
        if isinstance(p, doctest.Example):
            examples.append((p.source.rstrip("\n"), p.want.rstrip("\n")))
        else:
            prose.append(p)
    clean = "".join(prose)
    clean = re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", clean)
    return clean.strip(), examples


def strip_doctests(source: str) -> str:
    """The task outline: the source with every doctest (`>>>` lines, `...`
    continuations and expected output) removed from its docstrings."""
    import ast

    try:
        tree = ast.parse(source or "")
    except SyntaxError:
        return _strip_doctest_lines(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(getattr(first, "value", None), ast.Constant) \
                    and isinstance(first.value.value, str):
                first.value.value = split_doctests(first.value.value)[0]
    return ast.unparse(tree).rstrip() + "\n"


def _strip_doctest_lines(source: str) -> str:
    """Fallback for sources that do not parse: drop each `>>>` line, its `...`
    continuations and the expected-output lines up to the next blank line,
    docstring end or parameter line."""
    out, in_example = [], False
    for line in (source or "").splitlines():
        s = line.strip()
        if s.startswith(">>>"):
            in_example = True
            continue
        if in_example:
            ends = s == "" or s.startswith((":", '"""', "'''", "def ", "class ", "@")) or s in ('"""', "'''")
            if not ends:
                continue  # continuation or expected output
            in_example = False
        out.append(line)
    return re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", "\n".join(out)).rstrip() + "\n"


# --------------------------------------------------------------------------
# the injection
# --------------------------------------------------------------------------


def goal_view() -> dict:
    return copy.deepcopy(GOAL_VIEW)


def lift_task(task: TaskLike, goal_mm: Any) -> Any:
    """Deterministic Lift: the task's public material becomes the goal model
    M0 (one Task, one Method per method to implement, one Example per
    doctest example, numbered E<i>.<j> in docstring order)."""
    root = goal_mm.get(f"{GOAL_VIEW_NAME}Root")()
    t = goal_mm.new("Task", name=task.entry, description=task.description or "",
                    outline=strip_doctests(task.prompt))
    root.all_Task.append(t)
    for i, m in enumerate(task.methods, 1):
        doc, examples = split_doctests(m.docstring)
        mo = goal_mm.new("Method", name=m.name, signature=m.signature, docstring=doc)
        mo.task = t
        root.all_Method.append(mo)
        t.methods.append(mo)
        for j, (call, expected) in enumerate(examples, 1):
            ex = goal_mm.new("Example", call=call, expected=expected)
            ex._amt_target_key = f"E{i}.{j}"
            root.all_Example.append(ex)
            mo.examples.append(ex)
            t.examples.append(ex)
    return root


def normalize(team_json: dict) -> dict:
    """The goal view is owned by Lift: always the fixed metamodel MM0."""
    t = dict(team_json or {})
    t["goal_view"] = GOAL_VIEW_NAME
    views = dict(t.get("views") or {}) if isinstance(t.get("views"), dict) else {}
    views[GOAL_VIEW_NAME] = goal_view()
    t["views"] = views
    return t
