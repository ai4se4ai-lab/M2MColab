"""Persistent trace model: TL_ij subseteq Match(M_i) x M_j x R (Sec III-C).

Elements are identified by a stable business key ("TypeName#id_or_name"),
not Python object identity or pyecore's XMI fragment paths, so a trace file
saved by one run can be reloaded and diffed against a later, edited source
model -- this is what lets Algorithm 1's stamp check work across process
invocations, not just within one.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_KEY_ATTRS = ("id", "name")


class TraceError(RuntimeError):
    pass


def element_key(element: Any) -> str:
    """Stable "Type#business-id" key for a pyecore model element."""
    type_name = element.eClass.name
    # Classes compiled from a typed team (autom2m) opt in to keying by
    # the unique engine target key: builder-defined business ids need not be
    # unique.
    if getattr(element.eClass, "_amt_engine_keyed", False):
        engine_key = getattr(element, "_amt_target_key", None)
        if engine_key:
            return f"{type_name}#{engine_key}"
    for attr in _KEY_ATTRS:
        if hasattr(element, attr):
            value = getattr(element, attr)
            if value not in (None, ""):
                return f"{type_name}#{value}"
    raise TraceError(
        f"element of type {type_name} has no 'id' or 'name' attribute to key a trace link on"
    )


def digest(value: Any) -> str:
    """Version stamp: a digest of den(e)_m, the footprint at acceptance time."""
    canonical = json.dumps(_to_jsonable(value), sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def _to_jsonable(value: Any) -> Any:
    """Content snapshot used for footprint digests (the version stamp).

    For a model element this deliberately serializes its *primitive*
    EAttributes only (never EReferences, to avoid cycles/over-reach beyond
    the declared footprint) -- so the digest changes exactly when an
    in-place edit (e.g. Criterion.text) changes what the prompt would say,
    which is what makes the stamp check in Algorithm 1 detect content
    changes on the same object, not just a different object being bound.
    """
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "stamp_material"):
        # a compiled footprint: the stamp covers the values at the footprint
        # *and* at the validator reads, #(fp_b(m) u vr_b(m)) (paper Def. 4)
        return _to_jsonable(value.stamp_material)
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    if hasattr(value, "eClass"):
        return {
            "_type": value.eClass.name,
            **{
                f.name: _to_jsonable(getattr(value, f.name))
                for f in value.eClass.eAllStructuralFeatures()
                if f.is_attribute
            },
        }
    if isinstance(value, (bytes,)):
        return str(value)
    if hasattr(value, "__iter__"):
        return [_to_jsonable(v) for v in value]
    return str(value)


@dataclass
class TraceLink:
    rule: str
    match_key: str  # combined key of the source match, e.g. "s=UserStory#S2"
    source_keys: dict[str, str]  # source pattern var -> element_key
    target_key: str  # element_key of the created target element
    stamps: dict[str, str] = field(default_factory=dict)  # attr -> footprint digest
    footprints: dict[str, Any] = field(default_factory=dict)  # attr -> last raw footprint (debug/report)
    # attr -> footprint digest at which the binding last *escalated*; lets the
    # engine re-report the escalation without re-sampling while that
    # footprint is unchanged (Alg. 1 line 11: escalate, never loop).
    failed_stamps: dict[str, str] = field(default_factory=dict)
    # Host mode only (values submitted by Claude Code rather than sampled in
    # one engine call): rejected submissions so far on the current footprint,
    # and the last rejection {value, reason, digest}, fed back into the next
    # prompt exactly like the in-engine resample loop's retry prompt.
    attempts: dict[str, int] = field(default_factory=dict)
    rejections: dict[str, dict] = field(default_factory=dict)
    # attr -> element keys owning a location the accepted value's footprint
    # or validator read (the link "connects" them to the target, paper Def. 5)
    reads: dict[str, list[str]] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "rule": self.rule,
            "match_key": self.match_key,
            "source_keys": self.source_keys,
            "target_key": self.target_key,
            "stamps": self.stamps,
            "failed_stamps": self.failed_stamps,
            "attempts": self.attempts,
            "rejections": self.rejections,
            "reads": self.reads,
            "footprints": {k: _to_jsonable(v) for k, v in self.footprints.items()},
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TraceLink":
        return cls(
            rule=d["rule"],
            match_key=d["match_key"],
            source_keys=d["source_keys"],
            target_key=d["target_key"],
            stamps=d.get("stamps", {}),
            footprints=d.get("footprints", {}),
            failed_stamps=d.get("failed_stamps", {}),
            attempts=d.get("attempts", {}),
            rejections=d.get("rejections", {}),
            reads=d.get("reads", {}),
        )


class TraceModel:
    """TL_ij for one hand-off T_ij."""

    def __init__(self, handoff: str) -> None:
        self.handoff = handoff
        self._links: dict[str, TraceLink] = {}  # keyed by f"{rule}::{match_key}"

    @staticmethod
    def _lk(rule: str, match_key: str) -> str:
        return f"{rule}::{match_key}"

    def links(self) -> list[TraceLink]:
        return list(self._links.values())

    def get(self, rule: str, match_key: str) -> TraceLink | None:
        return self._links.get(self._lk(rule, match_key))

    def put(self, link: TraceLink) -> None:
        self._links[self._lk(link.rule, link.match_key)] = link

    def remove(self, rule: str, match_key: str) -> None:
        self._links.pop(self._lk(rule, match_key), None)

    def matches_covered(self, rule: str | None = None) -> set[str]:
        return {l.match_key for l in self._links.values() if rule is None or l.rule == rule}

    def find_by_source_key(self, key: str) -> list[TraceLink]:
        """All links whose match involved source element `key` (any var)."""
        return [l for l in self._links.values() if key in l.source_keys.values()]

    def target_for_source(self, key: str) -> TraceLink | None:
        hits = self.find_by_source_key(key)
        return hits[0] if hits else None

    def save(self, path: str | Path) -> None:
        payload = {"handoff": self.handoff, "links": [l.to_dict() for l in self._links.values()]}
        Path(path).write_text(json.dumps(payload, indent=2, sort_keys=True))

    @classmethod
    def load(cls, path: str | Path) -> "TraceModel":
        p = Path(path)
        if not p.exists():
            return cls(handoff=p.stem)
        payload = json.loads(p.read_text())
        tm = cls(handoff=payload["handoff"])
        for d in payload["links"]:
            tm.put(TraceLink.from_dict(d))
        return tm
