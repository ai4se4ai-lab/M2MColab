"""Wiring inference: complete a team sketch into a typed team (AutoM2M-Lean, M1).

A typed team mixes two kinds of content. The *design* says which agents
exist, which views they fill in, which hand-offs connect the views, what each
LLM feature asks for and how it is validated. The *wiring* says how the parts
are connected: the structural bindings of every target object, the join
guards of rules with several sources, the navigation paths of footprints and
validator arguments, the writer and tools of every view, the goal
obligations, the deliverable and the acceptance predicate.

Most of the builder's rejected proposals fail on wiring, not on design
(validator arguments, ill-typed footprint paths, views without a writer,
unbound references), and the builder pays for every such slip with a full
revision. The wiring, however, is determined by the design up to a choice
the metamodel already fixes. Wiring inference therefore computes it, in the
way a compiler infers the types a programmer leaves out:

  * every path is found by breadth-first search on the metamodel graph
    (classes as nodes, references as edges), restricted to the views a rule
    may read, so the shortest well-typed path is chosen (W1);
  * a required reference is bound to the shortest path of its type, an
    attribute to the shortest path ending in an attribute of the same name,
    and an unbindable attribute becomes optional (W3);
  * rules with several sources join on their paths to the same goal method
    (the natural join of the sources, which is what makes them total, W4);
  * validator arguments are inferred from parameter types: a goal object
    parameter gets the path to the goal method, a test-code parameter the path
    to a feature that an upstream binding fills with test code (W1);
  * footprints keep the well-typed paths of the proposal (paths that do not
    type-check are replaced by the shortest catalogue path with the same last
    feature); the features a validator checks are added, so that every
    anchor flows into the checked value through production and check (W4);
  * a view without a writer is given the most similar agent, each class gets
    one producer (several proposed producers are merged into one join), and
    an agent gets the tools its validators need (W2, W6);
  * goal obligations, deliverable and acceptance predicate are fixed by the
    goal view (every example checked, every method delivered, the four
    engine clauses), so the builder no longer writes them (W4, W5);
  * a liveness analysis on the hand-off graph removes views that receive no
    work or whose work never reaches a behaviour check (dead code).

Inference is a deterministic, idempotent rewriting to a fixpoint and calls no
LLM. It never makes a team admissible by itself: the checker still decides,
and whatever inference cannot settle (a cycle, a design without any
behaviour check) remains a diagnostic for the builder.
"""
from __future__ import annotations

import copy
import json
import re
from collections import defaultdict, deque
from typing import Any

from .lift import GOAL_VIEW, GOAL_VIEW_NAME, normalize
from .typed_team import TeamFormatError, is_literal, parse_team, rule_env, type_path
from .vlib import LIBRARY_CLAUSES, TOOLS, VLIB

DONE = ["cover(G)", "valid", "fresh", "noEsc"]
GOAL_OBLIGATIONS = [{"class": "Example", "scope": "all", "mode": "checked", "anchors": ["call", "expected"]},
                    {"class": "Method", "scope": "all", "mode": "delivered", "anchors": ["signature", "docstring"]}]
CODE_VALIDATORS = ("passes_tests", "examples_run", "smoke_test")
TEST_VALIDATORS = ("test_valid",)
GM = f"{GOAL_VIEW_NAME}.Method"
_OFFERED = {k for k, v in VLIB.items() if v.offered}
_ALIASES = {"tests_pass": "passes_tests", "pass_tests": "passes_tests", "passestests": "passes_tests",
            "non_empty": "nonempty", "notempty": "nonempty", "not_empty": "nonempty", "compile": "compiles",
            "examples": "examples_run", "run_examples": "examples_run", "examples_pass": "examples_run",
            "testvalid": "test_valid", "valid_test": "test_valid", "valid_tests": "test_valid",
            "tests_valid": "test_valid", "define": "defines", "is_json": "json"}
MAX_HOPS = 3


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def _s(x: Any, default: str = "") -> str:
    return str(x).strip() if isinstance(x, (str, int, float)) else default


def _tokens(*texts: str) -> set[str]:
    out: set[str] = set()
    for t in texts:
        for w in re.findall(r"[A-Za-z][a-z]*|[A-Z]+(?![a-z])", t or ""):
            w = w.lower()
            if len(w) >= 3:
                out.add(w[:5])  # crude stemming: design/designer, test/tester/tests
    return out


def _view_of(team: dict, cls: str) -> str | None:
    for v, spec in (team.get("views") or {}).items():
        if cls in ((spec or {}).get("classes") or {}):
            return v
    return None


def _norm_type(team: dict, t: Any, default_view: str = "") -> str:
    """'View!Class' for any of 'View!Class', 'View.Class', 'Class'."""
    t = _s(t)
    for sep in ("!", "."):
        if sep in t:
            v, c = t.split(sep, 1)
            return f"{v.strip()}!{c.strip()}"
    if not t:
        return t
    v = _view_of(team, t) or (GOAL_VIEW_NAME if t in GOAL_VIEW["classes"] else default_view)
    return f"{v}!{t}"


def _split(t: str) -> tuple[str, str]:
    v, _, c = t.partition("!")
    return v, c


def _validator_id(x: Any) -> str | None:
    vid = _s(x.get("id") if isinstance(x, dict) else x)
    if not vid:
        return None
    key = vid.split("(")[0].strip()
    if key in _OFFERED:
        return key
    low = key.lower()
    return _ALIASES.get(low) or _ALIASES.get(low.replace("-", "_")) or (low if low in _OFFERED else None)


