"""models@run.time for the team: keeps Team causally connected to the
running hand-offs and drives them to a fixpoint (Sec III-D).

Cross-hand-off propagation needs no explicit message-passing data
structure: hand-offs are just re-run in registration order, and a
downstream hand-off's own stamp check (Algorithm 1) automatically detects
when an upstream hand-off changed a value its footprint reads. Iterating
to a fixpoint (capped by `max_passes`, escalating rather than looping
forever, mirroring Proposition 3) is enough to let obligations cascade
along the network (Req->Arch->Code->Test) within one team run.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..engine.binding import _retry_prompt, accept_sample, build_prompt
from ..engine.executor import (
    HandoffReport,
    PendingBinding,
    _index_existing_targets,
    run_handoff,
)
from ..engine.executor import (
    acceptance_holds as _handoff_acceptance_holds,
)
from ..engine.expr import eval_expr
from ..engine.helpers_loader import load_helpers
from ..engine.matcher import compute_matches
from ..engine.obligations import Obligation, from_handoff_report
from ..engine.trace import digest
from ..llm.base import LLMBackend
from ..rules.ast import Module, StochasticBinding
from ..rules.parser import parse_module_file
from .model import Team


@dataclass
class TeamRunReport:
    handoff_reports: dict[str, HandoffReport] = field(default_factory=dict)
    obligations: list[Obligation] = field(default_factory=list)
    passes: int = 0

    @property
    def escalations(self):
        return [e for r in self.handoff_reports.values() for e in r.escalations]

    @property
    def pending(self) -> list[PendingBinding]:
        """Host mode: bindings waiting for a value from the host."""
        return [p for r in self.handoff_reports.values() for p in r.pending]

    @property
    def blocked(self) -> list[PendingBinding]:
        """Host mode: bindings whose footprint reads a not-yet-filled upstream value."""
        return [p for r in self.handoff_reports.values() for p in r.blocked]

    def summary(self) -> str:
        lines = [f"Team run: {self.passes} pass(es)"]
        for name, r in self.handoff_reports.items():
            lines.append(
                f"  {name}: +{len(r.created)} created, -{len(r.deleted)} deleted, "
                f"{len(r.resampled)} resampled, {len(r.escalations)} escalation(s)"
            )
        if self.obligations:
            lines.append(f"  obligations discharged: {len(self.obligations)}")
            for o in self.obligations:
                lines.append(f"    - {o.handoff}: {o.target_key}.{o.binding} -> {o.agent}")
        if self.pending or self.blocked:
            lines.append(f"  pending host bindings: {len(self.pending)} ready, {len(self.blocked)} blocked on upstream")
        if self.escalations:
            lines.append(f"  ESCALATIONS: {len(self.escalations)}")
            for e in self.escalations:
                lines.append(f"    ! {e.rule}: {e.target_key}.{e.binding} ({e.reason})")
        return "\n".join(lines)


class TeamRuntime:
    def __init__(
        self,
        team: Team,
        llm: LLMBackend,
        *,
        max_resamples: int = 3,
        temperature: float = 0.2,
        max_passes: int = 5,
    ) -> None:
        self.team = team
        self.llm = llm
        self.max_resamples = max_resamples
        self.temperature = temperature
        self.max_passes = max_passes
        self._modules: dict[str, Module] = {}
        self._last_reports: dict[str, HandoffReport] = {}

    def module_for(self, handoff_name: str) -> Module:
        if handoff_name not in self._modules:
            spec = self.team.handoffs[handoff_name]
            self._modules[handoff_name] = parse_module_file(spec.rule_path)
        return self._modules[handoff_name]

    def run_handoff_once(self, handoff_name: str) -> HandoffReport:
        team = self.team
        spec = team.handoffs[handoff_name]
        module = self.module_for(handoff_name)
        source_roots = {sm.alias: team.roots[sm.mm_name] for sm in module.sources}
        target_root = team.roots[spec.target_mm]
        target_mm = team.views[spec.target_mm]
        trace = team.traces[handoff_name]
        report = run_handoff(
            module,
            source_roots,
            target_root,
            target_mm,
            trace,
            self.llm,
            base_dir=spec.rule_path.parent,
            max_resamples=self.max_resamples,
            temperature=self.temperature,
        )
        self._last_reports[handoff_name] = report
        return report

    def run_to_fixpoint(self) -> TeamRunReport:
        team_report = TeamRunReport()
        for _pass_num in range(self.max_passes):
            team_report.passes += 1
            any_change = False
            if getattr(self.llm, "deferred", False):
                # Seed from the persisted traces so a downstream hand-off
                # registered *before* its upstream still sees which upstream
                # values are unfilled; the executor adds to it as it goes.
                self.llm.incomplete = self.unstamped_targets()
            for handoff_name, spec in self.team.handoffs.items():
                this_pass = report = self.run_handoff_once(handoff_name)
                prev = team_report.handoff_reports.get(handoff_name)
                if prev is not None:
                    # Keep what happened across *all* passes (created/deleted/
                    # resampled); open items (escalations, pending) reflect
                    # the latest pass only.
                    report = HandoffReport(
                        handoff=report.handoff,
                        created=prev.created + report.created,
                        deleted=prev.deleted + report.deleted,
                        resampled=prev.resampled + report.resampled,
                        escalations=report.escalations,
                        pending=report.pending,
                        blocked=report.blocked,
                    )
                team_report.handoff_reports[handoff_name] = report
                owner = self.team.owner_of(spec.target_mm)
                team_report.obligations.extend(
                    from_handoff_report(handoff_name, this_pass, owning_agent=owner.name if owner else None)
                )
                if this_pass.created or this_pass.deleted or this_pass.resampled:
                    any_change = True
            if not any_change:
                break
        return team_report

    # ------------------------------------------------------------------
    # Host mode (Claude Code fills stochastic bindings)
    # ------------------------------------------------------------------

    def _locate(self, target_key: str, binding_name: str):
        """Resolve a target key + binding name to everything needed to judge
        a value for it, recomputing the match (sources may have changed)."""
        try:
            rule_name, var, match_key = target_key.split("::", 2)
        except ValueError:
            raise KeyError(f"malformed target key {target_key!r} (expected Rule::var::match)") from None
        for handoff_name, spec in self.team.handoffs.items():
            module = self.module_for(handoff_name)
            rule = next((r for r in module.rules if r.name == rule_name), None)
            if rule is None:
                continue
            tp = next((t for t in rule.to_clause.patterns if t.var == var), None)
            binding = None
            if tp is not None:
                binding = next(
                    (b for b in tp.bindings if isinstance(b, StochasticBinding) and b.name == binding_name), None
                )
            if binding is None:
                raise KeyError(f"rule {rule_name} has no stochastic binding {var}.{binding_name}")
            trace = self.team.traces[handoff_name]
            link = trace.get(rule_name, match_key)
            helpers = load_helpers(module.uses, spec.rule_path.parent)
            roots_by_mm = {sm.mm_name: self.team.roots[sm.mm_name] for sm in module.sources}
            match = next(
                (m for m in compute_matches(rule, roots_by_mm, helpers) if m.match_key == match_key), None
            )
            target_obj = _index_existing_targets(self.team.roots[spec.target_mm]).get(target_key)
            return handoff_name, tp, binding, link, match, target_obj, helpers
        raise KeyError(f"no hand-off defines rule {rule_name!r}")

    def unstamped_targets(self) -> set[str]:
        """Target keys with at least one stochastic binding never accepted."""
        out: set[str] = set()
        for handoff_name in self.team.handoffs:
            module = self.module_for(handoff_name)
            rules = {r.name: r for r in module.rules}
            for link in self.team.traces[handoff_name].links():
                rule = rules.get(link.rule)
                if rule is None:
                    continue
                for tp in rule.to_clause.patterns:
                    if any(isinstance(b, StochasticBinding) and b.name not in link.stamps for b in tp.bindings):
                        out.add(f"{rule.name}::{tp.var}::{link.match_key}")
        return out

    def submit_binding(
        self, target_key: str, binding_name: str, value: str, footprint_version: str | None = None
    ) -> dict:
        """Host mode: judge a host-supplied value for one pending binding.

        `footprint_version` (from the PendingBinding) pins the value to the
        footprint its prompt showed; a mismatch is refused as stale.

        Same acceptance path as an in-engine sample (`accept_sample`: @check,
        Lift conformance, stamping). A rejection returns the reason and the
        retry prompt; after `max_resamples` rejections on an unchanged
        footprint the binding escalates (failed stamp), exactly as Algorithm 1
        does after k in-engine samples.
        """
        handoff_name, tp, binding, link, match, target_obj, helpers = self._locate(target_key, binding_name)
        base = {"target_key": target_key, "binding": binding_name, "handoff": handoff_name}
        if link is None or match is None or target_obj is None:
            return {**base, "status": "stale", "reason": "this match no longer exists; call run again"}

        footprint = eval_expr(binding.footprint_expr, match.bindings, helpers)
        fp_digest = digest(footprint)
        if footprint_version is not None and footprint_version != fp_digest:
            # The value was produced from a prompt whose footprint has since
            # changed (e.g. an upstream value accepted meanwhile). Accepting it
            # would stamp it as derived from data it never saw.
            return {**base, "status": "stale", "reason": "the footprint changed since this prompt was issued; fetch it again with next_bindings"}
        if link.stamps.get(binding_name) == fp_digest:
            return {**base, "status": "already_accepted", "reason": "value is already accepted for the current footprint"}
        if link.failed_stamps.get(binding_name) == fp_digest:
            return {**base, "status": "escalated", "reason": "escalated earlier on this footprint; change the source to retry"}

        ok, reason = accept_sample(
            binding, tp.var, target_obj, match, link, value, helpers, footprint=footprint, fp_digest=fp_digest
        )
        if ok:
            return {**base, "status": "accepted"}

        prev = link.rejections.get(binding_name)
        attempts = (link.attempts.get(binding_name, 0) if prev and prev.get("digest") == fp_digest else 0) + 1
        link.attempts[binding_name] = attempts
        link.rejections[binding_name] = {"value": (value or "")[:400], "reason": reason, "digest": fp_digest}
        if attempts >= self.max_resamples:
            link.failed_stamps[binding_name] = fp_digest
            return {**base, "status": "escalated", "reason": reason, "attempts": attempts}
        retry = _retry_prompt(build_prompt(binding, match, helpers, footprint), value, reason)
        return {
            **base,
            "status": "rejected",
            "reason": reason,
            "attempts": attempts,
            "attempts_left": self.max_resamples - attempts,
            "retry_prompt": retry,
        }

    def stamps_fresh(self) -> bool:
        """Every covered stochastic binding has a stamp equal to the digest of
        its current footprint (phi's "all stamps are fresh")."""
        for handoff_name, spec in self.team.handoffs.items():
            module = self.module_for(handoff_name)
            helpers = load_helpers(module.uses, spec.rule_path.parent)
            roots_by_mm = {sm.mm_name: self.team.roots[sm.mm_name] for sm in module.sources}
            trace = self.team.traces[handoff_name]
            for rule in module.rules:
                for m in compute_matches(rule, roots_by_mm, helpers):
                    link = trace.get(rule.name, m.match_key)
                    if link is None:
                        return False
                    for tp in rule.to_clause.patterns:
                        for b in tp.bindings:
                            if isinstance(b, StochasticBinding):
                                fp = eval_expr(b.footprint_expr, m.bindings, helpers)
                                if link.stamps.get(b.name) != digest(fp):
                                    return False
        return True

    def acceptance_holds(self) -> bool:
        """phi for the whole team: every hand-off's own phi holds, and every
        stochastic binding's stamp is fresh (nothing pending or escalated)."""
        if not self.stamps_fresh():
            return False
        for handoff_name, spec in self.team.handoffs.items():
            module = self.module_for(handoff_name)
            helpers = load_helpers(module.uses, spec.rule_path.parent)
            source_roots = {sm.alias: self.team.roots[sm.mm_name] for sm in module.sources}
            trace = self.team.traces[handoff_name]
            last = self._last_reports.get(handoff_name, HandoffReport(handoff=handoff_name))
            if not _handoff_acceptance_holds(module, source_roots, trace, last, helpers):
                return False
        return True
