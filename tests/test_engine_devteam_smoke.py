"""End-to-end smoke test for the engine core: parses the paper's own
Req2Arch listing (Sec III-B running example), runs it over a small seed
Req model with the MockBackend, and checks Algorithm 1's guarantees:
every accepted UserStory gets exactly one Operation, guarded-out stories
get none, and `component <- s.epic` resolves through the trace to the
Component generated from that Epic (Proposition 1).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agentm2m.engine.executor import acceptance_holds, run_handoff
from agentm2m.engine.helpers_loader import load_helpers
from agentm2m.engine.trace import TraceModel
from agentm2m.llm.mock_backend import MockBackend
from agentm2m.metamodel.builder import MetamodelBuilder
from agentm2m.rules.parser import parse_module_file

RULE_TEXT = """
module Req2Arch;
create OUT : Arch from IN : Req;
uses 'helpers.py';

rule Epic2Component {
 from e : Req!Epic
 to c : Arch!Component ( name <- e.name )
}

rule Story2Operation {
 from s : Req!UserStory
          (s.status = #accepted)
 to  op : Arch!Operation (
   name      <- s.id.toOpName(),
   component <- s.epic,
   signature <- @llm('Derive an API signature',
                     s.criteria),
   @check signature.parses()
          and signature.params->notEmpty() )
}
"""

HELPERS_TEXT = """
import re

def toOpName(story_id):
    return "op_" + re.sub(r"[^A-Za-z0-9]+", "_", story_id).strip("_").lower()

def parses(signature):
    return bool(re.match(r"^[A-Za-z_][A-Za-z0-9_]*\\([^)]*\\)\\s*->\\s*\\S+$", signature or ""))

def params(signature):
    m = re.match(r"^[A-Za-z_][A-Za-z0-9_]*\\(([^)]*)\\)", signature or "")
    if not m or not m.group(1).strip():
        return []
    return [p.strip() for p in m.group(1).split(",")]
"""


def _build_req_mm() -> MetamodelBuilder:
    b = MetamodelBuilder("Req", "http://agentm2m/req")
    epic = b.eclass("Epic")
    b.attribute(epic, "name")
    criterion = b.eclass("Criterion")
    b.attribute(criterion, "text")
    story = b.eclass("UserStory")
    b.attribute(story, "id")
    b.attribute(story, "status")
    b.reference(story, "epic", "Epic", many=False, containment=False)
    b.reference(story, "criteria", "Criterion", many=True, containment=True)
    root = b.eclass("ReqModel")
    b.add_root_slot(root, "epics", "Epic")
    b.add_root_slot(root, "stories", "UserStory")
    return b


def _build_arch_mm() -> MetamodelBuilder:
    b = MetamodelBuilder("Arch", "http://agentm2m/arch")
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


@pytest.fixture()
def rule_dir(tmp_path: Path) -> Path:
    (tmp_path / "Req2Arch.agentm2m").write_text(RULE_TEXT)
    (tmp_path / "helpers.py").write_text(HELPERS_TEXT)
    return tmp_path


def test_story2operation_end_to_end(rule_dir: Path):
    req_mm = _build_req_mm()
    arch_mm = _build_arch_mm()

    req_root = req_mm.get("ReqModel")()
    epic = req_mm.new("Epic", name="Payments")
    req_root.epics.append(epic)

    s1 = req_mm.new("UserStory", id="S1", status="accepted", epic=epic)
    s1.criteria.append(req_mm.new("Criterion", text="must return 200 on success"))
    s2 = req_mm.new("UserStory", id="S2", status="draft", epic=epic)
    req_root.stories.append(s1)
    req_root.stories.append(s2)

    arch_root = arch_mm.get("ArchModel")()

    module = parse_module_file(rule_dir / "Req2Arch.agentm2m")
    trace = TraceModel(handoff="Req2Arch")
    llm = MockBackend(script=["handleThing(id: string) -> Result"])

    report = run_handoff(
        module,
        source_roots={"IN": req_root},
        target_root=arch_root,
        target_mm=arch_mm,
        trace=trace,
        llm=llm,
        base_dir=rule_dir,
    )

    # Prop. 1: exactly one Operation per accepted story, none for the draft one.
    assert len(arch_root.operations) == 1
    assert len(arch_root.components) == 1
    op = arch_root.operations[0]
    assert op.name == "op_s1"
    # `component <- s.epic` resolved through the trace to the generated Component.
    assert op.component is arch_root.components[0]
    assert op.signature == "handleThing(id: string) -> Result"
    assert not report.escalations

    helpers = load_helpers(module.uses, rule_dir)
    assert acceptance_holds(module, {"IN": req_root}, trace, report, helpers)

    # Re-running with an unchanged source model must not re-invoke the LLM
    # (stamp matches -> skip), proven here by the mock's script being
    # exhausted: a second call would just replay the last scripted value,
    # but resampled should be empty since the footprint digest is unchanged.
    report2 = run_handoff(
        module,
        source_roots={"IN": req_root},
        target_root=arch_root,
        target_mm=arch_mm,
        trace=trace,
        llm=llm,
        base_dir=rule_dir,
    )
    assert report2.resampled == []
    assert report2.created == []


def test_change_propagation_triggers_resample(rule_dir: Path):
    req_mm = _build_req_mm()
    arch_mm = _build_arch_mm()
    req_root = req_mm.get("ReqModel")()
    epic = req_mm.new("Epic", name="Payments")
    req_root.epics.append(epic)
    s1 = req_mm.new("UserStory", id="S1", status="accepted", epic=epic)
    crit = req_mm.new("Criterion", text="must return 200")
    s1.criteria.append(crit)
    req_root.stories.append(s1)
    arch_root = arch_mm.get("ArchModel")()

    module = parse_module_file(rule_dir / "Req2Arch.agentm2m")
    trace = TraceModel(handoff="Req2Arch")
    llm = MockBackend(script=["v1(a: string) -> Result", "v2(a: string, b: string) -> Result"])

    run_handoff(module, {"IN": req_root}, arch_root, arch_mm, trace, llm, base_dir=rule_dir)
    op = arch_root.operations[0]
    assert op.signature == "v1(a: string) -> Result"

    # Analyst tightens the criterion (Sec III-C "Change scenario" runbox).
    crit.text = "must return 200 AND include a request id header"
    report = run_handoff(module, {"IN": req_root}, arch_root, arch_mm, trace, llm, base_dir=rule_dir)

    assert any(b == "signature" for _, b in report.resampled)
    assert op.signature == "v2(a: string, b: string) -> Result"