# --------------------------------------------------------------------------
# 1. sanitize: make the JSON parseable as a typed team (shapes only)
# --------------------------------------------------------------------------

def sanitize(team: Any) -> dict:
    t = copy.deepcopy(team) if isinstance(team, dict) else {}
    t["name"] = _s(t.get("name"), "team") or "team"
    views_in = t.get("views") if isinstance(t.get("views"), dict) else {}
    views: dict = {}
    for vn, vs in views_in.items():
        vn = _s(vn)
        if not vn or vn == GOAL_VIEW_NAME:
            continue
        classes = (vs or {}).get("classes") if isinstance(vs, dict) else None
        if not isinstance(classes, dict):
            classes = {}
        cl = {}
        for cn, cs in classes.items():
            cs = cs if isinstance(cs, dict) else {}
            attrs = cs.get("attributes") if isinstance(cs.get("attributes"), dict) else {}
            refs = cs.get("references") if isinstance(cs.get("references"), dict) else {}
            a2, r2 = {}, {}
            for an, at in attrs.items():
                ts = at.get("type", "string") if isinstance(at, dict) else at
                ts = _s(ts, "string") or "string"
                if ("." in ts or "!" in ts) and ts.rstrip("?*").lower() not in ("string", "int", "boolean"):
                    r2[_s(an)] = {"type": ts.rstrip("?*").replace("!", "."), "required": not ts.endswith("?"),
                                  "many": ts.endswith("*")}
                else:
                    base = ts.rstrip("?*").lower()
                    a2[_s(an)] = ("string" if base not in ("string", "int", "boolean") else base) + \
                        ("?" if ts.endswith("?") or (isinstance(at, dict) and at.get("required") is False) else "")
            for rn, rs in refs.items():
                if isinstance(rs, str):
                    rs = {"type": rs}
                if not isinstance(rs, dict):
                    continue
                r2[_s(rn)] = {"type": _s(rs.get("type")).replace("!", "."), "required": bool(rs.get("required", False)),
                              "many": bool(rs.get("many", False))}
            cl[_s(cn)] = {"attributes": a2, "references": r2}
        views[vn] = {"classes": cl}
    t["views"] = views
    # agents (a sketch may carry "writes" inline)
    agents, writes = [], defaultdict(list)
    for a in t.get("agents") or []:
        if not isinstance(a, dict) or not _s(a.get("name")):
            continue
        name = _s(a["name"])
        if any(x["name"] == name for x in agents):
            continue
        agents.append({"name": name, "role": _s(a.get("role")), "tools": [x for x in (a.get("tools") or []) if x in TOOLS]})
        inline = a.get("writes")
        for v in (inline if isinstance(inline, list) else [inline] if isinstance(inline, str) else []):
            writes[name].append(_s(v))
    t["agents"] = agents
    w = t.get("writes") if isinstance(t.get("writes"), dict) else {}
    for a, vs in w.items():
        for v in (vs if isinstance(vs, list) else [vs]):
            if _s(v) and _s(v) not in writes[_s(a)]:
                writes[_s(a)].append(_s(v))
    t["writes"] = {a: vs for a, vs in writes.items()}
    # hand-offs and rules
    hs = []
    for i, h in enumerate(t.get("handoffs") or []):
        if not isinstance(h, dict):
            continue
        rules = []
        for j, r in enumerate(h.get("rules") or []):
            if not isinstance(r, dict):
                continue
            frm = []
            for s in (r.get("from") or []) if isinstance(r.get("from"), list) else [r.get("from")]:
                if isinstance(s, dict) and _s(s.get("type")):
                    frm.append({"var": _s(s.get("var"), "x") or "x", "type": _norm_type(t, s.get("type"))})
            to = r.get("to") if isinstance(r.get("to"), dict) else {}
            llm = []
            for b in r.get("llm") or []:
                if not isinstance(b, dict) or not _s(b.get("feature")):
                    continue
                fp = b.get("footprint") or []
                fp = [fp] if isinstance(fp, str) else [_s(p) for p in fp if _s(p)]
                vals = b.get("validator", b.get("validators"))
                vals = vals if isinstance(vals, list) else [vals] if vals else []
                vlist = []
                for v in vals:
                    vid = _validator_id(v)
                    if vid:
                        args = v.get("args") if isinstance(v, dict) and isinstance(v.get("args"), dict) else {}
                        vlist.append({"id": vid, "args": {_s(k): _s(p) for k, p in args.items() if _s(p)}})
                llm.append({"feature": _s(b["feature"]), "prompt": _s(b.get("prompt")), "footprint": fp,
                            "validator": vlist})
            bind = r.get("bind") if isinstance(r.get("bind"), dict) else {}
            rules.append({"name": _s(r.get("name"), f"R{i}_{j}") or f"R{i}_{j}", "from": frm,
                          "guard": _s(r.get("guard")) or None,
                          "to": {"var": _s(to.get("var"), "t") or "t", "type": _norm_type(t, to.get("type"), _s(h.get("target")))},
                          "bind": {_s(k): _s(v) for k, v in bind.items() if _s(k) and _s(v)}, "llm": llm})
        hs.append({"name": _s(h.get("name"), f"H{i}") or f"H{i}", "sources": [_s(x) for x in h.get("sources") or [] if _s(x)],
                   "target": _s(h.get("target")), "rules": rules})
    t["handoffs"] = hs
    t["goal"] = t.get("goal") if isinstance(t.get("goal"), list) else []
    t["deliverable"] = t.get("deliverable") if isinstance(t.get("deliverable"), dict) else None
    t["done"] = [d for d in t.get("done") or [] if isinstance(d, str)]
    # unique rule names; unique variable names inside a rule
    seen: set[str] = set()
    for h in t["handoffs"]:
        for r in h["rules"]:
            n, k = r["name"], 2
            while r["name"] in seen:
                r["name"] = f"{n}{k}"
                k += 1
            seen.add(r["name"])
            used: set[str] = set()
            for s in r["from"]:
                v, k = s["var"], 2
                while s["var"] in used:
                    s["var"] = f"{v}{k}"
                    k += 1
                used.add(s["var"])
            if r["to"]["var"] in used:
                r["to"]["var"] = r["to"]["var"] + "_t"
    return t


