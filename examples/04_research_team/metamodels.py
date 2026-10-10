"""View metamodels for the ResearchTeam example (examples/04_research_team):
Literature (Literature-Reviewer), Experiments (Experiment-Designer), Report
(Report-Writer). Each is an EMF-compatible pyecore EPackage built with
agenthot.metamodel.MetamodelBuilder, following the same style as
examples/01_devteam/metamodels.py.

Unlike 01_devteam's exclusively 1:1 hand-offs, this example's Report view is
populated by an n:m (multi-source) hand-off -- see rules/ExpLit2Report.agenthot
and README.md -- so ReportSection references BOTH an Experiments!ExperimentPlan
and a Literature!Claim.
"""
from __future__ import annotations

from agenthot.metamodel import MetamodelBuilder


def build_literature_mm() -> MetamodelBuilder:
    b = MetamodelBuilder("Literature", "http://agenthot/examples/research_team/literature")
    paper = b.eclass("Paper")
    b.attribute(paper, "id")
    b.attribute(paper, "title")

    claim = b.eclass("Claim")
    b.attribute(claim, "id")
    b.attribute(claim, "text")
    b.attribute(claim, "topic")

    b.reference(paper, "claims", "Claim", many=True, containment=True)

    root = b.eclass("LiteratureModel")
    b.add_root_slot(root, "papers", "Paper")
    return b


def build_experiments_mm(literature_mm: MetamodelBuilder) -> MetamodelBuilder:
    b = MetamodelBuilder("Experiments", "http://agenthot/examples/research_team/experiments")
    plan = b.eclass("ExperimentPlan")
    b.attribute(plan, "id")
    b.attribute(plan, "topic")
    b.attribute(plan, "method")
    b.reference(plan, "claim", literature_mm.get("Claim"), many=False, containment=False)

    root = b.eclass("ExperimentsModel")
    b.add_root_slot(root, "plans", "ExperimentPlan")
    return b


def build_report_mm(experiments_mm: MetamodelBuilder, literature_mm: MetamodelBuilder) -> MetamodelBuilder:
    b = MetamodelBuilder("Report", "http://agenthot/examples/research_team/report")
    section = b.eclass("ReportSection")
    b.attribute(section, "id")
    b.attribute(section, "title")
    b.attribute(section, "body")
    # Two cross-metamodel references -- this element is the product of an
    # n:m hand-off, so it points into BOTH source views, not just one.
    b.reference(section, "plan", experiments_mm.get("ExperimentPlan"), many=False, containment=False)
    b.reference(section, "claim", literature_mm.get("Claim"), many=False, containment=False)

    root = b.eclass("ReportModel")
    b.add_root_slot(root, "sections", "ReportSection")
    return b
