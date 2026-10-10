"""Validator library: the only validators a typed team may use (paper Table 4).

Each entry declares
  * strength: "form" (parses / compiles / conforms) or "behaviour" (executes
    the value against an oracle or test);
  * tools: what the owning agent must have (tau(b), condition W6);
  * params: typed arguments. A parameter is a *read* (its data flows into
    the check, so it contributes check edges to the feature-level data-flow
    graph of W4, and its values enter the version stamp) or a *locator*
    (it only says which goal object the value belongs to). For an object
    parameter, `reads` lists the feature paths of that object the validator
    inspects, e.g. test_valid reads m.signature, m.examples.call and
    m.examples.expected.

Runtime implementations take the candidate value, the target object that
will hold it, and the evaluated arguments. They execute code only through a
benchmark-agnostic Workbench (assemble, run examples, run tests), set per
run in a RunContext; behaviour validators run on the assembled artefact,
with methods that have no accepted value yet replaced by stubs.
"""
from __future__ import annotations

import ast
import contextvars
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from agenthot.engine.validators import Rejected

TOOLS = ("exec", "search")  # the declared tool vocabulary (W6 is relative to it)


ALL = "*"  # reads of an object parameter: its attributes and those one many-valued reference away


@dataclass(frozen=True)
class Param:
    type: str  # "string" | "@goal.Method" (a goal object of that class) | "@goal.*" (any goal object)
    mode: str  # "read" | "locator"
    # object params: feature paths read, relative to the argument, per goal
    # class ("*": any other class); a string param reads its own value
    reads: tuple = ()
    optional: bool = False

    def reads_for(self, cls_name: str) -> tuple | str:
        for k, v in self.reads:
            if k == cls_name:
                return v
        for k, v in self.reads:
            if k == "*":
                return v
        return ()


@dataclass(frozen=True)
class ValidatorSpec:
    id: str
    strength: str  # "form" | "behaviour"
    tools: tuple[str, ...]
    params: dict[str, Param]
    doc: str
    offered: bool = True  # False: fault injection only, never offered to a builder


_M = "@goal.Method"
_ANY = "@goal.*"
_TEST_READS = (("Method", ("signature", "examples.call", "examples.expected")), ("*", ALL))
VLIB: dict[str, ValidatorSpec] = {
    s.id: s
    for s in [
        ValidatorSpec("nonempty", "form", (), {}, "v is a non-empty string"),
        ValidatorSpec("compiles", "form", (), {}, "v parses and compiles as Python"),
        ValidatorSpec("json", "form", (), {}, "v is a JSON document"),
        ValidatorSpec("defines", "form", (), {"method": Param(_ANY, "locator")},
                      "v defines a function with the method's name"),
        ValidatorSpec("smoke_test", "behaviour", ("exec",), {"body": Param("string", "read")},
                      "v is the outcome (PASS or FAIL) of a fixed smoke test of the class assembled with body b"),
        ValidatorSpec("test_valid", "behaviour", ("exec",),
                      {"method": Param(_ANY, "read", _TEST_READS)},
                      "v is test code that runs, fails on a stub of the method, and asserts every example of it"),
        ValidatorSpec("passes_tests", "behaviour", ("exec",),
                      {"tests": Param("string", "read"), "method": Param(_ANY, "locator", optional=True)},
                      "the assembled class with v passes the test code c"),
        ValidatorSpec("examples_run", "behaviour", ("exec",),
                      {"method": Param(_M, "read", (("Method", ("examples.call", "examples.expected")),))},
                      "the assembled class with v passes the documented examples of the method"),
        # fault injection only (RQ4); never offered to a builder
        ValidatorSpec("unsat", "behaviour", (), {}, "rejects every value", offered=False),
        ValidatorSpec("flaky", "behaviour", (), {}, "accepts at random", offered=False),
        ValidatorSpec("weak", "form", (), {}, "accepts every value", offered=False),
    ]
}

# Library clauses of the acceptance predicate (W5 admits them besides the
# four engine clauses): evaluated by the engine on the deliverable.
LIBRARY_CLAUSES = {"examples_pass": "the assembled deliverable passes every public example"}


