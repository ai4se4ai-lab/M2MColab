"""The team megamodel Team = (A, V, T, omega, phi) (Sec III-A, Fig. 1).

Kept as plain Python state rather than a modeled Ecore instance: it is
engine-internal bookkeeping the agents never exchange as an artifact (unlike
the view models M_i, which *are* EMF/pyecore models -- see
metamodel/builder.py and docs/ARCHITECTURE.md for the rationale).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..engine.trace import TraceModel
from ..metamodel.builder import MetamodelBuilder


@dataclass
class Agent:
    name: str
    view: str  # the metamodel name this agent owns (omega(agent) = {view})


@dataclass
class HandoffSpec:
    name: str  # module name, e.g. "Req2Arch"
    rule_path: Path
    target_mm: str


@dataclass
class Team:
    agents: dict[str, Agent] = field(default_factory=dict)
    views: dict[str, MetamodelBuilder] = field(default_factory=dict)
    handoffs: dict[str, HandoffSpec] = field(default_factory=dict)
    write_rights: dict[str, set[str]] = field(default_factory=dict)  # agent name -> set of MM names (omega)
    roots: dict[str, Any] = field(default_factory=dict)  # MM name -> live root model instance
    traces: dict[str, TraceModel] = field(default_factory=dict)  # handoff name -> TraceModel

    def add_agent(self, name: str, view: str) -> Agent:
        agent = Agent(name=name, view=view)
        self.agents[name] = agent
        self.write_rights.setdefault(name, set()).add(view)
        return agent

    def add_view(self, mm: MetamodelBuilder, root: Any) -> None:
        self.views[mm.package.name] = mm
        self.roots[mm.package.name] = root

    def add_handoff(self, name: str, rule_path: str | Path, target_mm: str) -> HandoffSpec:
        spec = HandoffSpec(name=name, rule_path=Path(rule_path), target_mm=target_mm)
        self.handoffs[name] = spec
        self.traces.setdefault(name, TraceModel(handoff=name))
        return spec

    def owner_of(self, mm_name: str) -> Agent | None:
        for agent_name, mms in self.write_rights.items():
            if mm_name in mms:
                return self.agents[agent_name]
        return None
