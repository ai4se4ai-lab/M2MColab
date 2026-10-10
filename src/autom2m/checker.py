"""Deterministic admission checker for typed teams: conditions W1–W6.

All six are evaluated on the team specification alone (no task data, no LLM
call, no execution). Usage:

    python -m agentm2m.auto.checker teams/devteam_proposal.json [--naive]
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict, deque
from dataclasses import dataclass, field

from .typed_team import (
    DONE_VOCABULARY,
    Attr,
    Ref,
    TeamFormatError,
    TypedTeam,
    is_literal,
    load_team,
    parse_team,
    rule_env,
    type_path,
)
from .vlib import VLIB


@dataclass(frozen=True)
class Diagnostic:
    cond: str  # "W1".."W6"
    element: str  # offending element (rule.binding, view, clause, ...)
    message: str
    severity: str = "error"  # "error" | "warning"

    def __str__(self) -> str:
        return f"{self.cond}  {self.message}"


@dataclass
class CheckResult:
    diagnostics: list[Diagnostic] = field(default_factory=list)
    warnings: list[Diagnostic] = field(default_factory=list)

    @property
    def admitted(self) -> bool:
        return not self.diagnostics

    def conds(self) -> set[str]:
        return {d.cond for d in self.diagnostics}

    def report(self) -> str:
        lines = [str(d) for d in self.diagnostics]
        lines += [f"{w.cond}  warning: {w.message}" for w in self.warnings]
        lines.append("ADMITTED" if self.admitted else f"REJECTED: {len(self.diagnostics)} violation(s)")
        return "\n".join(lines)


_PATH_RE = re.compile(r"\b([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+)\b")


def _param_type(team: TypedTeam, t: str) -> str:
    return t.replace("@goal.", f"{team.goal_view}.")


class Checker:
    def __init__(self, team: TypedTeam, *, anchored: bool = True) -> None:
        self.t = team
        self.anchored = anchored
        self.res = CheckResult()

    # ------------------------------------------------------------------
    def err(self, cond: str, element: str, msg: str) -> None:
        d = Diagnostic(cond, element, msg)
        if d not in self.res.diagnostics:
            self.res.diagnostics.append(d)

    def warn(self, cond: str, element: str, msg: str) -> None:
        self.res.warnings.append(Diagnostic(cond, element, msg, "warning"))

    def run(self) -> CheckResult:
        self.w1()
        self.w2()
        self.w3()
        self.w4()
        self.w5()
        self.w6()
        order = {f"W{i}": i for i in range(1, 7)}
        self.res.diagnostics.sort(key=lambda d: order.get(d.cond, 9))
        return self.res

    # ------------------------------------------------------------------
    # W1: hand-offs are well typed
    # ------------------------------------------------------------------
    def w1(self) -> None:
        t = self.t
        for h in t.handoffs:
            if h.target not in t.views:
                self.err("W1", h.name, f"hand-off {h.name}: target view {h.target!r} is not declared")
            for s in h.sources:
                if s not in t.views:
                    self.err("W1", h.name, f"hand-off {h.name}: source view {s!r} is not declared")
            for r in h.rules:
                where = f"{r.name}"
                env = rule_env(r)
                for var, view, cname in r.sources:
                    if view not in h.sources:
                        self.err("W1", where, f"{where}: source {var} : {view}!{cname} is not in the sources {h.sources} of {h.name}")
                    elif t.cls(view, cname) is None:
                        self.err("W1", where, f"{where}: {view}!{cname} is not declared")
                if r.target_view != h.target:
                    self.err("W1", where, f"{where}: creates {r.target_view}!{r.target_cls}, but {h.name} targets {h.target}")
                tdecl = t.cls(r.target_view, r.target_cls)
                if tdecl is None:
                    self.err("W1", where, f"{where}: target class {r.target_view}!{r.target_cls} is not declared")
                    continue
                if r.guard:
                    for p in _PATH_RE.findall(r.guard):
                        if p.split(".")[0] in env:
                            pt = type_path(t, p, env)
                            if not pt.ok:
                                self.err("W1", f"{where}.guard", f"{where} guard: path '{p}': {pt.error}")
                for f, expr in r.bind.items():
                    feat = tdecl.feature(f)
                    if feat is None:
                        self.err("W1", f"{where}.{f}", f"{where}.{f}: {r.target_view}!{r.target_cls} has no feature '{f}' (declare it in the class or remove the binding; features: {sorted(list(tdecl.attrs) + list(tdecl.refs))})")
                        continue
                    if is_literal(expr):
                        if isinstance(feat, Ref):
                            self.err("W1", f"{where}.{f}", f"{where}.{f}: a literal cannot fill reference '{f}'")
                        continue
                    pt = type_path(t, expr, env, object_reads_all=False)
                    if not pt.ok:
                        self.err("W1", f"{where}.{f}", f"{where}.{f}: path '{expr}': {pt.error}")
                        continue
                    self._views_ok(h, where, f, expr, pt.views)
                    if isinstance(feat, Attr) and pt.end_kind == "object":
                        self.err("W1", f"{where}.{f}", f"{where}.{f}: '{expr}' is an object, but {f} is an attribute of type {feat.type}")
                    if isinstance(feat, Ref) and pt.end_kind == "attr":
                        self.err("W1", f"{where}.{f}", f"{where}.{f}: '{expr}' is a value, but {f} references {feat.view}!{feat.cls}")
                for b in r.llm:
                    bw = f"{where}.{b.feature}"
                    feat = tdecl.feature(b.feature)
                    if feat is None:
                        self.err("W1", bw, f"{bw}: {r.target_view}!{r.target_cls} has no feature '{b.feature}'")
                    elif not isinstance(feat, Attr):
                        self.err("W1", bw, f"{bw}: stochastic bindings may only fill primitive attributes")
                    if not b.footprint:
                        self.err("W1", bw, f"{bw}: empty footprint")
                    for p in b.footprint:
                        pt = type_path(t, p, env)
                        if not pt.ok:
                            self.err("W1", bw, f"{bw}: footprint path '{p}': {pt.error}")
                        else:
                            self._views_ok(h, where, b.feature, p, pt.views)
                    if not b.validators:
                        self.err("W1", bw, f"{bw}: stochastic binding without a validator")
                    for v in b.validators:
                        spec = VLIB.get(v.id)
                        if spec is None:
                            self.err("W1", bw, f"{bw}: unknown validator '{v.id}'")
                            continue
                        for pname, param in spec.params.items():
                            if pname not in v.args:
                                self.err("W1", bw, f"{bw}: validator {v.id} needs argument '{pname}'")
                                continue
                            pt = type_path(t, v.args[pname], env, object_reads_all=False)
                            if not pt.ok:
                                self.err("W1", bw, f"{bw}: validator {v.id}.{pname} path '{v.args[pname]}': {pt.error}")
                                continue
                            self._views_ok(h, where, b.feature, v.args[pname], pt.views)
                            want = _param_type(t, param.type)
                            if want == "string" and pt.end_kind != "attr":
                                self.err("W1", bw, f"{bw}: validator {v.id}.{pname} expects a string attribute, '{v.args[pname]}' is an object; navigate to one of its attributes")
                            elif want.endswith(".*") and not (pt.end_kind == "object" and pt.end_type.startswith(want[:-1])):
                                self.err("W1", bw, f"{bw}: validator {v.id}.{pname} expects an object of view {want[:-2]}, got {pt.end_type or pt.end_kind}; pass the {want[:-2]} object itself (a variable or reference path such as 'm'), not one of its attributes")
                            elif want != "string" and not want.endswith(".*") and pt.end_type != want:
                                self.err("W1", bw, f"{bw}: validator {v.id}.{pname} expects {want}, got {pt.end_type or pt.end_kind}")
                        for extra in v.args:
                            if extra not in spec.params:
                                self.err("W1", bw, f"{bw}: validator {v.id} has no parameter '{extra}'")

    def _views_ok(self, h, where: str, f: str, path: str, views: set[str]) -> None:
        outside = sorted(views - set(h.sources))
        if outside:
            self.err("W1", f"{where}.{f}", f"{where}.{f}: path '{path}' reads view(s) {outside} that are not sources of {h.name}")

    # ------------------------------------------------------------------
    # W2: every artefact has exactly one writer
    # ------------------------------------------------------------------
    def w2(self) -> None:
        t = self.t
        writers: dict[str, list[str]] = defaultdict(list)
        for a, vs in t.writes.items():
            if a not in t.agents:
                self.err("W2", a, f"write rights given to unknown agent {a!r}")
            for v in vs:
                writers[v].append(a)
        for v in t.views:
            if v == t.goal_view:
                if len(writers.get(v, [])) > 1:
                    self.err("W2", v, f"goal view {v} written by {sorted(writers[v])}; at most one agent may own the lifted task")
                continue
            ws = writers.get(v, [])
            if len(ws) != 1:
                self.err("W2", v, f"view {v} written by {sorted(ws)}; needs exactly one writer")
        for v in writers:
            if v not in t.views:
                self.err("W2", v, f"write right on undeclared view {v!r}")
        producers: dict[str, list[str]] = defaultdict(list)
        for h, r in t.rules():
            producers[f"{r.target_view}.{r.target_cls}"].append(f"{h.name}.{r.name}")
        for cls, rs in producers.items():
            if len(rs) > 1:
                self.err("W2", cls, f"class {cls} is created by {len(rs)} rules {rs}; needs exactly one producer (give the other rule its own class or merge the rules)")

    # ------------------------------------------------------------------
    # W3: targets are complete
    # ------------------------------------------------------------------
    def w3(self) -> None:
        t = self.t
        maps: set[tuple[str, str]] = set()  # (source class qname, target class qname)
        for _h, r in t.rules():
            for _v, view, c in r.sources:
                maps.add((f"{view}.{c}", f"{r.target_view}.{r.target_cls}"))
        for h, r in t.rules():
            tdecl = t.cls(r.target_view, r.target_cls)
            if tdecl is None:
                continue
            bound = list(r.bind) + [b.feature for b in r.llm]
            for fname in set(bound):
                if bound.count(fname) > 1:
                    self.err("W3", f"{r.name}.{fname}", f"{r.name}: feature {fname} is bound {bound.count(fname)} times")
            for fname, feat in list(tdecl.attrs.items()) + list(tdecl.refs.items()):
                if feat.required and fname not in bound:
                    self.err("W3", f"{r.name}.{fname}", f"{r.name}: mandatory feature {tdecl.name}.{fname} is never bound (bind it in 'bind' or 'llm', or mark it optional)")
            env = rule_env(r)
            for fname, expr in r.bind.items():
                feat = tdecl.feature(fname)
                if not isinstance(feat, Ref) or is_literal(expr):
                    continue
                pt = type_path(t, expr, env, object_reads_all=False)
                if not pt.ok or pt.end_kind != "object":
                    continue
                want = f"{feat.view}.{feat.cls}"
                if pt.end_type != want and (pt.end_type, want) not in maps:
                    self.err("W3", f"{r.name}.{fname}",
                             f"{r.name}.{fname}: '{expr}' yields {pt.end_type}, but no rule maps it to {want} (dangling reference)")

    # ------------------------------------------------------------------
    # W4: every obligation reaches a check that reads it
    # ------------------------------------------------------------------
    def dataflow(self):
        """Feature-level data-flow graph F and the set B of checked features."""
        t = self.t
        edges: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
        checked: set[tuple[str, str]] = set()
        for _h, r in t.rules():
            env = rule_env(r)
            tq = f"{r.target_view}.{r.target_cls}"
            for fname, expr in r.bind.items():
                if is_literal(expr):
                    continue
                pt = type_path(t, expr, env, object_reads_all=False)
                for rd in pt.reads:
                    edges[rd].add((tq, fname))
            for b in r.llm:
                reads = []
                for p in b.footprint:
                    reads += type_path(t, p, env).reads
                behaviour = False
                for v in b.validators:
                    spec = VLIB.get(v.id)
                    if spec is None:
                        continue
                    behaviour = behaviour or spec.strength == "behaviour"
                    for pname, param in spec.params.items():
                        if param.mode == "read" and pname in v.args:
                            reads += type_path(t, v.args[pname], env).reads
                for rd in reads:
                    edges[rd].add((tq, b.feature))
                if behaviour:
                    checked.add((tq, b.feature))
        return edges, checked

    def _reach(self, edges, starts) -> set:
        seen, q = set(starts), deque(starts)
        while q:
            n = q.popleft()
            for m in edges.get(n, ()):
                if m not in seen:
                    seen.add(m)
                    q.append(m)
        return seen

    def handoff_graph(self) -> dict[str, set[str]]:
        g: dict[str, set[str]] = defaultdict(set)
        for h in self.t.handoffs:
            for s in h.sources:
                g[s].add(h.target)
        return g

    def w4(self) -> None:
        t = self.t
        edges, checked = self.dataflow()
        gv = t.goal_view
        if not t.goal:
            self.err("W4", "goal", "no goal obligation declared")
        for ob in t.goal:
            decl = t.cls(gv, ob.cls)
            label = f"({ob.cls}, {ob.scope})"
            if decl is None:
                self.err("W4", label, f"goal obligation {label}: {gv}!{ob.cls} is not a goal-view class")
                continue
            starts = [(decl.qname, a) for a in decl.attrs]
            if ob.kind == "delivered":
                d = t.deliverable or {}
                target = (f"{d.get('view')}.{d.get('class')}", str(d.get("feature")))
                if not d:
                    self.err("W4", label, f"goal obligation {label} must be delivered, but the team declares no deliverable")
                    continue
                ddecl = t.cls(str(d.get("view")), str(d.get("class")))
                if ddecl is None or ddecl.feature(str(d.get("feature"))) is None:
                    self.err("W4", label, f"deliverable {target[0]}.{target[1]} is not declared")
                    continue
                fr = ddecl.refs.get(str(d.get("for")))
                if fr is None or (fr.view, fr.cls) != (gv, ob.cls):
                    self.err("W4", label, f"deliverable {target[0]} must reference the {gv}!{ob.cls} it delivers via '{d.get('for')}'")
                if target not in checked:
                    self.err("W4", label, f"deliverable {target[0]}.{target[1]} is never checked by a behavioural validator")
                if self.anchored:
                    if target not in self._reach(edges, starts):
                        self.err("W4", label, f"goal obligation {label}: no deliverable value is derived from {ob.cls}")
                else:
                    if not self._class_path(decl.qname, {target[0]}):
                        self.err("W4", label, f"goal obligation {label}: no rule chain from {ob.cls} creates the deliverable")
                continue
            if self.anchored:
                if not (self._reach(edges, starts) & checked):
                    self.err("W4", label, f"goal obligation {label}: no behavioural validator reads data derived from {ob.cls}")
            else:
                checked_classes = {c for c, _f in checked}
                if not self._class_path(decl.qname, checked_classes):
                    self.err("W4", label, f"goal obligation {label}: no rule chain reaches a checked class")
            # guard warnings (instance-level coverage is enforced by cover(G) at runtime)
            for _h, r in t.rules():
                if r.guard and any(view == gv and c == ob.cls for _v, view, c in r.sources) and r.guard.strip() != ob.scope.strip():
                    self.warn("W4", r.name, f"guard of {r.name} differs from the scope of {label}; coverage is enforced at runtime")
        # (b) no idle or unverified agent
        hg = self.handoff_graph()
        reach_from_goal = self._reach(hg, [gv])
        views_with_check = {c.split(".")[0] for c, _f in checked}
        for v in t.views:
            if v == gv:
                continue
            if v not in reach_from_goal:
                self.err("W4", v, f"view {v} is not reachable from the goal view (its agent receives no work)")
            elif not (self._reach(hg, [v]) & views_with_check):
                self.err("W4", v, f"view {v} reaches no behavioural check (its work is never verified)")

    def _class_path(self, start: str, goals: set[str]) -> bool:
        """Naive (class-level) coverage: is some goal class reachable in the
        type graph whose edges are rule source -> rule target, plus the
        references between classes (in both directions)?"""
        g: dict[str, set[str]] = defaultdict(set)
        for _h, r in self.t.rules():
            for _v, view, c in r.sources:
                g[f"{view}.{c}"].add(f"{r.target_view}.{r.target_cls}")
        for decl in self.t.classes.values():
            for ref in decl.refs.values():
                other = f"{ref.view}.{ref.cls}"
                if ref.view == decl.view:  # links inside one view (e.g. containment)
                    g[decl.qname].add(other)
                    g[other].add(decl.qname)
        reach = self._reach(g, [start])
        return bool(reach & (goals - {start})) or start in goals

    # ------------------------------------------------------------------
    # W5: the engine decides completion
    # ------------------------------------------------------------------
    def w5(self) -> None:
        for c in self.t.done:
            if c not in DONE_VOCABULARY:
                self.err("W5", c, f"done-clause {c} is not engine-checkable")
        for c in DONE_VOCABULARY:
            if c not in self.t.done:
                self.err("W5", c, f"acceptance predicate lacks engine clause {c}")
        # acyclic view graph (Kahn's algorithm; iterative, so deep chains are fine)
        g = self.handoff_graph()
        nodes = set(g) | {w for ws in g.values() for w in ws}
        indeg = {n: 0 for n in nodes}
        for ws in g.values():
            for w in ws:
                indeg[w] += 1
        q = deque(n for n in nodes if indeg[n] == 0)
        seen = 0
        while q:
            u = q.popleft()
            seen += 1
            for w in g.get(u, ()):
                indeg[w] -= 1
                if indeg[w] == 0:
                    q.append(w)
        if seen != len(nodes):
            self.err("W5", "views", "hand-off graph has a cycle; termination is not guaranteed")

    # ------------------------------------------------------------------
    # W6: work goes to agents that can do it
    # ------------------------------------------------------------------
    def w6(self) -> None:
        t = self.t
        for h in t.handoffs:
            owners = t.owner_of(h.target)
            for r in h.rules:
                for b in r.llm:
                    need = set(b.tools)
                    for v in b.validators:
                        if v.id in VLIB:
                            need |= set(VLIB[v.id].tools)
                    for a in owners:
                        have = set(t.agents[a].tools) if a in t.agents else set()
                        if not need <= have:
                            self.err("W6", f"{r.name}.{b.feature}",
                                     f"{r.name}.{b.feature} needs {sorted(need)}; owner {a} has {sorted(have)}")


def check(team: TypedTeam | dict, *, anchored: bool = True) -> CheckResult:
    if isinstance(team, dict):
        try:
            team = parse_team(team)
        except TeamFormatError as exc:
            return CheckResult([Diagnostic("W1", "format", str(exc))])
    return Checker(team, anchored=anchored).run()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="AutoM2M admission checker (W1–W6)")
    ap.add_argument("team", nargs="+")
    ap.add_argument("--naive", action="store_true", help="class-level (non-anchored) W4")
    a = ap.parse_args(argv)
    rc = 0
    for p in a.team:
        if len(a.team) > 1:
            print(f"== {p}")
        res = check(load_team(p), anchored=not a.naive)
        print(res.report())
        rc |= 0 if res.admitted else 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
