"""A persistent agenthot workspace (`<project>/.agenthot/`) and the operations
the Claude Code plugin exposes over MCP.

    .agenthot/
      team.yaml            design-time team model (views, owners, seeds, hand-offs)
      rules/*.agenthot     hybrid hand-off modules (+ helpers.py)
      state/state.json     runtime state: view models, trace models, and
                           runtime team evolutions (HOTs) -- models@run.time

Every public method returns plain JSON-able dicts, so the MCP server
(`autom2m.mcp_server`) is a thin wrapper and everything here is testable
without MCP. Mutating operations persist immediately; before each operation
the workspace reloads if another process changed the files on disk.
"""
from __future__ import annotations

import contextlib
import copy
import json
import os
import shutil
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

from .config import LLMConfig
from .engine.executor import _TARGET_KEY_ATTR, _index_existing_targets
from .engine.expr import eval_expr
from .engine.helpers_loader import load_helpers
from .engine.matcher import compute_matches
from .engine.trace import TraceLink, TraceModel, digest, element_key
from .llm.base import LLMBackend
from .llm.factory import make_backend
from .llm.host_backend import HostBackend
from .rules.ast import Module, StochasticBinding
from .rules.parser import parse_module_file
from .store import dump_models, load_models
from .team.hot import TeamChange, apply_hot
from .team.model import Team
from .team.runtime import TeamRuntime
from .team.spec import (
    SpecError,
    _PendingRef,
    build_view_metamodel,
    create_element,
    index_elements,
    load_spec_file,
    order_views,
    resolve_key,
    resolve_refs,
    seed_root,
    set_values,
)

WORKSPACE_DIRNAME = ".agenthot"
STATE_VERSION = 1
_MAX_TEXT = 2000


class WorkspaceError(RuntimeError):
    """A user-facing error (bad input, missing workspace, permission)."""


def list_templates() -> list[str]:
    root = resources.files("agenthot") / "templates"
    return sorted(p.name for p in root.iterdir() if p.is_dir() and (p / "team.yaml").is_file())


def _template_dir(name: str) -> Path:
    if name not in list_templates():
        raise WorkspaceError(f"unknown template {name!r}; available: {', '.join(list_templates())}")
    return Path(str(resources.files("agenthot") / "templates" / name))


def _inside(base: Path, p: Path) -> bool:
    try:
        p.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def _short(v: Any) -> Any:
    if isinstance(v, str) and len(v) > _MAX_TEXT:
        return v[:_MAX_TEXT] + f" ...[truncated {len(v) - _MAX_TEXT} chars]"
    return v


@dataclass
class _Loaded:
    team: Team
    runtime: TeamRuntime
    spec: dict
    evolutions: list[dict] = field(default_factory=list)


