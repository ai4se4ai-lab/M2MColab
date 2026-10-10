"""JSON persistence for a whole team's view models, across processes.

Why not XMI (agenthot.metamodel.io)? Views reference each other
(CodeEdit.operation -> Arch!Operation, TestCase.criterion -> Req!Criterion),
and Algorithm 1's incremental re-execution needs to know which element each
hand-off created for which match (the `_amt_target_key` bookkeeping tag,
which XMI does not carry). This store keeps both: every element gets a
path-based uid ("View:/feature.i/..."), non-containment references --
including cross-view ones -- are stored as uids, and engine-created
elements keep their target key. That removes the "incremental re-execution
is in-process only" limitation of docs/ARCHITECTURE.md.
"""
from __future__ import annotations

from typing import Any

from .engine.executor import _TARGET_KEY_ATTR
from .metamodel.builder import MetamodelBuilder


class StoreError(RuntimeError):
    pass


def _assign_uids(view: str, root: Any, uids: dict[int, str]) -> None:
    def walk(el: Any, path: str) -> None:
        uids[id(el)] = f"{view}:{path}"
        for f in el.eClass.eAllStructuralFeatures():
            if f.is_attribute or not f.containment:
                continue
            val = getattr(el, f.name)
            if f.many:
                for i, child in enumerate(val):
                    walk(child, f"{path.rstrip('/')}/{f.name}.{i}")
            elif val is not None:
                walk(val, f"{path.rstrip('/')}/{f.name}")

    walk(root, "/")


def _dump_el(el: Any, uids: dict[int, str]) -> dict:
    d: dict[str, Any] = {"uid": uids[id(el)], "type": f"{el.eClass.ePackage.name}.{el.eClass.name}"}
    tk = getattr(el, _TARGET_KEY_ATTR, None)
    if tk is not None:
        d["target_key"] = tk
    attrs: dict[str, Any] = {}
    refs: dict[str, Any] = {}
    children: dict[str, Any] = {}
    for f in el.eClass.eAllStructuralFeatures():
        val = getattr(el, f.name)
        if f.is_attribute:
            if f.many:
                if len(val):
                    attrs[f.name] = list(val)
            elif val is not None:
                attrs[f.name] = val
        elif f.containment:
            if f.many:
                if len(val):
                    children[f.name] = [_dump_el(c, uids) for c in val]
            elif val is not None:
                children[f.name] = _dump_el(val, uids)
        else:
            if f.many:
                ids = [uids[id(t)] for t in val if id(t) in uids]
                if ids:
                    refs[f.name] = ids
            elif val is not None and id(val) in uids:
                refs[f.name] = uids[id(val)]
    if attrs:
        d["attrs"] = attrs
    if refs:
        d["refs"] = refs
    if children:
        d["children"] = children
    return d


def dump_models(roots: dict[str, Any]) -> dict[str, dict]:
    """view name -> JSON-able tree of that view's model."""
    uids: dict[int, str] = {}
    for view, root in roots.items():
        _assign_uids(view, root, uids)
    return {view: _dump_el(root, uids) for view, root in roots.items()}


def load_models(data: dict[str, dict], views: dict[str, MetamodelBuilder]) -> dict[str, Any]:
    """Inverse of `dump_models`: rebuild every view's root (with cross-view
    references and engine target keys) from `data`."""
    classes: dict[str, Any] = {}
    for mm in views.values():
        for cls in mm._classes.values():
            classes[f"{mm.package.name}.{cls.name}"] = cls

    by_uid: dict[str, Any] = {}
    deferred: list[tuple[Any, dict]] = []

    def build(d: dict) -> Any:
        cls = classes.get(d["type"])
        if cls is None:
            raise StoreError(f"stored element type {d['type']!r} is not in the team's metamodels")
        obj = cls()
        by_uid[d["uid"]] = obj
        if "target_key" in d:
            setattr(obj, _TARGET_KEY_ATTR, d["target_key"])
        for name, val in (d.get("attrs") or {}).items():
            f = obj.eClass.findEStructuralFeature(name)
            if f is None:
                raise StoreError(f"{d['type']} has no attribute {name!r} (metamodel changed?)")
            if f.many:
                getattr(obj, name).extend(val)
            else:
                setattr(obj, name, val)
        for name, val in (d.get("children") or {}).items():
            f = obj.eClass.findEStructuralFeature(name)
            if f is None:
                raise StoreError(f"{d['type']} has no containment {name!r} (metamodel changed?)")
            if f.many:
                getattr(obj, name).extend(build(c) for c in val)
            else:
                setattr(obj, name, build(val))
        if d.get("refs"):
            deferred.append((obj, d["refs"]))
        return obj

    roots = {view: build(tree) for view, tree in data.items() if view in views}
    for obj, refs in deferred:
        for name, val in refs.items():
            f = obj.eClass.findEStructuralFeature(name)
            if f is None:
                raise StoreError(f"{obj.eClass.name} has no reference {name!r} (metamodel changed?)")
            if f.many:
                getattr(obj, name).extend(by_uid[u] for u in val if u in by_uid)
            elif val in by_uid:
                setattr(obj, name, by_uid[val])
    return roots
