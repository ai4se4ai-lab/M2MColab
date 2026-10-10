"""OCL helpers for rule modules compiled from typed teams.

Loaded through the generated `uses 'helpers.py';` declaration. Prompts live
in the per-run registry (`REGISTRY`), so the rule text carries identifiers
only. `fpv` builds a stochastic binding's footprint: the values the LLM may
see (serialised as `object.feature = value` lines), plus the validator reads
vr_b(m), which enter the version stamp but never the prompt, plus the keys of
the objects owning every location read (the trace connections behind
cover(G)).
"""
from __future__ import annotations

import contextvars
from typing import Any

from autom2m import vlib as _vlib

from .engine.trace import element_key

# per-run registry: prompt key -> prompt text
REGISTRY: contextvars.ContextVar[dict] = contextvars.ContextVar("am2m_registry", default={})


class IllTypedBinding(RuntimeError):
    """Raised by the footprint of a binding the compiler could not type
    (only in unchecked teams): the engine escalates it without sampling."""


def _is_many(v: Any) -> bool:
    return not isinstance(v, (str, bytes)) and not hasattr(v, "eClass") and hasattr(v, "__iter__")


def nav(obj: Any, path: str) -> Any:
    """Navigate `a.b.c` from obj, flattening many-valued references."""
    cur: Any = obj
    segs = path.split(".")
    if any(not seg or seg.startswith("_") for seg in segs):
        raise ValueError(f"path {path!r} may only navigate model features")
    for seg in segs:
        if _is_many(cur):
            out = []
            for x in cur:
                v = getattr(x, seg, None)
                out.extend(list(v) if _is_many(v) else [v])
            cur = out
        else:
            cur = getattr(cur, seg, None) if cur is not None else None
    if _is_many(cur):
        return list(cur)
    return cur


def _render(v: Any) -> str:
    if v is None:
        return "(none)"
    if hasattr(v, "eClass"):
        attrs = {f.name: getattr(v, f.name) for f in v.eClass.eAllStructuralFeatures() if f.is_attribute}
        return "; ".join(f"{k}={val}" for k, val in attrs.items() if val not in (None, "")) or v.eClass.name
    return str(v)


def _lines(label: str, value: Any) -> list[str]:
    if _is_many(value):
        vals = list(value)
        if not vals:
            return [f"{label} = (none)"]
        return [f"{label}[{i}] = {_indent(_render(x))}" for i, x in enumerate(vals, 1)]
    return [f"{label} = {_indent(_render(value))}"]


def _indent(text: str) -> str:
    return text.replace("\n", "\n    ") if "\n" in text else text


def _keys(owner: Any) -> list[str]:
    out = []
    for o in (owner if _is_many(owner) else [owner]):
        if hasattr(o, "eClass"):
            try:
                out.append(element_key(o))
            except Exception:  # noqa: BLE001
                continue
    return out


class Footprint(list):
    """The visible footprint (a list of `object.feature = value` lines) with
    the stamp material #(fp u vr) and the read owners attached."""

    def __init__(self, visible: list[str], stamp: dict, owners: list[str]) -> None:
        super().__init__(visible)
        self.stamp_material = stamp
        self.owner_keys = owners

    def prompt_text(self) -> str:
        return "\n".join(self) or "(empty)"


def fpv(_receiver: Any, n_visible: int, *triples: Any) -> Footprint:
    """Footprint: (label, value, owner) triples; the first `n_visible` are
    shown to the LLM, the rest are validator reads (stamp only)."""
    visible, fp_vals, vr_vals, owners = [], [], [], []
    for i in range(0, len(triples), 3):
        label, value, owner = triples[i], triples[i + 1], triples[i + 2]
        rendered = [_render(x) for x in value] if _is_many(value) else _render(value)
        if i // 3 < n_visible:
            visible += _lines(str(label), value)
            fp_vals.append([label, rendered])
        else:
            vr_vals.append([label, rendered])
        for k in _keys(owner):
            if k not in owners:
                owners.append(k)
    return Footprint(visible, {"fp": fp_vals, "vr": vr_vals}, owners)


def fp(_receiver: Any, *labelled: Any) -> list[str]:
    """Older footprint form: alternating (label, value) arguments."""
    out = []
    for i in range(0, len(labelled), 2):
        out += _lines(str(labelled[i]), labelled[i + 1])
    return out


def illtyped(_receiver: Any, reason: str) -> Any:
    raise IllTypedBinding(reason)


def prompt_of(_receiver: Any, key: str) -> str:
    return REGISTRY.get().get(key, key)


def same(a: Any, b: Any) -> bool:
    return a is b


_CODE_VALIDATORS = {"defines", "examples_run", "passes_tests"}


def _recording(fn):
    """Wrap a validator: remember the latest rejected candidate per goal
    element, so a run that escalates can still submit its best effort and
    attribution can re-check the exact value that failed."""

    def wrapped(value: Any, target: Any = None, *args: Any) -> Any:
        verdict = fn(value, target, *args)
        ctx = _vlib.CURRENT.get()
        if ctx is not None and not verdict:
            vid = fn.__name__[2:]
            goal = None
            try:
                goal = _vlib.goal_method_of(target, next((a for a in args if hasattr(a, "eClass")), None))
            except Exception:  # noqa: BLE001
                goal = None
            name = getattr(goal, "name", goal) if goal is not None else None
            if name:
                ctx.last_rejected_any[f"{name}|{vid}"] = str(value)
                if vid in _CODE_VALIDATORS:
                    ctx.last_rejected[str(name)] = str(value)
            tk = getattr(target, "_amt_target_key", None)
            if tk:
                ctx.last_rejected_any[f"{tk}|{vid}"] = str(value)
        return verdict

    wrapped.__name__ = fn.__name__
    return wrapped


# validators (receiver = sampled value; first argument = the target object)
for _name, _fn in _vlib.IMPLEMENTATIONS.items():
    globals()[f"v_{_name}"] = _recording(_fn)
