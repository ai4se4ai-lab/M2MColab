"""Seed Alerts model for examples/05_incident_response_team: two raw alerts
that Monitor hands to Triage."""
from __future__ import annotations

from agentm2m.metamodel import MetamodelBuilder


def build_seed_alerts_model(alerts_mm: MetamodelBuilder):
    root = alerts_mm.get("AlertsModel")()

    a1 = alerts_mm.new(
        "Alert", id="A1", source="payments-api",
        message="5xx error rate spiked to 12 percent on the payments API over the last 5 minutes",
    )
    a2 = alerts_mm.new(
        "Alert", id="A2", source="auth-service",
        message="auth-service p99 latency exceeded 2 seconds for 10 consecutive minutes",
    )

    root.alerts.append(a1)
    root.alerts.append(a2)
    return root
