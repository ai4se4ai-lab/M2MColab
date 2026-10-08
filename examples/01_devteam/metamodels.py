"""The four view metamodels of the paper's DevTeam running example
(Fig. 1 / Sec III-B): Req (Analyst), Arch (Architect), Code (Developer),
Test (Tester). Each is an EMF-compatible pyecore EPackage built with
agentm2m.metamodel.MetamodelBuilder.
"""
from __future__ import annotations

from agentm2m.metamodel import MetamodelBuilder


def build_req_mm() -> MetamodelBuilder:
    b = MetamodelBuilder("Req", "http://agentm2m/examples/devteam/req")
    epic = b.eclass("Epic")
    b.attribute(epic, "name")

    criterion = b.eclass("Criterion")
    b.attribute(criterion, "id")
    b.attribute(criterion, "text")

    story = b.eclass("UserStory")
    b.attribute(story, "id")
    b.attribute(story, "status")  # "draft" | "accepted"
    b.reference(story, "epic", "Epic", many=False, containment=False)
    b.reference(story, "criteria", "Criterion", many=True, containment=True)

    root = b.eclass("ReqModel")
    b.add_root_slot(root, "epics", "Epic")
    b.add_root_slot(root, "stories", "UserStory")
    return b


def build_arch_mm() -> MetamodelBuilder:
    b = MetamodelBuilder("Arch", "http://agentm2m/examples/devteam/arch")
    component = b.eclass("Component")
    b.attribute(component, "name")

    operation = b.eclass("Operation")
    b.attribute(operation, "name")
    b.attribute(operation, "signature")
    b.reference(operation, "component", "Component", many=False, containment=False)

    root = b.eclass("ArchModel")
    b.add_root_slot(root, "components", "Component")
    b.add_root_slot(root, "operations", "Operation")
    return b


def build_code_mm(arch_mm: MetamodelBuilder) -> MetamodelBuilder:
    b = MetamodelBuilder("Code", "http://agentm2m/examples/devteam/code")
    edit = b.eclass("CodeEdit")
    b.attribute(edit, "name")
    b.attribute(edit, "body")
    b.reference(edit, "operation", arch_mm.get("Operation"), many=False, containment=False)

    root = b.eclass("CodeModel")
    b.add_root_slot(root, "edits", "CodeEdit")
    return b


def build_test_mm(req_mm: MetamodelBuilder) -> MetamodelBuilder:
    b = MetamodelBuilder("Test", "http://agentm2m/examples/devteam/test")
    case = b.eclass("TestCase")
    b.attribute(case, "name")
    b.attribute(case, "oracle")
    b.reference(case, "criterion", req_mm.get("Criterion"), many=False, containment=False)

    root = b.eclass("TestModel")
    b.add_root_slot(root, "cases", "TestCase")
    return b
