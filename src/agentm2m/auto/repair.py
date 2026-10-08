"""Step F: checked re-composition.

A team delta Δ (a revised typed team) is admitted only if Θ ⊕ Δ passes the
checker. It is then applied to the *running* team:
  * rule-level edits (footprint, prompt, validator, rules of an existing
    hand-off): the hand-off's module is regenerated in place; bindings whose
    prompt or validator changed lose their stamp (an explicit obligation), a
    changed footprint invalidates its stamp through the digest check, and
    every other accepted value is kept;
  * additive edits (new views, agents, hand-offs): a HOT adds them to the
    running team with fresh traces (team/hot.py semantics);
  * anything else (changed or removed views, classes, write rights): the
    team is rebuilt from scratch, as a free-form builder must.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from ..metamodel.builder import MetamodelBuilder
from .compile import (
    GOAL_VIEW,
    CompiledTeam,
    Session,
    _handoff_order,
    _PRIM,
    compile_team,
    generate_module,
)
from .typed_team import TypedTeam, parse_team


@dataclass
class ApplyResult:
    session: Session
    mode: str  # "in-place" | "hot" | "rebuild" | "noop"
    kept: int
    before: int
    invalidated: int


def normalize(team_json: dict) -> dict:
    """The goal view is owned by Lift: always the fixed metamodel."""
    t = dict(team_json)
    t["goal_view"] = "Goal"
    views = dict(t.get("views") or {})
    views["Goal"] = json.loads(json.dumps(GOAL_VIEW))
    t["views"] = views
    return t


def _stamps(session: Session) -> int:
    return sum(len(l.stamps) for t in session.ct.team.traces.values() for l in t.links())


def _sig(team: TypedTeam):
    return (json.dumps(team.raw.get("views"), sort_keys=True), json.dumps(team.writes, sort_keys=True),
            json.dumps(sorted((a.name, tuple(a.tools)) for a in team.agents.values())))


def apply_delta(session: Session, new_team: TypedTeam, task) -> ApplyResult:
    old = session.ct.typed
    before = _stamps(session)
    old_h = {h.name: h for h in old.handoffs}
    new_h = {h.name: h for h in new_team.handoffs}
    same_struct = _sig(old) == _sig(new_team)
    removed = set(old_h) - set(new_h)
    retargeted = any(old_h[n].target != new_h[n].target or old_h[n].sources != new_h[n].sources
                     for n in set(old_h) & set(new_h))
    old_views = old.raw.get("views") or {}
    new_views = new_team.raw.get("views") or {}
    additive = (not removed and not retargeted
                and all(json.dumps(old_views[v], sort_keys=True) == json.dumps(new_views.get(v), sort_keys=True) for v in old_views)
                and all(new_team.writes.get(a) == vs for a, vs in old.writes.items()))
    if not (same_struct or additive) or removed or retargeted:
        ct = compile_team(new_team, task, session.ct.workdir.parent / (session.ct.workdir.name + "_rebuild"))
        s2 = Session(ct, session.llm, session.ctx, k=session.k, temperature=session.temperature,
                     max_passes=session.rt.max_passes)
        session.ctx.bench.bind(s2.current_bodies)  # type: ignore[attr-defined]
        return ApplyResult(s2, "rebuild", 0, before, before)

    ct: CompiledTeam = session.ct
    mode = "in-place"
    # HOT: new views (referencing existing classes), agents and hand-offs
    added_views = [v for v in new_team.views if v not in ct.mms]
    if added_views or set(new_h) - set(old_h):
        mode = "hot"
    for v in added_views:
        b = MetamodelBuilder(v, f"http://agentm2m/auto/{new_team.name}/{v.lower()}")
        for decl in new_team.classes_of(v):
            cls = b.eclass(decl.name)
            cls._amt_engine_keyed = True
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
    # regenerate modules
    registry: dict[str, str] = {}
    bindings: dict = {}
    invalidated = 0
    order = _handoff_order(new_team)
    for h in order:
        text = generate_module(new_team, h, registry, bindings)
        if h.name not in old_h:
            path = ct.workdir / f"{h.name}.agentm2m"
            path.write_text(text)
            ct.team.add_handoff(h.name, path, h.target)
        elif text != ct.rule_texts.get(h.name):
            (ct.workdir / f"{h.name}.agentm2m").write_text(text)
            session.rt._modules.pop(h.name, None)
        ct.rule_texts[h.name] = text
    # explicit obligations: prompt or validator changed
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
    # keep the runtime's hand-off registration order topological
    ct.team.handoffs = {n: ct.team.handoffs[n] for n in ct.handoff_order}
    return ApplyResult(session, mode, _stamps(session), before, invalidated)


def retry_bindings(session: Session, targets: list[tuple[str, str, str]]) -> int:
    """Remedy for sampling faults: clear the failed stamp so the binding gets a fresh budget."""
    n = 0
    for handoff, target_key, binding in targets:
        for link in session.ct.team.traces.get(handoff, None).links() if handoff in session.ct.team.traces else []:
            if link.target_key == target_key and binding in link.failed_stamps:
                del link.failed_stamps[binding]
                n += 1
    return n


def resample_upstream(session: Session, handoff: str, target_key: str, binding: str) -> int:
    """Remedy for upstream faults: the upstream value becomes an obligation."""
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