# --------------------------------------------------------------------------
# metamodel graph search
# --------------------------------------------------------------------------

class _Index:
    """The typed view of a (sanitized) team plus breadth-first path search."""

    def __init__(self, team: dict) -> None:
        self.raw = team
        self.tt = parse_team(normalize(team))

    def cls(self, qtype: str):
        v, c = _split(qtype)
        return self.tt.cls(v, c)

    def stochastic(self) -> set[tuple[str, str]]:
        return {(f"{r.target_view}.{r.target_cls}", b.feature) for _h, r in self.tt.rules() for b in r.llm}

    def search(self, rule: dict, allowed: set[str], *, want_object: str | None = None, want_attr: str | None = None,
               single: bool = True, max_hops: int = MAX_HOPS, avoid_stochastic: bool = False) -> list[str]:
        """Shortest paths from the rule's variables (in their order) that end
        in an object of class `want_object` or in an attribute named
        `want_attr`, inside the allowed views."""
        stoch = self.stochastic() if avoid_stochastic else set()
        out: list[str] = []
        frontier: list[tuple[str, Any, bool]] = []
        for s in rule["from"]:
            decl = self.cls(s["type"])
            if decl is not None and decl.view in allowed:
                frontier.append((s["var"], decl, False))
        seen = set()
        for _depth in range(max_hops + 1):
            nxt = []
            for path, decl, many in frontier:
                if (path, decl.qname) in seen:
                    continue
                seen.add((path, decl.qname))
                if want_object and decl.qname == want_object and not (single and many):
                    out.append(path)
                if want_attr and want_attr in decl.attrs and not (single and (many or decl.attrs[want_attr].many)):
                    if (decl.qname, want_attr) not in stoch:
                        out.append(f"{path}.{want_attr}")
                for rn, ref in decl.refs.items():
                    other = self.tt.cls(ref.view, ref.cls)
                    if other is not None and other.view in allowed and not (single and ref.many):
                        nxt.append((f"{path}.{rn}", other, many or ref.many))
            if out:
                return out
            frontier = nxt
        return out

    def catalogue(self, rule: dict, allowed: set[str], hops: int = 2) -> list[str]:
        """Every attribute path from the rule's variables (at most `hops`
        references), shortest first."""
        out: list[str] = []
        frontier = []
        for s in rule["from"]:
            decl = self.cls(s["type"])
            if decl is not None and decl.view in allowed:
                frontier.append((s["var"], decl))
        for depth in range(hops + 1):
            nxt = []
            for path, decl in frontier:
                out += [f"{path}.{a}" for a in decl.attrs]
                if depth < hops:
                    for rn, ref in decl.refs.items():
                        other = self.tt.cls(ref.view, ref.cls)
                        if other is not None and other.view in allowed:
                            nxt.append((f"{path}.{rn}", other))
            frontier = nxt
        return list(dict.fromkeys(out))

    def typed(self, rule: dict, path: str):
        r = self._rule(rule["name"])
        return type_path(self.tt, path, rule_env(r), object_reads_all=False) if r else None

    def _rule(self, name: str):
        return next((r for _h, r in self.tt.rules() if r.name == name), None)


def _allowed(team: dict, h: dict) -> set[str]:
    return set(h["sources"]) | {GOAL_VIEW_NAME}


def _path_ok(ix: _Index, rule: dict, h: dict, path: str, *, attr: bool | None = None) -> bool:
    pt = ix.typed(rule, path)
    if pt is None or not pt.ok:
        return False
    if pt.views - _allowed(ix.raw, h):
        return False
    if attr is True and pt.end_kind != "attr":
        return False
    if attr is False and pt.end_kind != "object":
        return False
    return True


def _method_path(ix: _Index, rule: dict, h: dict) -> str | None:
    ps = ix.search(rule, _allowed(ix.raw, h), want_object=GM)
    return ps[0] if ps else None


def _task_path(ix: _Index, rule: dict, h: dict) -> str | None:
    ps = ix.search(rule, _allowed(ix.raw, h), want_object=f"{GOAL_VIEW_NAME}.Task")
    return ps[0] if ps else None


# --------------------------------------------------------------------------
# 2. the inference steps (each a deterministic rewrite of the JSON)
# --------------------------------------------------------------------------

