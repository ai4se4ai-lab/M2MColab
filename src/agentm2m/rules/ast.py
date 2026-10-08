"""AST for parsed hand-off rule files.

Structural constructs (module/rule/pattern/binding shape) are turned into
these dataclasses. Expression subtrees (guards, structural binding RHS,
@llm prompt/footprint, @check) are intentionally left as raw `lark.Tree` /
`lark.Token` objects -- they are evaluated later, per-match, by
`agentm2m.engine.expr.eval_expr`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Union

from lark import Token, Tree

Expr = Union[Tree, Token]


@dataclass
class SourcePattern:
    var: str
    model_alias: str
    type_name: str


@dataclass
class FromClause:
    patterns: list[SourcePattern]
    guard: Expr | None


@dataclass
class StructuralBinding:
    name: str
    expr: Expr


@dataclass
class StochasticBinding:
    name: str
    prompt_expr: Expr
    footprint_expr: Expr
    check_expr: Expr | None


Binding = Union[StructuralBinding, StochasticBinding]


@dataclass
class TargetPattern:
    var: str
    model_alias: str
    type_name: str
    bindings: list[Binding] = field(default_factory=list)


@dataclass
class ToClause:
    patterns: list[TargetPattern]


@dataclass
class Rule:
    name: str
    from_clause: FromClause
    to_clause: ToClause


@dataclass
class FromModel:
    alias: str
    mm_name: str


@dataclass
class Module:
    name: str
    target_alias: str
    target_mm: str
    sources: list[FromModel]
    uses: str | None
    rules: list[Rule]
