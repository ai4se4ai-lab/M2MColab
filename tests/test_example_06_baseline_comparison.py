"""Regression test for examples/06_baseline_comparison: calls run_freetext,
run_shared_schema, and run_agentm2m directly with the deterministic
MockBackend (not `--llm mock` as a subprocess) and checks the exact P1
deficit the example illustrates -- see README.md.

Loads run.py in isolation, evicting the plain module names it caches
afterwards -- see tests/test_example_04_research_team.py's docstring.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

EXAMPLE_DIR = Path(__file__).resolve().parents[1] / "examples" / "06_baseline_comparison"


def _load_run_module():
    sys.path.insert(0, str(EXAMPLE_DIR))
    for name in ("run", "metamodels", "seed_models"):
        sys.modules.pop(name, None)
    try:
        spec = importlib.util.spec_from_file_location("run", EXAMPLE_DIR / "run.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules["run"] = module
        spec.loader.exec_module(module)
    finally:
        for name in ("run", "metamodels", "seed_models"):
            sys.modules.pop(name, None)
        while str(EXAMPLE_DIR) in sys.path:
            sys.path.remove(str(EXAMPLE_DIR))
    return module


from agentm2m.llm.mock_backend import MockBackend  # noqa: E402


def test_baseline_comparison_demonstrates_p1_deficit():
    run_mod = _load_run_module()
    llm = MockBackend()

    ft = run_mod.run_freetext(llm)
    assert ft["required_field_survived"] is False
    assert ft["reference_resolved"] is False
    assert ft["validator_enforced"] is False
    assert ft["tokens"] > 0

    ss = run_mod.run_shared_schema(llm)
    assert ss["validator_enforced"] is True
    assert ss["reference_resolved"] is False
    # The mock backend never produces well-shaped custom JSON for this
    # schema, so the write is (correctly) rejected -- see README.md.
    assert ss["accepted"] is False
    assert ss["required_field_survived"] is False

    am = run_mod.run_agentm2m(llm)
    assert am["required_field_survived"] is True
    assert am["reference_resolved"] is True
    assert am["validator_enforced"] is True
    assert am["phi_holds"] is True
    assert am["escalations"] == []

    # agentm2m's footprint-bounded prompt should never be larger than the
    # two baselines', which both serialize the whole task into the prompt.
    assert am["tokens"] < ft["tokens"]
    assert am["tokens"] < ss["tokens"]
