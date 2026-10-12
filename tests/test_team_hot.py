"""Team megamodel + HOT: the paper's runtime-evolution scenario (Fig. 1's
dashed Security Reviewer, Sec III-D). Adding an agent mid-run must not
require hand-written glue, and the new agent must get obligations for
every pre-existing match on arrival.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agenthot.llm.mock_backend import MockBackend
from agenthot.metamodel.builder import MetamodelBuilder
from agenthot.team.hot import TeamChange, apply_hot
from agenthot.team.model import Team
from agenthot.team.runtime import TeamRuntime

REQ2ARCH = """
module Req2Arch;
create OUT : Arch from IN : Req;
uses 'helpers.py';

rule Story2Operation {
 from s : Req!UserStory (s.status = #accepted)
 to  op : Arch!Operation (
   name      <- s.id.toOpName(),
   signature <- @llm('Derive an API signature', s.criteria),
   @check signature.parses() )
}
"""

ARCH2SEC = """
module Arch2Sec;
create OUT : Sec from IN : Arch;
uses 'helpers.py';

rule Operation2Review {
 from op : Arch!Operation
 to sr : Sec!SecurityReview (
   operation <- op,
   notes     <- @llm('Flag any auth/authz concerns for this operation', op.signature) )
}
"""

HELPERS = """
import re

def toOpName(story_id):
    return "op_" + re.sub(r"[^A-Za-z0-9]+", "_", story_id).strip("_").lower()

def parses(signature):
    return bool(re.match(r"^[A-Za-z_][A-Za-z0-9_]*\\([^)]*\\)\\s*->\\s*\\S+$", signature or ""))
"""


def _build_req_mm() -> MetamodelBuilder:
    b = MetamodelBuilder("Req", "http://agenthot/req")
    criterion = b.eclass("Criterion")
    b.attribute(criterion, "text")
    story = b.eclass("UserStory")
    b.attribute(story, "id")
    b.attribute(story, "status")
    b.reference(story, "criteria", "Criterion", many=True, containment=True)
    root = b.eclass("ReqModel")
    b.add_root_slot(root, "stories", "UserStory")
    return b


def _build_arch_mm() -> MetamodelBuilder:
    b = MetamodelBuilder("Arch", "http://agenthot/arch")
    operation = b.eclass("Operation")
    b.attribute(operation, "name")
    b.attribute(operation, "signature")
    root = b.eclass("ArchModel")
    b.add_root_slot(root, "operations", "Operation")
    return b


def _build_sec_mm(arch_mm: MetamodelBuilder) -> MetamodelBuilder:
    b = MetamodelBuilder("Sec", "http://agenthot/sec")
    review = b.eclass("SecurityReview")
    b.attribute(review, "notes")
    b.reference(review, "operation", arch_mm.get("Operation"), many=False, containment=False)
    root = b.eclass("SecModel")
    b.add_root_slot(root, "reviews", "SecurityReview")
    return b


@pytest.fixture()
def rules_dir(tmp_path: Path) -> Path:
    (tmp_path / "Req2Arch.agenthot").write_text(REQ2ARCH)
    (tmp_path / "Arch2Sec.agenthot").write_text(ARCH2SEC)
    (tmp_path / "helpers.py").write_text(HELPERS)
    return tmp_path


def test_security_reviewer_hot_retroactive_obligations(rules_dir: Path):
    req_mm = _build_req_mm()
    arch_mm = _build_arch_mm()

    req_root = req_mm.get("ReqModel")()
    for sid in ["S1", "S2", "S3"]:
        story = req_mm.new("UserStory", id=sid, status="accepted")
        story.criteria.append(req_mm.new("Criterion", text=f"criterion for {sid}"))
        req_root.stories.append(story)

    arch_root = arch_mm.get("ArchModel")()

    team = Team()
    team.add_agent("Analyst", "Req")
    team.add_view(req_mm, req_root)
    team.add_agent("Architect", "Arch")
    team.add_view(arch_mm, arch_root)
    team.add_handoff("Req2Arch", rules_dir / "Req2Arch.agenthot", target_mm="Arch")

    llm = MockBackend()
    runtime = TeamRuntime(team, llm)
    report1 = runtime.run_to_fixpoint()

    assert len(arch_root.operations) == 3
    assert not report1.escalations
    # No Security Reviewer yet: adding one later must need no hand-written glue.
    assert "Sec" not in team.views

    # --- runtime team evolution: HOT adds a Security Reviewer -------------
    sec_mm = _build_sec_mm(arch_mm)
    sec_root = sec_mm.get("SecModel")()
    apply_hot(
        team,
        TeamChange(
            agent_name="SecurityReviewer",
            view=sec_mm,
            view_root=sec_root,
            handoff_name="Arch2Sec",
            rule_path=rules_dir / "Arch2Sec.agenthot",
        ),
    )

    assert team.owner_of("Sec").name == "SecurityReviewer"

    report2 = runtime.run_to_fixpoint()

    # Retroactive obligations: all 3 pre-existing operations get a review,
    # with no hand-written glue beyond registering the hand-off (P3).
    assert len(sec_root.reviews) == 3
    reviewed_ops = {r.operation for r in sec_root.reviews}
    assert reviewed_ops == set(arch_root.operations)
    assert any(o.handoff == "Arch2Sec" for o in report2.obligations)
    assert not report2.escalations
    assert runtime.acceptance_holds()
