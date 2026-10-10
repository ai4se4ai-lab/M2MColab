"""Reusable @check validator building blocks (Definition 1: chk_b is a
type/OCL constraint, a parser, or an executable oracle).

A `@check expr` is just an OCL-subset expression evaluated by
`engine.expr.eval_expr` with the rule module's helpers in scope, so most
validators are plain Python predicates registered as helpers. This module
provides the handful that are generic enough to reuse across examples;
example-specific `helpers.py` files import from here and add their own.
"""
from __future__ import annotations

import ast
import re


class Rejected:
    """Falsy result a validator can return instead of `False` to say *why*
    a sample was rejected. The engine puts the reason into the resample
    prompt, so the next sample can fix the actual problem instead of
    repeating the same answer (a bare `False` gives the model nothing to
    act on). Truthiness is identical to `False` everywhere else."""

    __slots__ = ("reason",)

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return f"Rejected({self.reason!r})"


def python_compiles(source: str) -> bool:
    """A parser-based validator: does `source` parse as valid Python?"""
    try:
        ast.parse(source)
        return True
    except SyntaxError:
        return False


def matches_regex(text: str, pattern: str) -> bool:
    return re.search(pattern, text or "") is not None


def signature_parses(signature: str) -> bool:
    """`name(param: Type, ...) -> ReturnType` shape used by the DevTeam example."""
    return matches_regex(signature, r"^[A-Za-z_][A-Za-z0-9_]*\([^)]*\)\s*->\s*\S+$")


def signature_params(signature: str) -> list[str]:
    m = re.match(r"^[A-Za-z_][A-Za-z0-9_]*\(([^)]*)\)", signature or "")
    if not m or not m.group(1).strip():
        return []
    return [p.strip() for p in m.group(1).split(",")]


def run_pytest_oracle(oracle_body: str, code_body: str, *, timeout: float = 15.0) -> bool:
    """Executable-oracle validator: write oracle+code to a temp module and run it.

    Used by examples whose @check needs to actually execute the generated
    test against a stub/implementation rather than just parse it.
    """
    from ..sandbox import run_python  # the one place untrusted code runs

    return run_python(code_body + "\n\n" + oracle_body, timeout=timeout).ok
