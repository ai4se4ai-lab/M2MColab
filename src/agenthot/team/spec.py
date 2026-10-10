"""Declarative team specification (`.agentm2m/team.yaml`).

Lets a team be declared as data instead of Python (compare
examples/01_devteam/metamodels.py + run.py): view metamodels, their owning
agents (omega), seed models, and hand-offs. Everything is built through the
existing `MetamodelBuilder` and `Team` API, so a YAML-declared team is
indistinguishable from a hand-wired one at run time.

    name: devteam
    views:
      Req:
        owner: Analyst
        classes:
          Epic:      {attributes: [name]}
          Criterion: {attributes: [id, text]}
          UserStory:
            attributes: [id, status]
            references:
              epic: Epic                                   # non-containment, single
              criteria: {type: Criterion, many: true, containment: true}
        root: {class: ReqModel, slots: {epics: Epic, stories: UserStory}}
        seed:
          epics: [{name: Payments}]
          stories:
            - {id: S1, status: accepted, epic: "Epic#Payments",
               criteria: [{id: S1.1, text: "..."}]}
      Arch:
        owner: Architect
        classes:
          Operation:
            attributes: [name, signature]
            references: {story: Req.UserStory}             # cross-view reference
        root: {class: ArchModel, slots: {operations: Operation}}
    handoffs:
      - {name: Req2Arch, rule: rules/Req2Arch.agentm2m}   # target view read from the module

Seed/edit values for a non-containment reference are element keys
("Type#id_or_name", as in trace links), optionally view-qualified
("Req:Criterion#S1.1"); containment references take nested dicts.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ..engine.trace import element_key
from ..metamodel.builder import MetamodelBuilder

_PRIM_TYPES = {"string", "str", "boolean", "bool", "int", "integer"}


class SpecError(ValueError):
    """The team specification is malformed or inconsistent."""


def load_spec_file(path: str | Path) -> dict:
    p = Path(path)
    try:
        data = yaml.safe_load(p.read_text())
    except yaml.YAMLError as exc:
        raise SpecError(f"{p}: invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise SpecError(f"{p}: top level must be a mapping")
    data.setdefault("views", {})
    data.setdefault("handoffs", [])
    if not isinstance(data["views"], dict):
        raise SpecError("'views' must be a mapping of view name -> view spec")
    if not isinstance(data["handoffs"], list):
        raise SpecError("'handoffs' must be a list")
    return data


# ----------------------------------------------------------------------
# Metamodels
# ----------------------------------------------------------------------


def _norm_attributes(raw: Any, where: str) -> list[tuple[str, str, bool]]:
    """-> [(name, type, many)]. Accepts `[a, b]`, `[{a: int}]`, `{a: str}`,
    or `{a: {type: int, many: true}}`."""
    out: list[tuple[str, str, bool]] = []
    if raw is None:
        return out
    items: list[tuple[str, Any]]
    if isinstance(raw, dict):
        items = list(raw.items())
    elif isinstance(raw, list):
        items = []
        for it in raw:
            if isinstance(it, str):
                items.append((it, "string"))
            elif isinstance(it, dict) and len(it) == 1:
                items.append(next(iter(it.items())))
            else:
                raise SpecError(f"{where}: bad attribute entry {it!r}")
    else:
        raise SpecError(f"{where}: 'attributes' must be a list or mapping")
    for name, t in items:
        many = False
        if isinstance(t, dict):
            many = bool(t.get("many", False))
            t = t.get("type", "string")
        t = str(t or "string").lower()
        if t not in _PRIM_TYPES:
            raise SpecError(f"{where}.{name}: unsupported attribute type {t!r} (use string|int|boolean)")
        out.append((str(name), t, many))
    return out


def _norm_references(raw: Any, where: str) -> list[tuple[str, str, bool, bool]]:
    """-> [(name, type_ref, many, containment)]."""
    out: list[tuple[str, str, bool, bool]] = []
    if raw is None:
        return out
    if not isinstance(raw, dict):
        raise SpecError(f"{where}: 'references' must be a mapping")
    for name, r in raw.items():
        if isinstance(r, str):
            out.append((str(name), r, False, False))
        elif isinstance(r, dict) and "type" in r:
            out.append((str(name), str(r["type"]), bool(r.get("many", False)), bool(r.get("containment", False))))
        else:
            raise SpecError(f"{where}.{name}: reference needs a type (e.g. `Epic` or `{{type: Epic, many: true}}`)")
    return out


def _view_dependencies(vname: str, vspec: dict) -> set[str]:
    deps: set[str] = set()
    for cname, cspec in (vspec.get("classes") or {}).items():
        for _n, tref, _m, _c in _norm_references((cspec or {}).get("references"), f"{vname}.{cname}"):
            if "." in tref:
                deps.add(tref.split(".", 1)[0])
    deps.discard(vname)
    return deps


def order_views(views: dict[str, dict], known: set[str] | None = None) -> list[str]:
    """Topological order so a view is built after every view it references."""
    known = set(known or ())
    remaining = dict(views)
    order: list[str] = []
    while remaining:
        ready = [
            v for v, s in remaining.items()
            if _view_dependencies(v, s or {}) <= (known | set(order))
        ]
        if not ready:
            missing = {v: sorted(_view_dependencies(v, s or {}) - known - set(order) - set(remaining)) for v, s in remaining.items()}
            unknown = {v: m for v, m in missing.items() if m}
            if unknown:
                raise SpecError(f"views reference unknown views: {unknown}")
            raise SpecError(f"cyclic cross-view references between views {sorted(remaining)}")
        for v in ready:
            order.append(v)
            del remaining[v]
    return order


def build_view_metamodel(
    team_name: str, vname: str, vspec: dict, existing: dict[str, MetamodelBuilder]
) -> MetamodelBuilder:
    """Build one view metamodel MM_i from its spec; cross-view references
    (`Other.Class`) resolve against already-built `existing` metamodels."""
    if not isinstance(vspec, dict):
        raise SpecError(f"view {vname}: spec must be a mapping")
    classes = vspec.get("classes") or {}
    if not isinstance(classes, dict) or not classes:
        raise SpecError(f"view {vname}: needs at least one class under 'classes'")
    root = vspec.get("root") or {}
    root_cls_name = root.get("class") or f"{vname}Model"
    slots = root.get("slots") or {}
    if not slots:
        raise SpecError(f"view {vname}: root needs 'slots' (feature -> class) so rules can create elements")
    if root_cls_name in classes:
        raise SpecError(f"view {vname}: root class {root_cls_name} must not also be listed under classes")

    b = MetamodelBuilder(vname, vspec.get("nsURI") or f"http://agentm2m/{team_name}/{vname.lower()}")

    # Pass 1: classes (respecting `extends` within the view).
    pending = dict(classes)
    while pending:
        progressed = False
        for cname, cspec in list(pending.items()):
            raw_supers = (cspec or {}).get("extends") or []
            supers = [raw_supers] if isinstance(raw_supers, str) else list(raw_supers)
            if all(s in b._classes for s in supers):
                for s in supers:
                    if s not in classes:
                        raise SpecError(f"view {vname}: {cname} extends unknown class {s}")
                b.eclass(str(cname), super_types=supers)
                del pending[cname]
                progressed = True
        if not progressed:
            raise SpecError(f"view {vname}: cyclic or unknown 'extends' among {sorted(pending)}")

    # Pass 2: features.
    for cname, cspec in classes.items():
        cspec = cspec or {}
        cls = b.get(str(cname))
        for name, t, many in _norm_attributes(cspec.get("attributes"), f"{vname}.{cname}"):
            b.attribute(cls, name, t, many=many)
        for name, tref, many, containment in _norm_references(cspec.get("references"), f"{vname}.{cname}"):
            if "." in tref:
                other_view, other_cls = tref.split(".", 1)
                if other_view == vname:
                    target: Any = other_cls
                else:
                    if other_view not in existing:
                        raise SpecError(f"{vname}.{cname}.{name}: unknown view {other_view!r}")
                    try:
                        target = existing[other_view].get(other_cls)
                    except KeyError:
                        raise SpecError(f"{vname}.{cname}.{name}: view {other_view} has no class {other_cls}") from None
                    if containment:
                        raise SpecError(f"{vname}.{cname}.{name}: cross-view references cannot be containment")
            else:
                target = tref
            if isinstance(target, str) and target not in b._classes:
                raise SpecError(f"{vname}.{cname}.{name}: unknown class {target!r}")
            b.reference(cls, name, target, many=many, containment=containment)

    root_cls = b.eclass(root_cls_name)
    for feature, cname in slots.items():
        if cname not in b._classes:
            raise SpecError(f"view {vname}: root slot {feature} -> unknown class {cname!r}")
        b.add_root_slot(root_cls, str(feature), str(cname))
    return b


# ----------------------------------------------------------------------
# Model instances (seed + edit)
# ----------------------------------------------------------------------


@dataclass
class _PendingRef:
    obj: Any
    feature: Any
    value: Any  # key string or list of key strings
    view: str


def _feature(obj_or_cls: Any, name: str):
    cls = obj_or_cls if hasattr(obj_or_cls, "eAllStructuralFeatures") and not hasattr(obj_or_cls, "eClass") else obj_or_cls.eClass
    for f in cls.eAllStructuralFeatures():
        if f.name == name:
            return f
    return None


def _coerce(feature: Any, value: Any) -> Any:
    tname = getattr(feature.eType, "name", "")
    if value is None:
        return None
    if tname == "EInt":
        return int(value)
    if tname == "EBoolean":
        if isinstance(value, str):
            return value.strip().lower() in {"true", "1", "yes"}
        return bool(value)
    return str(value) if not isinstance(value, str) else value


def _find_class(mm: MetamodelBuilder, name: str) -> Any:
    try:
        return mm.get(name)
    except KeyError:
        raise SpecError(f"view {mm.package.name} has no class {name!r}") from None


def set_values(obj: Any, data: dict, mm: MetamodelBuilder, view: str, refs_out: list[_PendingRef]) -> None:
    """Assign `data` (attr -> value, containment ref -> dict(s), plain ref ->
    key(s)) onto `obj`. Plain references are queued in `refs_out` so they
    can be resolved once every element exists."""
    for k, v in data.items():
        if k == "type":
            continue
        f = _feature(obj, k)
        if f is None:
            raise SpecError(f"{obj.eClass.name} has no feature {k!r}")
        if f.is_attribute:
            if f.many:
                coll = getattr(obj, k)
                coll.clear()
                coll.extend(_coerce(f, x) for x in (v or []))
            else:
                setattr(obj, k, _coerce(f, v))
        elif f.containment:
            items = v if isinstance(v, list) else [v]
            if not f.many and len(items) > 1:
                raise SpecError(f"{obj.eClass.name}.{k} holds a single element")
            children = [create_element(mm, f.eType.name, it, view, refs_out) for it in items if it is not None]
            if f.many:
                coll = getattr(obj, k)
                coll.clear()
                coll.extend(children)
            else:
                setattr(obj, k, children[0] if children else None)
        else:
            refs_out.append(_PendingRef(obj=obj, feature=f, value=v, view=view))


def create_element(mm: MetamodelBuilder, class_name: str, data: Any, view: str, refs_out: list[_PendingRef]) -> Any:
    if not isinstance(data, dict):
        raise SpecError(f"{class_name}: element data must be a mapping, got {data!r}")
    cls = _find_class(mm, str(data.get("type") or class_name))
    obj = cls()
    set_values(obj, data, mm, view, refs_out)
    return obj


def index_elements(roots: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """view -> element key -> element, for every keyable element."""
    out: dict[str, dict[str, Any]] = {}
    for view, root in roots.items():
        idx: dict[str, Any] = {}
        for el in root.eAllContents():
            try:
                idx.setdefault(element_key(el), el)
            except Exception:  # noqa: BLE001 - unkeyed helper elements are fine
                continue
        out[view] = idx
    return out


def resolve_key(key: str, view: str, index: dict[str, dict[str, Any]]) -> Any:
    if not isinstance(key, str):
        raise SpecError(f"reference value must be an element key string, got {key!r}")
    if ":" in key.split("#", 1)[0]:
        v, k = key.split(":", 1)
        hit = index.get(v, {}).get(k)
        if hit is None:
            raise SpecError(f"no element {k!r} in view {v!r}")
        return hit
    if key in index.get(view, {}):
        return index[view][key]
    hits = [idx[key] for idx in index.values() if key in idx]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise SpecError(f"no element with key {key!r} (keys look like 'Type#id', optionally 'View:Type#id')")
    raise SpecError(f"key {key!r} is ambiguous across views; qualify it as 'View:{key}'")


def resolve_refs(refs: list[_PendingRef], index: dict[str, dict[str, Any]]) -> None:
    for pr in refs:
        f = pr.feature
        if f.many:
            vals = pr.value if isinstance(pr.value, list) else [pr.value]
            targets = [resolve_key(v, pr.view, index) for v in vals if v is not None]
            coll = getattr(pr.obj, f.name)
            coll.clear()
            coll.extend(targets)
        else:
            setattr(pr.obj, f.name, None if pr.value is None else resolve_key(pr.value, pr.view, index))


def seed_root(mm: MetamodelBuilder, view: str, seed: dict | None, refs_out: list[_PendingRef]) -> Any:
    root_cls = mm.get(mm.root_name)
    root = root_cls()
    if seed:
        if not isinstance(seed, dict):
            raise SpecError(f"view {view}: seed must be a mapping of root slot -> list of elements")
        set_values(root, seed, mm, view, refs_out)
    return root
