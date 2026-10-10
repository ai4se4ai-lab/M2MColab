"""OCL-callable helpers for the DevTeam rule modules (`uses 'helpers.py';`).

Validators return a `Rejected(reason)` instead of a bare False so the reason
is fed back to whoever produces the next attempt. Validators that execute
generated code run it through agenthot.sandbox (resource limits, a
timeout, and real isolation when AGENTHOT_SANDBOX=bwrap|command).
"""
from __future__ import annotations

import re

from agenthot.sandbox import run_python
from agenthot.engine.validators import (
    Rejected,
    python_compiles,
    signature_params,
    signature_parses,
)

_FENCE = re.compile(r"^```[a-zA-Z0-9_-]*\n(.*?)\n```\s*$", re.DOTALL)


def _unfence(text: str) -> str:
    text = (text or "").strip()
    m = _FENCE.match(text)
    return m.group(1) if m else text


def toOpName(story_id: str) -> str:
    return "op_" + re.sub(r"[^A-Za-z0-9]+", "_", story_id).strip("_").lower()


def criteriaText(criteria) -> str:
    """Structural: a story's acceptance criteria as text, carried forward onto
    its Operation (no LLM involved)."""
    return "\n".join(f"{c.id}: {c.text}" for c in criteria)


def implFootprint(op) -> list:
    """Footprint of CodeEdit.body: the signature and the criteria it must meet."""
    return [op.signature, op.criteria]


def parses(signature: str):
    if signature_parses((signature or "").strip()):
        return True
    return Rejected("expected exactly one line: name(param: Type, ...) -> ReturnType")


def params(signature: str) -> list[str]:
    return signature_params((signature or "").strip())


def compiles(body: str):
    if python_compiles(_unfence(body)):
        return True
    return Rejected("not valid Python")


def failsOnStub(oracle_src: str, timeout: float = 10.0):
    """The oracle must compile AND fail against an unimplemented stub, i.e. it
    really calls implementation() and asserts something (Sec. III-B)."""
    src = _unfence(oracle_src)
    if not python_compiles(src):
        return Rejected("not valid Python")
    harness = (
        "def implementation(*args, **kwargs):\n"
        "    raise NotImplementedError('stub')\n\n"
        f"{src}\n\n"
        "import sys\n"
        "if 'test_oracle' not in globals() or not callable(test_oracle):\n"
        "    sys.exit(3)\n"
        "try:\n"
        "    test_oracle()\n"
        "except Exception:\n"
        "    sys.exit(0)\n"
        "sys.exit(4)\n"
    )
    res = run_python(harness, timeout=timeout)  # sandboxed: see agenthot.sandbox
    if res.timeout:
        return Rejected(f"oracle did not finish within {timeout:.0f}s")
    rc = res.returncode
    if rc == 0:
        return True
    if rc == 3:
        return Rejected("define a function named test_oracle()")
    if rc == 4:
        return Rejected("test_oracle() passed against an unimplemented stub; it must call implementation() and assert on the result")
    return Rejected(f"oracle crashed before running (exit code {rc})")


def parsesRisk(raw: str):
    if (raw or "").strip().lower() in {"low", "medium", "high"}:
        return True
    return Rejected("answer with exactly one word: low, medium, or high")