def _usage(rule: dict, var: str) -> list[str]:
    """The features a rule accesses on a variable (first navigation step)."""
    texts = []
    for b in rule.get("llm") or []:
        texts += list(b.get("footprint") or [])
        for v in b.get("validator") or []:
            texts += list((v.get("args") or {}).values())
    texts += list((rule.get("bind") or {}).values())
    texts.append(rule.get("guard") or "")
    out = []
    for tx in texts:
        out += re.findall(rf"(?<![\w.]){re.escape(var)}\.([A-Za-z_]\w*)", tx or "")
    return out


def _goal_class_by_usage(feats: list[str], hint: str = "") -> str:
    """Structural type inference: the goal class whose features best explain
    the features a variable is used with (ties: Method, the unit of work)."""
    best, score = "Method", -1.0
    for cname, spec in GOAL_VIEW["classes"].items():
        names = set(spec.get("attributes") or {}) | set(spec.get("references") or {})
        s = sum(1 for f in feats if f in names) + (0.5 if hint.lower() == cname.lower() else 0)
        if s > score or (s == score and cname == "Method"):
            best, score = cname, s
    return best


def _resolve_goal_aliases(t: dict) -> None:
    """Variables typed by classes that do not exist in the goal view, and
    classes that copy a goal class without any producer, denote goal
    classes: retype them by usage."""
    goal = set(GOAL_VIEW["classes"])
    produced = {r["to"]["type"] for h in t["handoffs"] for r in h["rules"]}
    alias: dict[str, str] = {}
    for v, spec in t["views"].items():
        for c, cs in spec["classes"].items():
            q = f"{v}!{c}"
            if q in produced:
                continue
            feats = set(cs["attributes"]) | set(cs["references"])
            gfeats = {g: set(s.get("attributes") or {}) | set(s.get("references") or {}) for g, s in GOAL_VIEW["classes"].items()}
            if c in goal or any(feats and feats <= gf for gf in gfeats.values()):
                alias[q] = f"{GOAL_VIEW_NAME}!{c if c in goal else _goal_class_by_usage(list(feats), c)}"
    for h in t["handoffs"]:
        for r in h["rules"]:
            for s in r["from"]:
                v, c = _split(s["type"])
                if s["type"] in alias:
                    s["type"] = alias[s["type"]]
                elif v == GOAL_VIEW_NAME and c not in goal:
                    s["type"] = f"{GOAL_VIEW_NAME}!{_goal_class_by_usage(_usage(r, s['var']), c)}"
                elif v and v != GOAL_VIEW_NAME and v not in t["views"] and c in goal:
                    s["type"] = f"{GOAL_VIEW_NAME}!{c}"
    # references to aliased or undeclared goal classes
    for v, spec in t["views"].items():
        for c, cs in spec["classes"].items():
            for rn, ref in cs["references"].items():
                rt = _norm_type(t, ref.get("type"), v)
                rv, rc = _split(rt)
                if rt in alias:
                    ref["type"] = alias[rt].replace("!", ".")
                elif rv == GOAL_VIEW_NAME and rc not in goal:
                    ref["type"] = f"{GOAL_VIEW_NAME}.{_goal_class_by_usage([], rc)}"
    # drop the alias classes (and views left empty)
    for q in alias:
        v, c = _split(q)
        t["views"].get(v, {}).get("classes", {}).pop(c, None)
    for v in [v for v, s in t["views"].items() if not s["classes"]]:
        if not any(h["target"] == v for h in t["handoffs"]):
            t["views"].pop(v)

def _declare(t: dict) -> None:
    """Rule targets are declared classes; LLM features are single-valued
    string attributes; a class whose rule reads a goal method references it."""
    for h in t["handoffs"]:
        for r in h["rules"]:
            v, c = _split(r["to"]["type"])
            if not v:
                v = h["target"] or v
            if not c:
                continue
            if v == GOAL_VIEW_NAME:
                continue
            if v not in t["views"]:
                t["views"][v] = {"classes": {}}
            r["to"]["type"] = f"{v}!{c}"
            cls = t["views"][v]["classes"].setdefault(c, {"attributes": {}, "references": {}})
            for b in r["llm"]:
                f = b["feature"]
                cls["references"].pop(f, None)
                if cls["attributes"].get(f, "string").rstrip("?") != "string" or f not in cls["attributes"]:
                    cls["attributes"][f] = "string"
            if not cls["attributes"].get("name") and "name" not in cls["references"]:
                cls["attributes"]["name"] = "string"
            # source classes that are not declared anywhere: declare them where named
            for s in r["from"]:
                sv, sc = _split(s["type"])
                if sv and sv != GOAL_VIEW_NAME and sc and sv in t["views"] and sc not in t["views"][sv]["classes"]:
                    other = _view_of(t, sc)
                    if other:
                        s["type"] = f"{other}!{sc}"


def _goal_refs(t: dict) -> None:
    """Every agent class gets a reference `method` to the goal method its
    producer works on (if the producer can reach one), so that downstream
    rules can join on it and the deliverable can be located."""
    ix = _Index(t)
    for h in t["handoffs"]:
        for r in h["rules"]:
            v, c = _split(r["to"]["type"])
            cls = t["views"].get(v, {}).get("classes", {}).get(c)
            if cls is None:
                continue
            if any(_s(x.get("type")).replace("!", ".") == GM and not x.get("many") for x in cls["references"].values()):
                continue
            if _method_path(ix, r, h) is not None and "method" not in cls["attributes"]:
                cls["references"]["method"] = {"type": GM, "required": True, "many": False}


