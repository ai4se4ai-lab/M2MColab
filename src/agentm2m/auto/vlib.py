"""Validator library: the only validators a typed team may use.

Each entry declares
  * strength: "form" (parses / compiles / conforms) or "behaviour" (executes
    the value against an oracle or test);
  * tools: what the owning agent must have (τ, condition W6);
  * params: typed arguments, each either a *read* (its value flows into the
    check, so it contributes edges to the feature-level data-flow graph
    used by W4) or a *locator* (it only says which goal element the value
    belongs to).

Runtime implementations receive the sampled value plus the evaluated
argument values, and consult a benchmark-agnostic `RunContext` (set per
run) to assemble and execute code.
"""
from __future__ import annotations

import ast
import contextvars
import random
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from ..engine.validators import Rejected


@dataclass(frozen=True)
class Param:
    type: str  # "string" or "@goal.<Class>" (a goal-view class) or "object"
    mode: str  # "read" | "locator"


@dataclass(frozen=True)
class ValidatorSpec:
    id: str
    strength: str  # "form" | "behaviour"
    tools: tuple[str, ...]
    params: dict[str, Param]
    doc: str
    offered: bool = True  # False: used only by fault injection


VLIB: dict[str, ValidatorSpec] = {
    s.id: s
    for s in [
        ValidatorSpec("nonempty", "form", (), {}, "value is non-empty text"),
        ValidatorSpec("json", "form", (), {}, "value is a JSON document"),
        ValidatorSpec("compiles", "form", (), {}, "value contains Python code that parses"),
        ValidatorSpec("defines", "form", (), {"method": Param("@goal.*", "locator")},
                      "value defines a function with the method's name"),
        ValidatorSpec("runs", "behaviour", ("exec",), {}, "value is Python code that executes without raising"),
        ValidatorSpec("examples_run", "behaviour", ("exec",), {"method": Param("@goal.*", "read")},
                      "the method, spliced into the class, runs the method's public docstring examples without raising"),
        ValidatorSpec("examples_match", "behaviour", ("exec",), {"method": Param("@goal.*", "read")},
                      "as examples_run, and every printed result equals the documented one"),
        ValidatorSpec("passes_tests", "behaviour", ("exec",),
                      {"method": Param("@goal.*", "locator"), "tests": Param("string", "read")},
                      "the method, spliced into the class, passes the given test code"),
        ValidatorSpec("test_valid", "behaviour", ("exec",), {"method": Param("@goal.*", "locator")},
                      "value is test code that calls the method and fails on a stub implementation"),
        # fault-injection only (RQ3); never offered to a builder
        ValidatorSpec("unsat", "behaviour", (), {}, "rejects every value", offered=False),
        ValidatorSpec("flaky", "behaviour", (), {}, "accepts at random", offered=False),
        ValidatorSpec("weak", "form", (), {}, "accepts every value", offered=False),
    ]
}


