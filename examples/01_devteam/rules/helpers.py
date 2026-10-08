"""Helpers for the DevTeam rule modules, loaded via each module's
`uses 'helpers.py';` declaration (see agentm2m.engine.helpers_loader).
Reuses the generic validator building blocks from agentm2m.engine.validators
where possible; adds the DevTeam-specific ones the paper's listing calls
(`toOpName`, `parses`, `params`).
"""
from __future__ import annotations

import re

from agentm2m.engine.validators import python_compiles, signature_parses, signature_params


def toOpName(story_id: str) -> str:
    return "op_" + re.sub(r"[^A-Za-z0-9]+", "_", story_id).strip("_").lower()


def parses(signature: str) -> bool:
    return signature_parses(signature)


def params(signature: str) -> list[str]:
    return signature_params(signature)


def compiles(body: str) -> bool:
    return python_compiles(body)


def failsOnStub(oracle_src: str) -> bool:
    """@check for Criterion2TestCase: the oracle must compile *and* actually
    fail against an unimplemented stub (Sec III-B: "its validator requires
    that the oracle compiles and fails on a stub"), i.e. it must call
    `implementation()` and assert something about the result rather than
    being a vacuous always-pass check.
    """
    if not python_compiles(oracle_src):
        return False

    def _stub(*_args, **_kwargs):
        raise NotImplementedError("stub")

    namespace = {"implementation": _stub}
    try:
        exec(oracle_src, namespace)  # noqa: S102 - sandboxed namespace, prototype-only
        test_fn = namespace.get("test_oracle")
        if not callable(test_fn):
            return False
        test_fn()
    except Exception:
        return True  # failed against the stub, as required
    return False  # did not fail -> not a meaningful oracle
