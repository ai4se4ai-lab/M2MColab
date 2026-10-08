"""Step D: compile an admitted typed team onto the unchanged AgentM2M runtime
and run it until the engine-decided acceptance predicate φ holds or the
run stops.

  Θ.views     -> pyecore metamodels (MetamodelBuilder), one root per view
  Θ.handoffs  -> generated `.agentm2m` rule modules (parsed by rules/parser.py)
  validators  -> `@check` expressions over the validator library (vlib)
  task        -> goal-view model by a deterministic Lift (lift_task)
  φ           -> cover(G) ∧ valid ∧ fresh ∧ noObl, evaluated by the engine
"""
from __future__ import annotations

import re
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ..engine.binding import Escalation
from ..engine.expr import eval_expr
from ..engine.helpers_loader import load_helpers
from ..engine.matcher import compute_matches
from ..engine.trace import element_key
from ..llm.base import LLMBackend
from ..metamodel.builder import MetamodelBuilder
from ..rules.ast import StochasticBinding
from ..team.model import Team
from ..team.runtime import TeamRuntime
from . import rt_helpers
from .typed_team import Attr, Ref, TypedTeam, is_literal
from .vlib import VLIB, CURRENT, RunContext

GOAL_VIEW_NAME = "Goal"
GOAL_VIEW = {
    "classes": {
        "Task": {"attributes": {"name": "string", "description": "string", "skeleton": "string"}},
        "Method": {
            "attributes": {"name": "string", "signature": "string", "docstring": "string", "examples": "string?"},
            "references": {"task": {"type": "Goal.Task", "required": True}},
        },
    }
}


class CompileError(RuntimeError):
    pass


class TaskLike(Protocol):
    entry: str
    description: str
    prompt: str
    methods: list


# --------------------------------------------------------------------------
# metamodels
# --------------------------------------------------------------------------

_PRIM = {"string": "string", "str": "string", "int": "int", "integer": "int", "boolean": "boolean", "bool": "boolean"}


def build_metamodels(team: TypedTeam) -> dict[str, MetamodelBuilder]:
    mms: dict[str, MetamodelBuilder] = {}
    for v in team.views:
        mms[v] = MetamodelBuilder(v, f"http://agentm2m/auto/{team.name}/{v.lower()}")
    # pass 1: classes
    for decl in team.classes.values():
        cls = mms[decl.view].eclass(decl.name)
        cls._amt_engine_keyed = decl.view != team.goal_view
    # pass 2: features
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
    # roots
    for v, b in mms.items():
        root = b.eclass(f"{v}Root")
        for decl in team.classes_of(v):
            b.add_root_slot(root, f"all_{decl.name}", decl.name)
    return mms


def lift_task(task: TaskLike, goal_mm: MetamodelBuilder) -> Any:
    """Deterministic Lift: the task's public material becomes the goal model."""
    root = goal_mm.get(f"{GOAL_VIEW_NAME}Root")()
    t = goal_mm.new("Task", name=task.entry, description=task.description or "", skeleton=task.prompt)
    root.all_Task.append(t)
    for m in task.methods:
        mo = goal_mm.new("Method", name=m.name, signature=m.signature, docstring=m.docstring, examples=m.examples or "")
        mo.task = t
        root.all_Method.append(mo)
    return root


# --------------------------------------------------------------------------
# rule generation
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
        if m.group(1) in vars_:
            return _expr(m.group(0))
        return m.group(0)

    g = re.sub(r"'[^']*'", lambda m: m.group(0).replace(".", "\x00"), guard)  # protect literals
    g = _PATH_IN_GUARD.sub(sub, g).replace("\x00", ".")
    return g.replace("==", "=").replace("!=", "<>")


FORMAT_CODE = (
    "Answer with the complete Python definition of the method `{name}` (signature line and body) "
    "in ONE ```python code block. Do not repeat the class."
)
FORMAT_TESTS = (
    "Answer with Python unittest code in ONE ```python block: a unittest.TestCase class whose tests call `{name}` "
    "(creating an instance first if it is a method) and assert concrete expected values derived from the "
    "documentation. The code under test is already defined; do not import it or redefine it."
)
FORMAT_TEXT = "Answer concisely in plain text (no code unless asked)."


