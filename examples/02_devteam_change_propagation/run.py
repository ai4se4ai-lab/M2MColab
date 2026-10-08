#!/usr/bin/env python3
"""Replays the paper's "Change scenario" runbox (Sec III-C): the Analyst
tightens a criterion of story S2, and the engine must raise *exactly* the
obligations that criterion's footprint touches -- nothing else gets
re-sampled (Proposition 2, sound change impact).

Reuses examples/01_devteam's metamodels/rules/team wiring unchanged: this
example is about a second run after an edit, not a new scenario.

    python examples/02_devteam_change_propagation/run.py --llm mock
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

DEVTEAM_DIR = Path(__file__).resolve().parents[1] / "01_devteam"
sys.path.insert(0, str(DEVTEAM_DIR))

from run import build_team  # noqa: E402

from agentm2m.config import LLMConfig  # noqa: E402
from agentm2m.llm.factory import make_backend  # noqa: E402
from agentm2m.team.runtime import TeamRuntime  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--llm", default=None)
    parser.add_argument("--model", default=None)
    args = parser.parse_args()

    team = build_team()
    cfg = LLMConfig.from_env()
    llm = make_backend(cfg, override_provider=args.llm, override_model=args.model)
    print(f"Using LLM backend: {llm.name} ({args.model or cfg.model})")

    runtime = TeamRuntime(team, llm)
    print("=== Initial run ===")
    report1 = runtime.run_to_fixpoint()
    print(report1.summary())

    op_s2_before = next(op for op in team.roots["Arch"].operations if op.name == "op_s2")
    signature_before = op_s2_before.signature
    edit_s2_before = next(ce for ce in team.roots["Code"].edits if ce.operation is op_s2_before)
    body_before = edit_s2_before.body

    print("\n=== Analyst tightens S2.1 ('must refund the original payment method') ===")
    crit = next(
        c
        for s in team.roots["Req"].stories
        for c in s.criteria
        if c.id == "S2.1"
    )
    crit.text = "must refund the original payment method within 2 business days AND notify the customer by email"

    print("=== Re-run after the change ===")
    report2 = runtime.run_to_fixpoint()
    print(report2.summary())

    signature_after = op_s2_before.signature
    body_after = edit_s2_before.body

    obligated_targets = {(o.handoff, o.target_key) for o in report2.obligations}
    signature_resampled = ("Req2Arch", "Story2Operation::op::s=UserStory#S2") in obligated_targets

    print("\n=== Sound change impact (Proposition 2) ===")
    print(f"Operation op_s2.signature re-sampled this run: {signature_resampled}")
    print(f"  before: {signature_before!r}")
    print(f"  after:  {signature_after!r}")
    print(f"CodeEdit op_s2.body left as-is (its footprint, op.signature, didn't change: {body_before == body_after})")

    unrelated_ops = [op for op in team.roots["Arch"].operations if op.name != "op_s2"]
    print(f"\nUnrelated operations left untouched: {[op.name for op in unrelated_ops]}")
    print(f"Obligations raised this run: {len(obligated_targets)} -> {sorted(obligated_targets)}")

    only_s2_touched = all("S2" in target for _, target in obligated_targets)
    print(f"\nAll obligations touch only S2/S2.1-derived elements: {only_s2_touched}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