def catalogue() -> str:
    lines = []
    for s in VLIB.values():
        if not s.offered:
            continue
        ps = []
        for k, p in s.params.items():
            t = {"string": "a string attribute path", _M: "a Goal!Method object (e.g. m or d.method)"}.get(p.type, "a Goal object")
            ps.append(f"{k}: {t}{' (optional)' if p.optional else ''}")
        reads = []
        for k, p in s.params.items():
            if p.mode != "read":
                continue
            r = p.reads_for("Method") if p.type != "string" else ()
            reads += [f"{k}.{x}" for x in r] if isinstance(r, tuple) and r else [k]
        tools = f"; needs tools {list(s.tools)}" if s.tools else ""
        rd = f"; reads {', '.join(reads)}" if reads else ""
        lines.append(f"- {s.id}({', '.join(ps)}) [{s.strength}{tools}{rd}]: {s.doc}")
    return "\n".join(lines)


def tools_needed(validator_ids) -> set[str]:
    out: set[str] = set()
    for v in validator_ids:
        if v in VLIB:
            out |= set(VLIB[v].tools)
    return out


# --------------------------------------------------------------------------
# runtime
# --------------------------------------------------------------------------


class Workbench(Protocol):
    """What a domain adapter provides to validators."""

    def method_names(self) -> list[str]: ...
    def current_bodies(self) -> dict[str, str]: ...  # accepted deliverable values so far
    def assemble(self, bodies: dict[str, str]) -> str: ...
    def run_examples(self, code: str, only: list[str]) -> dict: ...
    def run_tests(self, code: str, tests: str) -> dict: ...
    def extract_function(self, text: str, name: str) -> str | None: ...
    def extract_code(self, text: str) -> str: ...
    def bind(self, bodies: Callable[[], dict[str, str]]) -> None: ...  # deliverable values provider


@dataclass
class RunContext:
    bench: Workbench
    rng: random.Random = field(default_factory=lambda: random.Random(0))
    exec_calls: int = 0
    exec_seconds: float = 0.0
    # target_key/binding -> last rejected value (used to submit best effort)
    last_rejected: dict[str, str] = field(default_factory=dict)
    # "<goal name>|<validator id>" -> last rejected value (attribution)
    last_rejected_any: dict[str, str] = field(default_factory=dict)
    # goal Method objects by name (set by the session; locator fallback)
    goal_methods: dict[str, Any] = field(default_factory=dict)


CURRENT: contextvars.ContextVar[RunContext | None] = contextvars.ContextVar("am2m_run_ctx", default=None)


def _ctx() -> RunContext:
    ctx = CURRENT.get()
    if ctx is None:
        raise RuntimeError("no AutoM2M RunContext set")
    return ctx


def is_goal(obj: Any) -> bool:
    return getattr(getattr(obj, "eClass", None), "_amt_goal", False)


def goal_method_of(target: Any, explicit: Any = None) -> Any:
    """The goal Method a value belongs to: the explicit locator argument, or
    the goal Method the target object references (directly or through one
    reference), or the only Method of a function-level task."""
    if explicit is not None and hasattr(explicit, "eClass"):
        return explicit
    if target is not None and hasattr(target, "eClass"):
        seen = []
        for f in target.eClass.eAllStructuralFeatures():
            if f.is_reference:
                v = getattr(target, f.name, None)
                if hasattr(v, "eClass"):
                    if is_goal(v) and v.eClass.name == "Method":
                        return v
                    seen.append(v)
        for v in seen:
            for f in v.eClass.eAllStructuralFeatures():
                if f.is_reference:
                    w = getattr(v, f.name, None)
                    if hasattr(w, "eClass") and is_goal(w) and w.eClass.name == "Method":
                        return w
    ctx = _ctx()
    if len(ctx.goal_methods) == 1:
        return next(iter(ctx.goal_methods.values()))
    names = ctx.bench.method_names()
    if len(names) == 1:
        return ctx.goal_methods.get(names[0], names[0])
    return None


def _name(method: Any) -> str:
    return str(getattr(method, "name", method))


def _exec(fn: Callable[[], dict]) -> dict:
    ctx = _ctx()
    ctx.exec_calls += 1
    t0 = time.perf_counter()
    try:
        return fn()
    finally:
        ctx.exec_seconds += time.perf_counter() - t0


def _fn(value: str, method: Any) -> str | Rejected:
    if method is None:
        return Rejected("cannot tell which method this value implements")
    fn = _ctx().bench.extract_function(value, _name(method))
    if fn is None:
        return Rejected(f"answer must contain a Python definition `def {_name(method)}(...)` in a ```python block")
    return fn


