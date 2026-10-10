"""Lift: text-to-model step for stochastic bindings that populate a whole
element from unstructured LLM output, rejected unless conformant to MM_j.

Convention: a stochastic binding named `self` (`self <- @llm(prompt, fp)`)
is a Lift binding -- its sampled text is JSON, parsed and validated against
the target element's declared EAttributes before being written, instead of
being assigned to one attribute like an ordinary stochastic binding. See
examples/05_incident_response_team for a worked example.
"""
from __future__ import annotations

import json
import re
from typing import Any

LIFT_BINDING_NAME = "self"


class LiftRejected(RuntimeError):
    pass


def lift_json_into_element(raw_text: str, target: Any) -> None:
    """Parse `raw_text` as JSON and set it onto `target`'s EAttributes.

    Rejects (raises LiftRejected, which the executor treats as a failed
    @check -> triggers resampling / escalation) unless every JSON key names
    a real EAttribute of the target's EClass and required (non-nullable
    with no default) attributes are all present.
    """
    payload = _extract_json(raw_text)
    if payload is None or not isinstance(payload, dict):
        raise LiftRejected(f"LLM output does not conform to {target.eClass.name}: not a JSON object")

    valid_attrs = {f.name for f in target.eClass.eAllStructuralFeatures()}
    unknown = set(payload) - valid_attrs
    if unknown:
        raise LiftRejected(f"unknown fields for {target.eClass.name}: {sorted(unknown)}")

    for key, value in payload.items():
        setattr(target, key, value)


def _extract_json(raw_text: str) -> Any:
    raw_text = raw_text.strip()
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
