"""Component 7, Attribution: locate by trace lookup, classify by bounded replay
(paper Sec. 3.5, Algorithm 4).

Locate. A failed `valid` clause or an escalation names one pair (t, b): a
target object, a stochastic binding, its rule, hand-off and owner, and the
stamped footprint its LLM saw. A failed cover(G) clause names, through the
trace (Proposition 2), a binding on the anchoring chain of the goal object
that is escalated or invalid.

Classify (Algorithm 4), for the failed value v of b on its recorded footprint:
  1. validator      chk_b(v, vr_b) is unstable over 3 reruns (no LLM call)
  2. sampling       Replays(b, fp) pass sometimes
  3. footprint(j)   Replays(b, fp u Widen_j(fp)) pass sometimes, j = 1..h;
                    the repair keeps only the added paths without which no
                    replay passes
  4. upstream(b')   a value u in the footprint written upstream by a
                    stochastic binding b', once re-sampled, makes
                    Replays(b, fp) pass; b' is then attributed in turn, with
                    its accepted value treated as failed
  5. specification  no supported value satisfies prompt and validator

Replays draw samples one at a time; the adaptive budget stops at the first
pass, at most n_rep = 3. In `exhaustive` mode (RQ4) every step draws all
n_rep samples and every step runs, so the classification under any budget
n <= n_rep can be derived from the same draws (`derive`).
"""
from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from agenthot import rt_helpers
from agenthot.engine.binding import _footprint_to_text
from agenthot.engine.expr import eval_expr
from agenthot.engine.helpers_loader import load_helpers
from agenthot.engine.matcher import compute_matches
from agenthot.engine.trace import element_key
from agenthot.rules.ast import StochasticBinding
from agenthot.session import Failure, Session

from .typed_team import rule_env, type_path

FAULT_CLASSES = ("validator", "sampling", "footprint", "upstream", "specification")


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
    hops: int | None = None  # footprint faults: the widening j that made replays pass
    widened: list[str] = field(default_factory=list)  # minimal added paths (footprint repair)
    upstream: dict | None = None  # upstream faults: the producing binding and its own report
    stability: list[bool] = field(default_factory=list)
    steps: list[dict] = field(default_factory=list)  # [{"step", "draws": [bool], "calls"}]
    llm_calls: int = 0
    reason: str = ""
    footprint: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    def responsible(self) -> dict:
        """The binding the fault is attributed to (upstream: the producer)."""
        if self.fault_class == "upstream" and self.upstream:
            inner = self.upstream.get("report") or {}
            if inner.get("fault_class") == "upstream" and inner.get("upstream"):
                return FaultReport(**inner).responsible()
            return {k: self.upstream.get(k) for k in ("handoff", "rule", "binding", "target_key", "agent")}
        return {"handoff": self.handoff, "rule": self.rule, "binding": self.binding, "target_key": self.target_key,
                "agent": None if self.fault_class == "validator" else self.agent}

    def summary(self) -> str:
        where = f"{self.handoff}/{self.rule}.{self.binding}" if self.rule else (self.goal_element or "?")
        up = f" upstream={self.upstream['rule']}.{self.upstream['binding']}" if self.upstream else ""
        wid = f" widen={self.widened}" if self.widened else ""
        return f"{self.fault_class} fault at {where} (agent {self.agent}){up}{wid}: {self.reason[:200]}"


def derive(steps: list[dict], n: int, *, adaptive: bool = True) -> tuple[str, int]:
    """Classification and LLM calls under a replay budget of n draws per
    step, from an exhaustive record (the same draws as the adaptive run)."""
    calls = 0
    for st in steps:
        calls += st.get("pre_calls", 0)
        draws = st["draws"][:n]
        hit = next((i for i, d in enumerate(draws) if d), None)
        calls += (hit + 1) if (hit is not None and adaptive) else len(draws)
        if hit is not None:
            name = st["step"]
            return ("footprint" if name.startswith("footprint") else "upstream" if name.startswith("upstream") else name), calls
    return "specification", calls


