"""Match-set computation: Mt = {(r,m) | m |= p_r and g_r(m)} (Algorithm 1, line 1).

Sources are read-only, so the match set is fixed once, before any rule
fires (Proposition 1's premise) -- this module only ever reads
`source_models`, never mutates them.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from typing import Any

from ..rules.ast import Rule
from .expr import Helpers, eval_expr
from .trace import element_key


@dataclass
class Match:
    rule_name: str
    bindings: dict[str, Any]  # source pattern var -> source EObject
    match_key: str


def iter_all(roots: list[Any]):
    seen: set[int] = set()
    for root in roots:
        if id(root) not in seen:
            seen.add(id(root))
            yield root
        for el in root.eAllContents():
            if id(el) not in seen:
                seen.add(id(el))
                yield el


def instances_of(roots: list[Any], type_name: str) -> list[Any]:
    return [el for el in iter_all(roots) if el.eClass.name == type_name]


def compute_matches(rule: Rule, source_roots: dict[str, Any], helpers: Helpers) -> list[Match]:
    """`source_roots`: model alias -> single root element of that source model."""
    patterns = rule.from_clause.patterns
    candidates_per_var = [
        (p.var, instances_of([source_roots[p.model_alias]], p.type_name)) for p in patterns
    ]

    matches: list[Match] = []
    for combo in product(*[c for _, c in candidates_per_var]):
        bindings = {var: el for (var, _), el in zip(candidates_per_var, combo)}
        if rule.from_clause.guard is not None:
            if not eval_expr(rule.from_clause.guard, bindings, helpers):
                continue
        key = "|".join(f"{var}={element_key(el)}" for var, el in sorted(bindings.items()))
        matches.append(Match(rule_name=rule.name, bindings=bindings, match_key=key))
    return matches
