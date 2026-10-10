"""Component 5, the Compiler: a synthesis higher-order transformation from an
admitted typed team to the artefacts the AgentHOT runtime executes (paper
Sec. 3.1, step D), which also loads the goal model M0.

  Theta.views     -> pyecore metamodels (metamodel generator), one root per view
  Theta.handoffs  -> generated `.agenthot` rule modules (rule-module generator)
  footprints      -> `fpv(...)`: visible footprint fp_b, validator reads vr_b
                     (stamped, never shown) and the owners of every read
  validators      -> `@check` expressions over the validator library
  task            -> M0 by the Lifter (autom2m.lift)

With `lenient=True` (the Typed-NC ablation: a typed team that was never
checked) ill-typed parts do not stop compilation: an ill-typed stochastic
binding compiles to a footprint that raises, so the engine escalates it
without sampling; an ill-typed structural binding is dropped; an ill-typed
guard never matches.
"""
from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from autom2m.typed_team import TypedTeam, check_paths, is_literal, rule_env, type_path
from autom2m.vlib import VLIB

from .llm.base import LLMBackend
from .metamodel.builder import MetamodelBuilder
from .team.model import Team
from .team.runtime import TeamRuntime

_PRIM = {"string": "string", "str": "string", "int": "int", "integer": "int", "boolean": "boolean", "bool": "boolean"}


class CompileError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# metamodel generator
# --------------------------------------------------------------------------


def build_metamodels(team: TypedTeam) -> dict[str, MetamodelBuilder]:
    mms: dict[str, MetamodelBuilder] = {}
    for v in team.views:
        mms[v] = MetamodelBuilder(v, f"http://autom2m/{team.name}/{v.lower()}")
    for decl in team.classes.values():
        cls = mms[decl.view].eclass(decl.name)
        goal = decl.view == team.goal_view
        cls._amt_goal = goal
        # builder-defined business ids need not be unique, so agents' classes
        # are keyed by the engine's target key; goal classes without a name
        # (Example) by the Lift's E<i>.<j> key
        cls._amt_engine_keyed = (not goal) or not ({"id", "name"} & set(decl.attrs))
    for decl in team.classes.values():
        b = mms[decl.view]
        cls = b.get(decl.name)
        for a in decl.attrs.values():
            b.attribute(cls, a.name, _PRIM.get(a.type, "string"), many=a.many)
        for r in decl.refs.values():
            if r.view not in mms:
                raise CompileError(f"{decl.qname}.{r.name}: unknown view {r.view}")
            try:
                target = mms[r.view].get(r.cls)
            except KeyError:
                raise CompileError(f"{decl.qname}.{r.name}: unknown class {r.view}.{r.cls}") from None
            b.reference(cls, r.name, target, many=r.many, containment=False)
    for v, b in mms.items():
        root = b.eclass(f"{v}Root")
        for decl in team.classes_of(v):
            b.add_root_slot(root, f"all_{decl.name}", decl.name)
    return mms


# --------------------------------------------------------------------------
# rule-module generator
# --------------------------------------------------------------------------


def _expr(path: str) -> str:
    p = path.strip()
    if is_literal(p):
        if p[0] == '"':
            p = "'" + p[1:-1].replace("'", "") + "'"
        return p
    parts = p.split(".")
    if len(parts) == 1:
        return parts[0]
    return f"{parts[0]}.nav('{'.'.join(parts[1:])}')"


_PATH_IN_GUARD = re.compile(r"\b([A-Za-z_]\w*)((?:\.[A-Za-z_]\w*)+)\b")


def _guard(guard: str, vars_: set[str]) -> str:
    def sub(m: re.Match) -> str:
        return _expr(m.group(0)) if m.group(1) in vars_ else m.group(0)

    g = re.sub(r"'[^']*'", lambda m: m.group(0).replace(".", "\x00"), guard)  # protect literals
    g = _PATH_IN_GUARD.sub(sub, g).replace("\x00", ".")
    return g.replace("==", "=").replace("!=", "<>")


FORMAT_CODE = (
    "Answer with the complete Python definition of the method named in the context (signature line and body) "
    "in ONE ```python code block. Do not repeat the class."
)
FORMAT_TESTS = (
    "Answer with Python unittest code in ONE ```python block: a unittest.TestCase class whose tests call the "
    "method named in the context (creating an instance first if it is a method) and assert the concrete expected "
    "values of its documented examples. The code under test is already defined; do not import or redefine it."
)
FORMAT_VERDICT = "Answer with one word: PASS or FAIL."
FORMAT_TEXT = "Answer concisely in plain text (no code unless asked)."