class Attributor:
    def __init__(self, session: Session, *, n_rep: int = 3, h: int = 2, exhaustive: bool = False,
                 minimize: bool = True, max_minimize_probes: int = 8, max_depth: int | None = None) -> None:
        self.s = session
        self.n_rep = n_rep
        self.h = h
        self.exhaustive = exhaustive
        self.minimize = minimize
        self.max_minimize_probes = max_minimize_probes
        self.max_depth = max_depth if max_depth is not None else max(1, len(session.ct.handoff_order))
        self.calls = 0

    # ---------------------------------------------------------------
    # context
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
        obj = self.s.object_by_target_key(target_key)
        link = ct.team.traces[handoff].get(rule_name, match_key)
        return helpers, tp, b, match, obj, link

    def _check(self, b, tp, obj, match, helpers, value) -> bool:
        if b.check_expr is None:
            return bool(str(value or "").strip())
        scope = {**match.bindings, tp.var: obj, b.name: value}
        try:
            return bool(eval_expr(b.check_expr, scope, helpers))
        except Exception:  # noqa: BLE001
            return False

    def _sample(self, b, match, helpers, footprint) -> str:
        head = eval_expr(b.prompt_expr, match.bindings, helpers)
        prompt = f"{head}\n\nContext (footprint only):\n{_footprint_to_text(footprint)}"
        self.calls += 1
        try:
            return self.s.llm.generate(prompt, temperature=self.s.temperature)
        except Exception as exc:  # noqa: BLE001
            return f"(llm error: {exc})"

    def _replays(self, draw: Callable[[], bool]) -> list[bool]:
        out = []
        for _ in range(self.n_rep):
            ok = draw()
            out.append(ok)
            if ok and not self.exhaustive:
                break
        return out

    # ---------------------------------------------------------------
    # footprints
    # ---------------------------------------------------------------
    def _typed_rule(self, rule_name: str):
        return next((h, r) for h, r in self.s.ct.typed.rules() if r.name == rule_name)

    def widen_paths(self, rule_name: str, j: int) -> list[str]:
        """Every path from the rule's source variables that follows at most
        j references and ends in an attribute, within the views the rule
        may read (its sources and the goal view)."""
        typed = self.s.ct.typed
        h, rule = self._typed_rule(rule_name)
        allowed = set(h.sources) | {typed.goal_view}
        env = rule_env(rule)
        out: list[str] = []
        frontier = [(v, typed.cls(view, c)) for v, view, c in rule.sources]
        for depth in range(j + 1):
            nxt = []
            for path, decl in frontier:
                if decl is None or decl.view not in allowed:
                    continue
                out += [f"{path}.{a}" for a in decl.attrs]
                if depth < j:
                    for rn, ref in decl.refs.items():
                        nxt.append((f"{path}.{rn}", typed.cls(ref.view, ref.cls)))
            frontier = nxt
        return [p for p in dict.fromkeys(out) if type_path(typed, p, env).ok]

    def _footprint_for(self, paths: list[str], match) -> Any:
        triples: list[Any] = []
        for p in paths:
            parts = p.split(".")
            val = match.bindings.get(parts[0])
            owner = val
            if len(parts) > 1:
                owner = rt_helpers.nav(val, ".".join(parts[1:-1])) if len(parts) > 2 else val
                val = rt_helpers.nav(val, ".".join(parts[1:]))
            triples += [p, val, owner]
        return rt_helpers.fpv(None, len(paths), *triples)

    # ---------------------------------------------------------------
    # upstream values in a footprint
    # ---------------------------------------------------------------
    def _upstream_candidates(self, rule_name: str, feature: str, match) -> list[tuple[Any, str, str]]:
        """(object, producing rule, feature) for values in the footprint or
        validator reads that an upstream stochastic binding wrote."""
        typed = self.s.ct.typed
        _h, rule = self._typed_rule(rule_name)
        b = next(x for x in rule.llm if x.feature == feature)
        producers = {}
        for _hh, r in typed.rules():
            for x in r.llm:
                producers[(f"{r.target_view}.{r.target_cls}", x.feature)] = r.name
        env = rule_env(rule)
        meta = self.s.ct.bindings.get((rule_name, feature))
        reads = list(b.footprint) + (list(meta.check_reads) if meta else [])
        out, seen = [], set()
        for p in reads:
            pt = type_path(typed, p, env, object_reads_all=False)
            if not pt.ok or not pt.reads:
                continue
            cls, f = pt.reads[-1]
            if (cls, f) not in producers:
                continue
            parts = p.split(".")
            obj = match.bindings.get(parts[0])
            if len(parts) > 2:
                obj = rt_helpers.nav(obj, ".".join(parts[1:-1]))
            for o in (obj if isinstance(obj, list) else [obj]):
                if o is not None and hasattr(o, "eClass") and (id(o), f) not in seen:
                    seen.add((id(o), f))
                    out.append((o, producers[(cls, f)], f))
        return out

    # ---------------------------------------------------------------
    # locate
    # ---------------------------------------------------------------
    def locate(self, f: Failure) -> Failure | None:
        """A (t, b) pair for a failure; cover(G) failures are followed
        through the trace to an escalated or invalid binding on the chain."""
        if f.clause in ("valid", "noEsc") and f.handoff and f.rule and f.target_key and f.binding:
            return f
        if f.clause != "cover(G)" or not f.goal_element:
            return None
        conn = self.s.connections()
        failed = {}  # target key -> an escalated or never accepted binding of it
        for hname, trace in self.s.ct.team.traces.items():
            for link in trace.links():
                for (r, bn) in self.s.ct.bindings:
                    if r == link.rule and bn not in link.stamps:
                        failed.setdefault(link.target_key, (hname, r, bn))
        objs = self.s.objects()
        q, seen = deque([f.goal_element]), {f.goal_element}
        while q:
            k = q.popleft()
            for c in sorted(conn.get(k, ())):
                o = objs.get(c)
                tk = getattr(o, "_amt_target_key", None)
                if tk in failed:
                    h, r, bn = failed[tk]
                    return Failure("cover(G)", h, r, tk, bn, f.goal_element, f.reason)
                if c not in seen:
                    seen.add(c)
                    q.append(c)
        return None

    # ---------------------------------------------------------------
    # classify (Algorithm 4)
    # ---------------------------------------------------------------
    def attribute(self, f: Failure) -> FaultReport:
        toks = self.s._enter()
        start = self.calls
        try:
            loc = self.locate(f)
            if loc is None:
                owner = None
                if f.rule:
                    h = next((h for h, r in self.s.ct.typed.rules() if r.name == f.rule), None)
                    owner = (sorted(self.s.ct.typed.owner_of(h.target)) or [None])[0] if h else None
                rep = FaultReport(f.clause, "coverage" if f.clause == "cover(G)" else "unlocated", rule=f.rule,
                                  goal_element=f.goal_element, agent=owner, reason=f.reason)
            else:
                rep = self.classify(loc.handoff, loc.rule, loc.target_key, loc.binding, clause=f.clause,
                                    reason=f.reason, goal_element=f.goal_element)
        finally:
            self.s._exit(toks)
        rep.llm_calls = self.calls - start
        return rep

    def _failed_value(self, obj, b, link, meta) -> str | None:
        ctx = self.s.ctx
        tk = getattr(obj, "_amt_target_key", None)
        if meta:
            for v in meta.validators:
                val = ctx.last_rejected_any.get(f"{tk}|{v.id}")
                if val:
                    return val
        if link is not None and (link.rejections.get(b.name) or {}).get("value"):
            return link.rejections[b.name]["value"]
        return getattr(obj, b.name, None)

    def classify(self, handoff: str, rule_name: str, target_key: str, binding: str, *, clause: str = "valid",
                 reason: str = "", goal_element: str | None = None, oracle: Callable[[str], bool] | None = None,
                 failed_value: str | None = None, depth: int = 0) -> FaultReport:
        ct = self.s.ct
        meta = ct.bindings.get((rule_name, binding))
        helpers, tp, b, match, obj, link = self._context(handoff, rule_name, target_key, binding)
        rep = FaultReport(clause, "specification", handoff, rule_name, binding, target_key,
                          meta.owner if meta else None, goal_element, reason=reason)
        if match is None or obj is None:
            rep.fault_class = "unlocated"
            return rep
        try:
            footprint = eval_expr(b.footprint_expr, match.bindings, helpers)
        except Exception as exc:  # noqa: BLE001 - an ill-typed binding has no footprint to replay
            rep.reason = f"{reason}; footprint cannot be evaluated: {exc}"
            return rep
        rep.footprint = _footprint_to_text(footprint)[:2000]
        accepts = oracle or (lambda v: self._check(b, tp, obj, match, helpers, v))
        expected = oracle is not None  # an upstream value under recursion was accepted
        cand = failed_value if failed_value is not None else self._failed_value(obj, b, link, meta)

        # 1. validator: unstable verdicts on the same value (no LLM call)
        if cand:
            rep.stability = [self._check(b, tp, obj, match, helpers, cand) for _ in range(3)]
            if any(v != expected for v in rep.stability):
                rep.fault_class = "validator"
                rep.steps.append({"step": "validator", "draws": [], "stability": rep.stability})
                return rep

        steps: list[dict] = []

        def record(name: str, draw: Callable[[], bool], pre_calls: int = 0) -> bool:
            before = self.calls
            draws = self._replays(draw)
            steps.append({"step": name, "draws": draws, "calls": self.calls - before + pre_calls, "pre_calls": pre_calls})
            return any(draws)

        decided: str | None = None
        # 2. sampling
        if record("sampling", lambda: accepts(self._sample(b, match, helpers, footprint))):
            decided = "sampling"
        # 3. footprint, widened by j references
        current = list(meta.footprint) if meta else []
        widened_at: tuple[int, list[str]] | None = None
        for j in range(1, self.h + 1):
            if decided and not self.exhaustive:
                break
            added = [p for p in self.widen_paths(rule_name, j) if p not in current]
            if not added:
                steps.append({"step": f"footprint{j}", "draws": [], "calls": 0, "pre_calls": 0})
                continue
            wfp = self._footprint_for(current + added, match)
            if record(f"footprint{j}", lambda wfp=wfp: accepts(self._sample(b, match, helpers, wfp))) and decided is None:
                decided, widened_at = "footprint", (j, added)
        # 4. upstream
        up_hit = None
        for up_obj, up_rule, up_feat in self._upstream_candidates(rule_name, binding, match):
            if decided and not self.exhaustive:
                break
            up_meta = ct.bindings.get((up_rule, up_feat))
            tk = getattr(up_obj, "_amt_target_key", None)
            if up_meta is None or tk is None:
                continue
            uh, utp, ub, umatch, uobj, _ul = self._context(up_meta.handoff, up_rule, tk, up_feat)
            if umatch is None:
                continue
            try:
                ufp = eval_expr(ub.footprint_expr, umatch.bindings, uh)
            except Exception:  # noqa: BLE001
                continue
            before = self.calls
            new_val = None
            for _ in range(self.n_rep):  # re-sample u until its own validator accepts it
                v = self._sample(ub, umatch, uh, ufp)
                if self._check(ub, utp, uobj, umatch, uh, v):
                    new_val = v
                    break
            pre = self.calls - before
            old = getattr(up_obj, up_feat)
            if new_val is None:
                steps.append({"step": f"upstream:{up_rule}.{up_feat}", "draws": [], "calls": pre, "pre_calls": pre})
                continue
            try:
                setattr(up_obj, up_feat, new_val)
                fp2 = eval_expr(b.footprint_expr, match.bindings, helpers)
                hit = record(f"upstream:{up_rule}.{up_feat}", lambda fp2=fp2: accepts(self._sample(b, match, helpers, fp2)),
                             pre_calls=pre)
            finally:
                setattr(up_obj, up_feat, old)
            if hit and decided is None:
                decided = "upstream"
                up_hit = (up_obj, up_rule, up_feat, up_meta, tk, new_val, old)
        rep.steps = steps
        rep.fault_class = decided or "specification"
        if rep.fault_class == "footprint" and widened_at:
            rep.hops = widened_at[0]
            rep.widened = self._minimize(b, match, helpers, accepts, current, widened_at[1]) if self.minimize \
                else widened_at[1]
        if rep.fault_class == "upstream" and up_hit:
            up_obj, up_rule, up_feat, up_meta, tk, new_val, old = up_hit
            rep.upstream = {"handoff": up_meta.handoff, "rule": up_rule, "binding": up_feat, "target_key": tk,
                            "agent": up_meta.owner, "resampled": str(new_val)[:400]}
            if depth + 1 < self.max_depth:
                down_value = cand

                def downstream(u: str, up_obj=up_obj, up_feat=up_feat) -> bool:
                    # the downstream binding's failed value (or one replay) passes with u in place
                    prev = getattr(up_obj, up_feat)
                    try:
                        setattr(up_obj, up_feat, u)
                        if down_value and self._check(b, tp, obj, match, helpers, down_value):
                            return True
                        fp3 = eval_expr(b.footprint_expr, match.bindings, helpers)
                        return self._check(b, tp, obj, match, helpers, self._sample(b, match, helpers, fp3))
                    finally:
                        setattr(up_obj, up_feat, prev)

                uh, utp, ub, umatch, uobj, _ = self._context(up_meta.handoff, up_rule, tk, up_feat)

                def up_oracle(u: str) -> bool:
                    return self._check(ub, utp, uobj, umatch, uh, u) and downstream(u)

                inner = self.classify(up_meta.handoff, up_rule, tk, up_feat, clause="upstream", reason="",
                                      oracle=up_oracle, failed_value=old, depth=depth + 1)
                rep.upstream["report"] = inner.to_dict()
        return rep

    def _minimize(self, b, match, helpers, accepts, current: list[str], added: list[str]) -> list[str]:
        """Keep only the added paths without which no replay passes (greedy,
        by navigation prefix first, then path by path; bounded probes)."""
        keep = list(added)
        probes = 0

        def passes(paths: list[str]) -> bool:
            nonlocal probes
            probes += 1
            wfp = self._footprint_for(current + paths, match)
            for _ in range(self.n_rep):
                if accepts(self._sample(b, match, helpers, wfp)):
                    return True
            return False

        groups: dict[str, list[str]] = {}
        for p in keep:
            groups.setdefault(".".join(p.split(".")[:2]), []).append(p)
        for g, ps in list(groups.items()):
            if probes >= self.max_minimize_probes or len(groups) <= 1:
                break
            trial = [p for p in keep if p not in ps]
            if trial and passes(trial):
                keep = trial
                groups.pop(g)
        for p in list(keep):
            if probes >= self.max_minimize_probes or len(keep) <= 1:
                break
            trial = [x for x in keep if x != p]
            if passes(trial):
                keep = trial
        return keep