def _format_for(validators) -> str:
    ids = {v.id for v in validators}
    if ids & {"defines", "examples_run", "examples_match", "passes_tests"}:
        return FORMAT_CODE
    if "test_valid" in ids:
        return FORMAT_TESTS
    if ids & {"compiles", "runs"}:
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
    prompt_key: str
    behaviour: bool


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


def _handoff_order(team: TypedTeam) -> list:
    """Hand-offs in topological order of the view graph (sources first)."""
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


def generate_module(team: TypedTeam, h, registry: dict[str, str], bindings: dict, method_hint: str = "the method") -> str:
    owner = (team.owner_of(h.target) or [None])[0]
    role = team.agents[owner].role if owner in team.agents else ""
    froms = ", ".join(f"IN_{s} : {s}" for s in h.sources)
    lines = [f"module {h.name};", f"create OUT : {h.target} from {froms};", "uses 'helpers.py';"]
    for r in h.rules:
        vars_ = {v for v, _, _ in r.sources}
        src = ", ".join(f"{v} : {view}!{c}" for v, view, c in r.sources)
        guard = f" ({_guard(r.guard, vars_)})" if r.guard else ""
        recv = r.sources[0][0]
        binds = [f"{f} <- {_expr(e)}" for f, e in r.bind.items()]
        for b in r.llm:
            key = f"{r.name}.{b.feature}"
            # name of the goal element this value is for, for the format hint
            registry[key] = (
                (f"You are the {owner}. {role}\n\n" if owner else "")
                + f"Task: {b.prompt}\n\n"
                + _format_for(b.validators).replace("`{name}`", "named in the context").replace("{name}", "the method")
            )
            fp_args = ", ".join(f"'{p}', {_expr(p)}" for p in b.footprint) or "'context', ''"
            checks = []
            for v in b.validators:
                spec = VLIB.get(v.id)
                args = [_expr(v.args[p]) for p in (spec.params if spec else v.args) if p in v.args]
                checks.append(f"{b.feature}.v_{v.id}({', '.join(args)})")
            check = " and ".join(checks) or f"{b.feature}.v_nonempty()"
            binds.append(f"{b.feature} <- @llm({recv}.prompt_of('{key}'), {recv}.fp({fp_args}))")
            binds.append(f"@check {check}")
            behaviour = any(VLIB.get(v.id) and VLIB[v.id].strength == "behaviour" for v in b.validators)
            bindings[(r.name, b.feature)] = BindingMeta(h.name, r.name, r.target_var, b.feature, owner,
                                                         list(b.validators), list(b.footprint), key, behaviour)
        body = ",\n      ".join(binds)
        lines.append(f"rule {r.name} {{\n  from {src}{guard}\n  to {r.target_var} : {r.target_view}!{r.target_cls} (\n      {body} ) }}")
    return "\n".join(lines) + "\n"


def compile_team(team: TypedTeam, task: TaskLike, workdir: Path | None = None) -> CompiledTeam:
    workdir = Path(workdir or tempfile.mkdtemp(prefix="am2m_team_"))
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "helpers.py").write_text("from agentm2m.auto.rt_helpers import *  # noqa: F401,F403\n")
    mms = build_metamodels(team)
    tm = Team()
    for v, b in mms.items():
        root = lift_task(task, b) if v == team.goal_view else b.get(f"{v}Root")()
        tm.add_view(b, root)
    for a, vs in team.writes.items():
        for v in vs:
            tm.add_agent(a, v)
    registry: dict[str, str] = {}
    bindings: dict = {}
    order = _handoff_order(team)
    texts = {}
    for h in order:
        text = generate_module(team, h, registry, bindings)
        path = workdir / f"{h.name}.agentm2m"
        path.write_text(text)
        texts[h.name] = text
        tm.add_handoff(h.name, path, h.target)
    ct = CompiledTeam(team, tm, mms, workdir, registry, bindings, [h.name for h in order], texts)
    # parse every module now, so syntax problems surface as compile errors
    rt = TeamRuntime(tm, _NullLLM())
    for h in order:
        try:
            rt.module_for(h.name)
        except Exception as exc:  # noqa: BLE001
            raise CompileError(f"hand-off {h.name} does not compile: {exc}") from exc
    return ct


class _NullLLM(LLMBackend):
    name = "null"

    def generate(self, prompt: str, *, temperature: float = 0.2) -> str:  # pragma: no cover
        return ""


