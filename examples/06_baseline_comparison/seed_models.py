"""Seed Story model for examples/06_baseline_comparison's agentm2m path
(run_agentm2m() in run.py). The SAME underlying story -- id, epic,
description, constraint -- is also used (as a plain dict, STORY in run.py)
by run_freetext() and run_shared_schema(), so all three paths work the same
toy task."""
from __future__ import annotations

from agentm2m.metamodel import MetamodelBuilder


def build_seed_story_model(story_mm: MetamodelBuilder):
    root = story_mm.get("StoryModel")()

    payments = story_mm.new("Epic", name="Payments")
    root.epics.append(payments)

    s1 = story_mm.new(
        "UserStory",
        id="S1",
        epic=payments,
        description="As a user, I want to cancel my subscription so that I stop being charged.",
        constraint="Cancellation must be blocked if there is a pending refund on the account.",
    )
    root.stories.append(s1)
    return root
