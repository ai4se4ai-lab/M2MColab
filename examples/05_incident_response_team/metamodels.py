"""View metamodels for the IncidentResponseTeam example
(examples/05_incident_response_team): Alerts (Monitor), Incidents (Triage),
Actions (Remediation), Reports (Postmortem).

Unlike every earlier example, `RemediationAction` is populated by a Lift
binding (see rules/Incident2Action.agentm2m and
agentm2m.engine.lift.lift_json_into_element): its `name`, `command`, and
`risk_level` EAttributes are all written in one stochastic call, from
LLM-sampled JSON, rather than one attribute per binding.
"""
from __future__ import annotations

from agentm2m.metamodel import MetamodelBuilder


def build_alerts_mm() -> MetamodelBuilder:
    b = MetamodelBuilder("Alerts", "http://agentm2m/examples/incident_response/alerts")
    alert = b.eclass("Alert")
    b.attribute(alert, "id")
    b.attribute(alert, "source")
    b.attribute(alert, "message")

    root = b.eclass("AlertsModel")
    b.add_root_slot(root, "alerts", "Alert")
    return b


def build_incidents_mm(alerts_mm: MetamodelBuilder) -> MetamodelBuilder:
    b = MetamodelBuilder("Incidents", "http://agentm2m/examples/incident_response/incidents")
    incident = b.eclass("Incident")
    b.attribute(incident, "id")
    b.attribute(incident, "severity")
    b.attribute(incident, "summary")
    b.reference(incident, "alert", alerts_mm.get("Alert"), many=False, containment=False)

    root = b.eclass("IncidentsModel")
    b.add_root_slot(root, "incidents", "Incident")
    return b


def build_actions_mm(incidents_mm: MetamodelBuilder) -> MetamodelBuilder:
    b = MetamodelBuilder("Actions", "http://agentm2m/examples/incident_response/actions")
    action = b.eclass("RemediationAction")
    b.attribute(action, "id")
    # name / command / risk_level are written in ONE call by the Lift
    # binding (`self <- @llm(...)`), not one @llm binding each.
    b.attribute(action, "name")
    b.attribute(action, "command")
    b.attribute(action, "risk_level")
    # dryRun is a SEPARATE, ordinary stochastic binding validated by an
    # executable-oracle @check (see rules/Incident2Action.agentm2m).
    b.attribute(action, "dryRun")
    b.reference(action, "incident", incidents_mm.get("Incident"), many=False, containment=False)

    root = b.eclass("ActionsModel")
    b.add_root_slot(root, "actions", "RemediationAction")
    return b


def build_reports_mm(actions_mm: MetamodelBuilder) -> MetamodelBuilder:
    b = MetamodelBuilder("Reports", "http://agentm2m/examples/incident_response/reports")
    report = b.eclass("PostmortemReport")
    b.attribute(report, "id")
    b.attribute(report, "narrative")
    b.reference(report, "action", actions_mm.get("RemediationAction"), many=False, containment=False)

    root = b.eclass("ReportsModel")
    b.add_root_slot(root, "reports", "PostmortemReport")
    return b