def format_hint(validators) -> str:
    ids = {v.id for v in validators}
    if ids & {"defines", "examples_run", "passes_tests"}:
        return FORMAT_CODE
    if "test_valid" in ids:
        return FORMAT_TESTS
    if "smoke_test" in ids:
        return FORMAT_VERDICT
    if "compiles" in ids:
        return "Answer with Python code in ONE ```python block."
    if "json" in ids:
        return "Answer with one JSON document only."
    return FORMAT_TEXT


@dataclass
class BindingMeta:
    handoff: str
    rule: str
    target_var: str
    feature: str
    owner: str | None
    validators: list
    footprint: list[str]
    check_reads: list[str]
    prompt_key: str
    behaviour: bool
    ill_typed: str = ""


@dataclass
class CompiledTeam:
    typed: TypedTeam
    team: Team
    mms: dict[str, MetamodelBuilder]
    workdir: Path
    registry: dict[str, str]
    bindings: dict[tuple[str, str], BindingMeta]
    handoff_order: list[str]
    rule_texts: dict[str, str] = field(default_factory=dict)
    lenient: bool = False


def handoff_order(team: TypedTeam) -> list:
    """Hand-offs in topological order of the view graph (sources first);
    on a cycle (unchecked teams only) the declaration order breaks it."""
    produced_by = {h.target: h for h in team.handoffs}
    order, seen = [], set()

    def visit(h, stack=()):
        if h.name in seen or h.name in stack:
            return
        for s in h.sources:
            if s in produced_by and produced_by[s] is not h:
                visit(produced_by[s], stack + (h.name,))
        seen.add(h.name)
        order.append(h)

    for h in team.handoffs:
        visit(h)
    return order


def _owner_expr(team: TypedTeam, path: str, env) -> str:
    """The object(s) owning the location a path reads: the path without its
    last attribute, or the object the path names."""
    p = path.strip()
    if is_literal(p):
        return "''"
    pt = type_path(team, p, env, object_reads_all=False)
    parts = p.split(".")
    if pt.ok and pt.end_kind == "attr" and len(parts) > 1:
        return _expr(".".join(parts[:-1]))
    return _expr(p)


def is_ref_literal_ok(feat, literal: str) -> bool:
    """Can a literal fill this feature (unchecked teams)?"""
    if hasattr(feat, "cls"):
        return False
    t = getattr(feat, "type", "string")
    lit = literal.strip()
    if t == "boolean":
        return lit in ("true", "false")
    if t == "int":
        return lit.lstrip("-").isdigit()
    return True


def binding_prompt(team: TypedTeam, owner: str | None, b) -> str:
    role = team.agents[owner].role if owner in team.agents else ""
    return ((f"You are the {owner}. {role}\n\n" if owner else "") + f"Task: {b.prompt}\n\n" + format_hint(b.validators))


