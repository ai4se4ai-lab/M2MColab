"""Component 8, Repair: checked re-composition (paper Sec. 3.5, step F).

A fault report becomes a team delta Delta: mechanically for sampling,
footprint and upstream faults (retry, widen, re-sample), and by the builder
otherwise. Theta (+) Delta is the typed team with Delta's edits applied; it
takes effect only if the checker admits it, and then by the cheapest
application that preserves what was already built:

  in place     only rules, prompts, footprints or validators changed: the
               affected rule modules are regenerated; values whose prompt or
               validator changed lose their stamps, a changed footprint makes
               its stamp stale; every other accepted value is kept;
  extension    only views, agents or hand-offs were added: the compiler
               generates the new modules (a higher-order transformation), so
               new agents receive work for everything already built;
  rebuild      anything else: the team is compiled and run from scratch, as
               a free-form builder must always do.
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field

from agenthot.compiler import CompiledTeam, _PRIM, compile_team, generate_module, handoff_order
from agenthot.metamodel.builder import MetamodelBuilder
from agenthot.session import Session

from .attribution import FaultReport
from .lift import normalize  # noqa: F401  (re-exported: the goal view belongs to Lift)
from .typed_team import TypedTeam


@dataclass
class ApplyResult:
    session: Session
    mode: str  # "in-place" | "extension" | "rebuild" | "noop"
    kept: int
    before: int
    invalidated: int


@dataclass
class Plan:
    """What the planner derived from the fault reports, cheapest first."""

    retry: list[tuple[str, str, str]] = field(default_factory=list)  # sampling: (handoff, target, binding)
    resample: list[dict] = field(default_factory=list)  # upstream: the producing binding
    widen: dict[tuple[str, str], list[str]] = field(default_factory=dict)  # footprint: (rule, feature) -> paths
    builder: list[FaultReport] = field(default_factory=list)  # specification / validator / coverage

    def empty(self) -> bool:
        return not (self.retry or self.resample or self.widen or self.builder)


def plan(reports: list[FaultReport]) -> Plan:
    p = Plan()

    def visit(rep: FaultReport) -> None:
        if rep.fault_class == "sampling" and rep.rule:
            p.retry.append((rep.handoff, rep.target_key, rep.binding))
        elif rep.fault_class == "footprint" and rep.widened:
            key = (rep.rule, rep.binding)
            p.widen[key] = list(dict.fromkeys(p.widen.get(key, []) + rep.widened))
        elif rep.fault_class == "upstream" and rep.upstream:
            p.resample.append(rep.upstream)
            inner = rep.upstream.get("report")
            if inner:
                visit(FaultReport(**inner))
        elif rep.fault_class in ("specification", "validator", "coverage", "unlocated"):
            p.builder.append(rep)

    for r in reports:
        visit(r)
    return p


def widen_delta(team_json: dict, widen: dict[tuple[str, str], list[str]]) -> dict:
    """Theta (+) Delta for a footprint repair: add the paths to the footprints."""
    new = copy.deepcopy(team_json)
    for h in new.get("handoffs", []):
        for r in h.get("rules", []):
            for b in r.get("llm", []):
                add = widen.get((r.get("name"), b.get("feature")))
                if add:
                    fp = list(b.get("footprint", []))
                    b["footprint"] = fp + [x for x in add if x not in fp]
    return new


def _stamps(session: Session) -> int:
    return sum(len(l.stamps) for t in session.ct.team.traces.values() for l in t.links())


def _views_json(team: TypedTeam) -> dict:
    return team.raw.get("views") or {}


def classify_delta(old: TypedTeam, new: TypedTeam) -> str:
    """'noop', 'in-place', 'extension' or 'rebuild' (see module docstring)."""
    if json.dumps(old.raw, sort_keys=True) == json.dumps(new.raw, sort_keys=True):
        return "noop"
    old_h = {h.name: h for h in old.handoffs}
    new_h = {h.name: h for h in new.handoffs}
    removed = set(old_h) - set(new_h)
    retargeted = any(old_h[n].target != new_h[n].target or old_h[n].sources != new_h[n].sources
                     for n in set(old_h) & set(new_h))
    ov, nv = _views_json(old), _views_json(new)
    views_same = all(json.dumps(ov[v], sort_keys=True) == json.dumps(nv.get(v), sort_keys=True) for v in ov)
    writes_kept = all(sorted(new.writes.get(a, [])) == sorted(vs) for a, vs in old.writes.items())
    agents_kept = set(old.agents) <= set(new.agents)
    goal_same = json.dumps(old.raw.get("goal"), sort_keys=True) == json.dumps(new.raw.get("goal"), sort_keys=True) \
        and json.dumps(old.deliverable, sort_keys=True) == json.dumps(new.deliverable, sort_keys=True)
    if removed or retargeted or not views_same or not writes_kept or not agents_kept or not goal_same:
        return "rebuild"
    added = (set(nv) - set(ov)) or (set(new_h) - set(old_h)) or (set(new.agents) - set(old.agents))
    return "extension" if added else "in-place"


def apply_delta(session: Session, new_team: TypedTeam, task) -> ApplyResult:
    """Install an *admitted* Theta (+) Delta in the running team."""
    old = session.ct.typed
    before = _stamps(session)
    mode = classify_delta(old, new_team)
    if mode == "noop":
        return ApplyResult(session, "noop", before, before, 0)
    if mode == "rebuild":
        ct = compile_team(new_team, task, session.ct.workdir.parent / (session.ct.workdir.name + "_rebuild"),
                          lenient=session.ct.lenient)
        s2 = Session(ct, session.llm, session.ctx, k=session.k, temperature=session.temperature,
                     max_passes=session.rt.max_passes)
        session.ctx.bench.bind(s2.current_bodies)  # type: ignore[attr-defined]
        return ApplyResult(s2, "rebuild", 0, before, before)

    ct: CompiledTeam = session.ct
    old_h = {h.name for h in old.handoffs}
    # extension: new views (referring to existing classes), agents and hand-offs
    added_views = [v for v in new_team.views if v not in ct.mms]
    for v in added_views:
        b = MetamodelBuilder(v, f"http://autom2m/{new_team.name}/{v.lower()}")
        for decl in new_team.classes_of(v):
            cls = b.eclass(decl.name)
            cls._amt_engine_keyed = True
            cls._amt_goal = False
        for decl in new_team.classes_of(v):
            cls = b.get(decl.name)
            for a in decl.attrs.values():
                b.attribute(cls, a.name, _PRIM.get(a.type, "string"), many=a.many)
            for r in decl.refs.values():
                target = b.get(r.cls) if r.view == v else ct.mms[r.view].get(r.cls)
                b.reference(cls, r.name, target, many=r.many, containment=False)
        root = b.eclass(f"{v}Root")
        for decl in new_team.classes_of(v):
            b.add_root_slot(root, f"all_{decl.name}", decl.name)
        ct.mms[v] = b
        ct.team.add_view(b, root())
    for a, vs in new_team.writes.items():
        for v in vs:
            if v in added_views:
                ct.team.add_agent(a, v)
    # regenerate the rule modules
    registry: dict[str, str] = {}
    bindings: dict = {}
    invalidated = 0
    order = handoff_order(new_team)
    for h in order:
        text = generate_module(new_team, h, registry, bindings, lenient=ct.lenient)
        path = ct.workdir / f"{h.name}.agenthot"
        if h.name not in old_h:
            path.write_text(text)
            ct.team.add_handoff(h.name, path, h.target)
        elif text != ct.rule_texts.get(h.name):
            path.write_text(text)
            session.rt._modules.pop(h.name, None)
        ct.rule_texts[h.name] = text
    # values whose prompt or validator changed become stale explicitly
    for key, meta in bindings.items():
        old_meta = ct.bindings.get(key)
        changed = old_meta is None or [(v.id, v.args) for v in old_meta.validators] != [(v.id, v.args) for v in meta.validators] \
            or ct.registry.get(meta.prompt_key) != registry.get(meta.prompt_key)
        if changed and meta.handoff in ct.team.traces:
            for link in ct.team.traces[meta.handoff].links():
                if link.rule == meta.rule and meta.feature in link.stamps:
                    del link.stamps[meta.feature]
                    invalidated += 1
    # every escalation earns a fresh budget after a repair
    for t in ct.team.traces.values():
        for link in t.links():
            link.failed_stamps.clear()
    ct.typed = new_team
    ct.registry.clear()
    ct.registry.update(registry)
    ct.bindings = bindings
    ct.handoff_order = [h.name for h in order]
    ct.team.handoffs = {n: ct.team.handoffs[n] for n in ct.handoff_order}
    return ApplyResult(session, mode, _stamps(session), before, invalidated)


def retry_bindings(session: Session, targets: list[tuple[str, str, str]]) -> int:
    """Sampling faults: clear the escalation, giving the binding a fresh budget."""
    n = 0
    for handoff, target_key, binding in targets:
        trace = session.ct.team.traces.get(handoff)
        for link in trace.links() if trace else []:
            if link.target_key == target_key and binding in link.failed_stamps:
                del link.failed_stamps[binding]
                n += 1
    return n


def resample_upstream(session: Session, handoff: str, target_key: str, binding: str) -> int:
    """Upstream faults: the upstream value loses its stamp and is re-sampled;
    its dependants become stale through their stamps."""
    n = 0
    trace = session.ct.team.traces.get(handoff)
    if trace is None:
        return 0
    for link in trace.links():
        if link.target_key == target_key and binding in link.stamps:
            del link.stamps[binding]
            n += 1
    for t in session.ct.team.traces.values():
        for link in t.links():
            link.failed_stamps.clear()
    return n