def _spliced(method: Any, fn: str) -> str:
    ctx = _ctx()
    bodies = dict(ctx.bench.current_bodies())
    bodies[_name(method)] = fn
    return ctx.bench.assemble(bodies)


def v_nonempty(value: Any, target: Any = None) -> bool | Rejected:
    return bool(str(value or "").strip()) or Rejected("empty answer")


def v_compiles(value: Any, target: Any = None) -> bool | Rejected:
    code = _ctx().bench.extract_code(str(value or ""))
    if not code.strip():
        return Rejected("no code found")
    try:
        compile(code, "<value>", "exec")
        return True
    except (SyntaxError, ValueError) as exc:
        return Rejected(f"does not compile: {exc}")


def v_json(value: Any, target: Any = None) -> bool | Rejected:
    import json

    text = str(value or "")
    try:
        json.loads(_ctx().bench.extract_code(text) if "```" in text else text)
        return True
    except Exception as exc:  # noqa: BLE001
        return Rejected(f"not valid JSON: {exc}")


def v_defines(value: Any, target: Any = None, method: Any = None) -> bool | Rejected:
    fn = _fn(str(value), goal_method_of(target, method))
    return fn if isinstance(fn, Rejected) else True


SMOKE = r'''
import json
SRC = {src!r}
ENTRY = {entry!r}
out = "PASS"
try:
    g = {{"__name__": "smoke"}}
    exec(compile(SRC, "solution.py", "exec"), g)
    obj = g.get(ENTRY)
    if obj is None:
        out = "FAIL: %s is not defined" % ENTRY
    elif isinstance(obj, type):
        try:
            obj()
        except TypeError:
            pass  # the constructor needs arguments: loading is the smoke test
except BaseException as e:
    out = "FAIL: %s: %s" % (type(e).__name__, str(e)[:200])
print("{mark}" + json.dumps({{"outcome": out}}))
'''


def v_smoke_test(value: Any, target: Any = None, body: Any = None) -> bool | Rejected:
    """v must be the outcome of a fixed smoke test (load the module, construct
    the class) of the artefact assembled with body b."""
    from agenthot.sandbox import RESULT_MARK, run_python_json

    ctx = _ctx()
    code = ctx.bench.extract_code(str(body or ""))
    name = None
    try:
        name = next((n.name for n in ast.parse(code).body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))), None)
    except SyntaxError:
        pass
    bodies = dict(ctx.bench.current_bodies())
    if name:
        bodies[name] = code
    src = ctx.bench.assemble(bodies)
    entry = getattr(getattr(ctx.bench, "task", None), "entry", "") or ""
    res = _exec(lambda: (run_python_json(SMOKE.format(src=src, entry=entry, mark=RESULT_MARK), timeout=20)[0] or {}))
    outcome = str(res.get("outcome", "FAIL: crashed"))
    said = str(value or "").strip().upper()
    if said.startswith(outcome.split(":")[0]):
        return True
    return Rejected(f"the smoke test says {outcome[:120]!r}, the value says {str(value)[:60]!r}")


def _literals(code: str) -> list[Any]:
    out: list[Any] = []
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if isinstance(node, (ast.Constant, ast.List, ast.Tuple, ast.Dict, ast.Set, ast.UnaryOp, ast.BinOp)):
            try:
                out.append(ast.literal_eval(node))
            except Exception:  # noqa: BLE001
                continue
    return out


def _norm(s: str) -> str:
    return "".join(ch for ch in str(s) if not ch.isspace()).replace('"', "'")


def asserts_example(code: str, call: str, expected: str, literals: list | None = None) -> bool:
    """Does test code assert this example? Its expected value (or, for an
    exception, the exception's name) must appear in the tests as a literal."""
    exp = (expected or "").strip()
    if not exp:
        return True  # a statement example has nothing to assert
    if exp.startswith("Traceback") or exp.splitlines()[-1].strip().split(":")[0].endswith(("Error", "Exception")):
        exc = exp.splitlines()[-1].strip().split(":")[0].split(".")[-1]
        return exc in code
    lits = _literals(code) if literals is None else literals
    try:
        val = ast.literal_eval(exp)
        if any(type(x) is type(val) and x == val for x in lits) or any(x == val and not isinstance(x, bool) for x in lits):
            return True
    except Exception:  # noqa: BLE001
        pass
    return _norm(exp) in _norm(code)


