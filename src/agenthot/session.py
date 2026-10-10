"""Component 6, the Runtime: runs a compiled typed team to a fixpoint
(Algorithm 2 over all hand-offs in topological order) and evaluates the
acceptance predicate phi with the engine clauses of paper Def. 5:

  cover(G)  every in-scope goal object of an obligation (C, s, mu, F_C) has a
            descendant holding an accepted value of a behaviour-validated
            binding (mu = checked) or of the deliverable feature (delivered);
            a trace link connects to its target every object of its match and
            every object owning a location its recorded footprint or
            validator reads read;
  valid     every accepted value passes its validator on the final models;
  fresh     every stamp equals the digest of the current values;
  noEsc     nothing is escalated;

plus optional library clauses (vlib.LIBRARY_CLAUSES). The engine, not an
agent, decides that the team is done. Failed clauses become `Failure`
records that name a hand-off, rule, target and binding (or a goal object):
the input of attribution.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

from autom2m.vlib import CURRENT, LIBRARY_CLAUSES, RunContext

from . import rt_helpers
from .compiler import CompiledTeam
from .engine.binding import Escalation
from .engine.expr import eval_expr
from .engine.helpers_loader import load_helpers
from .engine.matcher import compute_matches
from .engine.trace import element_key
from .llm.base import LLMBackend
from .rules.ast import StochasticBinding
from .team.runtime import TeamRuntime

ENGINE_CLAUSES = ("cover(G)", "valid", "fresh", "noEsc")


@dataclass
class Failure:
    clause: str  # "cover(G)" | "valid" | "fresh" | "noEsc" | a library clause
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
    deliverables: dict[str, str]  # goal method name -> value (accepted or best effort)
    accepted: dict[str, bool]  # goal method name -> deliverable accepted?
    passes: int
    accepted_values: int
    clauses: dict[str, bool] = field(default_factory=dict)
    seconds: float = 0.0


class Session:
    """A compiled team plus its runtime, kept across repairs."""

    def __init__(self, ct: CompiledTeam, llm: LLMBackend, ctx: RunContext, *, k: int = 3,
                 temperature: float = 0.2, max_passes: int = 4) -> None:
        self.ct = ct
        self.llm = llm
        self.ctx = ctx
        self.k = k
        self.temperature = temperature
        self.rt = TeamRuntime(ct.team, llm, max_resamples=k, temperature=temperature, max_passes=max_passes)
        self.last_report = None
        goal_root = ct.team.roots.get(ct.typed.goal_view)
        ctx.goal_methods = {m.name: m for m in getattr(goal_root, "all_Method", [])} if goal_root is not None else {}

    # ---- snapshots (the same state, another session) ------------------
    def snapshot(self) -> dict:
        """The run state: view models and trace links (JSON-able)."""
        from .store import dump_models

        return {"models": dump_models(self.ct.team.roots),
                "traces": {h: [l.to_dict() for l in t.links()] for h, t in self.ct.team.traces.items()},
                "last_rejected": dict(self.ctx.last_rejected)}

    def restore(self, snap: dict) -> None:
        """Load a snapshot taken from a session of the same views."""
        from .engine.trace import TraceLink, TraceModel
        from .store import load_models

        roots = load_models(snap["models"], self.ct.team.views)
        for v, root in roots.items():
            self.ct.team.roots[v] = root
        for h, links in snap["traces"].items():
            if h in self.ct.team.traces:
                tm = TraceModel(handoff=h)
                for d in links:
                    tm.put(TraceLink.from_dict(d))
                self.ct.team.traces[h] = tm
        self.ctx.last_rejected.update(snap.get("last_rejected") or {})
        goal_root = self.ct.team.roots.get(self.ct.typed.goal_view)
        self.ctx.goal_methods = {m.name: m for m in getattr(goal_root, "all_Method", [])} if goal_root is not None else {}

    # per-run registries
    def _enter(self):
        return CURRENT.set(self.ctx), rt_helpers.REGISTRY.set(self.ct.registry)

    def _exit(self, toks) -> None:
        CURRENT.reset(toks[0])
        rt_helpers.REGISTRY.reset(toks[1])

    # ---- objects and links -------------------------------------------
    def objects(self) -> dict[str, Any]:
        """element key -> object, over every view."""
        out = {}
        for root in self.ct.team.roots.values():
            for o in root.eAllContents():
                try:
                    out[element_key(o)] = o
                except Exception:  # noqa: BLE001
                    continue
        return out

    def object_by_target_key(self, target_key: str) -> Any:
        for root in self.ct.team.roots.values():
            for o in root.eAllContents():
                if getattr(o, "_amt_target_key", None) == target_key:
                    return o
        return None

    def link_for(self, target_key: str):
        for hname, trace in self.ct.team.traces.items():
            for link in trace.links():
                if link.target_key == target_key:
                    return hname, link
        return None, None

    def connections(self) -> dict[str, set[str]]:
        """Def. 5: object key -> keys of the objects its links connect it to."""
        tk2key = {}
        for root in self.ct.team.roots.values():
            for o in root.eAllContents():
                tk = getattr(o, "_amt_target_key", None)
                if tk:
                    try:
                        tk2key[tk] = element_key(o)
                    except Exception:  # noqa: BLE001
                        pass
        g: dict[str, set[str]] = defaultdict(set)
        for trace in self.ct.team.traces.values():
            for link in trace.links():
                tgt = tk2key.get(link.target_key)
                if tgt is None:
                    continue
                for sk in link.source_keys.values():
                    g[sk].add(tgt)
                for b, keys in link.reads.items():
                    if b in link.stamps:
                        for k in keys:
                            g[k].add(tgt)
        return g

    # ---- deliverables --------------------------------------------------
    def _goal_method(self, obj: Any) -> Any:
        """The goal Method a deliverable object belongs to: a reference of the
        object (or of an object it references), else its trace ancestry."""
        from autom2m.vlib import is_goal

        for f in obj.eClass.eAllStructuralFeatures():
            if f.is_reference:
                v = getattr(obj, f.name, None)
                if hasattr(v, "eClass") and is_goal(v) and v.eClass.name == "Method":
                    return v
        tk = getattr(obj, "_amt_target_key", None)
        objs = None
        seen = set()
        q = deque([tk])
        while q:
            cur = q.popleft()
            if cur in seen or cur is None:
                continue
            seen.add(cur)
            _h, link = self.link_for(cur)
            if link is None:
                continue
            objs = objs or self.objects()
            for sk in link.source_keys.values():
                o = objs.get(sk)
                if o is not None and is_goal(o) and o.eClass.name == "Method":
                    return o
                if o is not None:
                    q.append(getattr(o, "_amt_target_key", None))
        if len(self.ctx.goal_methods) == 1:
            return next(iter(self.ctx.goal_methods.values()))
        return None

    def deliverable_objects(self) -> list[Any]:
        d = self.ct.typed.deliverable or {}
        v, c = d.get("view"), d.get("class")
        if v not in self.ct.team.roots or not c:
            return []
        return list(getattr(self.ct.team.roots[v], f"all_{c}", []) or [])

    def current_bodies(self) -> dict[str, str]:
        """Accepted deliverable values, as function sources keyed by method."""
        d = self.ct.typed.deliverable or {}
        out: dict[str, str] = {}
        for o in self.deliverable_objects():
            val = getattr(o, str(d.get("feature")), None)
            if not val:
                continue
            goal = self._goal_method(o)
            if goal is None:
                continue
            fn = self.ctx.bench.extract_function(val, goal.name)
            if fn:
                out[goal.name] = fn
        return out

    # ---- running -------------------------------------------------------
    def run(self) -> RunResult:
        toks = self._enter()
        t0 = time.perf_counter()
        try:
            report = self.rt.run_to_fixpoint()
            self.last_report = report
            res = self._evaluate(report)
            res.seconds = time.perf_counter() - t0
            return res
        finally:
            self._exit(toks)

    def evaluate(self, report=None) -> RunResult:
        if CURRENT.get() is not self.ctx:
            toks = self._enter()
            try:
                return self._evaluate(report)
            finally:
                self._exit(toks)
        return self._evaluate(report)

    def _evaluate(self, report=None) -> RunResult:
        report = report or self.last_report
        typed = self.ct.typed
        failures: list[Failure] = []
        escalations = list(report.escalations) if report else []
        clauses: dict[str, bool] = {}
        # noEsc
        for e in escalations:
            meta = self.ct.bindings.get((e.rule, e.binding))
            failures.append(Failure("noEsc", meta.handoff if meta else None, e.rule, e.target_key, e.binding, reason=e.reason))
        clauses["noEsc"] = not escalations
        # fresh
        fresh = self.rt.stamps_fresh()
        clauses["fresh"] = fresh
        if not fresh:
            failures.append(Failure("fresh", reason="a stamp does not match the digest of its current values"))
        # valid
        invalid = self._revalidate()
        clauses["valid"] = not invalid
        failures += invalid
        # cover(G)
        uncovered = self._cover()
        clauses["cover(G)"] = not uncovered
        failures += uncovered
        # library clauses (agent claims are never evaluated: W5 rejects them,
        # and an unchecked team's claims are ignored)
        for c in typed.done:
            if c in LIBRARY_CLAUSES:
                ok, why = self._library_clause(c)
                clauses[c] = ok
                if not ok:
                    failures.append(Failure(c, reason=why))
        # deliverables (best effort for escalated methods)
        d = typed.deliverable or {}
        accepted, bodies = {}, {}
        for o in self.deliverable_objects():
            goal = self._goal_method(o)
            val = getattr(o, str(d.get("feature")), None)
            if goal is not None and val:
                bodies[goal.name] = val
                accepted[goal.name] = True
        for name, val in self.ctx.last_rejected.items():
            if name not in bodies:
                bodies[name] = val
                accepted[name] = False
        n_acc = sum(len(l.stamps) for t in self.ct.team.traces.values() for l in t.links())
        return RunResult(not failures, failures, escalations, bodies, accepted, report.passes if report else 0, n_acc, clauses)

    def _library_clause(self, clause: str) -> tuple[bool, str]:
        if clause == "examples_pass":
            bench = self.ctx.bench
            code = bench.assemble(self.current_bodies())
            res = bench.run_examples(code, bench.method_names())
            if res.get("error"):
                return False, f"the deliverable does not load: {str(res['error'])[:200]}"
            if res.get("failed") or res.get("pending"):
                f = (res.get("failed") or [{}])[0]
                return False, f"public example `{f.get('example', '?')}` fails" if f else "a method is missing"
            return True, ""
        return False, f"unknown library clause {clause}"

    def _revalidate(self) -> list[Failure]:
        """valid: re-run the validator of every accepted value on the final models."""
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
                    obj = registry.get(link.target_key)
                    for tp in rule.to_clause.patterns:
                        for b in tp.bindings:
                            if not isinstance(b, StochasticBinding) or b.name not in link.stamps or obj is None:
                                continue
                            if b.check_expr is None:
                                continue
                            val = getattr(obj, b.name, None)
                            scope = {**m.bindings, tp.var: obj, b.name: val}
                            try:
                                verdict = eval_expr(b.check_expr, scope, helpers)
                            except Exception as exc:  # noqa: BLE001
                                verdict, reason = False, f"validator raised {exc}"
                            else:
                                reason = getattr(verdict, "reason", "validator failed on the final models")
                            if not verdict:
                                out.append(Failure("valid", hname, rule.name, link.target_key, b.name, reason=reason))
        return out

    def _in_scope(self, ob, obj) -> bool:
        s = (ob.scope or "all").strip()
        if s in ("all", "", "true"):
            return True
        var = s.split(".")[0].strip()
        try:
            return bool(_eval_scope(s, var, obj))
        except Exception:  # noqa: BLE001
            return True

    def _cover(self) -> list[Failure]:
        typed = self.ct.typed
        goal_root = self.ct.team.roots[typed.goal_view]
        conn = self.connections()
        objs = self.objects()
        d = typed.deliverable or {}
        checked_keys, delivered_keys = set(), set()
        for trace in self.ct.team.traces.values():
            for link in trace.links():
                for bname in link.stamps:
                    meta = self.ct.bindings.get((link.rule, bname))
                    obj = self.object_by_target_key(link.target_key) if meta else None
                    if obj is None:
                        continue
                    key = element_key(obj)
                    if meta.behaviour:
                        checked_keys.add(key)
                    if (obj.eClass.name == d.get("class") and bname == d.get("feature")
                            and obj.eClass.ePackage.name == d.get("view")):
                        delivered_keys.add(key)
        out = []
        for ob in typed.goal:
            want = checked_keys if ob.mode == "checked" else delivered_keys
            for g in getattr(goal_root, f"all_{ob.cls}", []) or []:
                if not self._in_scope(ob, g):
                    continue
                gk = element_key(g)
                seen, q, ok = {gk}, deque([gk]), False
                while q and not ok:
                    k = q.popleft()
                    for c in conn.get(k, ()):
                        if c in want:
                            ok = True
                            break
                        if c not in seen:
                            seen.add(c)
                            q.append(c)
                if not ok:
                    first = next((objs.get(c) for c in conn.get(gk, ())), None)
                    out.append(Failure("cover(G)", goal_element=gk,
                                       target_key=getattr(first, "_amt_target_key", None),
                                       reason=f"{gk} has no descendant with an accepted "
                                              f"{'behaviour-checked value' if ob.mode == 'checked' else 'deliverable'}"))
        return out


def _eval_scope(scope: str, var: str, obj: Any) -> bool:
    from .compiler import _guard
    from .rules.parser import parse_module

    mod = parse_module(f"module S; create O : X from I : Y;\nrule R {{ from {var} : Y!Z ({_guard(scope, {var})}) to t : X!Z ( ) }}")
    guard = mod.rules[0].from_clause.guard
    return bool(eval_expr(guard, {var: obj}, {"nav": rt_helpers.nav}))