def _handoff_views(t: dict) -> None:
    """A hand-off reads exactly the views its rules read, and targets the
    view its rules create in."""
    # a rule that creates in another view than its hand-off's target moves to
    # a hand-off that targets that view (a new one if there is none)
    moved: list[dict] = []
    for h in t["handoffs"]:
        targets = [_split(r["to"]["type"])[0] for r in h["rules"]]
        if not h["target"] and targets:
            h["target"] = max(set(targets), key=targets.count)
        stay = []
        for r in h["rules"]:
            (stay if _split(r["to"]["type"])[0] == h["target"] else moved).append(r)
        h["rules"] = stay
    for r in moved:
        tv = _split(r["to"]["type"])[0]
        h = next((x for x in t["handoffs"] if x["target"] == tv), None)
        if h is None:
            sv = _split(r["from"][0]["type"])[0] if r["from"] else GOAL_VIEW_NAME
            name, k = f"{sv}2{tv}", 2
            while any(x["name"] == name for x in t["handoffs"]):
                name, k = f"{sv}2{tv}{k}", k + 1
            h = {"name": name, "sources": [], "target": tv, "rules": []}
            t["handoffs"].append(h)
        h["rules"].append(r)
    for h in t["handoffs"]:
        srcs = []
        for r in h["rules"]:
            for s in r["from"]:
                v = _split(s["type"])[0]
                if v and v not in srcs:
                    srcs.append(v)
        keep = [s for s in h["sources"] if s in t["views"] or s == GOAL_VIEW_NAME]
        h["sources"] = list(dict.fromkeys(srcs + [s for s in keep if s in srcs]))
        targets = [_split(r["to"]["type"])[0] for r in h["rules"]]
        if targets and (h["target"] not in targets):
            h["target"] = targets[0]
        h["rules"] = [r for r in h["rules"] if _split(r["to"]["type"])[0] == h["target"]] or h["rules"]
        # a rule must not read its own target view
        h["sources"] = [s for s in h["sources"] if s != h["target"]]
        h["rules"] = [r for r in h["rules"] if all(_split(s["type"])[0] != h["target"] for s in r["from"])]
    t["handoffs"] = [h for h in t["handoffs"] if h["rules"] and h["target"] and h["target"] != GOAL_VIEW_NAME]


def _merge_producers(t: dict) -> None:
    """One producer per class (W2): rules that create the same class are
    merged into one rule that joins all their sources."""
    by_cls: dict[str, list[tuple[dict, dict]]] = defaultdict(list)
    for h in t["handoffs"]:
        for r in h["rules"]:
            by_cls[r["to"]["type"]].append((h, r))
    for cls, prods in by_cls.items():
        if len(prods) < 2:
            continue
        h0, r0 = prods[0]
        for h, r in prods[1:]:
            have = {s["type"]: s["var"] for s in r0["from"]}
            ren: dict[str, str] = {}
            for s in r["from"]:
                if s["type"] in have:
                    ren[s["var"]] = have[s["type"]]
                    continue
                var, k = s["var"], 2
                while var in {x["var"] for x in r0["from"]}:
                    var = f"{s['var']}{k}"
                    k += 1
                ren[s["var"]] = var
                r0["from"].append({"var": var, "type": s["type"]})
            ren[r["to"]["var"]] = r0["to"]["var"]

            def rn(p: str) -> str:
                head, _, rest = p.partition(".")
                return ren.get(head, head) + (("." + rest) if rest else "")

            feats = {b["feature"] for b in r0["llm"]}
            for b in r["llm"]:
                if b["feature"] in feats:
                    tgt = next(x for x in r0["llm"] if x["feature"] == b["feature"])
                    tgt["footprint"] = list(dict.fromkeys(tgt["footprint"] + [rn(p) for p in b["footprint"]]))
                    have_v = {v["id"] for v in tgt["validator"]}
                    tgt["validator"] += [{"id": v["id"], "args": {k: rn(a) for k, a in v["args"].items()}}
                                         for v in b["validator"] if v["id"] not in have_v]
                else:
                    r0["llm"].append({**b, "footprint": [rn(p) for p in b["footprint"]],
                                      "validator": [{"id": v["id"], "args": {k: rn(a) for k, a in v["args"].items()}}
                                                    for v in b["validator"]]})
            for f, e in r["bind"].items():
                r0["bind"].setdefault(f, rn(e))
            r0["guard"] = None  # re-inferred as the join of all sources
            h["rules"] = [x for x in h["rules"] if x is not r]
            if h is not h0:
                for s in r["from"]:
                    v = _split(s["type"])[0]
                    if v not in h0["sources"]:
                        h0["sources"].append(v)
    t["handoffs"] = [h for h in t["handoffs"] if h["rules"]]


