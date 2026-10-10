"""HOT: evolving the team at runtime (Sec III-D, "Security Reviewer" scenario).

Adding an agent is a declarative relation model (`TeamChange`) that a HOT
(`apply_hot`) turns into: a new agent + view, a newly registered hand-off
with a *fresh, empty* trace model, an updated write-rights omega, and a
strengthened acceptance predicate phi (TeamRuntime.acceptance_holds()
iterates every registered hand-off, so the new one now has to hold too).

Because the new hand-off's trace starts empty, its very first run treats
every pre-existing match in its source view as new -- so the new agent
receives obligations for *all* existing matches on arrival, with no
hand-written glue (P3, Sec III-D).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..metamodel.builder import MetamodelBuilder
from .model import Team


@dataclass
class TeamChange:
    """The relation model a HOT consumes: "add agent `agent_name`, owning a
    new view `view`, related to the existing team via hand-off `handoff_name`"."""

    agent_name: str
    view: MetamodelBuilder
    view_root: Any
    handoff_name: str
    rule_path: str | Path


def apply_hot(team: Team, change: TeamChange) -> None:
    team.add_view(change.view, change.view_root)
    team.add_agent(change.agent_name, change.view.package.name)
    team.add_handoff(change.handoff_name, change.rule_path, change.view.package.name)