def catalogue() -> str:
    lines = []
    for s in VLIB.values():
        if not s.offered:
            continue
        ps = ", ".join(f"{k}: {p.type.replace('@goal.*', 'a Goal object')}" for k, p in s.params.items())
        tools = f" needs tools {list(s.tools)}" if s.tools else ""
        lines.append(f"- {s.id}({ps}) [{s.strength}]{tools}: {s.doc}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# runtime
# --------------------------------------------------------------------------


class Workbench(Protocol):
    """What a benchmark adapter provides to validators."""

    def method_names(self) -> list[str]: ...
    def current_bodies(self) -> dict[str, str]: ...  # accepted deliverable values so far
    def assemble(self, bodies: dict[str, str]) -> str: ...
    def run_examples(self, code: str, only: list[str]) -> dict: ...
    def run_tests(self, code: str, tests: str) -> dict: ...
    def extract_function(self, text: str, name: str) -> str | None: ...
    def extract_code(self, text: str) -> str: ...


@dataclass
class RunContext:
    bench: Workbench
    rng: random.Random = field(default_factory=lambda: random.Random(0))
    exec_calls: int = 0
    # target_key/binding -> last rejected value (used to submit best effort)
    last_rejected: dict[str, str] = field(default_factory=dict)
    # "<goal name>|<validator id>" -> last rejected value (attribution)
    last_rejected_any: dict[str, str] = field(default_factory=dict)


CURRENT: contextvars.ContextVar[RunContext | None] = contextvars.ContextVar("am2m_run_ctx", default=None)


def _ctx() -> RunContext:
    ctx = CURRENT.get()
    if ctx is None:
        raise RuntimeError("no AutoM2M RunContext set")
    return ctx


def _name(method: Any) -> str:
    return str(getattr(method, "name", method))


def _fn(value: str, method: Any) -> str | Rejected:
    ctx = _ctx()
    fn = ctx.bench.extract_function(value, _name(method))
    if fn is None:
        return Rejected(f"answer must contain a Python definition `def {_name(method)}(...)` in a ```python block")
    return fn


def v_nonempty(value: Any) -> bool | Rejected:
    return bool(str(value or "").strip()) or Rejected("empty answer")


def v_json(value: Any) -> bool | Rejected:
    import json

    try:
        json.loads(_ctx().bench.extract_code(str(value)) if "```" in str(value) else str(value))
        return True
    except Exception as exc:  # noqa: BLE001
        return Rejected(f"not valid JSON: {exc}")


def v_compiles(value: Any) -> bool | Rejected:
    code = _ctx().bench.extract_code(str(value))
    try:
        ast.parse(code)
        return bool(code.strip()) or Rejected("no code found")
    except SyntaxError as exc:
        return Rejected(f"SyntaxError: {exc}")


def v_defines(value: Any, method: Any) -> bool | Rejected:
    fn = _fn(str(value), method)
    return fn if isinstance(fn, Rejected) else True


def _spliced(method: Any, fn: str) -> str:
    ctx = _ctx()
    bodies = dict(ctx.bench.current_bodies())
    bodies[_name(method)] = fn
    return ctx.bench.assemble(bodies)


def v_runs(value: Any) -> bool | Rejected:
    ctx = _ctx()
    ctx.exec_calls += 1
    res = ctx.bench.run_tests(ctx.bench.extract_code(str(value)), "")
    return True if not res.get("error") else Rejected(f"code raised: {res['error'][:300]}")


def v_examples_run(value: Any, method: Any) -> bool | Rejected:
    fn = _fn(str(value), method)
    if isinstance(fn, Rejected):
        return fn
    ctx = _ctx()
    ctx.exec_calls += 1
    res = ctx.bench.run_examples(_spliced(method, fn), [_name(method)])
    if res.get("error"):
        return Rejected(f"code does not load: {res['error'][:300]}")
    # a crash is an exception the documentation does not announce (setup NameErrors in informal
    # examples are ignored; examples whose expected output is a traceback expect the exception)
    crashes = [f for f in res.get("failed", []) if f["got"].startswith("EXC") and "NameError" not in f["got"]
               and "Traceback" not in f.get("expected", "") and "Error" not in f.get("expected", "")]
    if crashes:
        f = crashes[0]
        return Rejected(f"example `{f['example']}` raised {f['got'][4:200]}")
    return True


def v_examples_match(value: Any, method: Any) -> bool | Rejected:
    fn = _fn(str(value), method)
    if isinstance(fn, Rejected):
        return fn
    ctx = _ctx()
    ctx.exec_calls += 1
    res = ctx.bench.run_examples(_spliced(method, fn), [_name(method)])
    if res.get("error"):
        return Rejected(f"code does not load: {res['error'][:300]}")
    if res.get("failed"):
        f = res["failed"][0]
        return Rejected(f"example `{f['example']}` expected {f['expected']!r}, got {f['got']!r}")
    return True


def v_passes_tests(value: Any, method: Any, tests: Any) -> bool | Rejected:
    fn = _fn(str(value), method)
    if isinstance(fn, Rejected):
        return fn
    ctx = _ctx()
    tests_code = ctx.bench.extract_code(str(tests or ""))
    if not tests_code.strip():
        return Rejected("no tests available to check against")
    ctx.exec_calls += 1
    res = ctx.bench.run_tests(_spliced(method, fn), tests_code)
    if res.get("error"):
        return Rejected(f"tests could not run: {res['error'][:300]}")
    if res.get("errors"):
        return Rejected("failing tests: " + " | ".join(res["errors"][:3]))
    return True


def v_test_valid(value: Any, method: Any) -> bool | Rejected:
    ctx = _ctx()
    code = ctx.bench.extract_code(str(value))
    try:
        ast.parse(code)
    except SyntaxError as exc:
        return Rejected(f"test code has a SyntaxError: {exc}")
    name = _name(method)
    if name not in code and not (name.startswith("__") and name.endswith("__")):  # dunders are used via operators
        return Rejected(f"tests never call `{name}`")
    ctx.exec_calls += 1
    # all methods as non-pending stubs: a meaningful test must fail on them
    stubbed = ctx.bench.assemble({m: f"def {m}(self, *args, **kwargs):\n    raise NotImplementedError('stub')"
                                  for m in ctx.bench.method_names() if m != "__init__"} if len(ctx.bench.method_names()) > 1
                                 else {name: f"def {name}(*args, **kwargs):\n    raise NotImplementedError('stub')"})
    res = ctx.bench.run_tests(stubbed, code)
    if res.get("error"):
        return Rejected(f"test code does not load: {res['error'][:300]}")
    if res.get("n", 0) == 0:
        return Rejected("no test found: write unittest.TestCase classes or functions named test_*")
    if not res.get("errors"):
        return Rejected("tests pass on a stub implementation; they check nothing")
    return True


def v_unsat(value: Any, *args: Any) -> Rejected:
    return Rejected("validator cannot be satisfied")


def v_flaky(value: Any, *args: Any) -> bool | Rejected:
    return True if _ctx().rng.random() < 0.5 else Rejected("nondeterministic check failed")


def v_weak(value: Any, *args: Any) -> bool:
    return True


IMPLEMENTATIONS: dict[str, Callable[..., Any]] = {
    "nonempty": v_nonempty,
    "json": v_json,
    "compiles": v_compiles,
    "defines": v_defines,
    "runs": v_runs,
    "examples_run": v_examples_run,
    "examples_match": v_examples_match,
    "passes_tests": v_passes_tests,
    "test_valid": v_test_valid,
    "unsat": v_unsat,
    "flaky": v_flaky,
    "weak": v_weak,
}