def generate_module(team: TypedTeam, h, registry: dict[str, str], bindings: dict, *, lenient: bool = False) -> str:
    owner = (sorted(team.owner_of(h.target)) or [None])[0]
    srcs = list(h.sources)
    if lenient:  # an unchecked rule may match a view its hand-off forgot to declare
        srcs += [v for r in h.rules for _var, v, _c in r.sources if v not in srcs and v in team.views]
    froms = ", ".join(f"IN_{s} : {s}" for s in srcs)
    lines = [f"module {h.name};", f"create OUT : {h.target} from {froms};", "uses 'helpers.py';"]
    for r in h.rules:
        env = rule_env(r)
        vars_ = set(env)
        src = ", ".join(f"{v} : {view}!{c}" for v, view, c in r.sources)
        guard = ""
        if r.guard:
            ok = all(type_path(team, p, env, object_reads_all=False).ok
                     for p in re.findall(r"\b[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+", re.sub(r"'[^']*'", "''", r.guard))
                     if p.split(".")[0] in vars_)
            guard = f" ({_guard(r.guard, vars_)})" if ok or not lenient else " (false)"
        recv = r.sources[0][0]
        tdecl = team.cls(r.target_view, r.target_cls)
        binds = []
        for f, e in r.bind.items():
            if lenient:
                feat = tdecl.feature(f) if tdecl else None
                if feat is None:
                    continue
                if not is_literal(e):
                    pt = type_path(team, e, env, object_reads_all=False)
                    is_ref = hasattr(feat, "cls")
                    if not pt.ok or (pt.end_kind == "object") != is_ref or (pt.many and not feat.many):
                        continue
                    if is_ref and pt.end_type != f"{feat.view}.{feat.cls}" and feat.view != r.target_view:
                        continue  # a reference to a source-view class must yield that class
                    if not is_ref and pt.end_type != getattr(feat, "type", "string"):
                        continue  # an attribute must receive a value of its own type
                elif not is_ref_literal_ok(feat, e):
                    continue
            binds.append(f"{f} <- {_expr(e)}")
        for b in r.llm:
            key = f"{r.name}.{b.feature}"
            if lenient:
                feat = tdecl.feature(b.feature) if tdecl is not None else None
                if feat is None or hasattr(feat, "cls") or getattr(feat, "many", False):
                    continue  # an unchecked team's LLM value that no attribute can hold is dropped
            registry[key] = binding_prompt(team, owner, b)
            reads = check_paths(team, r, b)
            problems = [p for p in b.footprint + reads if not type_path(team, p, env).ok]
            problems += [f"validator {v.id}" for v in b.validators if v.id not in VLIB]
            ill = ""
            if problems:
                if not lenient:
                    raise CompileError(f"{key}: cannot compile {problems}")
                ill = f"{key} is ill-typed: {', '.join(problems)}"
                fp_expr = f"{recv}.illtyped('{ill.replace(chr(39), '')}')"
            else:
                trip = [f"'{p}', {_expr(p)}, {_owner_expr(team, p, env)}" for p in list(b.footprint) + reads]
                fp_expr = f"{recv}.fpv({len(b.footprint)}, {', '.join(trip)})"
            checks = []
            for v in b.validators:
                spec = VLIB.get(v.id)
                if spec is None:
                    continue
                args = [r.target_var] + [_expr(v.args[p]) for p in spec.params if p in v.args]
                checks.append(f"{b.feature}.v_{v.id}({', '.join(args)})")
            check = " and ".join(checks) or f"{b.feature}.v_nonempty({r.target_var})"
            binds.append(f"{b.feature} <- @llm({recv}.prompt_of('{key}'), {fp_expr})")
            binds.append(f"@check {check}")
            behaviour = any(VLIB.get(v.id) and VLIB[v.id].strength == "behaviour" for v in b.validators)
            bindings[(r.name, b.feature)] = BindingMeta(h.name, r.name, r.target_var, b.feature, owner,
                                                         list(b.validators), list(b.footprint), reads, key,
                                                         behaviour, ill)
        body = ",\n      ".join(binds)
        lines.append(f"rule {r.name} {{\n  from {src}{guard}\n  to {r.target_var} : {r.target_view}!{r.target_cls} (\n      {body} ) }}")
    return "\n".join(lines) + "\n"


def compile_team(team: TypedTeam, task: Any, workdir: Path | None = None, *, lenient: bool = False,
                 lifter: Callable[[Any, MetamodelBuilder], Any] | None = None) -> CompiledTeam:
    """Admitted Theta (+ task) -> metamodels, M0 and rule modules on a Team."""
    if lifter is None:
        from autom2m.lift import lift_task as lifter
    workdir = Path(workdir or tempfile.mkdtemp(prefix="am2m_team_"))
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "helpers.py").write_text("from agenthot.rt_helpers import *  # noqa: F401,F403\n")
    try:
        mms = build_metamodels(team)
    except (KeyError, ValueError) as exc:
        raise CompileError(f"the views do not form metamodels: {exc}") from exc
    tm = Team()
    for v, b in mms.items():
        root = lifter(task, b) if v == team.goal_view else b.get(f"{v}Root")()
        tm.add_view(b, root)
    for a, vs in team.writes.items():
        for v in vs:
            if v in mms:
                tm.add_agent(a, v)
    registry: dict[str, str] = {}
    bindings: dict = {}
    order = handoff_order(team)
    texts = {}
    for h in order:
        if h.target not in mms or any(s not in mms for s in h.sources):
            if lenient:
                continue
            raise CompileError(f"hand-off {h.name} names an undeclared view")
        text = generate_module(team, h, registry, bindings, lenient=lenient)
        path = workdir / f"{h.name}.agenthot"
        path.write_text(text)
        texts[h.name] = text
        tm.add_handoff(h.name, path, h.target)
    ct = CompiledTeam(team, tm, mms, workdir, registry, bindings, [n for n in texts], texts, lenient)
    rt = TeamRuntime(tm, _NullLLM())
    for name in list(texts):
        try:
            rt.module_for(name)
        except Exception as exc:  # noqa: BLE001
            if not lenient:
                raise CompileError(f"hand-off {name} does not compile: {exc}") from exc
            # an unparsable hand-off of an unchecked team simply never runs
            del tm.handoffs[name]
            texts.pop(name)
            ct.handoff_order.remove(name)
    return ct


class _NullLLM(LLMBackend):
    name = "null"

    def generate(self, prompt: str, *, temperature: float = 0.2, **kw) -> str:  # pragma: no cover
        return ""