def _validators(t: dict, ix: _Index) -> None:
    """Validator arguments by parameter type; unknown or extra arguments go."""
    test_feats: set[tuple[str, str]] = set()  # (class qname, feature) filled with test code
    for h in t["handoffs"]:
        for r in h["rules"]:
            v, c = _split(r["to"]["type"])
            for b in r["llm"]:
                if any(x["id"] in TEST_VALIDATORS for x in b["validator"]):
                    test_feats.add((f"{v}.{c}", b["feature"]))
    for h in t["handoffs"]:
        allowed = _allowed(t, h)
        for r in h["rules"]:
            mp = _method_path(ix, r, h)
            for b in r["llm"]:
                out = []
                for v in b["validator"]:
                    spec = VLIB.get(v["id"])
                    if spec is None or not spec.offered:
                        continue
                    args, ok = {}, True
                    for pname, param in spec.params.items():
                        arg = v["args"].get(pname)
                        if param.type == "string":
                            good = arg is not None and _path_ok(ix, r, h, arg, attr=True)
                            if not good:
                                cands = []
                                for qn, f in sorted(test_feats):
                                    cands += [p for p in ix.search(r, allowed, want_attr=f)
                                              if ix.typed(r, p) and ix.typed(r, p).reads
                                              and ix.typed(r, p).reads[-1] == (qn, f)]
                                arg = cands[0] if cands else None
                        else:
                            good = arg is not None and _path_ok(ix, r, h, arg, attr=False)
                            if good:
                                pt = ix.typed(r, arg)
                                want = param.type.replace("@goal.", f"{GOAL_VIEW_NAME}.")
                                good = pt.end_type == want if not want.endswith(".*") else pt.end_type.startswith(want[:-1])
                            if not good:
                                arg = mp
                        if arg is None:
                            if not param.optional:
                                ok = False
                            continue
                        args[pname] = arg
                    if ok and all(x["id"] != v["id"] for x in out):
                        out.append({"id": v["id"], "args": args})
                if not out:
                    out = [{"id": "nonempty", "args": {}}]
                b["validator"] = out


def _footprints(t: dict, ix: _Index) -> None:
    """Keep the well-typed footprint paths, repair the others by their last
    feature, and add what the validators check (anchoring, W4)."""
    stoch_self = None
    for h in t["handoffs"]:
        allowed = _allowed(t, h)
        for r in h["rules"]:
            cat = None
            mp = _method_path(ix, r, h)
            tp = _task_path(ix, r, h)
            tq = r["to"]["type"].replace("!", ".")
            for b in r["llm"]:
                fp: list[str] = []
                for p in b["footprint"]:
                    if is_literal(p):
                        continue
                    if _path_ok(ix, r, h, p):
                        pt = ix.typed(r, p)
                        # a footprint never reads the binding's own target object
                        if pt and pt.reads and pt.reads[0][0] == tq:
                            continue
                        fp.append(p)
                        continue
                    last = p.split(".")[-1]
                    cat = cat if cat is not None else ix.catalogue(r, allowed)
                    rep = [q for q in cat if q.split(".")[-1] == last]
                    if rep:
                        fp.append(rep[0])
                ids = {v["id"] for v in b["validator"]}
                need: list[str] = []
                if mp:
                    if ids & {"test_valid", "examples_run"}:
                        need += [f"{mp}.examples.call", f"{mp}.examples.expected"]
                    if ids & (set(CODE_VALIDATORS) | set(TEST_VALIDATORS) | {"compiles", "defines"}):
                        need += [f"{mp}.signature"]
                        need += [f"{mp}.task.outline"] if _path_ok(ix, r, h, f"{mp}.task.outline") else []
                    if not fp:
                        need += [f"{mp}.signature", f"{mp}.docstring"]
                elif tp and not fp:
                    need += [f"{tp}.outline"]
                for v in b["validator"]:  # a string the validator checks against is shown to the LLM
                    spec = VLIB[v["id"]]
                    for pname, param in spec.params.items():
                        if param.type == "string" and param.mode == "read" and pname in v["args"]:
                            need.append(v["args"][pname])
                if not fp and not need:
                    cat = cat if cat is not None else ix.catalogue(r, allowed, hops=1)
                    need += cat[:6]
                b["footprint"] = [p for p in dict.fromkeys(fp + need) if _path_ok(ix, r, h, p)]
    del stoch_self


def _binds(t: dict, ix: _Index) -> None:
    """Structural bindings (W1, W3): well-typed, non-stochastic, once each;
    every required feature bound or made optional."""
    stoch = ix.stochastic()
    for h in t["handoffs"]:
        allowed = _allowed(t, h)
        for r in h["rules"]:
            v, c = _split(r["to"]["type"])
            cls = t["views"].get(v, {}).get("classes", {}).get(c)
            if cls is None:
                continue
            llm_feats = {b["feature"] for b in r["llm"]}
            bind = {}
            for f, e in r["bind"].items():
                if f in llm_feats:
                    continue
                if f not in cls["attributes"] and f not in cls["references"]:
                    continue
                if is_literal(e):
                    if f in cls["attributes"]:
                        bind[f] = e
                    continue
                pt = ix.typed(r, e)
                if pt is None or not pt.ok or (pt.views - allowed) or any(rd in stoch for rd in pt.reads):
                    continue
                if f in cls["attributes"]:
                    if pt.end_kind != "attr" or (pt.many and not cls["attributes"][f].endswith("*")):
                        continue
                else:
                    ref = cls["references"][f]
                    want = _s(ref.get("type")).replace("!", ".")
                    if pt.end_kind != "object" or pt.end_type != want or (pt.many and not ref.get("many")):
                        continue
                bind[f] = e
            for an, at in list(cls["attributes"].items()):
                if an in bind or an in llm_feats:
                    continue
                cands = ix.search(r, allowed, want_attr=an, avoid_stochastic=True)
                cands = [p for p in cands if (ix.typed(r, p) and ix.typed(r, p).reads
                                              and ix.typed(r, p).reads[-1] not in stoch)]
                if cands:
                    bind[an] = cands[0]
                elif not at.endswith("?"):
                    cls["attributes"][an] = at.rstrip("*") + "?"
            for rn, ref in list(cls["references"].items()):
                if rn in bind:
                    continue
                want = _s(ref.get("type")).replace("!", ".")
                cands = ix.search(r, allowed, want_object=want, single=not ref.get("many"))
                if cands:
                    bind[rn] = cands[0]
                elif ref.get("required"):
                    ref["required"] = False
            r["bind"] = bind