_SETUP_ERRORS = {"NameError", "ModuleNotFoundError", "ImportError", "SyntaxError", "IndentationError",
                 "FileNotFoundError", "UnboundLocalError"}


def v_test_valid(value: Any, target: Any = None, method: Any = None) -> bool | Rejected:
    ctx = _ctx()
    method = goal_method_of(target, method)
    name = _name(method)
    code = ctx.bench.extract_code(str(value))
    try:
        ast.parse(code)
    except SyntaxError as exc:
        return Rejected(f"test code has a SyntaxError: {exc}")
    if name not in code and not (name.startswith("__") and name.endswith("__")):  # dunders run via operators
        return Rejected(f"tests never call `{name}`")
    # it asserts every example of the method
    lits = _literals(code)
    for ex in list(getattr(method, "examples", []) or []):
        if not asserts_example(code, ex.call, ex.expected, lits):
            return Rejected(f"tests do not assert the documented example `{ex.call.strip()[:80]}` -> {ex.expected.strip()[:60]}")
    # it runs, and fails on a stub of the method (other methods as accepted so far)
    bodies = dict(ctx.bench.current_bodies())
    bodies[name] = f"def {name}(*args, **kwargs):\n    raise NotImplementedError('stub')"
    res = _exec(lambda: ctx.bench.run_tests(ctx.bench.assemble(bodies), code))
    if res.get("error"):
        return Rejected(f"test code does not load: {res['error'][:300]}")
    if res.get("n", 0) == 0:
        return Rejected("no test found: write unittest.TestCase classes or functions named test_*")
    if not res.get("errors"):
        return Rejected("tests pass on a stub implementation; they check nothing")
    # they must fail because of the stub, not crash on their own setup
    crash = next((e for e in res["errors"] if e.split(":")[0].strip().split(".")[-1] in _SETUP_ERRORS), None)
    if crash:
        return Rejected(f"tests crash on their own setup ({crash[:160]}); create every object the examples use, "
                        "with the class's real name")
    return True


def v_passes_tests(value: Any, target: Any = None, tests: Any = None, method: Any = None) -> bool | Rejected:
    method = goal_method_of(target, method)
    fn = _fn(str(value), method)
    if isinstance(fn, Rejected):
        return fn
    ctx = _ctx()
    tests_code = ctx.bench.extract_code(str(tests or ""))
    if not tests_code.strip():
        return Rejected("no tests available to check against")
    res = _exec(lambda: ctx.bench.run_tests(_spliced(method, fn), tests_code))
    if res.get("error"):
        return Rejected(f"tests could not run: {res['error'][:300]}")
    if res.get("errors"):
        return Rejected("failing tests: " + " | ".join(res["errors"][:3]))
    return True


def v_examples_run(value: Any, target: Any = None, method: Any = None) -> bool | Rejected:
    method = goal_method_of(target, method)
    fn = _fn(str(value), method)
    if isinstance(fn, Rejected):
        return fn
    ctx = _ctx()
    res = _exec(lambda: ctx.bench.run_examples(_spliced(method, fn), [_name(method)]))
    if res.get("error"):
        return Rejected(f"code does not load: {res['error'][:300]}")
    if res.get("failed"):
        f = res["failed"][0]
        return Rejected(f"example `{f['example']}` expected {f['expected']!r}, got {f['got']!r}")
    return True


def v_unsat(value: Any, *args: Any) -> Rejected:
    return Rejected("validator cannot be satisfied")


def v_flaky(value: Any, *args: Any) -> bool | Rejected:
    return True if _ctx().rng.random() < 0.5 else Rejected("nondeterministic check failed")


def v_weak(value: Any, *args: Any) -> bool:
    return True


IMPLEMENTATIONS: dict[str, Callable[..., Any]] = {
    "nonempty": v_nonempty,
    "compiles": v_compiles,
    "json": v_json,
    "defines": v_defines,
    "smoke_test": v_smoke_test,
    "test_valid": v_test_valid,
    "passes_tests": v_passes_tests,
    "examples_run": v_examples_run,
    "unsat": v_unsat,
    "flaky": v_flaky,
    "weak": v_weak,
}
