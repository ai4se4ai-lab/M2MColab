"""Typed team Θ = (A, V, T, ω, κ, τ, G, φ) in the constrained JSON format a
team builder emits (Sec. "Step A" of the AutoM2M design).

The format is deliberately narrow, so that every condition W1–W6 is
decidable on it:
  * structural bindings and footprints are navigation paths (`m.task.name`)
    or quoted literals;
  * validators are drawn from a library (vlib.VLIB) that declares their
    strength (form / behaviour) and the tools they need (τ);
  * φ is a list of clause names.

Example (abridged):
{
  "name": "devteam",
  "goal_view": "Goal",
  "agents": [{"name": "Developer", "role": "...", "tools": ["exec"]}],
  "views": {"Code": {"classes": {"MethodImpl": {
       "attributes": {"name": "string", "code": "string"},
       "references": {"method": {"type": "Goal.Method", "required": true}}}}}},
  "writes": {"Developer": ["Code"]},
  "handoffs": [{"name": "Goal2Code", "sources": ["Goal"], "target": "Code",
    "rules": [{"name": "Method2Impl",
      "from": [{"var": "m", "type": "Goal!Method"}],
      "to": {"var": "i", "type": "Code!MethodImpl"},
      "bind": {"name": "m.name", "method": "m"},
      "llm": [{"feature": "code", "prompt": "Implement the method",
               "footprint": ["m.signature", "m.docstring"],
               "validator": [{"id": "examples_run", "args": {"method": "m"}}]}]}]}],
  "goal": [{"class": "Method", "scope": "all", "kind": "checked"}],
  "deliverable": {"view": "Code", "class": "MethodImpl", "feature": "code", "for": "method"},
  "done": ["cover(G)", "valid", "fresh", "noObl"]
}
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PRIMITIVES = {"string", "int", "boolean"}
DONE_VOCABULARY = ("cover(G)", "valid", "fresh", "noObl")


class TeamFormatError(ValueError):
    """The JSON is not a typed team at all (cannot even be checked)."""


@dataclass
class Attr:
    name: str
    type: str
    required: bool = True
    many: bool = False


@dataclass
class Ref:
    name: str
    view: str
    cls: str
    required: bool = False
    many: bool = False


@dataclass
class ClassDecl:
    view: str
    name: str
    attrs: dict[str, Attr] = field(default_factory=dict)
    refs: dict[str, Ref] = field(default_factory=dict)

    @property
    def qname(self) -> str:
        return f"{self.view}.{self.name}"

    def feature(self, name: str) -> Attr | Ref | None:
        return self.attrs.get(name) or self.refs.get(name)


@dataclass
class ValidatorUse:
    id: str
    args: dict[str, str] = field(default_factory=dict)


@dataclass
class LLMBinding:
    feature: str
    prompt: str
    footprint: list[str]
    validators: list[ValidatorUse]
    tools: list[str] = field(default_factory=list)  # extra tools the prompt itself needs


@dataclass
class Rule:
    name: str
    sources: list[tuple[str, str, str]]  # (var, view, class)
    guard: str | None
    target_var: str
    target_view: str
    target_cls: str
    bind: dict[str, str]
    llm: list[LLMBinding]


@dataclass
class Handoff:
    name: str
    sources: list[str]
    target: str
    rules: list[Rule]


@dataclass
class GoalObligation:
    cls: str
    scope: str = "all"
    kind: str = "checked"  # "checked" (G1) or "delivered" (G2)


@dataclass
class Agent:
    name: str
    role: str
    tools: list[str]


@dataclass
class TypedTeam:
    name: str
    goal_view: str
    agents: dict[str, Agent]
    classes: dict[str, ClassDecl]  # qname -> decl
    views: list[str]
    writes: dict[str, list[str]]
    handoffs: list[Handoff]
    goal: list[GoalObligation]
    deliverable: dict | None
    done: list[str]
    raw: dict = field(default_factory=dict, repr=False)

    # ---- lookups -------------------------------------------------------
    def cls(self, view: str, name: str) -> ClassDecl | None:
        return self.classes.get(f"{view}.{name}")

    def classes_of(self, view: str) -> list[ClassDecl]:
        return [c for c in self.classes.values() if c.view == view]

    def rules(self):
        for h in self.handoffs:
            for r in h.rules:
                yield h, r

    def owner_of(self, view: str) -> list[str]:
        return [a for a, vs in self.writes.items() if view in vs]

    def to_json(self) -> dict:
        return copy.deepcopy(self.raw)


# --------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------


def _split_type(t: str, default_view: str) -> tuple[str, str]:
    t = str(t).strip()
    for sep in ("!", "."):
        if sep in t:
            v, c = t.split(sep, 1)
            return v.strip(), c.strip()
    return default_view, t


def _parse_attr(name: str, spec: Any) -> Attr:
    if isinstance(spec, dict):
        t = str(spec.get("type", "string"))
        return Attr(name, t.rstrip("?*"), bool(spec.get("required", True)), bool(spec.get("many", False)))
    t = str(spec)
    required = not t.endswith("?")
    many = t.endswith("*")
    return Attr(name, t.rstrip("?*").strip().lower() or "string", required and not many, many)


def _parse_validators(spec: Any) -> list[ValidatorUse]:
    if spec is None:
        return []
    if isinstance(spec, (str, dict)):
        spec = [spec]
    out = []
    for v in spec:
        if isinstance(v, str):
            out.append(ValidatorUse(v, {}))
        elif isinstance(v, dict) and "id" in v:
            out.append(ValidatorUse(str(v["id"]), {str(k): str(p) for k, p in (v.get("args") or {}).items()}))
    return out


def parse_team(data: dict | str | Path) -> TypedTeam:
    if isinstance(data, Path) or (isinstance(data, str) and not data.lstrip().startswith("{")):
        data = json.loads(Path(data).read_text())
    elif isinstance(data, str):
        data = json.loads(data)
    if not isinstance(data, dict):
        raise TeamFormatError("team must be a JSON object")
    try:
        goal_view = str(data.get("goal_view", "Goal"))
        agents = {}
        for a in data.get("agents") or []:
            agents[str(a["name"])] = Agent(str(a["name"]), str(a.get("role", "")), [str(t) for t in a.get("tools") or []])
        classes: dict[str, ClassDecl] = {}
        views = []
        for vname, vspec in (data.get("views") or {}).items():
            views.append(str(vname))
            for cname, cspec in ((vspec or {}).get("classes") or {}).items():
                cspec = cspec or {}
                decl = ClassDecl(str(vname), str(cname))
                for an, at in (cspec.get("attributes") or {}).items():
                    decl.attrs[str(an)] = _parse_attr(str(an), at)
                for rn, rs in (cspec.get("references") or {}).items():
                    if isinstance(rs, str):
                        rs = {"type": rs}
                    rv, rc = _split_type(rs.get("type", ""), str(vname))
                    decl.refs[str(rn)] = Ref(str(rn), rv, rc, bool(rs.get("required", False)), bool(rs.get("many", False)))
                classes[decl.qname] = decl
        handoffs = []
        for h in data.get("handoffs") or []:
            rules = []
            for r in h.get("rules") or []:
                srcs = []
                for s in r.get("from") or []:
                    v, c = _split_type(s["type"], "")
                    srcs.append((str(s["var"]), v, c))
                to = r.get("to") or {}
                tv, tc = _split_type(to.get("type", ""), str(h.get("target", "")))
                llm = []
                for b in r.get("llm") or []:
                    fp = b.get("footprint") or []
                    if isinstance(fp, str):
                        fp = [fp]
                    llm.append(LLMBinding(str(b["feature"]), str(b.get("prompt", "")), [str(p) for p in fp],
                                          _parse_validators(b.get("validator", b.get("validators"))),
                                          [str(t) for t in b.get("tools") or []]))
                rules.append(Rule(str(r["name"]), srcs, r.get("guard") or None, str(to.get("var", "t")), tv, tc,
                                  {str(k): str(v) for k, v in (r.get("bind") or {}).items()}, llm))
            handoffs.append(Handoff(str(h["name"]), [str(s) for s in h.get("sources") or []], str(h.get("target", "")), rules))
        goal = [GoalObligation(str(g["class"]).split(".")[-1].split("!")[-1], str(g.get("scope", "all")), str(g.get("kind", "checked")))
                for g in data.get("goal") or []]
        writes = {str(a): [str(v) for v in (vs if isinstance(vs, list) else [vs])] for a, vs in (data.get("writes") or {}).items()}
        return TypedTeam(
            name=str(data.get("name", "team")), goal_view=goal_view, agents=agents, classes=classes,
            views=views, writes=writes, handoffs=handoffs, goal=goal,
            deliverable=data.get("deliverable"), done=[str(d) for d in data.get("done") or []], raw=copy.deepcopy(data),
        )
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        raise TeamFormatError(f"malformed typed team: {type(exc).__name__}: {exc}") from exc


def load_team(path: str | Path) -> TypedTeam:
    return parse_team(json.loads(Path(path).read_text()))


# --------------------------------------------------------------------------
# path typing (shared by the checker and the compiler)
# --------------------------------------------------------------------------


@dataclass
class PathType:
    """Result of typing a navigation path against a rule's source variables."""

    ok: bool
    error: str = ""
    reads: list[tuple[str, str]] = field(default_factory=list)  # (class qname, feature)
    end_kind: str = ""  # "attr" | "object" | "literal"
    end_type: str = ""  # primitive type or class qname
    many: bool = False
    views: set[str] = field(default_factory=set)