def _guards(t: dict, ix: _Index) -> None:
    """A rule with several sources joins them on their goal method."""
    stoch = ix.stochastic()
    for h in t["handoffs"]:
        allowed = _allowed(t, h)
        for r in h["rules"]:
            g = r.get("guard")
            if g:
                good = True
                for p in re.findall(r"(?<!['\"\w])([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+)", re.sub(r"'[^']*'|\"[^\"]*\"", "''", g)):
                    if p.split(".")[0] not in {s["var"] for s in r["from"]}:
                        continue
                    pt = ix.typed(r, p)
                    if pt is None or not pt.ok or (pt.views - allowed) or any(rd in stoch for rd in pt.reads):
                        good = False
                        break
                if not good:
                    g = None
            if len(r["from"]) >= 2:
                ms = []
                for s in r["from"]:
                    one = {"from": [s], "name": r["name"]}
                    ps = ix.search(one, allowed, want_object=GM)
                    ms.append(ps[0] if ps else None)
                if all(ms):
                    join = " and ".join(f"{ms[0]} = {m}" for m in ms[1:])
                    if not g or not all(f"{ms[0]} = {m}" in g or f"{m} = {ms[0]}" in g for m in ms[1:]):
                        g = join
            r["guard"] = g or None


def _goal(t: dict, ix: _Index, kind: str) -> None:
    """Goal obligations, deliverable and acceptance predicate from the goal
    view; a deliverable without an examples check gets one (anchoring)."""
    t["goal"] = copy.deepcopy(GOAL_OBLIGATIONS)
    extra = [c for c in t.get("done") or [] if c in LIBRARY_CLAUSES]
    t["done"] = list(DONE) + list(dict.fromkeys(extra))
    # producers of LLM features, scored as candidates for the deliverable
    out_deg = defaultdict(int)
    for h in t["handoffs"]:
        for s in h["sources"]:
            out_deg[s] += 1
    best, score = None, -1
    d = t.get("deliverable") or {}
    for h in t["handoffs"]:
        for r in h["rules"]:
            v, c = _split(r["to"]["type"])
            for b in r["llm"]:
                ids = {x["id"] for x in b["validator"]}
                s = (4 if ids & {"passes_tests", "examples_run"} else 2 if ids & {"compiles", "defines", "smoke_test"} else 0)
                s += 1 if out_deg.get(v, 0) == 0 else 0
                s += 1 if re.search(r"code|body|impl|source|solution", b["feature"], re.I) else 0
                s += 1 if (d.get("view"), _split(_norm_type(t, d.get("class") or "", d.get("view") or ""))[1],
                           d.get("feature")) == (v, c, b["feature"]) else 0
                s -= 6 if ids & set(TEST_VALIDATORS) else 0
                if s > score:
                    best, score = (h, r, b), s
    if best is None:
        t["deliverable"] = None
        return
    h, r, b = best
    v, c = _split(r["to"]["type"])
    t["deliverable"] = {"view": v, "class": c, "feature": b["feature"]}
    ids = {x["id"] for x in b["validator"]}
    mp = _method_path(ix, r, h)
    if mp and not any(VLIB[i].strength == "behaviour" for i in ids if i in VLIB):
        # the deliverable must be behaviour-checked: run the method's documented examples
        b["validator"].append({"id": "examples_run", "args": {"method": mp}})
        ids.add("examples_run")
    if mp:
        anchored = False
        for hh in t["handoffs"]:
            for rr in hh["rules"]:
                for bb in rr["llm"]:
                    if {x["id"] for x in bb["validator"]} & {"test_valid", "examples_run"}:
                        anchored = True
        if not anchored and "examples_run" not in ids:
            b["validator"].append({"id": "examples_run", "args": {"method": mp}})


def _writes(t: dict) -> None:
    """Exactly one writer per agent view (W2), chosen by name similarity."""
    agents = [a["name"] for a in t["agents"]]
    w = {a: [v for v in vs if v in t["views"] and v != GOAL_VIEW_NAME] for a, vs in (t.get("writes") or {}).items()
         if a in agents}
    roles = {a["name"]: a.get("role", "") for a in t["agents"]}

    def sim(agent: str, view: str) -> float:
        cls = " ".join(t["views"][view]["classes"])
        feats = " ".join(b["feature"] for h in t["handoffs"] if h["target"] == view for r in h["rules"] for b in r["llm"])
        a, vtoks = _tokens(agent), _tokens(view, cls, feats)
        return 2 * len(a & vtoks) + 0.5 * len(_tokens(roles.get(agent, "")) & vtoks)

    used = {v for vs in w.values() for v in vs}
    for view in t["views"]:
        writers = [a for a, vs in w.items() if view in vs]
        if len(writers) > 1:
            keep = max(writers, key=lambda a: (sim(a, view), -len(w[a]), -agents.index(a)))
            for a in writers:
                if a != keep:
                    w[a] = [x for x in w[a] if x != view]
        elif not writers:
            free = [a for a in agents if not w.get(a)]
            pool = free or agents
            if not pool:
                name = f"{view}Writer"
                t["agents"].append({"name": name, "role": f"You fill in the {view} view.", "tools": []})
                agents.append(name)
                pool = [name]
            pick = max(pool, key=lambda a: (sim(a, view), -len(w.get(a, [])), -agents.index(a)))
            w.setdefault(pick, []).append(view)
        used.add(view)
    t["writes"] = {a: vs for a, vs in w.items() if vs}


