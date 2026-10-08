#!/usr/bin/env python3
"""Pedagogical (not statistically rigorous -- that is evaluation/'s job)
side-by-side comparison of three hand-off strategies on one deliberately
tricky toy task: an Analyst's vague user story handed to an Architect who
must produce an API operation signature, while a mandatory constraint
survives the hand-off intact and a reference to the story's Epic resolves
to a real object, not a guessed label.

Three self-contained paths, one task, one LLM backend:

  1. run_freetext(llm)     -- a Python function that serializes the story to
                              prose (dropping the constraint, on purpose --
                              P1's silent information loss) and asks the LLM
                              for free text back. No structure, no validation.
  2. run_shared_schema(llm) -- a single JSON-schema-validated shared `dict`
                              (PatchBoard-style; docs/DS-A2A.tex Sec IV/
                              Discussion, "Why not one shared schema?"),
                              hand-rolled here since `jsonschema` is not an
                              existing dependency of this project.
  3. run_agentm2m(llm)     -- the real engine (metamodels.py + rules/ +
                              Team/TeamRuntime): a structural binding copies
                              the constraint verbatim (never sent to the
                              LLM), and a trace-resolved reference links the
                              Operation to the real Component.

    python examples/06_baseline_comparison/run.py --llm mock
    python examples/06_baseline_comparison/run.py                 # local Ollama
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from metamodels import build_arch_mm, build_story_mm  # noqa: E402
from seed_models import build_seed_story_model  # noqa: E402

from agentm2m.config import LLMConfig  # noqa: E402
from agentm2m.llm.base import LLMBackend  # noqa: E402
from agentm2m.llm.factory import make_backend  # noqa: E402
from agentm2m.team.model import Team  # noqa: E402
from agentm2m.team.runtime import TeamRuntime  # noqa: E402

# The one underlying task, shared by all three paths.
STORY = {
    "id": "S1",
    "epic": "Payments",
    "description": "As a user, I want to cancel my subscription so that I stop being charged.",
    "constraint": "Cancellation must be blocked if there is a pending refund on the account.",
}


# ---------------------------------------------------------------------------
# 1. Free-text hand-off: no structure, no validation.
# ---------------------------------------------------------------------------

def _serialize_freetext(story: dict) -> str:
    """Deliberately lossy prose serialization: DROPS the `constraint` field
    entirely, the way a hurried free-text hand-off silently drops
    information a downstream agent never asked to see (P1)."""
    return f"User story {story['id']} (epic: {story['epic']}): {story['description']}"


def run_freetext(llm: LLMBackend) -> dict[str, Any]:
    prose = _serialize_freetext(STORY)
    prompt = f"Given this user story, produce an API operation name and signature.\n\n{prose}"
    raw = llm.generate(prompt, temperature=0.2)
    # No validation of any kind is applied to `raw` before it is "accepted" --
    # this is the point: a free-text hand-off has no acceptance gate at all.
    return {
        "raw_output": raw,
        "required_field_survived": "refund" in raw.lower(),  # was never even in the prompt
        "reference_resolved": False,  # no structured reference exists in free text
        "validator_enforced": False,  # nothing checks the shape of `raw`
        "tokens": llm.count_tokens(prompt),
    }


# ---------------------------------------------------------------------------
# 2. Shared-schema hand-off (PatchBoard-style): validates each write, but
#    does not relate views (docs/DS-A2A.tex, Sec IV/Discussion).
# ---------------------------------------------------------------------------

_SCHEMA = {
    "name": str,
    "sig": str,
    "component_ref": str,
    "constraint": str,
}


def _extract_json(raw_text: str) -> Any:
    raw_text = (raw_text or "").strip()
    try:
        return json.loads(raw_text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", raw_text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return None


def _validate_against_schema(payload: Any, schema: dict[str, type]) -> bool:
    """Hand-rolled minimal shape validator (jsonschema is not a dependency
    of this project -- see README.md). Checks presence, type, and
    non-emptiness of every required key -- exactly the kind of per-write
    check PatchBoard-style shared state performs."""
    if not isinstance(payload, dict):
        return False
    for key, typ in schema.items():
        if key not in payload or not isinstance(payload[key], typ) or not payload[key].strip():
            return False
    return True


def run_shared_schema(llm: LLMBackend) -> dict[str, Any]:
    board: dict[str, Any] = {}
    prompt = (
        "Given this user story JSON, respond with a JSON object with exactly these keys: "
        "name, sig, component_ref, constraint. "
        f"Story: {json.dumps(STORY)}"
    )
    raw = llm.generate(prompt, temperature=0.2)
    payload = _extract_json(raw)
    accepted = _validate_against_schema(payload, _SCHEMA)
    if accepted:
        board["operation"] = payload

    required_field_survived = accepted and STORY["constraint"].strip().lower() in str(
        payload.get("constraint", "")
    ).strip().lower()

    return {
        "raw_output": raw,
        "accepted": accepted,
        "required_field_survived": required_field_survived,
        # component_ref is just a free string the LLM wrote; nothing here
        # verifies it names a real, existing Component/Epic elsewhere.
        "reference_resolved": False,
        # The validator DOES run on every write, whether or not it passes --
        # that per-write shape check is exactly what a shared schema gives
        # you, and exactly what run_freetext lacks.
        "validator_enforced": True,
        "tokens": llm.count_tokens(prompt),
        "board": board,
    }


# ---------------------------------------------------------------------------
# 3. agentm2m hand-off: structural guarantees (trace link, resolved
#    reference, bounded footprint).
# ---------------------------------------------------------------------------

def run_agentm2m(llm: LLMBackend) -> dict[str, Any]:
    story_mm = build_story_mm()
    arch_mm = build_arch_mm()
    story_root = build_seed_story_model(story_mm)
    arch_root = arch_mm.get("ArchModel")()

    team = Team()
    team.add_agent("Analyst", "Story")
    team.add_view(story_mm, story_root)
    team.add_agent("Architect", "Arch")
    team.add_view(arch_mm, arch_root)
    team.add_handoff("Story2Arch", HERE / "rules" / "Story2Arch.agentm2m", target_mm="Arch")

    runtime = TeamRuntime(team, llm)
    report = runtime.run_to_fixpoint()

    story = story_root.stories[0]
    op = arch_root.operations[0]
    component = arch_root.components[0]

    # The exact prompt the engine sent -- footprint-bounded to s.description
    # only (see agentm2m.engine.binding._footprint_to_text): the constraint
    # is never part of it, because it was never in the footprint expression.
    footprint_prompt = (
        f"Derive an API operation signature for this user story\n\n"
        f"Context (footprint only):\n{story.description}"
    )

    return {
        "raw_output": op.signature,
        "required_field_survived": op.constraint == story.constraint,
        "reference_resolved": op.component is component,
        "validator_enforced": True,  # @check signature.parses() always gates acceptance
        "tokens": llm.count_tokens(footprint_prompt),
        "phi_holds": runtime.acceptance_holds(),
        "escalations": report.escalations,
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def print_table(results: dict[str, dict[str, Any]]) -> None:
    headers = ["Approach", "Required field survived?", "Reference resolved?", "Validator enforced?", "Tokens sent to LLM"]
    rows = [headers]
    for name, r in results.items():
        rows.append([
            name,
            str(r["required_field_survived"]),
            str(r["reference_resolved"]),
            str(r["validator_enforced"]),
            str(r["tokens"]),
        ])
    widths = [max(len(row[i]) for row in rows) for i in range(len(headers))]
    for i, row in enumerate(rows):
        print("  " + " | ".join(cell.ljust(widths[j]) for j, cell in enumerate(row)))
        if i == 0:
            print("  " + "-+-".join("-" * w for w in widths))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--llm", default=None, help="ollama|openai|anthropic|mock (default: $LLM_PROVIDER, else ollama)")
    parser.add_argument("--model", default=None, help="model tag override")
    args = parser.parse_args()

    cfg = LLMConfig.from_env()
    llm = make_backend(cfg, override_provider=args.llm, override_model=args.model)
    print(f"Using LLM backend: {llm.name} ({args.model or cfg.model})")
    print(f"\nTask: story {STORY['id']!r} -- {STORY['description']!r}")
    print(f"Required constraint: {STORY['constraint']!r}\n")

    print("=== 1. Free-text hand-off ===")
    ft = run_freetext(llm)
    print(f"  LLM raw output: {ft['raw_output']!r}")
    print(f"  required-field survived: {ft['required_field_survived']}")
    print(f"  validator enforced before acceptance: {ft['validator_enforced']}")

    print("\n=== 2. Shared-schema hand-off (PatchBoard-style) ===")
    ss = run_shared_schema(llm)
    print(f"  LLM raw output: {ss['raw_output']!r}")
    print(f"  schema accepted the write: {ss['accepted']}")
    print(f"  required-field survived: {ss['required_field_survived']}")
    print(f"  validator enforced before acceptance: {ss['validator_enforced']}")

    print("\n=== 3. agentm2m hand-off ===")
    am = run_agentm2m(llm)
    print(f"  Operation.signature: {am['raw_output']!r}")
    print(f"  required-field survived (structural copy, never sent to the LLM): {am['required_field_survived']}")
    print(f"  reference correctly resolved via trace: {am['reference_resolved']}")
    print(f"  validator enforced before acceptance: {am['validator_enforced']}")

    print("\n=== Comparison ===")
    print_table({"free-text": ft, "shared-schema": ss, "agentm2m": am})

    print(
        "\nP1 (lossy hand-offs) in action: only the agentm2m path guarantees the required "
        "constraint survives AND that the component reference is a real, resolved link -- "
        "see README.md for why Propositions 1-3 don't hold for the other two paths."
    )

    print(f"\nphi holds: {am['phi_holds']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
