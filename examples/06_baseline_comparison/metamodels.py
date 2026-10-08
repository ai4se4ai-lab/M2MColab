"""The (tiny) agentm2m-path metamodels for examples/06_baseline_comparison:
Story (Analyst) -> Arch (Architect), used only by run_agentm2m() in run.py.
run_freetext() and run_shared_schema() do NOT use these -- they operate on
plain Python dicts, on purpose (see README.md): the whole point of this
example is to compare agentm2m's structural guarantees against two baselines
that have no metamodel at all.
"""
from __future__ import annotations

from agentm2m.metamodel import MetamodelBuilder


def build_story_mm() -> MetamodelBuilder:
    b = MetamodelBuilder("Story", "http://agentm2m/examples/baseline_comparison/story")
    epic = b.eclass("Epic")
    b.attribute(epic, "name")

    story = b.eclass("UserStory")
    b.attribute(story, "id")
    b.attribute(story, "description")
    b.attribute(story, "constraint")
    b.reference(story, "epic", "Epic", many=False, containment=False)

    root = b.eclass("StoryModel")
    b.add_root_slot(root, "epics", "Epic")
    b.add_root_slot(root, "stories", "UserStory")
    return b


def build_arch_mm() -> MetamodelBuilder:
    b = MetamodelBuilder("Arch", "http://agentm2m/examples/baseline_comparison/arch")
    component = b.eclass("Component")
    b.attribute(component, "name")

    operation = b.eclass("Operation")
    b.attribute(operation, "id")
    b.attribute(operation, "name")
    b.attribute(operation, "signature")
    # `constraint` is a STRUCTURAL copy of the story's constraint -- never
    # sent to the LLM at all, so it cannot be dropped or paraphrased.
    b.attribute(operation, "constraint")
    b.reference(operation, "component", "Component", many=False, containment=False)

    root = b.eclass("ArchModel")
    b.add_root_slot(root, "components", "Component")
    b.add_root_slot(root, "operations", "Operation")
    return b