def _tools(t: dict) -> None:
    need = defaultdict(set)
    for h in t["handoffs"]:
        for r in h["rules"]:
            for b in r["llm"]:
                for v in b["validator"]:
                    if v["id"] in VLIB:
                        need[h["target"]] |= set(VLIB[v["id"]].tools)
    for a in t["agents"]:
        have = set(a.get("tools") or []) & set(TOOLS)
        for v in t["writes"].get(a["name"], []):
            have |= need.get(v, set())
        a["tools"] = sorted(have)


def _liveness(t: dict) -> None:
    """Drop views that receive no work or whose work reaches no behaviour
    check and no deliverable (dead code), with their hand-offs."""
    for _ in range(len(t["views"]) + 1):
        g: dict[str, set[str]] = defaultdict(set)
        rev: dict[str, set[str]] = defaultdict(set)
        for h in t["handoffs"]:
            for s in h["sources"]:
                g[s].add(h["target"])
                rev[h["target"]].add(s)
        reach = {GOAL_VIEW_NAME}
        q = deque([GOAL_VIEW_NAME])
        while q:
            n = q.popleft()
            for m in g.get(n, ()):
                if m not in reach:
                    reach.add(m)
                    q.append(m)
        checked = set()
        for h in t["handoffs"]:
            for r in h["rules"]:
                for b in r["llm"]:
                    if any(VLIB.get(v["id"]) and VLIB[v["id"]].strength == "behaviour" for v in b["validator"]):
                        checked.add(h["target"])
        d = (t.get("deliverable") or {}).get("view")
        if d:
            checked.add(d)
        back = set(checked)
        q = deque(checked)
        while q:
            n = q.popleft()
            for m in rev.get(n, ()):
                if m not in back:
                    back.add(m)
                    q.append(m)
        dead = [v for v in t["views"] if v not in reach or v not in back]
        if not dead:
            return
        for v in dead:
            t["views"].pop(v, None)
        t["handoffs"] = [h for h in t["handoffs"] if h["target"] in t["views"]
                         and all(s in t["views"] or s == GOAL_VIEW_NAME for s in h["sources"])]
        for a in list(t.get("writes") or {}):
            t["writes"][a] = [v for v in t["writes"][a] if v in t["views"]]


def _break_cycles(t: dict) -> None:
    """W5 requires an acyclic hand-off graph: drop the hand-off sources that
    close a cycle (back edges of a depth-first search from the goal view),
    and the rules that read only through them."""
    g: dict[str, list[dict]] = defaultdict(list)
    for h in t["handoffs"]:
        for s in h["sources"]:
            g[s].append(h)
    color: dict[str, int] = {}
    back: list[tuple[str, dict]] = []

    def dfs(u: str) -> None:
        color[u] = 1
        for h in g.get(u, ()):
            v = h["target"]
            if color.get(v) == 1:
                back.append((u, h))
            elif color.get(v) is None:
                dfs(v)
        color[u] = 2

    for start in [GOAL_VIEW_NAME] + list(t["views"]):
        if color.get(start) is None:
            dfs(start)
    for src, h in back:
        h["sources"] = [s for s in h["sources"] if s != src]
        h["rules"] = [r for r in h["rules"] if all(_split(s["type"])[0] != src for s in r["from"])]
    t["handoffs"] = [h for h in t["handoffs"] if h["rules"]]


# --------------------------------------------------------------------------
# the fixpoint
# --------------------------------------------------------------------------

def infer(team: Any, kind: str = "class", *, max_rounds: int = 4) -> dict:
    """Complete a team sketch (or an ill-wired proposal) into a typed team.
    Deterministic and idempotent; raises TeamFormatError only if the JSON
    cannot be read as a team at all."""
    t = sanitize(team)
    if not t["handoffs"] or not t["views"]:
        raise TeamFormatError("the sketch declares no views or no hand-offs")
    for _ in range(max_rounds):
        before = json.dumps(t, sort_keys=True)
        _resolve_goal_aliases(t)
        _declare(t)
        _handoff_views(t)
        _break_cycles(t)
        _merge_producers(t)
        _break_cycles(t)
        _handoff_views(t)
        _goal_refs(t)
        ix = _Index(t)
        _validators(t, ix)
        _binds(t, ix)
        ix = _Index(t)
        _guards(t, ix)
        _footprints(t, ix)
        _goal(t, ix, kind)
        _liveness(t)
        _writes(t)
        _tools(t)
        if json.dumps(t, sort_keys=True) == before:
            break
    t["goal_view"] = GOAL_VIEW_NAME
    return t


def wiring_diff(before: Any, after: dict) -> dict:
    """How much of the team inference supplied (for the evaluation)."""
    def size(x: Any) -> int:
        return len(json.dumps(x, separators=(",", ":"))) if x is not None else 0

    return {"chars_before": size(before), "chars_after": size(after)}
