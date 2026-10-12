"""Helpers for the ResearchTeam rule modules, loaded via each module's
`uses 'helpers.py';` declaration (see agenthot.engine.helpers_loader).
"""
from __future__ import annotations


def notTooShort(text: str) -> bool:
    """@check validator reused by both hand-offs: a stochastic value must be
    a non-trivial string, not an empty or near-empty completion."""
    return len((text or "").strip()) >= 10


def sectionId(plan, claim) -> str:
    """Structural helper: a ReportSection's id must be unique per (plan,
    claim) pair, since the n:m hand-off can match the same plan against
    several claims (and vice versa) -- unlike a 1:1 hand-off where the
    target's id can just copy the single source element's id."""
    return f"{plan.id}-{claim.id}"


def combinedFootprint(plan, claim):
    """The footprint for ReportSection.body: a list of BOTH source elements
    bound in the match, so the stochastic binding's prompt is built from
    both views, not just one -- this is what an n:m hand-off's footprint
    looks like (contrast with every 01_devteam footprint, which is always a
    single source element or its own sub-collection)."""
    return [plan, claim]
