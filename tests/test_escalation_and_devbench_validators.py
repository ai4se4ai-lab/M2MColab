"""Regression tests for two sources of wasted LLM calls found in the
DevBench pilot logs:

1. An escalated stochastic binding was re-sampled in full on every later
   pass even though its footprint had not changed (Alg. 1 line 11:
   "escalate, never loop" -- across passes, not only within one).
2. devbench_rules/helpers.py's failsOnStub rejected every "prose + fenced
   block" oracle, so Req2Test escalated 100% of the time.
"""
from __future__ import annotations

import pytest

# Tests the earlier DevBench pilot harness (not part of this tree).
pytest.importorskip("evaluation.harness.devbench_rules.helpers", reason="legacy DevBench pilot harness not present")

from pathlib import Path

import pytest

from agenthot.engine.executor import acceptance_holds, run_handoff
from agenthot.engine.helpers_loader import load_helpers
from agenthot.engine.trace import TraceModel
from agenthot.llm.mock_backend import MockBackend
from agenthot.rules.parser import parse_module_file
from evaluation.harness.devbench_rules import helpers as devbench_helpers
from evaluation.harness.token_meter import TokenMeter
from tests.test_engine_devteam_smoke import HELPERS_TEXT, RULE_TEXT, _build_arch_mm, _build_req_mm


@pytest.fixture()
def rule_dir(tmp_path: Path) -> Path:
    (tmp_path / "Req2Arch.agenthot").write_text(RULE_TEXT)
    (tmp_path / "helpers.py").write_text(HELPERS_TEXT)
    return tmp_path


def _seed():
    req_mm = _build_req_mm()
    arch_mm = _build_arch_mm()
    req_root = req_mm.get("ReqModel")()
    epic = req_mm.new("Epic", name="Payments")
    req_root.epics.append(epic)
    story = req_mm.new("UserStory", id="S1", status="accepted", epic=epic)
    story.criteria.append(req_mm.new("Criterion", text="must return 200 on success"))
    req_root.stories.append(story)
    return req_root, arch_mm.get("ArchModel")(), arch_mm, story


def _run(module, req_root, arch_root, arch_mm, trace, llm, rule_dir):
    return run_handoff(
        module, source_roots={"IN": req_root}, target_root=arch_root, target_mm=arch_mm,
        trace=trace, llm=llm, base_dir=rule_dir, max_resamples=2,
    )


def test_escalated_binding_not_resampled_on_unchanged_footprint(rule_dir: Path):
    req_root, arch_root, arch_mm, story = _seed()
    module = parse_module_file(rule_dir / "Req2Arch.agenthot")
    helpers = load_helpers(module.uses, rule_dir)
    trace = TraceModel(handoff="Req2Arch")
    llm = TokenMeter(MockBackend(script=["not a signature"]))  # always rejected by @check

    first = _run(module, req_root, arch_root, arch_mm, trace, llm, rule_dir)
    assert llm.calls == 2 and len(first.escalations) == 1

    # Second pass, same footprint: no LLM call, but the escalation is still
    # reported, so phi (acceptance) must still be false.
    second = _run(module, req_root, arch_root, arch_mm, trace, llm, rule_dir)
    assert llm.calls == 2
    assert len(second.escalations) == 1
    assert not acceptance_holds(module, {"IN": req_root}, trace, second, helpers)

    # Editing the footprint earns a fresh resample budget.
    story.criteria[0].text = "must return 201 on success"
    third = _run(module, req_root, arch_root, arch_mm, trace, llm, rule_dir)
    assert llm.calls == 4
    assert len(third.escalations) == 1


def test_accepted_after_retry_clears_failed_stamp(rule_dir: Path):
    req_root, arch_root, arch_mm, story = _seed()
    module = parse_module_file(rule_dir / "Req2Arch.agenthot")
    trace = TraceModel(handoff="Req2Arch")
    llm = MockBackend(script=["bad", "bad", "handleThing(id: string) -> Result"])

    _run(module, req_root, arch_root, arch_mm, trace, llm, rule_dir)
    story.criteria[0].text = "changed"
    report = _run(module, req_root, arch_root, arch_mm, trace, llm, rule_dir)
    assert not report.escalations
    link = trace.links()[-1]
    assert "signature" not in link.failed_stamps
    assert arch_root.operations[0].signature == "handleThing(id: string) -> Result"


PROSE_AND_FENCE = (
    "Here is the oracle:\n```python\ndef test_oracle():\n"
    "    assert implementation('abc') == 'cba-9f2'\n```\nThis checks the suffix."
)


