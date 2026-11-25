"""Component 4, the Checker: admission of typed teams by the six
well-formedness conditions W1-W6 (paper Sec. 3.4, Algorithm 3).

All six are evaluated on the team specification alone: no task data, no LLM
call, no execution. Diagnostics name the condition, the offending element
and (for the builder) a hint.

    python -m autom2m.checker teams/devteam_proposal.json
    python -m autom2m.checker teams/devteam_admitted.json --w4 one-sided

W4 variants (RQ2): `two-sided` (Def. 9, the default), `one-sided` (check
edges suffice) and `path-only` (a class-level path from the goal class to
some behaviour-checked class).
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field

from .typed_team import (
    DONE_VOCABULARY,
    Attr,
    Ref,
    TeamFormatError,
    TypedTeam,
    check_paths,
    is_literal,
    load_team,
    parse_team,
    rule_env,
    type_path,
)
from .vlib import LIBRARY_CLAUSES, TOOLS, VLIB

W4_MODES = ("two-sided", "one-sided", "path-only")
COND_ORDER = {f"W{i}": i for i in range(1, 7)}


@dataclass(frozen=True)
class Diagnostic:
    cond: str  # "W1".."W6"
    element: str  # offending element (rule.binding, view, clause, ...)
    message: str
    severity: str = "error"  # "error" | "warning"
    hint: str = ""

    def __str__(self) -> str:
        return f"{self.cond}  {self.message}"

    def with_hint(self) -> str:
        return f"{self}" + (f"\n        hint: {self.hint}" if self.hint else "")


@dataclass
class CheckResult:
    diagnostics: list[Diagnostic] = field(default_factory=list)
    warnings: list[Diagnostic] = field(default_factory=list)
    seconds: float = 0.0
    cond_seconds: dict[str, float] = field(default_factory=dict)

    @property
    def admitted(self) -> bool:
        return not self.diagnostics

    def conds(self) -> set[str]:
        return {d.cond for d in self.diagnostics}

    def first_cond(self) -> str | None:
        return self.diagnostics[0].cond if self.diagnostics else None

    def report(self, *, hints: bool = False, warnings: bool = True) -> str:
        lines = [d.with_hint() if hints else str(d) for d in self.diagnostics]
        if warnings:
            lines += [f"{w.cond}  warning: {w.message}" for w in self.warnings]
        lines.append("ADMITTED" if self.admitted else f"REJECTED: {len(self.diagnostics)} violation(s)")
        return "\n".join(lines)


_PATH_RE = re.compile(r"(?<!['\"\w])([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+)")
_IND = "\n        "


def _goal_type(team: TypedTeam, t: str) -> str:
    return t.replace("@goal.", f"{team.goal_view}.")


def _paths_in(expr: str) -> list[str]:
    stripped = re.sub(r"'[^']*'|\"[^\"]*\"", "''", expr or "")
    return _PATH_RE.findall(stripped)


class Checker:
    def __init__(self, team: TypedTeam, *, w4: str = "two-sided") -> None:
        if w4 not in W4_MODES:
            raise ValueError(f"w4 must be one of {W4_MODES}")
        self.t = team
        self.w4_mode = w4
        self.res = CheckResult()

    # ------------------------------------------------------------------
    def err(self, cond: str, element: str, msg: str, hint: str = "") -> None:
        d = Diagnostic(cond, element, msg, "error", hint)
        if all((x.cond, x.element, x.message) != (d.cond, d.element, d.message) for x in self.res.diagnostics):
            self.res.diagnostics.append(d)

    def warn(self, cond: str, element: str, msg: str) -> None:
        d = Diagnostic(cond, element, msg, "warning")
        if d not in self.res.warnings:
            self.res.warnings.append(d)

    def run(self) -> CheckResult:
        t0 = time.perf_counter()
        for name, fn in (("W1", self.w1), ("W2", self.w2), ("W3", self.w3), ("W4", self.w4),
                         ("W5", self.w5), ("W6", self.w6)):
            s = time.perf_counter()
            fn()
            self.res.cond_seconds[name] = time.perf_counter() - s
        self.res.diagnostics.sort(key=lambda d: COND_ORDER.get(d.cond, 9))
        self.res.seconds = time.perf_counter() - t0
        return self.res

    def _allowed_views(self, h) -> set[str]:
        # a rule reads its hand-off's source views; every view may navigate to
        # the (read-only) goal view, since agents' views refer to the goal by design
        return set(h.sources) | {self.t.goal_view}

    def _views_ok(self, h, element: str, path: str, views: set[str]) -> None:
        outside = sorted(views - self._allowed_views(h))
        if outside:
            self.err("W1", element, f"{element}: path '{path}' reads view(s) {outside} that are not sources of {h.name}",
                     f"add {outside} to the sources of {h.name} or read the data through a source view")

    # ------------------------------------------------------------------
    # W1: hand-offs are well typed and stratified
    # ------------------------------------------------------------------
    def stochastic_features(self) -> set[tuple[str, str]]:
        return {(f"{r.target_view}.{r.target_cls}", b.feature) for _h, r in self.t.rules() for b in r.llm}

    def w1(self) -> None:
        t = self.t
        stoch = self.stochastic_features()
        for h in t.handoffs:
            if h.target not in t.views:
                self.err("W1", h.name, f"hand-off {h.name}: target view {h.target!r} is not declared")
            if h.target == t.goal_view:
                self.err("W1", h.name, f"hand-off {h.name} targets the goal view {t.goal_view}, which only Lift writes")
            if h.target in h.sources:
                self.err("W1", h.name, f"hand-off {h.name} reads its own target view {h.target}")
            for s in h.sources:
                if s not in t.views:
                    self.err("W1", h.name, f"hand-off {h.name}: source view {s!r} is not declared")
            for r in h.rules:
                where = r.name
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
                    for p in _paths_in(r.guard):
                        if p.split(".")[0] not in env:
                            continue
                        pt = type_path(t, p, env, object_reads_all=False)
                        if not pt.ok:
                            self.err("W1", f"{where}.guard", f"{where} guard: path '{p}':{_IND}{pt.error}", pt.hint)
                            continue
                        self._views_ok(h, f"{where}.guard", p, pt.views)
                        for rd in pt.reads:
                            if rd in stoch:
                                self.err("W1", f"{where}.guard", f"{where} guard reads {rd[0]}.{rd[1]}, which a stochastic binding writes (stratification)",
                                         "guards may only read structural features")
                for f, expr in r.bind.items():
                    el = f"{where}.{f}"
                    feat = tdecl.feature(f)
                    if feat is None:
                        self.err("W1", el, f"{el}: {r.target_view}!{r.target_cls} has no feature '{f}'",
                                 f"declare it in the class or remove the binding; features: {sorted(list(tdecl.attrs) + list(tdecl.refs))}")
                        continue
                    if is_literal(expr):
                        if isinstance(feat, Ref):
                            self.err("W1", el, f"{el}: a literal cannot fill reference '{f}'")
                        continue
                    pt = type_path(t, expr, env, object_reads_all=False)
                    if not pt.ok:
                        self.err("W1", el, f"{el}: path '{expr}':{_IND}{pt.error}", pt.hint)
                        continue
                    self._views_ok(h, el, expr, pt.views)
                    if isinstance(feat, Attr) and pt.end_kind == "object":
                        self.err("W1", el, f"{el}: '{expr}' is an object, but {f} is an attribute of type {feat.type}",
                                 "navigate to one of its attributes (e.g. 'm.examples.call'), or declare a reference")
                    if isinstance(feat, Ref) and pt.end_kind == "attr":
                        self.err("W1", el, f"{el}: '{expr}' is a value, but {f} references {feat.view}!{feat.cls}")
                    if pt.many and not feat.many:
                        self.err("W1", el, f"{el}: '{expr}' yields many values, but {f} is single-valued",
                                 "declare the feature with \"many\": true, or bind a single-valued path")
                    for rd in pt.reads:
                        if rd in stoch:
                            self.err("W1", el, f"{el}: structural binding reads {rd[0]}.{rd[1]}, which a stochastic binding writes (stratification)",
                                     "make it a stochastic binding or read a structural feature")
                for b in r.llm:
                    bw = f"{where}.{b.feature}"
                    feat = tdecl.feature(b.feature)
                    if feat is None:
                        self.err("W1", bw, f"{bw}: {r.target_view}!{r.target_cls} has no feature '{b.feature}'",
                                 f"declare attribute '{b.feature}' in class {r.target_cls}, or let the LLM fill one of "
                                 f"its attributes {sorted(tdecl.attrs)}")
                    elif not isinstance(feat, Attr):
                        self.err("W1", bw, f"{bw}: stochastic bindings may only fill primitive attributes")
                    elif feat.many:
                        self.err("W1", bw, f"{bw}: stochastic bindings may only fill single-valued attributes",
                                 f"declare {b.feature} as \"string\" (one value), not many-valued")
                    if not b.footprint:
                        self.err("W1", bw, f"{bw}: empty footprint", "list the source paths the LLM may read")
                    for p in b.footprint:
                        pt = type_path(t, p, env)
                        if not pt.ok:
                            self.err("W1", bw, f"{bw}: footprint path '{p}':{_IND}{pt.error}", pt.hint)
                        else:
                            self._views_ok(h, bw, p, pt.views)
                    if not b.validators:
                        self.err("W1", bw, f"{bw}: stochastic binding without a validator", "add a validator from the library")
                    for v in b.validators:
                        spec = VLIB.get(v.id)
                        if spec is None or not spec.offered:
                            self.err("W1", bw, f"{bw}: unknown validator '{v.id}'",
                                     f"use one of {[k for k, s in VLIB.items() if s.offered]}")
                            continue
                        for pname, param in spec.params.items():
                            if pname not in v.args:
                                if not param.optional:
                                    self.err("W1", bw, f"{bw}: validator {v.id} needs argument '{pname}'")
                                continue
                            arg = v.args[pname]
                            pt = type_path(t, arg, env, object_reads_all=False)
                            if not pt.ok:
                                self.err("W1", bw, f"{bw}: validator {v.id}.{pname} path '{arg}':{_IND}{pt.error}", pt.hint)
                                continue
                            self._views_ok(h, bw, arg, pt.views)
                            want = _goal_type(t, param.type)
                            if want == "string" and pt.end_kind not in ("attr", "literal"):
                                self.err("W1", bw, f"{bw}: validator {v.id}.{pname} expects a string, '{arg}' is an object",
                                         "navigate to one of its attributes")
                            elif want.endswith(".*") and not (pt.end_kind == "object" and pt.end_type.startswith(want[:-1])):
                                self.err("W1", bw, f"{bw}: validator {v.id}.{pname} expects a {t.goal_view} object, got {pt.end_type or pt.end_kind}",
                                         "pass the goal object itself (a variable or reference path such as 'm' or 'd.method')")
                            elif want != "string" and not want.endswith(".*") and not (pt.end_kind == "object" and pt.end_type == want):
                                self.err("W1", bw, f"{bw}: validator {v.id}.{pname} expects a {want} object, got {pt.end_type or pt.end_kind}",
                                         "pass the object itself (a variable or reference path such as 'm' or 'd.method')")
                        for extra in v.args:
                            if extra not in spec.params:
                                self.err("W1", bw, f"{bw}: validator {v.id} has no parameter '{extra}'")

    # ------------------------------------------------------------------
    # W2: every artefact has one writer
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
                if writers.get(v):
                    self.err("W2", v, f"goal view {v} written by {sorted(writers[v])}; only Lift writes it")
                continue
            ws = writers.get(v, [])
            if len(ws) != 1:
                self.err("W2", v, f"view {v} written by {sorted(ws)}; needs exactly one writer",
                         f"keep {v} in the 'writes' of exactly one agent" + (" and remove it from the others" if ws else ""))
        for v in writers:
            if v not in t.views:
                self.err("W2", v, f"write right on undeclared view {v!r}")
        producers: dict[str, list[str]] = defaultdict(list)
        for _h, r in t.rules():
            producers[f"{r.target_view}.{r.target_cls}"].append(r.name)
        for cls, rs in producers.items():
            if len(rs) > 1:
                self.err("W2", cls, f"class {cls} is created by {len(rs)} rules {rs}; needs exactly one producer",
                         "give the other rule its own class or merge the rules")

    # ------------------------------------------------------------------
    # W3: targets are complete
    # ------------------------------------------------------------------
    def class_maps(self) -> set[tuple[str, str]]:
        """(source class, target class) pairs some rule maps (ATL resolution)."""
        maps = set()
        for _h, r in self.t.rules():
            for _v, view, c in r.sources:
                maps.add((f"{view}.{c}", f"{r.target_view}.{r.target_cls}"))
        return maps

    def w3(self) -> None:
        t = self.t
        maps = self.class_maps()
        for _h, r in t.rules():
            tdecl = t.cls(r.target_view, r.target_cls)
            if tdecl is None:
                continue
            bound = list(r.bind) + [b.feature for b in r.llm]
            for fname in set(bound):
                if bound.count(fname) > 1:
                    self.err("W3", f"{r.name}.{fname}", f"{r.name}: feature {fname} is bound {bound.count(fname)} times")
            for fname, feat in list(tdecl.attrs.items()) + list(tdecl.refs.items()):
                if feat.required and fname not in bound:
                    self.err("W3", f"{r.name}.{fname}", f"{r.name}: mandatory feature {tdecl.name}.{fname} is never bound",
                             "bind it in 'bind' or 'llm', or mark it optional")
            env = rule_env(r)
            for fname, expr in r.bind.items():
                feat = tdecl.feature(fname)
                if not isinstance(feat, Ref) or is_literal(expr):
                    continue
                pt = type_path(t, expr, env, object_reads_all=False)
                if not pt.ok or pt.end_kind != "object":
                    continue
                want = f"{feat.view}.{feat.cls}"
                if pt.end_type == want:
                    continue
                if feat.view == r.target_view:
                    if (pt.end_type, want) not in maps:
                        self.err("W3", f"{r.name}.{fname}",
                                 f"{r.name}.{fname}: '{expr}' yields {pt.end_type}, but no rule maps it to {want} (unresolvable reference)")
                else:
                    self.err("W3", f"{r.name}.{fname}",
                             f"{r.name}.{fname}: '{expr}' yields {pt.end_type}, but {fname} references {want}")

    # ------------------------------------------------------------------
    # W4: every goal is anchored (feature-level data flow, Def. 9)
    # ------------------------------------------------------------------
    def graph(self):
        """(production edges, check edges, B): the feature-level data-flow
        graph F_Theta and the behaviour-checked features."""
        t = self.t
        prod: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
        chk: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
        behaviour: set[tuple[str, str]] = set()
        for _h, r in t.rules():
            env = rule_env(r)
            tq = f"{r.target_view}.{r.target_cls}"
            for fname, expr in r.bind.items():
                if is_literal(expr):
                    continue
                for rd in type_path(t, expr, env, object_reads_all=False).reads:
                    prod[rd].add((tq, fname))
            for b in r.llm:
                for p in b.footprint:
                    for rd in type_path(t, p, env).reads:
                        prod[rd].add((tq, b.feature))
                for p in check_paths(t, r, b):
                    for rd in type_path(t, p, env).reads:
                        chk[rd].add((tq, b.feature))
                if any(VLIB.get(v.id) and VLIB[v.id].strength == "behaviour" for v in b.validators):
                    behaviour.add((tq, b.feature))
        return prod, chk, behaviour

    @staticmethod
    def _reach(edges, starts) -> set:
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

    @staticmethod
    def _dist(edges, starts) -> dict:
        """BFS distances from the start nodes."""
        dist = {n: 0 for n in starts}
        q = deque(starts)
        while q:
            n = q.popleft()
            for m in edges.get(n, ()):
                if m not in dist:
                    dist[m] = dist[n] + 1
                    q.append(m)
        return dist

    def _anchored_at(self, starts, prod, chk, behaviour, union) -> list[tuple[str, str]]:
        """The behaviour-checked features at which all anchors are anchored,
        closest first."""
        if not starts:
            return []
        if self.w4_mode == "one-sided":
            dists = [self._dist(union, [a]) for a in starts]
            cands = set.intersection(*[set(d) for d in dists]) & behaviour
        else:
            dists = [self._dist(prod, [a]) for a in starts]
            creach = [{y for x in d for y in chk.get(x, ())} for d in dists]
            cands = set.intersection(*[set(d) for d in dists]) & set.intersection(*creach) & behaviour
        udist = [self._dist(union, [a]) for a in starts] if self.w4_mode != "one-sided" else dists
        return sorted(cands, key=lambda c: (max(d.get(c, 0) for d in udist), c))

    def _guard_kind(self, r, scope: str) -> str:
        """'implied' (by the scope), 'join' (equates structural references to
        the same goal object) or 'other'."""
        key = (r.name, scope)
        cache = self.__dict__.setdefault("_gk_cache", {})
        if key in cache:
            return cache[key]
        g = (r.guard or "").strip()
        kind = "join"
        if not g or g == "true" or g == scope.strip():
            kind = "implied"
        else:
            env = rule_env(r)
            for conj in re.split(r"\band\b", g):
                m = re.fullmatch(r"\s*([\w.]+)\s*={1,2}\s*([\w.]+)\s*", conj)
                if not m or is_literal(m.group(1)) or is_literal(m.group(2)):
                    kind = "other"
                    break
                a = type_path(self.t, m.group(1), env, object_reads_all=False)
                b = type_path(self.t, m.group(2), env, object_reads_all=False)
                if not (a.ok and b.ok and a.end_kind == b.end_kind == "object" and a.end_type == b.end_type
                        and a.end_type.startswith(self.t.goal_view + ".")):
                    kind = "other"
                    break
        cache[key] = kind
        return kind

    def _total_classes(self, scope: str) -> tuple[dict[str, bool], dict[str, tuple[str, str]]]:
        """Totality of every class (iterative, memoised per scope): a goal
        class, or a class created by a rule whose guard is implied by the
        scope or is a join and whose source classes are total. Returns
        (class -> total, class -> (rule, warning) for non-implied guards)."""
        cache = self.__dict__.setdefault("_total_cache", {})
        if scope in cache:
            return cache[scope]
        t = self.t
        producer = {}
        for _h, r in t.rules():
            producer.setdefault(f"{r.target_view}.{r.target_cls}", r)
        total: dict[str, bool] = {}
        warns: dict[str, tuple[str, str]] = {}
        for root in list(t.classes):
            if root in total:
                continue
            stack = [(root, False)]
            onpath = set()
            while stack:
                q, expanded = stack.pop()
                if q in total:
                    continue
                if q.split(".")[0] == t.goal_view:
                    total[q] = True
                    continue
                r = producer.get(q)
                if r is None:
                    total[q] = False
                    continue
                srcs = [f"{v}.{c}" for _var, v, c in r.sources]
                if expanded:
                    onpath.discard(q)
                    total[q] = all(total.get(x, False) for x in srcs)
                    if self._guard_kind(r, scope) == "other":
                        warns[q] = (r.name, f"guard of {r.name} is not implied by the scope; coverage is enforced at run time by cover(G)")
                    continue
                if q in onpath:
                    total[q] = False  # a cycle never bottoms out in the goal
                    continue
                onpath.add(q)
                stack.append((q, True))
                stack.extend((x, False) for x in srcs if x not in total)
        cache[scope] = (total, warns)
        return total, warns

    def _chain_rules(self, fwd: set, target, rev_union) -> list:
        """Rules writing a feature on some path from the anchors to `target`."""
        chain = fwd & self._reach(rev_union, [target])
        writers = self.__dict__.setdefault("_writers", None)
        if writers is None:
            writers = defaultdict(list)
            for _h, r in self.t.rules():
                for f in list(r.bind) + [b.feature for b in r.llm]:
                    writers[(f"{r.target_view}.{r.target_cls}", f)].append(r)
            self._writers = writers
        out, seen = [], set()
        for node in chain:
            for r in writers.get(node, ()):
                if r.name not in seen:
                    seen.add(r.name)
                    out.append(r)
        return out

    def _chain_problems(self, label: str, chain_rules, scope: str) -> tuple[list[str], list[tuple[str, str]]]:
        total, warns = self._total_classes(scope)
        errors, w = [], []
        for r in chain_rules:
            if self._guard_kind(r, scope) == "other":
                w.append((r.name, f"guard of {r.name} is not implied by the scope of {label}; "
                                  "coverage is enforced at run time by cover(G)"))
            for _var, v, c in r.sources:
                q = f"{v}.{c}"
                if not total.get(q, False):
                    if not any(x.target_view == v and x.target_cls == c for _h, x in self.t.rules()) and v != self.t.goal_view:
                        errors.append(f"goal {label}: class {q} on the anchoring chain is never created")
                    else:
                        errors.append(f"goal {label}: class {q} on the anchoring chain is not total")
                elif q in warns:
                    w.append((warns[q][0], f"{warns[q][1]} ({label})"))
        return sorted(set(errors)), w

    def _apply_totality(self, label: str, results) -> bool:
        """Accept the first candidate chain without errors; else report."""
        for errors, warns in results:
            if not errors:
                for el, msg in warns:
                    self.warn("W4", el, msg)
                return True
        if results:
            for e in results[0][0]:
                self.err("W4", label, e)
        return False

    def _class_path(self, start: str, goals: set[str]) -> bool:
        """Path-only coverage: is a goal class reachable in the class graph
        whose edges are rule source -> rule target (plus links inside a view)?"""
        g = self.__dict__.get("_class_graph")
        if g is None:
            g = defaultdict(set)
            for _h, r in self.t.rules():
                for _v, view, c in r.sources:
                    g[f"{view}.{c}"].add(f"{r.target_view}.{r.target_cls}")
            for decl in self.t.classes.values():
                for ref in decl.refs.values():
                    other = f"{ref.view}.{ref.cls}"
                    if ref.view == decl.view:
                        g[decl.qname].add(other)
                        g[other].add(decl.qname)
            self._class_graph = g
        reach = self._reach(g, [start])
        return bool(reach & (goals - {start})) or start in goals

    def w4(self) -> None:
        t = self.t
        prod, chk, behaviour = self.graph()
        union: dict = defaultdict(set)
        for e in (prod, chk):
            for k, vs in e.items():
                union[k] |= vs
        rev_union: dict = defaultdict(set)
        for k, vs in union.items():
            for v in vs:
                rev_union[v].add(k)
        gv = t.goal_view
        if not t.goal:
            self.err("W4", "goal", "no goal obligation declared", "declare what must be checked and what must be delivered")
        d = t.deliverable or {}
        dnode = (f"{d.get('view')}.{d.get('class')}", str(d.get("feature"))) if d else None
        for ob in t.goal:
            decl = t.cls(gv, ob.cls)
            label = ob.label()
            if decl is None:
                self.err("W4", label, f"goal {label}: {gv}!{ob.cls} is not a goal-view class")
                continue
            anchors = ob.anchors if ob.anchors else list(decl.attrs)
            bad = [a for a in anchors if decl.feature(a) is None]
            if bad:
                self.err("W4", label, f"goal {label}: anchor feature(s) {bad} are not features of {ob.cls}")
                continue
            if ob.mode not in ("checked", "delivered"):
                self.err("W4", label, f"goal {label}: mode must be 'checked' or 'delivered'")
                continue
            starts = [(decl.qname, a) for a in anchors]
            if ob.mode == "delivered":
                if not d:
                    self.err("W4", label, f"goal {label} must be delivered, but the team declares no deliverable")
                    continue
                ddecl = t.cls(str(d.get("view")), str(d.get("class")))
                if ddecl is None or not isinstance(ddecl.feature(str(d.get("feature"))), Attr):
                    self.err("W4", label, f"deliverable {dnode[0]}.{dnode[1]} is not a declared attribute")
                    continue
                graph = prod if self.w4_mode == "two-sided" else union
                if self.w4_mode == "path-only":
                    ok = self._class_path(decl.qname, {dnode[0]})
                else:
                    ok = any(dnode in self._reach(graph, [s]) for s in starts)
                if not ok:
                    self.err("W4", label, f"goal {label}: no anchor feature reaches the deliverable {dnode[0]}.{dnode[1]}",
                             f"the rule that writes {dnode[1]} must (transitively) read {ob.cls}")
                elif self.w4_mode != "path-only":
                    fwd = set().union(*[self._reach(graph, [s]) for s in starts])
                    rev = rev_union if graph is union else {k: v for k, v in rev_union.items()}
                    self._apply_totality(label, [self._chain_problems(label, self._chain_rules(fwd, dnode, rev), ob.scope)])
                continue
            if self.w4_mode == "path-only":
                if not self._class_path(decl.qname, {c for c, _f in behaviour}):
                    self.err("W4", label, f"goal {label}: no rule chain reaches a behaviour-checked class")
                continue
            cands = self._anchored_at(starts, prod, chk, behaviour, union)
            if not cands:
                self.err("W4", label, f"goal {label}: anchor features {{{', '.join(anchors)}}}{_IND}reach no behaviour-checked value",
                         "a stochastic binding with a behaviour validator must read these features both in its "
                         "footprint (production) and in its validator (check)")
                continue
            # some anchoring chain must consist of rules with total source classes
            fwd = set().union(*[self._reach(union, [s]) for s in starts])
            results = []
            for at in cands:
                res = self._chain_problems(label, self._chain_rules(fwd, at, rev_union), ob.scope)
                results.append(res)
                if not res[0]:
                    break
            self._apply_totality(label, results)
        # (b) no idle or unverified agent
        hg = self.handoff_graph()
        reach_from_goal = self._reach(hg, [gv])
        views_with_check = {c.split(".")[0] for c, _f in behaviour}
        rev_hg: dict = defaultdict(set)
        for a, bs in hg.items():
            for b in bs:
                rev_hg[b].add(a)
        reaches_check = self._reach(rev_hg, list(views_with_check))
        for v in t.views:
            if v == gv:
                continue
            if v not in reach_from_goal:
                self.err("W4", v, f"view {v} is not reachable from the goal view (its agent receives no work)")
            elif v not in reaches_check:
                self.err("W4", v, f"view {v} reaches no behaviour-checked value (its work is never verified)")

    # ------------------------------------------------------------------
    # W5: the engine decides completion
    # ------------------------------------------------------------------
    def w5(self) -> None:
        for c in self.t.done:
            if c not in DONE_VOCABULARY and c not in LIBRARY_CLAUSES:
                self.err("W5", c, f"done-clause {c} is not an engine clause",
                         f"phi may only contain {list(DONE_VOCABULARY)} and library clauses {list(LIBRARY_CLAUSES)}")
        for c in DONE_VOCABULARY:
            if c not in self.t.done:
                self.err("W5", c, f"acceptance predicate lacks engine clause {c}")
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
        for a in t.agents.values():
            unknown = [x for x in a.tools if x not in TOOLS]
            if unknown:
                self.warn("W6", a.name, f"agent {a.name} declares tools {unknown} outside the vocabulary {list(TOOLS)}")
        for h in t.handoffs:
            writers = sorted(t.owner_of(h.target))
            for r in h.rules:
                for b in r.llm:
                    need = set(b.tools)
                    for v in b.validators:
                        if v.id in VLIB:
                            need |= set(VLIB[v.id].tools)
                    for a in writers:
                        have = set(t.agents[a].tools) if a in t.agents else set()
                        if not need <= have:
                            self.err("W6", f"{r.name}.{b.feature}",
                                     f"{r.name}.{b.feature} needs {sorted(need)}; writer {a} has {sorted(have)}",
                                     f"give {a} the tools {sorted(need - have)} or choose another validator")


def check(team: TypedTeam | dict, *, w4: str = "two-sided", anchored: bool | None = None) -> CheckResult:
    """Admit(Theta): empty diagnostics iff the team is admitted.
    `anchored=False` is the older spelling of `w4="path-only"`."""
    if anchored is False:
        w4 = "path-only"
    if isinstance(team, dict):
        try:
            team = parse_team(team)
        except TeamFormatError as exc:
            return CheckResult([Diagnostic("W1", "format", str(exc))])
    return Checker(team, w4=w4).run()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="AutoM2M admission checker (W1-W6)")
    ap.add_argument("team", nargs="+")
    ap.add_argument("--w4", default="two-sided", choices=W4_MODES)
    ap.add_argument("--naive", action="store_true", help="same as --w4 path-only")
    ap.add_argument("--hints", action="store_true")
    a = ap.parse_args(argv)
    rc = 0
    for p in a.team:
        if len(a.team) > 1:
            print(f"== {p}")
        res = check(load_team(p), w4="path-only" if a.naive else a.w4)
        print(res.report(hints=a.hints))
        rc |= 0 if res.admitted else 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
