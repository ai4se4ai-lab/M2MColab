"""Obl(Delta) (Sec III-C): the (target, binding) pairs whose accepted value
needs re-sampling because their footprint intersected a change.

The engine doesn't need a separate change-diffing pass to compute this --
Algorithm 1's stamp check (`stamp(t,b) != #den(e_b)_m`) *is* the obligation
test, evaluated fresh on every run. This module just gives that result a
name and routes it to the agent who owns the affected view, for reporting
(the CLI / examples print these; see docs/ARCHITECTURE.md, Proposition 2).
"""
from __future__ import annotations

from dataclasses import dataclass

from .executor import HandoffReport


@dataclass(frozen=True)
class Obligation:
    handoff: str
    target_key: str
    binding: str
    agent: str | None = None


def from_handoff_report(handoff: str, report: HandoffReport, *, owning_agent: str | None = None) -> list[Obligation]:
    return [
        Obligation(handoff=handoff, target_key=target_key, binding=binding, agent=owning_agent)
        for target_key, binding in report.resampled
    ]