class Workspace:
    def __init__(
        self,
        project_dir: str | Path,
        *,
        backend: str | None = None,
        model: str | None = None,
        llm: LLMBackend | None = None,
        max_resamples: int | None = None,
        max_passes: int = 8,
    ) -> None:
        self.project_dir = Path(project_dir).resolve()
        self.dir = self.project_dir / WORKSPACE_DIRNAME
        cfg = LLMConfig.from_env()
        self.backend_name = (backend or os.getenv("AGENTHOT_LLM") or "host").strip().lower()
        self._llm = llm or make_backend(cfg, override_provider=self.backend_name, override_model=model or os.getenv("AGENTHOT_MODEL") or None)
        if llm is not None:
            self.backend_name = getattr(llm, "name", self.backend_name)
        self.max_resamples = max_resamples or int(os.getenv("AGENTHOT_MAX_RESAMPLES", str(cfg.max_resamples)))
        self.temperature = cfg.temperature
        self.max_passes = max_passes
        self._lock = threading.RLock()
        self._loaded: _Loaded | None = None
        self._fingerprint: tuple | None = None

    # ------------------------------------------------------------------
    # paths, locking, (re)loading
    # ------------------------------------------------------------------

    @property
    def spec_path(self) -> Path:
        return self.dir / "team.yaml"

    @property
    def state_path(self) -> Path:
        return self.dir / "state" / "state.json"

    @property
    def is_host(self) -> bool:
        return bool(getattr(self._llm, "deferred", False))

    def exists(self) -> bool:
        return self.spec_path.is_file()

    def _disk_fingerprint(self) -> tuple:
        files = [self.spec_path, self.state_path, *sorted(self.dir.glob("rules/**/*"))]
        return tuple((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in files if p.is_file())

    @contextlib.contextmanager
    def _file_lock(self) -> Iterator[None]:
        lock_path = self.dir / "state" / ".lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "a+") as fh:
            try:
                import fcntl

                fcntl.flock(fh, fcntl.LOCK_EX)
            except (ImportError, OSError):  # pragma: no cover - non-POSIX
                pass
            try:
                yield
            finally:
                try:
                    import fcntl

                    fcntl.flock(fh, fcntl.LOCK_UN)
                except (ImportError, OSError):  # pragma: no cover
                    pass

    def _require(self) -> _Loaded:
        if not self.exists():
            raise WorkspaceError(
                f"no agenthot workspace at {self.dir}; create one with team_init "
                f"(templates: {', '.join(list_templates())})"
            )
        fp = self._disk_fingerprint()
        if self._loaded is None or fp != self._fingerprint:
            with self._file_lock():
                state = json.loads(self.state_path.read_text()) if self.state_path.is_file() else None
                self._loaded = self._build(state)
                self._fingerprint = self._disk_fingerprint()
        return self._loaded

    def _new_runtime(self, team: Team, llm: LLMBackend | None = None) -> TeamRuntime:
        return TeamRuntime(
            team,
            llm or self._llm,
            max_resamples=self.max_resamples,
            temperature=self.temperature,
            max_passes=self.max_passes,
        )

    def _rule_path(self, rel: str) -> Path:
        p = (self.dir / rel).resolve()
        if not _inside(self.dir, p):
            raise WorkspaceError(f"rule path {rel!r} escapes the workspace directory")
        if not p.is_file():
            raise WorkspaceError(f"rule file {rel!r} not found under {self.dir}")
        return p

    def _parse_rule(self, rel: str) -> Module:
        p = self._rule_path(rel)
        try:
            module = parse_module_file(p)
        except Exception as exc:
            raise WorkspaceError(f"{rel}: rule module does not parse: {exc}") from exc
        if module.uses and not _inside(self.dir, p.parent / module.uses):
            raise WorkspaceError(f"{rel}: `uses {module.uses!r}` escapes the workspace directory")
        return module

    def _build(self, state: dict | None, llm: LLMBackend | None = None) -> _Loaded:
        """Construct Team + runtime from team.yaml, then either the persisted
        state (models, traces, evolutions) or the spec's seed models."""
        try:
            spec = load_spec_file(self.spec_path)
        except SpecError as exc:
            raise WorkspaceError(str(exc)) from exc
        team_name = str(spec.get("name") or "team")
        evolutions = list((state or {}).get("evolutions", []))

        team = Team()
        refs: list[_PendingRef] = []
        seeds: dict[str, Any] = {}
        try:
            view_specs: dict[str, dict] = dict(spec["views"])
            for ev in evolutions:
                view_specs[ev["view"]] = ev["view_spec"]
            for vname in order_views(view_specs):
                vspec = view_specs[vname] or {}
                mm = build_view_metamodel(team_name, vname, vspec, team.views)
                owner = vspec.get("owner")
                if not owner:
                    raise SpecError(f"view {vname}: needs an 'owner' agent")
                if state is None:
                    root = seed_root(mm, vname, vspec.get("seed"), refs)
                else:
                    root = mm.get(mm.root_name)()
                team.add_view(mm, root)
                team.add_agent(str(owner), vname)
                seeds[vname] = root
            if state is None:
                resolve_refs(refs, index_elements(seeds))
        except SpecError as exc:
            raise WorkspaceError(f"team.yaml: {exc}") from exc

        handoffs = list(spec["handoffs"]) + [ev["handoff"] for ev in evolutions]
        for h in handoffs:
            if not isinstance(h, dict) or "rule" not in h:
                raise WorkspaceError(f"team.yaml: hand-off entries need a 'rule' path, got {h!r}")
            module = self._parse_rule(h["rule"])
            name = h.get("name") or module.name
            target = h.get("target") or module.target_mm
            for sm in module.sources:
                if sm.mm_name not in team.views:
                    raise WorkspaceError(f"hand-off {name}: source view {sm.mm_name!r} is not declared")
            if target not in team.views:
                raise WorkspaceError(f"hand-off {name}: target view {target!r} is not declared")
            if name in team.handoffs:
                raise WorkspaceError(f"duplicate hand-off name {name!r}")
            team.add_handoff(name, self._rule_path(h["rule"]), target_mm=target)

        if state is not None:
            if state.get("version") != STATE_VERSION:
                raise WorkspaceError(f"unsupported state version {state.get('version')!r}")
            try:
                roots = load_models(state.get("models", {}), team.views)
            except Exception as exc:
                raise WorkspaceError(
                    f"stored models no longer fit team.yaml ({exc}); fix the spec or reset the state with team_init(force=true)"
                ) from exc
            for vname, root in roots.items():
                team.roots[vname] = root
            for hname, payload in (state.get("traces") or {}).items():
                if hname in team.traces:
                    tm = TraceModel(handoff=hname)
                    for d in payload.get("links", []):
                        tm.put(TraceLink.from_dict(d))
                    team.traces[hname] = tm

        return _Loaded(team=team, runtime=self._new_runtime(team, llm), spec=spec, evolutions=evolutions)

    def _state_dict(self, loaded: _Loaded) -> dict:
        return {
            "version": STATE_VERSION,
            "models": dump_models(loaded.team.roots),
            "traces": {
                name: {"handoff": name, "links": [l.to_dict() for l in tm.links()]}
                for name, tm in loaded.team.traces.items()
            },
            "evolutions": loaded.evolutions,
        }

    def save(self) -> None:
        loaded = self._require()
        with self._file_lock():
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self._state_dict(loaded), indent=1, sort_keys=True, default=str))
            os.replace(tmp, self.state_path)
        self._fingerprint = self._disk_fingerprint()

    def _restore(self, state: dict) -> None:
        self._loaded = self._build(state)

    # ------------------------------------------------------------------
    # operations
    # ------------------------------------------------------------------

    def init(self, template: str = "devteam", *, force: bool = False) -> dict:
        with self._lock:
            src = _template_dir(template)
            if self.dir.exists():
                if not force:
                    raise WorkspaceError(f"{self.dir} already exists; pass force=true to replace it")
                shutil.rmtree(self.dir)
            shutil.copytree(src, self.dir, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            self._loaded = None
            self._fingerprint = None
            self._require()
            self.save()
            out = self.status()
            out["created"] = str(self.dir)
            out["template"] = template
            return out

    def binding_states(self, loaded: _Loaded | None = None) -> list[dict]:
        """Every (target, stochastic binding) with its state:
        fresh | stale (footprint changed since acceptance: an open obligation)
        | missing (never accepted) | escalated (failed on this footprint)."""
        loaded = loaded or self._require()
        team, rt = loaded.team, loaded.runtime
        out: list[dict] = []
        for hname, spec in team.handoffs.items():
            module = rt.module_for(hname)
            helpers = load_helpers(module.uses, spec.rule_path.parent)
            roots_by_mm = {sm.mm_name: team.roots[sm.mm_name] for sm in module.sources}
            trace = team.traces[hname]
            owner = team.owner_of(spec.target_mm)
            for rule in module.rules:
                for m in compute_matches(rule, roots_by_mm, helpers):
                    link = trace.get(rule.name, m.match_key)
                    for tp in rule.to_clause.patterns:
                        tkey = f"{rule.name}::{tp.var}::{m.match_key}"
                        for b in tp.bindings:
                            if not isinstance(b, StochasticBinding):
                                continue
                            if link is None:
                                state = "uncovered"
                            else:
                                d = digest(eval_expr(b.footprint_expr, m.bindings, helpers))
                                if link.stamps.get(b.name) == d:
                                    state = "fresh"
                                elif link.failed_stamps.get(b.name) == d:
                                    state = "escalated"
                                elif b.name in link.stamps:
                                    state = "stale"
                                else:
                                    state = "missing"
                            out.append({
                                "handoff": hname,
                                "target_key": tkey,
                                "binding": b.name,
                                "agent": owner.name if owner else None,
                                "state": state,
                            })
        return out

    def status(self) -> dict:
        with self._lock:
            loaded = self._require()
            team = loaded.team
            states = self.binding_states(loaded)
            counts: dict[str, int] = {}
            for s in states:
                counts[s["state"]] = counts.get(s["state"], 0) + 1
            handoffs = []
            for hname, spec in team.handoffs.items():
                module = loaded.runtime.module_for(hname)
                hs = [s for s in states if s["handoff"] == hname]
                handoffs.append({
                    "name": hname,
                    "rule": str(spec.rule_path.relative_to(self.dir)),
                    "sources": [sm.mm_name for sm in module.sources],
                    "target": spec.target_mm,
                    "trace_links": len(team.traces[hname].links()),
                    "bindings": {k: sum(1 for s in hs if s["state"] == k) for k in sorted({s["state"] for s in hs})},
                })
            views = {
                v: {
                    "owner": (team.owner_of(v).name if team.owner_of(v) else None),
                    "elements": sum(1 for _ in root.eAllContents()),
                }
                for v, root in team.roots.items()
            }
            open_items = [s for s in states if s["state"] != "fresh"]
            return {
                "workspace": str(self.dir),
                "team": loaded.spec.get("name"),
                "backend": self.backend_name,
                "agents": {a: sorted(team.write_rights.get(a, set())) for a in team.agents},
                "views": views,
                "handoffs": handoffs,
                "bindings": counts,
                "evolutions": [ev["agent"] for ev in loaded.evolutions],
                "phi": loaded.runtime.acceptance_holds() if not open_items else False,
                "open": open_items[:25],
                "open_total": len(open_items),
            }

    def validate(self) -> dict:
        """Check the spec, every rule module, helper paths and matching."""
        with self._lock:
            errors: list[str] = []
            handoffs: list[dict] = []
            if not self.exists():
                return {"ok": False, "errors": [f"no workspace at {self.dir}"], "handoffs": []}
            try:
                self._loaded = None
                loaded = self._require()
            except WorkspaceError as exc:
                return {"ok": False, "errors": [str(exc)], "handoffs": []}
            team = loaded.team
            for hname, spec in team.handoffs.items():
                module = loaded.runtime.module_for(hname)
                info = {"name": hname, "rules": [], "ok": True}
                try:
                    helpers = load_helpers(module.uses, spec.rule_path.parent)
                    roots_by_mm = {sm.mm_name: team.roots[sm.mm_name] for sm in module.sources}
                    for rule in module.rules:
                        for p in rule.from_clause.patterns:
                            team.views[p.model_alias].get(p.type_name)
                        for tp in rule.to_clause.patterns:
                            team.views[spec.target_mm].get(tp.type_name)
                            team.views[spec.target_mm].root_slot_for(tp.type_name)
                        n = len(compute_matches(rule, roots_by_mm, helpers))
                        n_st = sum(1 for tp in rule.to_clause.patterns for b in tp.bindings if isinstance(b, StochasticBinding))
                        info["rules"].append({"rule": rule.name, "matches": n, "stochastic_bindings": n_st})
                except KeyError as exc:
                    info["ok"] = False
                    errors.append(f"{hname}: unknown class or missing root slot {exc} (declare it in team.yaml)")
                except Exception as exc:  # noqa: BLE001 - reported as a validation error
                    info["ok"] = False
                    errors.append(f"{hname}: {type(exc).__name__}: {exc}")
                handoffs.append(info)
            return {"ok": not errors, "errors": errors, "handoffs": handoffs}

    # -- models ---------------------------------------------------------

    def _describe(self, el: Any, depth: int) -> dict:
        d: dict[str, Any] = {"type": el.eClass.name}
        try:
            d["key"] = element_key(el)
        except Exception:  # noqa: BLE001 - unkeyed element: no key shown
            pass
        tk = getattr(el, _TARGET_KEY_ATTR, None)
        if tk:
            d["engine_owned"] = tk
        for f in el.eClass.eAllStructuralFeatures():
            val = getattr(el, f.name)
            if f.is_attribute:
                if f.many:
                    if len(val):
                        d[f.name] = [_short(v) for v in val]
                elif val is not None:
                    d[f.name] = _short(val)
            elif f.containment:
                items = list(val) if f.many else ([val] if val is not None else [])
                if not items:
                    continue
                if depth > 0:
                    kids = [self._describe(c, depth - 1) for c in items]
                    d[f.name] = kids if f.many else kids[0]
                else:
                    d[f.name] = f"<{len(items)} element(s)>"
            else:
                items = list(val) if f.many else ([val] if val is not None else [])

                def ref(t: Any) -> str:
                    try:
                        return f"{t.eClass.ePackage.name}:{element_key(t)}"
                    except Exception:  # noqa: BLE001 - unkeyed element: show its type
                        return t.eClass.name

                if items:
                    d[f.name] = [ref(t) for t in items] if f.many else ref(items[0])
        return d

    def show(self, view: str, key: str | None = None, depth: int = 3) -> dict:
        with self._lock:
            loaded = self._require()
            if view not in loaded.team.roots:
                raise WorkspaceError(f"unknown view {view!r}; views: {sorted(loaded.team.roots)}")
            root = loaded.team.roots[view]
            if key:
                try:
                    el = resolve_key(key, view, {view: index_elements({view: root})[view]})
                except SpecError as exc:
                    raise WorkspaceError(str(exc)) from exc
                return {"view": view, "element": self._describe(el, depth)}
            return {"view": view, "owner": loaded.team.owner_of(view).name, "model": self._describe(root, depth)}

    def _apply_ops(self, loaded: _Loaded, view: str, ops: list[dict], as_agent: str) -> list[str]:
        team = loaded.team
        if view not in team.roots:
            raise WorkspaceError(f"unknown view {view!r}; views: {sorted(team.roots)}")
        if as_agent not in team.agents:
            raise WorkspaceError(f"unknown agent {as_agent!r}; agents: {sorted(team.agents)}")
        if view not in team.write_rights.get(as_agent, set()):
            owner = team.owner_of(view)
            raise WorkspaceError(
                f"write rights (omega): agent {as_agent} may not write view {view}; its owner is {owner.name if owner else '?'}"
            )
        if not isinstance(ops, list) or not ops:
            raise WorkspaceError("ops must be a non-empty list")
        mm = team.views[view]
        root = team.roots[view]
        applied: list[str] = []
        refs: list[_PendingRef] = []

        def lookup(key: str) -> Any:
            return resolve_key(key, view, {view: index_elements({view: root})[view]})

        def writable(el: Any, what: str) -> None:
            tk = getattr(el, _TARGET_KEY_ATTR, None)
            if tk:
                raise WorkspaceError(
                    f"cannot {what} {element_key(el)}: it is engine-owned (created by hand-off rule {tk.split('::')[0]}); "
                    "change its source view instead"
                )

        for i, op in enumerate(ops):
            kind = (op or {}).get("op")
            try:
                if kind == "create":
                    parent = lookup(op["parent"]) if op.get("parent") else root
                    if parent is not root:
                        writable(parent, "add children to")
                    feature = op.get("feature")
                    if not feature:
                        raise WorkspaceError(f"op {i}: create needs 'feature' (root slots: {sorted(mm._root_slot_for_type.values())})")
                    f = parent.eClass.findEStructuralFeature(feature)
                    if f is None or f.is_attribute or not f.containment:
                        raise WorkspaceError(f"op {i}: {parent.eClass.name}.{feature} is not a containment reference")
                    value = op.get("value") or {}
                    el = create_element(mm, value.get("type") or f.eType.name, value, view, refs)
                    if f.many:
                        getattr(parent, feature).append(el)
                    else:
                        setattr(parent, feature, el)
                    applied.append(f"create {el.eClass.name} in {feature}")
                elif kind == "set":
                    el = lookup(op["key"])
                    writable(el, "modify")
                    values = op.get("values") or {}
                    if not values:
                        raise WorkspaceError(f"op {i}: set needs non-empty 'values'")
                    set_values(el, values, mm, view, refs)
                    applied.append(f"set {op['key']}: {', '.join(values)}")
                elif kind == "delete":
                    el = lookup(op["key"])
                    writable(el, "delete")
                    el.delete()
                    applied.append(f"delete {op['key']}")
                else:
                    raise WorkspaceError(f"op {i}: unknown op {kind!r} (create|set|delete)")
            except SpecError as exc:
                raise WorkspaceError(f"op {i}: {exc}") from exc
            except KeyError as exc:
                raise WorkspaceError(f"op {i}: missing field {exc}") from exc
        try:
            resolve_refs(refs, index_elements(team.roots))
        except SpecError as exc:
            raise WorkspaceError(str(exc)) from exc
        # Conformance: every element a rule matches must still be keyable.
        for hname, spec in team.handoffs.items():
            module = loaded.runtime.module_for(hname)
            helpers = load_helpers(module.uses, spec.rule_path.parent)
            roots_by_mm = {sm.mm_name: team.roots[sm.mm_name] for sm in module.sources}
            for rule in module.rules:
                try:
                    compute_matches(rule, roots_by_mm, helpers)
                except Exception as exc:
                    raise WorkspaceError(f"edit rejected: hand-off {hname} can no longer match ({exc})") from exc
        return applied

    def edit(self, view: str, ops: list[dict], as_agent: str) -> dict:
        """Apply create/set/delete ops to `view` as `as_agent`, atomically:
        on any error nothing is changed. Enforces write rights (omega) and
        that hand-off targets are engine-owned (read-only to agents)."""
        with self._lock:
            loaded = self._require()
            snapshot = self._state_dict(loaded)
            try:
                applied = self._apply_ops(loaded, view, ops, as_agent)
                self._propagate_structure(loaded)
            except Exception:
                self._restore(snapshot)
                raise
            self.save()
            open_now = [s for s in self.binding_states(loaded) if s["state"] != "fresh"]
            return {
                "view": view,
                "applied": applied,
                "open_obligations": len(open_now),
                "next": "call run (host mode: then next_bindings/submit_binding) to discharge them",
            }

    def _propagate_structure(self, loaded: _Loaded) -> None:
        """Paper, Step 6: a change re-runs R^str deterministically. Run every
        hand-off with a deferred backend: targets are created/deleted and
        structural bindings recomputed, but nothing is sampled -- stale
        stochastic bindings simply remain open obligations."""
        rt = loaded.runtime
        llm = rt.llm
        rt.llm = HostBackend()
        try:
            rt.run_to_fixpoint()
        finally:
            rt.llm = llm

    # -- running ---------------------------------------------------------

    def impact(self, view: str | None = None, ops: list[dict] | None = None, as_agent: str | None = None) -> dict:
        """Obl(Delta) preview without any LLM call and without changing the
        workspace: on a scratch copy (optionally after applying `ops`), run
        the structural phase with the host backend and report which bindings
        would need (re-)sampling."""
        with self._lock:
            loaded = self._require()
            scratch = self._build(copy.deepcopy(self._state_dict(loaded)), llm=HostBackend())
            if ops:
                if not view or not as_agent:
                    raise WorkspaceError("impact with ops needs view and as_agent")
                self._apply_ops(scratch, view, ops, as_agent)
            report = scratch.runtime.run_to_fixpoint()
            items = report.pending + report.blocked
            obligations = [
                {"handoff": p.handoff, "target_key": p.target_key, "binding": p.binding,
                 "agent": self._owner_for_handoff(scratch.team, p.handoff), "ready": p in report.pending}
                for p in items if p.stale
            ]
            new = [
                {"handoff": p.handoff, "target_key": p.target_key, "binding": p.binding,
                 "ready": p in report.pending}
                for p in items if not p.stale
            ]
            created = [k for r in report.handoff_reports.values() for k in r.created]
            deleted = [k for r in report.handoff_reports.values() for k in r.deleted]
            escalated = [
                {"target_key": e.target_key, "binding": e.binding}
                for e in report.escalations
            ]
            return {
                "obligations": obligations,
                "new_bindings": new,
                "created": created,
                "deleted": deleted,
                "still_escalated": escalated,
                "llm_calls_upper_bound": (len(obligations) + len(new)) * self.max_resamples,
                "note": "direct obligations only: a re-sampled value that changes may oblige downstream bindings on the next run",
            }

    @staticmethod
    def _owner_for_handoff(team: Team, handoff: str) -> str | None:
        owner = team.owner_of(team.handoffs[handoff].target_mm)
        return owner.name if owner else None

    def run(self, max_passes: int | None = None) -> dict:
        """Run every hand-off to a fixpoint. With the host backend nothing is
        sampled: stale bindings come back as `pending` for the host to fill
        via next_bindings / submit_binding."""
        with self._lock:
            loaded = self._require()
            if max_passes:
                loaded.runtime.max_passes = max_passes
            report = loaded.runtime.run_to_fixpoint()
            self.save()
            pending = [p.to_dict() | {"agent": self._owner_for_handoff(loaded.team, p.handoff)} for p in report.pending]
            return {
                "backend": self.backend_name,
                "passes": report.passes,
                "handoffs": {
                    n: {"created": len(r.created), "deleted": len(r.deleted), "resampled": len(r.resampled),
                        "escalations": len(r.escalations), "pending": len(r.pending), "blocked": len(r.blocked)}
                    for n, r in report.handoff_reports.items()
                },
                "obligations_discharged": [
                    {"handoff": o.handoff, "target_key": o.target_key, "binding": o.binding, "agent": o.agent}
                    for o in report.obligations
                ],
                "escalations": [
                    {"target_key": e.target_key, "binding": e.binding, "rule": e.rule, "reason": e.reason}
                    for e in report.escalations
                ],
                "pending": len(pending),
                "blocked": len(report.blocked),
                "pending_preview": [{k: p[k] for k in ("target_key", "binding", "agent", "kind")} for p in pending[:10]],
                "phi": loaded.runtime.acceptance_holds() if not pending else False,
            }

    def next_bindings(self, agent: str | None = None, limit: int = 5) -> dict:
        """Host mode: the next bindings to fill, each with its complete,
        footprint-bounded prompt. Runs the structural phase first (free)."""
        with self._lock:
            if not self.is_host:
                raise WorkspaceError(
                    f"backend is {self.backend_name!r}: bindings are sampled by the engine; use run instead "
                    "(set AGENTHOT_LLM=host to let Claude Code fill them)"
                )
            loaded = self._require()
            report = loaded.runtime.run_to_fixpoint()
            self.save()
            items = []
            for p in report.pending:
                owner = self._owner_for_handoff(loaded.team, p.handoff)
                if agent and owner != agent:
                    continue
                items.append(p.to_dict() | {"agent": owner, "max_attempts": self.max_resamples})
            return {
                "bindings": items[: max(1, limit)],
                "remaining": max(0, len(items) - limit),
                "blocked_on_upstream": len(report.blocked),
                "escalations": len(report.escalations),
                "instructions": (
                    "For each binding answer ONLY from its prompt (the prompt already contains the whole allowed "
                    "footprint). Return just the value in the format the prompt asks for, then call submit_binding "
                    "with the binding's target_key, binding and footprint_version."
                ),
            }

    def submit_binding(self, target_key: str, binding: str, value: str, footprint_version: str | None = None) -> dict:
        with self._lock:
            loaded = self._require()
            try:
                result = loaded.runtime.submit_binding(target_key, binding, value, footprint_version)
            except KeyError as exc:
                raise WorkspaceError(str(exc.args[0] if exc.args else exc)) from exc
            self.save()
            return result

    def acceptance(self) -> dict:
        with self._lock:
            loaded = self._require()
            states = self.binding_states(loaded)
            open_items = [s for s in states if s["state"] != "fresh"]
            phi = (not open_items) and loaded.runtime.acceptance_holds()
            return {
                "phi": phi,
                "open": open_items[:25],
                "open_total": len(open_items),
                "meaning": "phi = every match covered by a trace link, every stochastic value accepted by its @check "
                "for its current footprint, nothing pending or escalated",
            }

    # -- traces ----------------------------------------------------------

    def trace_query(self, key: str, transitive: bool = True) -> dict:
        """What corresponds to element `key`: upstream sources it was
        generated from, and downstream targets generated from it."""
        with self._lock:
            loaded = self._require()
            team = loaded.team
            registries = {v: _index_existing_targets(r) for v, r in team.roots.items()}

            def target_obj(hname: str, tkey: str) -> Any:
                return registries[team.handoffs[hname].target_mm].get(tkey)

            def ek(obj: Any) -> str | None:
                try:
                    return element_key(obj)
                except Exception:  # noqa: BLE001 - unkeyed element
                    return None

            bare = key.split(":", 1)[1] if ":" in key.split("#", 1)[0] else key
            upstream = []
            for hname, tm in team.traces.items():
                for link in tm.links():
                    obj = target_obj(hname, link.target_key)
                    if link.target_key == key or (obj is not None and ek(obj) == bare):
                        upstream.append({"handoff": hname, "rule": link.rule, "sources": link.source_keys})

            downstream = []
            frontier, seen = [bare], {bare}
            while frontier:
                cur = frontier.pop(0)
                for hname, tm in team.traces.items():
                    for link in tm.find_by_source_key(cur):
                        obj = target_obj(hname, link.target_key)
                        tkey = ek(obj) if obj is not None else None
                        downstream.append({
                            "from": cur,
                            "handoff": hname,
                            "target_key": link.target_key,
                            "target": f"{team.handoffs[hname].target_mm}:{tkey}" if tkey else link.target_key,
                            "accepted_bindings": sorted(link.stamps),
                        })
                        if transitive and tkey and tkey not in seen:
                            seen.add(tkey)
                            frontier.append(tkey)
            return {"key": key, "upstream": upstream, "downstream": downstream}

    # -- evolution (HOT) ---------------------------------------------------

    def evolve(
        self,
        agent: str,
        view: str,
        view_spec: dict,
        handoff: str,
        rule: str,
        rule_text: str | None = None,
    ) -> dict:
        """Higher-order transformation: add agent `agent` owning new view
        `view`, connected by hand-off `handoff` (rule file `rule`, optionally
        written from `rule_text`). Its fresh trace model makes every existing
        match an obligation for the new agent on the next run."""
        with self._lock:
            loaded = self._require()
            team = loaded.team
            if view in team.views:
                raise WorkspaceError(f"view {view!r} already exists")
            if handoff in team.handoffs:
                raise WorkspaceError(f"hand-off {handoff!r} already exists")
            rule_path = (self.dir / rule).resolve()
            if not _inside(self.dir, rule_path):
                raise WorkspaceError(f"rule path {rule!r} escapes the workspace directory")
            wrote = False
            if rule_text is not None and rule_path.suffix != ".agenthot":
                # rule_text must never create Python: a `uses` clause would load it into the engine
                raise WorkspaceError(f"rule_text can only create a .agenthot rule module, not {rule_path.name!r}")
            if rule_text is not None:
                if rule_path.exists():
                    raise WorkspaceError(f"{rule} already exists; omit rule_text to use it as is")
                rule_path.parent.mkdir(parents=True, exist_ok=True)
                rule_path.write_text(rule_text)
                wrote = True
            try:
                module = self._parse_rule(rule)
                if module.target_mm != view:
                    raise WorkspaceError(f"{rule}: module creates {module.target_mm!r}, expected the new view {view!r}")
                for sm in module.sources:
                    if sm.mm_name not in team.views:
                        raise WorkspaceError(f"{rule}: source view {sm.mm_name!r} does not exist")
                vspec = dict(view_spec or {})
                vspec["owner"] = agent
                try:
                    mm = build_view_metamodel(str(loaded.spec.get("name") or "team"), view, vspec, team.views)
                except SpecError as exc:
                    raise WorkspaceError(f"view spec: {exc}") from exc
                for r in module.rules:
                    for tp in r.to_clause.patterns:
                        try:
                            mm.root_slot_for(tp.type_name)
                        except KeyError:
                            raise WorkspaceError(
                                f"rule {r.name} creates {tp.type_name} but the view has no root slot for it"
                            ) from None
                refs: list[_PendingRef] = []
                root = seed_root(mm, view, vspec.get("seed"), refs)
                apply_hot(team, TeamChange(agent_name=agent, view=mm, view_root=root, handoff_name=handoff, rule_path=rule_path))
                resolve_refs(refs, index_elements(team.roots))
            except Exception:
                if wrote:
                    rule_path.unlink(missing_ok=True)
                self._loaded = None
                raise
            loaded.evolutions.append({
                "agent": agent,
                "view": view,
                "view_spec": vspec,
                "handoff": {"name": handoff, "rule": str(rule_path.relative_to(self.dir))},
            })
            self.save()
            n_matches = sum(
                len(compute_matches(r, {sm.mm_name: team.roots[sm.mm_name] for sm in module.sources},
                                    load_helpers(module.uses, rule_path.parent)))
                for r in module.rules
            )
            return {
                "agent": agent,
                "view": view,
                "handoff": handoff,
                "existing_matches": n_matches,
                "next": "call run (or next_bindings in host mode): the new agent receives obligations for every existing match",
            }