@pytest.mark.parametrize(
    "src, expected",
    [
        (PROSE_AND_FENCE, True),
        ("def test_oracle():\n    assert implementation(1) == 2\n", True),
        # defines its own implementation -> tests itself, vacuous
        ("def implementation(x):\n    return x\n\ndef test_oracle():\n    assert implementation(1) == 1\n", False),
        # never calls implementation() -> not a check of the operation
        ("def test_oracle():\n    assert canary_checksum_1('abc') == 'cba-9f2'\n", False),
        # passes against the stub -> vacuous
        ("def test_oracle():\n    f = implementation\n    assert True\n", False),
        ("not python at all", False),
    ],
)
def test_fails_on_stub(src: str, expected: bool):
    assert bool(devbench_helpers.failsOnStub(src)) is expected


def test_compiles_and_parses_accept_prose_wrapped_code():
    assert devbench_helpers.compiles("Sure:\n```python\ndef f():\n    return 1\n```\nDone.")
    assert devbench_helpers.parses("Signature:\n```\nfoo(a, b) -> int\n```")


class _RecordingBackend(MockBackend):
    def __init__(self, script):
        super().__init__(script=script)
        self.prompts: list[str] = []

    def generate(self, prompt, *, temperature=0.2):
        self.prompts.append(prompt)
        return super().generate(prompt, temperature=temperature)


def test_resample_prompt_carries_rejection_feedback(rule_dir: Path):
    req_root, arch_root, arch_mm, _story = _seed()
    module = parse_module_file(rule_dir / "Req2Arch.agenthot")
    trace = TraceModel(handoff="Req2Arch")
    llm = _RecordingBackend(["The op takes an id.", "handleThing(id: string) -> Result"])

    report = _run(module, req_root, arch_root, arch_mm, trace, llm, rule_dir)
    assert not report.escalations
    first, second = llm.prompts
    assert "previous answer was rejected" not in first
    assert "previous answer was rejected" in second and "The op takes an id." in second
    # Feedback extends the footprint-bounded prompt; it never replaces it.
    assert second.startswith(first)


def test_parses_accepts_class_qualified_signature():
    assert devbench_helpers.parses("GeoText.__init__(self, text: str, country: str) -> None")
    assert not devbench_helpers.parses("GeoText takes a text and a country")


def test_defines_name_rejects_misnamed_body():
    fenced = "```python\ndef process_text(self, text):\n    self.text = text\n```"
    assert not devbench_helpers.definesName(fenced, "__init__")
    assert devbench_helpers.definesName("import os\n\ndef __init__(self, text):\n    pass\n", "__init__")


def test_validators_explain_rejections():
    verdict = devbench_helpers.failsOnStub("def test_oracle():\n    r = Result(1)\n    assert r.x == 1\n")
    assert not verdict and "implementation(...)" in verdict.reason
    assert "def __init__" in devbench_helpers.definesName("def process_text(self):\n    pass\n", "__init__").reason


def test_resample_prompt_carries_validator_reason(tmp_path: Path):
    helpers_text = HELPERS_TEXT + (
        "\nfrom agenthot.engine.validators import Rejected\n"
        "def strict(sig):\n"
        "    return True if parses(sig) else Rejected('use name(args) -> Type')\n"
    )
    (tmp_path / "helpers.py").write_text(helpers_text)
    (tmp_path / "Req2Arch.agenthot").write_text(
        RULE_TEXT.replace("@check signature.parses()\n          and signature.params->notEmpty()",
                          "@check signature.strict() and signature.params->notEmpty()")
    )
    req_root, arch_root, arch_mm, _story = _seed()
    module = parse_module_file(tmp_path / "Req2Arch.agenthot")
    llm = _RecordingBackend(["nope", "handleThing(id: string) -> Result"])
    report = _run(module, req_root, arch_root, arch_mm, TraceModel(handoff="Req2Arch"), llm, tmp_path)
    assert not report.escalations
    assert "use name(args) -> Type" in llm.prompts[1]


def test_parses_accepts_inline_backticked_signature():
    assert devbench_helpers.parses("`__init__(self, seconds: int, wpm: int) -> None`")


def test_truncated_oracle_is_salvaged():
    truncated = (
        "def test_oracle():\n"
        "    assert implementation('agenthot') == 'CANARY_OK'\n"
        "    assert implementation('x') == 'CANARY_MISS'\n"
        "    assert implementation('agenthot is in the middle"
    )
    assert devbench_helpers.failsOnStub(truncated)


def test_assembled_module_tolerates_annotations_on_body_imports():
    from evaluation.harness.devbench_common import GeneratedSymbol, assemble_module

    body = "def get_ohlc(df: DataFrame) -> DataFrame:\n    from pandas import DataFrame\n    return df\n"
    src = assemble_module([GeneratedSymbol(component="Renko", name="get_ohlc", body=body)])
    exec(compile(src, "m", "exec"), {})


def test_loads_rejects_body_with_broken_import():
    bad = "from typing import Context\n\ndef generate_license(template, context: Context) -> None:\n    pass\n"
    verdict = devbench_helpers.loads(bad)
    assert not verdict and "ImportError" in verdict.reason
    assert devbench_helpers.loads("import os\n\ndef get_suffix(name: str) -> str:\n    return os.path.splitext(name)[1]\n")
