"""OCL helpers for rule modules compiled from typed teams.

Loaded through the generated `uses 'helpers.py';` declaration. Prompts and
footprint labels live in the per-run registry (`REGISTRY`), so the rule text
carries identifiers only.
"""
from __future__ import annotations

import contextvars
from typing import Any

from . import vlib as _vlib

# per-run registry: prompt key -> prompt text
REGISTRY: contextvars.ContextVar[dict] = contextvars.ContextVar("am2m_registry", default={})


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
        body = "\n".join(f"  {k}: {val}" for k, val in attrs.items() if val not in (None, ""))
        return f"{v.eClass.name}:\n{body}"
    if _is_many(v):
        return "\n".join(f"- {_render(x)}" for x in v) or "(empty)"
    return str(v)


def fp(_receiver: Any, *labelled: Any) -> list[str]:
    """Footprint: alternating (label, value) arguments -> labelled texts."""
    out = []
    for i in range(0, len(labelled), 2):
        out.append(f"[{labelled[i]}]\n{_render(labelled[i + 1])}")
    return out


def prompt_of(_receiver: Any, key: str) -> str:
    return REGISTRY.get().get(key, key)


def same(a: Any, b: Any) -> bool:
    return a is b


_CODE_VALIDATORS = {"defines", "examples_run", "examples_match", "passes_tests"}


def _recording(fn):
    """Wrap a validator: remember the latest rejected candidate per goal
    element, so a run that escalates can still submit its best effort."""

    def wrapped(value: Any, *args: Any) -> Any:
        verdict = fn(value, *args)
        ctx = _vlib.CURRENT.get()
        if ctx is not None and not verdict:
            for a in args:
                if hasattr(a, "eClass") and getattr(a, "name", None):
                    vid = fn.__name__[2:]
                    ctx.last_rejected_any[f"{a.name}|{vid}"] = str(value)
                    if vid in _CODE_VALIDATORS:
                        ctx.last_rejected[str(a.name)] = str(value)
                    break
        return verdict

    wrapped.__name__ = fn.__name__
    return wrapped


# validators (receiver = sampled value)
for _name, _fn in _vlib.IMPLEMENTATIONS.items():
    globals()[f"v_{_name}"] = _recording(_fn)