def is_literal(expr: str) -> bool:
    e = expr.strip()
    return (len(e) >= 2 and e[0] == e[-1] and e[0] in "'\"") or e in ("true", "false") or e.lstrip("-").isdigit()


def type_path(team: TypedTeam, path: str, env: dict[str, tuple[str, str]], *, object_reads_all: bool = True) -> PathType:
    """`env`: var -> (view, class). Navigation through references may cross
    into other views; `views` collects every view a path touches."""
    p = path.strip()
    if is_literal(p):
        return PathType(True, end_kind="literal", end_type="string")
    parts = p.split(".")
    var = parts[0]
    if var not in env:
        return PathType(False, f"unbound variable '{var}' (this rule's source variables are {sorted(env)})")
    view, cname = env[var]
    decl = team.cls(view, cname)
    if decl is None:
        return PathType(False, f"{view}!{cname} is not declared")
    res = PathType(True, views={view})
    many = False
    for seg in parts[1:]:
        f = decl.feature(seg)
        if f is None:
            feats = sorted(list(decl.attrs) + list(decl.refs))
            return PathType(False, f"{view}!{decl.name} has no feature '{seg}' (its features are {feats})", res.reads)
        res.reads.append((decl.qname, seg))
        if isinstance(f, Attr):
            if seg != parts[-1]:
                return PathType(False, f"cannot navigate past attribute {decl.name}.{seg}", res.reads)
            res.end_kind, res.end_type, res.many = "attr", f.type, many or f.many
            return res
        nxt = team.cls(f.view, f.cls)
        if nxt is None:
            return PathType(False, f"reference {decl.name}.{seg} points to undeclared {f.view}!{f.cls}", res.reads)
        many = many or f.many
        decl, view = nxt, f.view
        res.views.add(view)
    res.end_kind, res.end_type, res.many = "object", decl.qname, many
    if object_reads_all:
        # A footprint that names an object renders all of its attributes.
        res.reads.extend((decl.qname, a) for a in decl.attrs)
    return res


def rule_env(rule: Rule) -> dict[str, tuple[str, str]]:
    return {v: (view, c) for v, view, c in rule.sources}
