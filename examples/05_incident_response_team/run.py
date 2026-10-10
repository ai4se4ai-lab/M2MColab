#!/usr/bin/env python3
"""Run the IncidentResponseTeam example: Monitor -> Triage -> Remediation ->
Postmortem. Demonstrates two mechanisms no earlier example exercises:

  1. Lift (text-to-model): Incident2Action.agenthot's `self <- @llm(...)`
     binding populates a whole RemediationAction from one LLM call, whose
     sampled text is parsed as JSON and written onto the element's
     EAttributes (agenthot.engine.lift.lift_json_into_element).
  2. An executable-oracle validator and a genuine escalation:
     `dryRun`'s @check (`passesDryRun`, rules/helpers.py) actually *runs*
     the sampled dry-run script as a subprocess rather than just parsing
     it, reusing agenthot.engine.validators.run_pytest_oracle. Under
     `--llm mock` it deterministically rejects every attempt, so this run
     ends with real Escalations and `phi holds: False` -- printed and
     explained below (Proposition 3: "escalate, never loop").

    python examples/05_incident_response_team/run.py --llm mock
    python examples/05_incident_response_team/run.py                 # local Ollama
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from metamodels import build_actions_mm, build_alerts_mm, build_incidents_mm, build_reports_mm  # noqa: E402
from seed_models import build_seed_alerts_model  # noqa: E402

from agenthot.config import LLMConfig  # noqa: E402
from agenthot.llm.factory import make_backend  # noqa: E402
from agenthot.team.model import Team  # noqa: E402
from agenthot.team.runtime import TeamRuntime  # noqa: E402


def build_team() -> Team:
    alerts_mm = build_alerts_mm()
    incidents_mm = build_incidents_mm(alerts_mm)
    actions_mm = build_actions_mm(incidents_mm)
    reports_mm = build_reports_mm(actions_mm)

    alerts_root = build_seed_alerts_model(alerts_mm)
    incidents_root = incidents_mm.get("IncidentsModel")()
    actions_root = actions_mm.get("ActionsModel")()
    reports_root = reports_mm.get("ReportsModel")()

    team = Team()
    team.add_agent("Monitor", "Alerts")
    team.add_view(alerts_mm, alerts_root)
    team.add_agent("Triage", "Incidents")
    team.add_view(incidents_mm, incidents_root)
    team.add_agent("Remediation", "Actions")
    team.add_view(actions_mm, actions_root)
    team.add_agent("Postmortem", "Reports")
    team.add_view(reports_mm, reports_root)

    rules = HERE / "rules"
    team.add_handoff("Alert2Incident", rules / "Alert2Incident.agenthot", target_mm="Incidents")
    team.add_handoff("Incident2Action", rules / "Incident2Action.agenthot", target_mm="Actions")
    team.add_handoff("Action2Report", rules / "Action2Report.agenthot", target_mm="Reports")
    return team


def print_models(team: Team) -> None:
    incidents = team.roots["Incidents"]
    actions = team.roots["Actions"]
    reports = team.roots["Reports"]

    print("\n-- Incidents (Triage's view) --")
    for i in incidents.incidents:
        print(f"  Incident {i.id} [{i.severity}] (alert={i.alert.id}): {i.summary}")

    print("\n-- Actions (Remediation's view -- name/command/risk_level lifted from one JSON call) --")
    for ra in actions.actions:
        print(f"  RemediationAction {ra.id}: name={ra.name!r} command={ra.command!r} risk_level={ra.risk_level!r}")
        print(f"      dryRun sampled: {ra.dryRun!r}")

    print("\n-- Reports (Postmortem's view) --")
    for pr in reports.reports:
        print(f"  PostmortemReport {pr.id} (action={pr.action.id}): {pr.narrative}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--llm", default=None, help="ollama|openai|anthropic|mock (default: $LLM_PROVIDER, else ollama)")
    parser.add_argument("--model", default=None, help="model tag override")
    args = parser.parse_args()

    team = build_team()
    cfg = LLMConfig.from_env()
    llm = make_backend(cfg, override_provider=args.llm, override_model=args.model)
    print(f"Using LLM backend: {llm.name} ({args.model or cfg.model})")

    # The mock backend is fully deterministic -- if `dryRun`'s @check
    # rejects its sampled script once, it will reject it every time, so
    # extra resamples never help. max_resamples=1 makes that explicit and
    # keeps the run fast, rather than burning 2 extra subprocess calls per
    # RemediationAction to reach the same, foregone conclusion.
    runtime = TeamRuntime(team, llm, max_resamples=1)
    report = runtime.run_to_fixpoint()

    print(report.summary())
    print_models(team)

    print("\n=== Escalations (Proposition 3: 'escalate, never loop') ===")
    if report.escalations:
        for e in report.escalations:
            print(f"  ! {e.rule}.{e.binding} on {e.target_key}: {e.reason}")
        print(
            "\nWhat this means operationally: the engine sampled a dry-run script for each "
            "RemediationAction and ran it as a real subprocess (agenthot.engine.validators."
            "run_pytest_oracle); none of them exited cleanly, so the `dryRun` @check rejected "
            "every attempt. Per Algorithm 1 / Proposition 3, the engine does NOT retry forever "
            "or silently accept an unvalidated remediation script -- it raises an Escalation and "
            "stops, leaving the RemediationAction's `dryRun` field unset. That escalation is the "
            "hand-off's explicit signal that a human (or a stronger validator/model) must look at "
            "this binding before the remediation is considered safe to run; the engine never lets "
            "an un-dry-run-tested command through by default."
        )
    else:
        print("  (none this run)")

    phi_holds = runtime.acceptance_holds()
    print(f"\nphi holds: {phi_holds}")
    if not phi_holds:
        print(
            "phi is False by design in this example: Incident2Action's `dryRun` validator is "
            "deliberately strict (an executable oracle, not a parser) and the mock backend cannot "
            "satisfy it, so `Incident2Action`'s own acceptance predicate has open escalations. "
            "This is the expected, intentional outcome for this example -- see README.md."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
