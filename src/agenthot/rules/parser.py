from __future__ import annotations

from pathlib import Path

from lark import Lark, Transformer

from . import ast as A

_GRAMMAR_PATH = Path(__file__).parent / "grammar.lark"
_parser = Lark(_GRAMMAR_PATH.read_text(), parser="earley", start="start")


class _BareCheck:
    """Marker for a comma-separated `@check expr` binding-list entry."""

    __slots__ = ("expr",)

    def __init__(self, expr):
        self.expr = expr


class _ToAST(Transformer):
    # -- literals / small pieces -------------------------------------
    def NAME(self, tok):
        return str(tok)

    def STRING(self, tok):
        return str(tok)[1:-1]

    # -- module header --------------------------------------------------
    def module_decl(self, children):
        (name,) = children
        return ("module", name)

    def from_model(self, children):
        alias, mm = children
        return A.FromModel(alias=alias, mm_name=mm)

    def from_model_list(self, children):
        return list(children)

    def create_decl(self, children):
        target_alias, target_mm, sources = children
        return ("create", target_alias, target_mm, sources)

    def uses_decl(self, children):
        (path,) = children
        return ("uses", path)

    # -- from clause ------------------------------------------------
    def source_pattern(self, children):
        var, model_alias, type_name = children
        return A.SourcePattern(var=var, model_alias=model_alias, type_name=type_name)

    def source_pattern_list(self, children):
        return list(children)

    def guard(self, children):
        (expr,) = children
        return expr

    def from_clause(self, children):
        patterns = children[0]
        guard = children[1] if len(children) > 1 else None
        return A.FromClause(patterns=patterns, guard=guard)

    # -- to clause ----------------------------------------------------
    def structural_binding(self, children):
        name, expr = children
        return A.StructuralBinding(name=name, expr=expr)

    def check_clause(self, children):
        (expr,) = children
        return _BareCheck(expr)

    def stochastic_binding(self, children):
        name, prompt_expr, footprint_expr = children
        return A.StochasticBinding(
            name=name, prompt_expr=prompt_expr, footprint_expr=footprint_expr, check_expr=None
        )

    def binding_list(self, children):
        # A bare `@check expr` entry (comma-separated, per the paper's
        # listing) attaches to the immediately preceding stochastic binding.
        bindings: list[A.Binding] = []
        for item in children:
            if isinstance(item, _BareCheck):
                if not bindings or not isinstance(bindings[-1], A.StochasticBinding):
                    raise ValueError("@check must follow a stochastic (@llm) binding")
                bindings[-1].check_expr = item.expr
            else:
                bindings.append(item)
        return bindings

    def target_pattern(self, children):
        var, model_alias, type_name = children[0], children[1], children[2]
        bindings = children[3] if len(children) > 3 and children[3] is not None else []
        return A.TargetPattern(var=var, model_alias=model_alias, type_name=type_name, bindings=bindings)

    def target_pattern_list(self, children):
        return list(children)

    def to_clause(self, children):
        (patterns,) = children
        return A.ToClause(patterns=patterns)

    # -- rule / start -------------------------------------------------
    def rule_decl(self, children):
        name, from_clause, to_clause = children
        return A.Rule(name=name, from_clause=from_clause, to_clause=to_clause)

    def start(self, children):
        module_name = None
        target_alias = target_mm = None
        sources: list[A.FromModel] = []
        uses = None
        rules: list[A.Rule] = []
        for child in children:
            if isinstance(child, tuple) and child[0] == "module":
                module_name = child[1]
            elif isinstance(child, tuple) and child[0] == "create":
                _, target_alias, target_mm, sources = child
            elif isinstance(child, tuple) and child[0] == "uses":
                uses = child[1]
            elif isinstance(child, A.Rule):
                rules.append(child)
        return A.Module(
            name=module_name,
            target_alias=target_alias,
            target_mm=target_mm,
            sources=sources,
            uses=uses,
            rules=rules,
        )


def parse_module(text: str) -> A.Module:
    tree = _parser.parse(text)
    return _ToAST().transform(tree)


def parse_module_file(path: str | Path) -> A.Module:
    return parse_module(Path(path).read_text())
