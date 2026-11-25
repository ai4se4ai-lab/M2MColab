"""Evaluator for the OCL-subset used in guards, structural bindings, @llm
prompt/footprint expressions, and @check validators.

This is intentionally a bounded subset of OCL (navigation, boolean
connectives, comparisons, and a handful of collection operations), not a
full OCL implementation -- see docs/ARCHITECTURE.md. Anything it can't
express natively (e.g. `s.id.toOpName()`, `signature.parses()`) is
resolved through a per-rule-module `helpers` dict populated from the
Python file named in the module's `uses "...";` declaration -- our
substitute for ATL's `helper context ... def: ...` blocks.
"""
from __future__ import annotations

from typing import Any, Callable

from lark import Token, Tree

from .validators import Rejected

Scope = dict[str, Any]
Helpers = dict[str, Callable[..., Any]]

_COMPARATORS: dict[str, Callable[[Any, Any], bool]] = {
    "EQ": lambda a, b: a == b,
    "NE": lambda a, b: a != b,
    "LE": lambda a, b: a <= b,
    "GE": lambda a, b: a >= b,
    "LT": lambda a, b: a < b,
    "GT": lambda a, b: a > b,
}


class OCLEvalError(RuntimeError):
    pass


def eval_expr(node: Tree | Token | Any, scope: Scope, helpers: Helpers | None = None) -> Any:
    helpers = helpers or {}
    if not isinstance(node, Tree):
        # Already-evaluated literal (e.g. a plain str slipped in) -- return as-is.
        return node

    data = node.data
    ch = node.children

    if data == "var":
        name = ch[0]
        if name not in scope:
            raise OCLEvalError(f"unbound variable '{name}'")
        return scope[name]

    if data == "attr_access":
        receiver = eval_expr(ch[0], scope, helpers)
        return _get_attr(receiver, ch[1], helpers)

    if data == "method_call":
        receiver = eval_expr(ch[0], scope, helpers)
        args = _eval_arglist(ch[2], scope, helpers)
        return _call_method(receiver, ch[1], args, helpers)

    if data == "arrow_call":
        receiver = eval_expr(ch[0], scope, helpers)
        return _eval_arrow(receiver, ch[1], ch[2], scope, helpers)

    if data == "compare":
        left = eval_expr(ch[0], scope, helpers)
        op_tok = ch[1].children[0]
        right = eval_expr(ch[2], scope, helpers)
        return _COMPARATORS[op_tok.type](left, right)

    # and/or keep booleans as before, except that a falsy validator result
    # carrying a reason (validators.Rejected) is passed through unchanged,
    # so `@check a() and b()` still tells the engine which part failed.
    if data == "or_op":
        left = eval_expr(ch[0], scope, helpers)
        if left:
            return True
        right = eval_expr(ch[1], scope, helpers)
        return True if right else (right if isinstance(right, Rejected) else False)

    if data == "and_op":
        left = eval_expr(ch[0], scope, helpers)
        if not left:
            return left if isinstance(left, Rejected) else False
        right = eval_expr(ch[1], scope, helpers)
        return right if isinstance(right, Rejected) else bool(right)

    if data == "not_op":
        return not eval_expr(ch[0], scope, helpers)

    if data == "string_lit":
        return ch[0]

    if data == "number_lit":
        s = str(ch[0])
        return float(s) if ("." in s or "e" in s.lower()) else int(s)

    if data == "true_lit":
        return True

    if data == "false_lit":
        return False

    if data == "symbol_lit":
        return ch[0]

    raise OCLEvalError(f"cannot evaluate expression node '{data}'")


# Rule expressions may come from people other than the operator (a hosted
# service's tenants), so navigation stays on model features and helpers:
# private/dunder names would reach Python internals (`x.__class__...`), and
# str.format can read arbitrary attributes through its format string.
_FORBIDDEN = frozenset({"format", "format_map", "mro"})


def _guard_name(name: str) -> None:
    if name.startswith("_") or name in _FORBIDDEN:
        raise OCLEvalError(f"'{name}' is not accessible from rule expressions")


def _get_attr(receiver: Any, name: str, helpers: Helpers) -> Any:
    _guard_name(name)
    if isinstance(receiver, dict) and name in receiver:
        return receiver[name]
    if not isinstance(receiver, dict) and hasattr(receiver, name):
        return getattr(receiver, name)
    if name in helpers:
        # Pseudo-property resolved as a zero-arg helper, e.g. `signature.params`
        # calling a Python helper `params(signature)` (our stand-in for an
        # ATL `helper context String def: params(): Sequence(String)`).
        return helpers[name](receiver)
    raise OCLEvalError(f"no attribute/reference '{name}' on {receiver!r}")


def _call_method(receiver: Any, name: str, args: list[Any], helpers: Helpers) -> Any:
    _guard_name(name)
    if name in helpers:
        return helpers[name](receiver, *args)
    method = getattr(receiver, name, None)
    if callable(method):
        return method(*args)
    raise OCLEvalError(
        f"no method '{name}' on {receiver!r} and no helper registered "
        f"(declare `uses \"helpers.py\";` and define def {name}(x, ...))"
    )


def _eval_arglist(call_args_tree: Tree, scope: Scope, helpers: Helpers) -> list[Any]:
    arglist = call_args_tree.children[0]
    if arglist is None:
        return []
    return [eval_expr(e, scope, helpers) for e in arglist.children]


def _to_list(receiver: Any) -> list[Any]:
    if receiver is None:
        return []
    if isinstance(receiver, (list, tuple, set)):
        return list(receiver)
    try:
        return list(receiver)
    except TypeError:
        return [receiver]


def _eval_arrow(receiver: Any, name: str, arrow_args_tree: Tree, scope: Scope, helpers: Helpers) -> Any:
    items = _to_list(receiver)
    arg_node = arrow_args_tree.children[0]

    def lambda_fn(var: str, body: Tree) -> Callable[[Any], Any]:
        return lambda e: eval_expr(body, {**scope, var: e}, helpers)

    if name in {"notEmpty"}:
        return len(items) > 0
    if name in {"isEmpty"}:
        return len(items) == 0
    if name in {"size"}:
        return len(items)
    if name in {"first"}:
        return items[0] if items else None
    if name in {"includes"}:
        args = [eval_expr(e, scope, helpers) for e in (arg_node.children if arg_node else [])]
        return args[0] in items if args else False
    if name in {"excludes"}:
        args = [eval_expr(e, scope, helpers) for e in (arg_node.children if arg_node else [])]
        return args[0] not in items if args else True
    if name in {"select", "reject", "collect", "forAll", "exists"}:
        if arg_node is None or arg_node.data != "arrow_lambda":
            raise OCLEvalError(f"->{name}(...) requires a lambda: e.g. ->{name}(x | expr)")
        var, body = arg_node.children
        fn = lambda_fn(var, body)
        if name == "select":
            return [e for e in items if fn(e)]
        if name == "reject":
            return [e for e in items if not fn(e)]
        if name == "collect":
            return [fn(e) for e in items]
        if name == "forAll":
            return all(fn(e) for e in items)
        if name == "exists":
            return any(fn(e) for e in items)

    raise OCLEvalError(f"unsupported collection operation '->{name}'")
