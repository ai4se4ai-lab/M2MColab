"""Seed Req model for the DevTeam example: a small backlog with one
rejected/draft story (to show the guard `s.status = #accepted` filtering
it out) and the S2 story used in the paper's "Change scenario" runbox.
"""
from __future__ import annotations

from agenthot.metamodel import MetamodelBuilder


def build_seed_req_model(req_mm: MetamodelBuilder):
    root = req_mm.get("ReqModel")()

    payments = req_mm.new("Epic", name="Payments")
    notifications = req_mm.new("Epic", name="Notifications")
    root.epics.append(payments)
    root.epics.append(notifications)

    s1 = req_mm.new("UserStory", id="S1", status="accepted", epic=payments)
    s1.criteria.append(req_mm.new("Criterion", id="S1.1", text="must return HTTP 200 on a successful charge"))
    s1.criteria.append(req_mm.new("Criterion", id="S1.2", text="must reject charges over the configured limit"))

    s2 = req_mm.new("UserStory", id="S2", status="accepted", epic=payments)
    s2.criteria.append(req_mm.new("Criterion", id="S2.1", text="must refund the original payment method"))

    s3 = req_mm.new("UserStory", id="S3", status="draft", epic=notifications)
    s3.criteria.append(req_mm.new("Criterion", id="S3.1", text="must send an email receipt"))

    for s in (s1, s2, s3):
        root.stories.append(s)

    return root
