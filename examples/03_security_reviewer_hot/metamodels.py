"""The Security Reviewer's view metamodel (Fig. 1's dashed M_sec), added at
runtime by the HOT in this example -- not part of the original team.
"""
from __future__ import annotations

from agentm2m.metamodel import MetamodelBuilder


def build_sec_mm(arch_mm: MetamodelBuilder) -> MetamodelBuilder:
    b = MetamodelBuilder("Sec", "http://agentm2m/examples/devteam/sec")
    review = b.eclass("SecurityReview")
    b.attribute(review, "notes")
    b.attribute(review, "risk")
    b.reference(review, "operation", arch_mm.get("Operation"), many=False, containment=False)

    root = b.eclass("SecModel")
    b.add_root_slot(root, "reviews", "SecurityReview")
    return b
