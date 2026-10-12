"""Algorithm 1: executing a hybrid hand-off T_ij.

    Mt <- {(r,m) | m |= p_r and g_r(m)}                      (fixed: M_i read-only)
    for (r,m) in Mt without a link in TL: create t; TL += (m,t,r)
    delete links (m,t,r) with (r,m) not in Mt, and their t
    evaluate all B^str; resolve source references via TL
    for (m,t,r) in TL, b in B_r^sem with stamp(t,b) != #den(e_b)_m:
        pi <- prompt_b (+) den(e_b)_m
        sample v ~ D(pi) until chk_b(v), at most k times
        if accepted: t.f_b <- v; stamp(t,b) <- #den(e_b)_m
        else: escalate, never loop
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..llm.base import LLMBackend, PendingSample
from ..metamodel.builder import MetamodelBuilder
from ..rules.ast import Module, StochasticBinding
from .binding import Escalation, apply_stochastic_binding, apply_structural_bindings
from .helpers_loader import load_helpers
from .matcher import compute_matches
from .trace import TraceLink, TraceModel, element_key

_TARGET_KEY_ATTR = "_amt_target_key"


@dataclass
class PendingBinding:
    """A stochastic binding awaiting a value from the host (host mode).

    `prompt` is prompt_b (+) den(e_b)_m -- the complete and only context the
    host may use to produce the value (footprint-bounded prompting)."""

    handoff: str
    rule: str
    target_key: str
    binding: str
    prompt: str
    fp_digest: str
    attempts: int = 0
    stale: bool = False  # True: re-sample of a previously accepted value (an obligation from a change)

    def to_dict(self) -> dict:
        return {
            "handoff": self.handoff,
            "rule": self.rule,
            "target_key": self.target_key,
            "binding": self.binding,
            "prompt": self.prompt,
            "attempts": self.attempts,
            "kind": "stale" if self.stale else "new",
            # the footprint digest this prompt was built from; a value
            # submitted for an older version is refused as stale
            "footprint_version": self.fp_digest,
        }


@dataclass
class HandoffReport:
    handoff: str
    created: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    resampled: list[tuple[str, str]] = field(default_factory=list)
    escalations: list[Escalation] = field(default_factory=list)
    # Host mode only: bindings waiting for a host-supplied value, and those
    # whose footprint still reads an unfilled upstream value.
    pending: list[PendingBinding] = field(default_factory=list)
    blocked: list[PendingBinding] = field(default_factory=list)

    @property
    def obligations_satisfied(self) -> list[tuple[str, str]]:
        """The (target, binding) pairs re-sampled this run == Obl(delta) this
        hand-off discharged (Proposition 2): stamp mismatches, resolved."""
        return self.resampled


def _index_existing_targets(target_root: Any) -> dict[str, Any]:
    registry: dict[str, Any] = {}
    candidates = [target_root, *target_root.eAllContents()]
    for el in candidates:
        key = getattr(el, _TARGET_KEY_ATTR, None)
        if key is not None:
            registry[key] = el
    return registry


def run_handoff(
    module: Module,
    source_roots: dict[str, Any],
    target_root: Any,
    target_mm: MetamodelBuilder,
    trace: TraceModel,
    llm: LLMBackend,
    *,
    base_dir: Path,
    max_resamples: int = 3,
    temperature: float = 0.2,
) -> HandoffReport:
    helpers = load_helpers(module.uses, base_dir)

    # Rule patterns reference metamodels by name (`Req!Epic`), while callers
    # pass source models keyed by the `create ... from IN : Req;` alias;
    # translate once so the matcher can look models up by metamodel name.
    roots_by_mm = {sm.mm_name: source_roots[sm.alias] for sm in module.sources}

    # Line 1: fixed match set (sources are read-only).
    matches_by_rule = {rule.name: compute_matches(rule, roots_by_mm, helpers) for rule in module.rules}
    current_keys_by_rule = {r: {m.match_key for m in ms} for r, ms in matches_by_rule.items()}

    report = HandoffReport(handoff=module.name)
    target_registry = _index_existing_targets(target_root)

    # Lines 2-3: create elements for new matches.
    for rule in module.rules:
        for m in matches_by_rule[rule.name]:
            for tp in rule.to_clause.patterns:
                target_key = f"{rule.name}::{tp.var}::{m.match_key}"
                link = trace.get(rule.name, m.match_key)
                if link is not None and target_key in target_registry:
                    continue
                obj = target_mm.get(tp.type_name)()
                setattr(obj, _TARGET_KEY_ATTR, target_key)
                slot = target_mm.root_slot_for(tp.type_name)
                getattr(target_root, slot).append(obj)
                target_registry[target_key] = obj
                report.created.append(target_key)
                if link is None:
                    link = TraceLink(
                        rule=rule.name,
                        match_key=m.match_key,
                        source_keys={v: element_key(el) for v, el in m.bindings.items()},
                        target_key=target_key,
                    )
                trace.put(link)

    # Line 4: delete stale links and their target elements.
    for link in list(trace.links()):
        if link.rule not in current_keys_by_rule:
            continue
        if link.match_key not in current_keys_by_rule[link.rule]:
            obj = target_registry.pop(link.target_key, None)
            if obj is not None:
                obj.delete()
            trace.remove(link.rule, link.match_key)
            report.deleted.append(link.target_key)

    # Line 5: structural bindings (resolved through the now-complete trace).
    for rule in module.rules:
        for m in matches_by_rule[rule.name]:
            for tp in rule.to_clause.patterns:
                target_key = f"{rule.name}::{tp.var}::{m.match_key}"
                apply_structural_bindings(tp, target_registry[target_key], m, trace, target_registry, helpers)

    # Lines 6-13: stochastic bindings, gated by the version-stamp check.
    for rule in module.rules:
        for m in matches_by_rule[rule.name]:
            for tp in rule.to_clause.patterns:
                target_key = f"{rule.name}::{tp.var}::{m.match_key}"
                obj = target_registry[target_key]
                link = trace.get(rule.name, m.match_key)
                assert link is not None
                for b in tp.bindings:
                    if isinstance(b, StochasticBinding):
                        try:
                            changed, escalation = apply_stochastic_binding(
                                b,
                                tp.var,
                                obj,
                                m,
                                link,
                                llm,
                                helpers,
                                max_resamples=max_resamples,
                                temperature=temperature,
                            )
                        except PendingSample as ps:
                            pb = PendingBinding(
                                handoff=module.name,
                                rule=rule.name,
                                target_key=target_key,
                                binding=b.name,
                                prompt=ps.prompt,
                                fp_digest=ps.fp_digest,
                                attempts=ps.attempts,
                                stale=ps.stale,
                            )
                            (report.blocked if ps.blocked else report.pending).append(pb)
                            incomplete = getattr(llm, "incomplete", None)
                            if incomplete is not None:
                                incomplete.add(target_key)
                            continue
                        if changed:
                            report.resampled.append((target_key, b.name))
                        if escalation is not None:
                            report.escalations.append(escalation)

    return report


def acceptance_holds(module: Module, source_roots: dict[str, Any], trace: TraceModel, report: HandoffReport, helpers) -> bool:
    """phi for this hand-off: every match covered, no open obligation, all
    validators passed (i.e. no escalations) this run."""
    roots_by_mm = {sm.mm_name: source_roots[sm.alias] for sm in module.sources}
    matches_by_rule = {rule.name: compute_matches(rule, roots_by_mm, helpers) for rule in module.rules}
    all_match_keys = {(r, m.match_key) for r, ms in matches_by_rule.items() for m in ms}
    covered = {(l.rule, l.match_key) for l in trace.links()}
    return all_match_keys <= covered and not report.escalations