# --------------------------------------------------------------------------
# running and φ
# --------------------------------------------------------------------------


@dataclass
class Failure:
    clause: str  # "cover(G)" | "valid" | "fresh" | "noObl"
    handoff: str | None = None
    rule: str | None = None
    target_key: str | None = None
    binding: str | None = None
    goal_element: str | None = None
    reason: str = ""


@dataclass
class RunResult:
    phi: bool
    failures: list[Failure]
    escalations: list[Escalation]
    deliverables: dict[str, str]  # goal element name -> value (accepted or best effort)
    accepted: dict[str, bool]  # goal element name -> deliverable accepted?
    passes: int
    accepted_values: int


class Session:
    """A compiled team plus its runtime, kept across repairs (Step F)."""

    def __init__(self, ct: CompiledTeam, llm: LLMBackend, ctx: RunContext, *, k: int = 3,
                 temperature: float = 0.6, max_passes: int = 4) -> None:
        self.ct = ct
        self.llm = llm
        self.ctx = ctx
        self.k = k
        self.temperature = temperature
        self.rt = TeamRuntime(ct.team, llm, max_resamples=k, temperature=temperature, max_passes=max_passes)
        self.last_report = None

    # context managers for the per-run registries
    def _enter(self):
        return CURRENT.set(self.ctx), rt_helpers.REGISTRY.set(self.ct.registry)

    def _exit(self, toks) -> None:
        CURRENT.reset(toks[0])
        rt_helpers.REGISTRY.reset(toks[1])

    # ---- deliverables --------------------------------------------------
    def deliverable_objects(self) -> list[Any]:
        d = self.ct.typed.deliverable or {}
        v, c = d.get("view"), d.get("class")
        if v not in self.ct.team.roots:
            return []
        root = self.ct.team.roots[v]
        return [o for o in getattr(root, f"all_{c}", [])] if c else []

    def current_bodies(self, accepted_only: bool = True) -> dict[str, str]:
        d = self.ct.typed.deliverable or {}
        out: dict[str, str] = {}
        for o in self.deliverable_objects():
            goal = getattr(o, str(d.get("for")), None)
            val = getattr(o, str(d.get("feature")), None)
            if goal is not None and val:
                fn = self.ctx.bench.extract_function(val, goal.name)
                if fn:
                    out[goal.name] = fn
        return out

    def run(self) -> RunResult:
        toks = self._enter()
        try:
            report = self.rt.run_to_fixpoint()
            self.last_report = report
            return self.evaluate(report)
        finally:
            self._exit(toks)

    def evaluate(self, report=None) -> RunResult:
        report = report or self.last_report
        failures: list[Failure] = []
        escalations = list(report.escalations) if report else []
        typed = self.ct.typed
        goal_root = self.ct.team.roots[typed.goal_view]
        # noObl
        for e in escalations:
            meta = self.ct.bindings.get((e.rule, e.binding))
            failures.append(Failure("noObl", meta.handoff if meta else None, e.rule, e.target_key, e.binding,
                                    reason=e.reason))
        # fresh
        if not self.rt.stamps_fresh():
            failures.append(Failure("fresh", reason="a stamp does not match its current footprint"))
        # valid: re-run every accepted value's validator on the final models
        failures += self._revalidate()
        # cover(G)
        failures += self._cover(goal_root)
        # deliverables
        d = typed.deliverable or {}
        accepted = {}
        bodies = {}
        for o in self.deliverable_objects():
            goal = getattr(o, str(d.get("for")), None)
            if goal is None:
                continue
            val = getattr(o, str(d.get("feature")), None)
            if val:
                bodies[goal.name] = val
                accepted[goal.name] = True
        for name, val in self.ctx.last_rejected.items():
            if name not in bodies:
                bodies[name] = val
                accepted[name] = False
        n_acc = sum(len(l.stamps) for t in self.ct.team.traces.values() for l in t.links())
        return RunResult(not failures, failures, escalations, bodies, accepted, report.passes if report else 0, n_acc)

    def _revalidate(self) -> list[Failure]:
        out = []
        for hname in self.ct.handoff_order:
            module = self.rt.module_for(hname)
            spec = self.ct.team.handoffs[hname]
            helpers = load_helpers(module.uses, spec.rule_path.parent)
            roots_by_mm = {sm.mm_name: self.ct.team.roots[sm.mm_name] for sm in module.sources}
            trace = self.ct.team.traces[hname]
            target_root = self.ct.team.roots[spec.target_mm]
            registry = {getattr(o, "_amt_target_key", None): o for o in target_root.eAllContents()}
            for rule in module.rules:
                for m in compute_matches(rule, roots_by_mm, helpers):
                    link = trace.get(rule.name, m.match_key)
                    if link is None:
                        continue
                    for tp in rule.to_clause.patterns:
                        obj = registry.get(link.target_key)
                        for b in tp.bindings:
                            if not isinstance(b, StochasticBinding) or b.name not in link.stamps or obj is None:
                                continue
                            meta = self.ct.bindings.get((rule.name, b.name))
                            if meta is None or not meta.behaviour or b.check_expr is None:
                                continue
                            val = getattr(obj, b.name, None)
                            scope = {**m.bindings, tp.var: obj, b.name: val}
                            try:
                                verdict = eval_expr(b.check_expr, scope, helpers)
                            except Exception as exc:  # noqa: BLE001
                                verdict = False
                                reason = f"validator raised {exc}"
                            else:
                                reason = getattr(verdict, "reason", "validator failed on the final models")
                            if not verdict:
                                out.append(Failure("valid", hname, rule.name, link.target_key, b.name, reason=reason))
        return out

    def _cover(self, goal_root) -> list[Failure]:
        """cover(G): every in-scope goal object has a descendant (via trace
        links, transitively) with an accepted behaviour-validated value;
        `delivered` obligations additionally need an accepted deliverable."""
        typed = self.ct.typed
        # graph: source element key -> target keys (with rule)
        children: dict[str, list[tuple[str, str]]] = defaultdict(list)
        validated: set[str] = set()
        for hname, trace in self.ct.team.traces.items():
            for link in trace.links():
                for sk in link.source_keys.values():
                    children[sk].append((link.target_key, link.rule))
                for bname in link.stamps:
                    meta = self.ct.bindings.get((link.rule, bname))
                    if meta and meta.behaviour:
                        validated.add(link.target_key)
        # source keys are element keys; engine-keyed targets appear as Type#<target_key>
        def kids(key: str):
            for tk, rule in children.get(key, []):
                yield tk
                # the same object, keyed as a source of a further hand-off
                for k2 in list(children):
                    if k2.endswith("#" + tk):
                        yield k2

        out = []
        d = typed.deliverable or {}
        delivered_ok = set()
        for o in self.deliverable_objects():
            goal = getattr(o, str(d.get("for")), None)
            if goal is not None and getattr(o, str(d.get("feature")), None):
                delivered_ok.add(element_key(goal))
        for ob in typed.goal:
            for g in getattr(goal_root, f"all_{ob.cls}", []):
                if ob.scope.strip() not in ("all", "", "true"):
                    var = ob.scope.split(".")[0].strip()
                    try:
                        from ..rules.parser import parse_module  # noqa: F401
                        if not _eval_scope(ob.scope, var, g):
                            continue
                    except Exception:  # noqa: BLE001
                        pass
                gk = element_key(g)
                if ob.kind == "delivered":
                    if gk not in delivered_ok:
                        out.append(Failure("cover(G)", goal_element=gk, reason=f"{gk} has no accepted deliverable"))
                    continue
                seen, stack, ok = set(), [gk], False
                while stack and not ok:
                    k = stack.pop()
                    for t in kids(k):
                        if t in seen:
                            continue
                        seen.add(t)
                        bare = t.split("#", 1)[1] if "#" in t and "::" in t.split("#", 1)[1] else t
                        if bare in validated or t in validated:
                            ok = True
                            break
                        stack.append(t)
                if not ok:
                    first = children.get(gk, [])
                    out.append(Failure("cover(G)", rule=first[0][1] if first else None, goal_element=gk,
                                       reason=f"{gk} has no validated descendant"))
        return out


def _eval_scope(scope: str, var: str, obj: Any) -> bool:
    from lark import Lark  # noqa: F401
    from ..rules.parser import parse_module

    mod = parse_module(f"module S; create O : X from I : Y;\nrule R {{ from {var} : Y!Z ({_guard(scope, {var})}) to t : X!Z ( ) }}")
    guard = mod.rules[0].from_clause.guard
    return bool(eval_expr(guard, {var: obj}, {"nav": rt_helpers.nav}))