def attribute_all(session: Session, failures: list[Failure], *, n_rep: int = 3, h: int = 2, limit: int = 2,
                  exhaustive: bool = False, minimize: bool = True) -> tuple[list[FaultReport], int]:
    """Attribute up to `limit` distinct root failures. A cover(G) failure whose
    chain leads to an already located binding is not attributed again, nor
    is a stale stamp left behind by an escalation."""
    att = Attributor(session, n_rep=n_rep, h=h, exhaustive=exhaustive, minimize=minimize)
    # escalations first (the symptoms nearest to their cause), then invalid values
    located = sorted((f for f in failures if f.clause in ("noEsc", "valid") and f.target_key),
                     key=lambda f: 0 if f.clause == "noEsc" else 1)
    roots = list(located)
    toks = session._enter()
    try:
        for f in failures:
            if f.clause == "cover(G)":
                loc = att.locate(f)
                if loc is None or all((x.rule, x.binding, x.target_key) != (loc.rule, loc.binding, loc.target_key)
                                      for x in roots):
                    roots.append(loc or f)
            elif f.clause == "fresh" and not located:
                roots.append(f)
            elif f.clause not in ("noEsc", "valid", "cover(G)", "fresh") and not located:
                roots.append(f)  # a library clause with no located binding
    finally:
        session._exit(toks)
    seen, reports = set(), []
    for f in roots:
        key = (f.rule, f.binding, f.target_key, f.goal_element if f.clause == "cover(G)" and not f.binding else None)
        if key in seen:
            continue
        seen.add(key)
        if len(reports) >= limit:
            break
        reports.append(att.attribute(f))
    return reports, att.calls


def goal_key(obj: Any) -> str | None:
    try:
        return element_key(obj)
    except Exception:  # noqa: BLE001
        return None
