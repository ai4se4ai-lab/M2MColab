"""Step E: attribution by construction.

Locate: a failed φ clause names a goal element (cover) or one pair
(target, stochastic binding), hence the rule, hand-off, owning agent and the
stamped footprint, by trace lookup.

Classify (bounded replay, at most 2r LLM calls per binding visited):
  0. validator fault  - the failing check gives different verdicts on the same value
  1. sampling fault   - some of r replays on the same footprint passes
  2. footprint fault  - some of r replays on a one-hop-widened footprint passes
  3. upstream fault   - a footprint value was LLM-written upstream, and re-sampling
                        it lets the binding pass
  4. specification    - otherwise (prompt/validator unsatisfiable on this footprint)
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from ..engine.binding import _footprint_to_text
from ..engine.expr import eval_expr
from ..engine.helpers_loader import load_helpers
from ..engine.matcher import compute_matches
from ..rules.ast import StochasticBinding
from . import rt_helpers
from .compile import Failure, Session
from .typed_team import Attr, rule_env, type_path


@dataclass
class FaultReport:
    clause: str
    fault_class: str  # validator|sampling|footprint|upstream|specification|coverage|unlocated
    handoff: str | None = None
    rule: str | None = None
    binding: str | None = None
    target_key: str | None = None
    agent: str | None = None
    goal_element: str | None = None
    upstream: dict | None = None  # located upstream binding for upstream faults
    widened: list[str] = field(default_factory=list)
    llm_calls: int = 0
    reason: str = ""
    footprint: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    def summary(self) -> str:
        where = f"{self.handoff}/{self.rule}.{self.binding}" if self.rule else (self.goal_element or "?")
        up = f" upstream={self.upstream['rule']}.{self.upstream['binding']}" if self.upstream else ""
        return f"{self.fault_class} fault at {where} (agent {self.agent}){up}: {self.reason[:200]}"


class Attributor:
    def __init__(self, session: Session, *, r: int = 2) -> None:
        self.s = session
        self.r = r
        self.calls = 0

    # ---------------------------------------------------------------
    def _context(self, handoff: str, rule_name: str, target_key: str, binding: str):
        rt, ct = self.s.rt, self.s.ct
        module = rt.module_for(handoff)
        spec = ct.team.handoffs[handoff]
        helpers = load_helpers(module.uses, spec.rule_path.parent)
        rule = next(r for r in module.rules if r.name == rule_name)
        _rn, var, match_key = target_key.split("::", 2)
        tp = next(t for t in rule.to_clause.patterns if t.var == var)
        b = next(x for x in tp.bindings if isinstance(x, StochasticBinding) and x.name == binding)
        roots_by_mm = {sm.mm_name: ct.team.roots[sm.mm_name] for sm in module.sources}
        match = next((m for m in compute_matches(rule, roots_by_mm, helpers) if m.match_key == match_key), None)
        target_root = ct.team.roots[spec.target_mm]
        obj = next((o for o in target_root.eAllContents() if getattr(o, "_amt_target_key", None) == target_key), None)
        link = ct.team.traces[handoff].get(rule_name, match_key)
        return module, helpers, rule, tp, b, match, obj, link

    def _prompt(self, b, match, helpers, footprint) -> str:
        head = eval_expr(b.prompt_expr, match.bindings, helpers)
        return f"{head}\n\nContext (footprint only):\n{_footprint_to_text(footprint)}"

    def _sample_ok(self, b, tp, obj, match, helpers, footprint) -> tuple[bool, str]:
        prompt = self._prompt(b, match, helpers, footprint)
        self.calls += 1
        try:
            value = self.s.llm.generate(prompt, temperature=max(self.s.temperature, 0.7))
        except Exception as exc:  # noqa: BLE001
            return False, f"llm error {exc}"
        if b.check_expr is None:
            return bool(value.strip()), value
        scope = {**match.bindings, tp.var: obj, b.name: value}
        try:
            return bool(eval_expr(b.check_expr, scope, helpers)), value
        except Exception:  # noqa: BLE001
            return False, value

    def _widened_paths(self, rule_name: str) -> list[str]:
        typed = self.s.ct.typed
        rule = next(r for _h, r in typed.rules() if r.name == rule_name)
        paths: list[str] = []
        for v, view, c in rule.sources:
            decl = typed.cls(view, c)
            if decl is None:
                continue
            paths += [f"{v}.{a}" for a in decl.attrs]
            for rn, ref in decl.refs.items():
                tdecl = typed.cls(ref.view, ref.cls)
                if tdecl is not None:
                    paths += [f"{v}.{rn}.{g}" for g in tdecl.attrs]
        return paths

    def _footprint_for(self, rule_name: str, feature: str, paths: list[str], match) -> list[str]:
        args: list[Any] = []
        for p in paths:
            parts = p.split(".")
            val = match.bindings.get(parts[0])
            if len(parts) > 1:
                val = rt_helpers.nav(val, ".".join(parts[1:]))
            args += [p, val]
        return rt_helpers.fp(None, *args)

    def _upstream_candidates(self, rule_name: str, feature: str, match) -> list[tuple[Any, str, str]]:
        """(object, producing rule, feature) for footprint values written by an upstream @llm binding."""
        typed = self.s.ct.typed
        rule = next(r for _h, r in typed.rules() if r.name == rule_name)
        b = next(x for x in rule.llm if x.feature == feature)
        producers = {}
        for _h, r in typed.rules():
            for x in r.llm:
                producers[(f"{r.target_view}.{r.target_cls}", x.feature)] = r.name
        env = rule_env(rule)
        out = []
        reads = list(b.footprint)
        for v in b.validators:
            reads += list(v.args.values())
        for p in reads:
            pt = type_path(typed, p, env, object_reads_all=False)
            if not pt.ok or not pt.reads:
                continue
            cls, f = pt.reads[-1]
            if (cls, f) in producers:
                parts = p.split(".")
                obj = match.bindings.get(parts[0])
                if len(parts) > 2:
                    obj = rt_helpers.nav(obj, ".".join(parts[1:-1]))
                if obj is not None and hasattr(obj, "eClass"):
                    out.append((obj, producers[(cls, f)], f))
        return out

    # ---------------------------------------------------------------
    def attribute(self, f: Failure) -> FaultReport:
        toks = self.s._enter()
        start = self.calls
        try:
            rep = self._attribute(f)
        finally:
            self.s._exit(toks)
        rep.llm_calls = self.calls - start
        return rep

    def _attribute(self, f: Failure) -> FaultReport:
        ct = self.s.ct
        if f.clause == "cover(G)" or not (f.handoff and f.rule and f.target_key and f.binding):
            meta_owner = None
            if f.rule:
                h = next((h for h, r in ct.typed.rules() if r.name == f.rule), None)
                meta_owner = (ct.typed.owner_of(h.target) or [None])[0] if h else None
            return FaultReport(f.clause, "coverage" if f.clause == "cover(G)" else "unlocated", rule=f.rule,
                               goal_element=f.goal_element, agent=meta_owner, reason=f.reason)
        meta = ct.bindings.get((f.rule, f.binding))
        module, helpers, rule, tp, b, match, obj, link = self._context(f.handoff, f.rule, f.target_key, f.binding)
        rep = FaultReport(f.clause, "specification", f.handoff, f.rule, f.binding, f.target_key,
                          meta.owner if meta else None, reason=f.reason)
        if match is None or obj is None:
            rep.fault_class = "unlocated"
            return rep
        footprint = eval_expr(b.footprint_expr, match.bindings, helpers)
        rep.footprint = _footprint_to_text(footprint)[:2000]
        goal_name = next((getattr(v, "name", None) for v in match.bindings.values()
                          if getattr(v, "eClass", None) is not None and v.eClass.name == "Method"), None)
        # 0. validator fault: same candidate, two verdicts
        cand = None
        if goal_name and meta:
            for v in meta.validators:
                cand = self.s.ctx.last_rejected_any.get(f"{goal_name}|{v.id}") or cand
        if cand is None:
            cand = getattr(obj, b.name, None)
        if cand and b.check_expr is not None:
            scope = {**match.bindings, tp.var: obj, b.name: cand}
            verdicts = []
            for _ in range(2):
                try:
                    verdicts.append(bool(eval_expr(b.check_expr, scope, helpers)))
                except Exception:  # noqa: BLE001
                    verdicts.append(False)
            if verdicts[0] != verdicts[1]:
                rep.fault_class = "validator"
                return rep
        # 1. sampling
        for _ in range(self.r):
            ok, _v = self._sample_ok(b, tp, obj, match, helpers, footprint)
            if ok:
                rep.fault_class = "sampling"
                return rep
        # 2. footprint (one hop wider)
        current = list(meta.footprint) if meta else []
        widened = current + [p for p in self._widened_paths(f.rule) if p not in current]
        if len(widened) > len(current):
            wfp = self._footprint_for(f.rule, f.binding, widened, match)
            for _ in range(self.r):
                ok, _v = self._sample_ok(b, tp, obj, match, helpers, wfp)
                if ok:
                    rep.fault_class = "footprint"
                    rep.widened = [p for p in widened if p not in current]
                    return rep
        # 3. upstream
        for up_obj, up_rule, up_feat in self._upstream_candidates(f.rule, f.binding, match):
            up_meta = ct.bindings.get((up_rule, up_feat))
            if up_meta is None:
                continue
            up_link = next((l for l in ct.team.traces[up_meta.handoff].links()
                            if l.target_key == getattr(up_obj, "_amt_target_key", None)), None)
            if up_link is None:
                continue
            u_module, u_helpers, u_rule, u_tp, u_b, u_match, u_obj, _ = self._context(
                up_meta.handoff, up_rule, up_link.target_key, up_feat)
            if u_match is None:
                continue
            u_fp = eval_expr(u_b.footprint_expr, u_match.bindings, u_helpers)
            old = getattr(up_obj, up_feat)
            try:
                for _ in range(self.r):
                    ok_u, new_val = self._sample_ok(u_b, u_tp, u_obj, u_match, u_helpers, u_fp)
                    if not ok_u:
                        continue
                    setattr(up_obj, up_feat, new_val)
                    fp2 = eval_expr(b.footprint_expr, match.bindings, helpers)
                    ok, _v = self._sample_ok(b, tp, obj, match, helpers, fp2)
                    if ok:
                        rep.fault_class = "upstream"
                        rep.upstream = {"handoff": up_meta.handoff, "rule": up_rule, "binding": up_feat,
                                        "target_key": up_link.target_key, "agent": up_meta.owner}
                        return rep
            finally:
                setattr(up_obj, up_feat, old)
        return rep


def attribute_all(session: Session, failures: list[Failure], *, r: int = 1, limit: int = 2) -> tuple[list[FaultReport], int]:
    """Attribute up to `limit` distinct root failures. Failures that are
    consequences of a located binding failure (a cover(G) gap of the same goal
    element, or stale stamps left by an escalation) are not attributed again."""
    att = Attributor(session, r=r)
    located = [f for f in failures if f.clause in ("noObl", "valid") and f.target_key]
    roots = list(located)
    for f in failures:
        if f.clause == "cover(G)":
            ge = f.goal_element or ""
            if not any(ge and ge in (x.target_key or "") for x in located):
                roots.append(f)
        elif f.clause == "fresh" and not located:
            roots.append(f)
    seen, reports = set(), []
    for f in roots:
        key = (f.rule, f.binding, f.target_key, f.goal_element if f.clause == "cover(G)" else None)
        if key in seen:
            continue
        seen.add(key)
        if len(reports) >= limit:
            break
        reports.append(att.attribute(f))
    return reports, att.calls
