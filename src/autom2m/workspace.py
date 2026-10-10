"""A persisted AutoM2M session: task, typed team, run state and history under
`.agentm2m/auto/`, so the loop can be driven step by step from Claude Code
(MCP), the CLI or the hosted service, resumed and inspected.

    task.json      the lifted task (pywork.Task)
    team.json      the admitted typed team
    proposal.json  the latest proposal the checker rejected (for the revise prompt)
    state.json     view models + trace links of the running team (store.dump_models)
    history.json   admission rounds, runs, fault reports and deltas

Host mode (the default) is AutoM2M without an LLM API key: the *host*
(Claude) acts as the builder (`builder_prompt` -> `submit_team`) and fills
the stochastic bindings (`next_bindings` -> `submit_binding`); admission,
structure, acceptance and φ stay with the checker and the engine. With an
engine backend, `run` samples bindings itself and `solve` runs the whole
loop (Algorithms 1 and 2) unattended.
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from ..config import LLMConfig
from ..engine.trace import TraceLink, TraceModel
from ..llm.base import LLMBackend
from ..llm.factory import make_backend
from ..llm.host_backend import HostBackend
from ..store import dump_models, load_models
from ..workspace import WORKSPACE_DIRNAME, WorkspaceError
from .attribution import attribute_all
from .checker import CheckResult, check
from .compile import CompileError, Session, compile_team
from .loop import AutoM2M
from .prompts import delta_prompt, propose_prompt, revise_prompt
from .pywork import PyWorkbench, Task, TaskSourceError, assemble, task_from_source
from .repair import apply_delta, normalize
from .typed_team import TeamFormatError, parse_team
from .vlib import RunContext

AUTO_DIRNAME = "auto"
STATE_VERSION = 1


class AutoWorkspaceError(WorkspaceError):
    """An actionable error for the caller (shown verbatim by the MCP server)."""


def check_report(res: CheckResult) -> dict:
    return {
        "admitted": res.admitted,
        "violated": sorted(res.conds()),
        "diagnostics": [{"cond": d.cond, "element": d.element, "message": d.message} for d in res.diagnostics],
        "warnings": [{"cond": d.cond, "element": d.element, "message": d.message} for d in res.warnings],
        "report": res.report(),
    }


def check_team_json(team_json: Any, *, naive: bool = False, python_goal: bool = False) -> dict:
    """W1–W6 on a typed team (dict or JSON text), as written. python_goal=True
    first replaces the goal view with the fixed Python-methods Goal view, as
    admission into a workspace does (the task is lifted into it)."""
    if isinstance(team_json, str):
        try:
            team_json = json.loads(team_json)
        except json.JSONDecodeError as exc:
            return check_report(CheckResult(_format_diag(f"the team is not valid JSON: {exc}")))
    if not isinstance(team_json, dict):
        return check_report(CheckResult(_format_diag("the team must be a JSON object")))
    return check_report(check(normalize(team_json) if python_goal else team_json, anchored=not naive))


def _format_diag(msg: str):
    from .checker import Diagnostic

    return [Diagnostic("W1", "format", msg)]


class AutoWorkspace:
    def __init__(
        self,
        project_dir: str | Path,
        *,
        backend: str | None = None,
        model: str | None = None,
        llm: LLMBackend | None = None,
        k: int = 3,
        temperature: float = 0.6,
        max_passes: int = 4,
    ) -> None:
        self.project_dir = Path(project_dir).resolve()
        self.dir = self.project_dir / WORKSPACE_DIRNAME / AUTO_DIRNAME
        self.backend_name = (backend or os.getenv("AGENTM2M_LLM") or "host").strip().lower()
        self.model = model or os.getenv("AGENTM2M_MODEL") or None
        self._llm = llm
        if llm is not None:
            self.backend_name = getattr(llm, "name", self.backend_name)
        self.k, self.temperature, self.max_passes = k, temperature, max_passes
        self._lock = threading.RLock()
        self._session: Session | None = None
        self._fingerprint: tuple | None = None

    # ------------------------------------------------------------------
    # files
    # ------------------------------------------------------------------

    def _p(self, name: str) -> Path:
        return self.dir / name

    def _read(self, name: str, default: Any = None) -> Any:
        p = self._p(name)
        if not p.is_file():
            return default
        return json.loads(p.read_text())

    def _write(self, name: str, data: Any) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self._p(name + ".tmp")
        tmp.write_text(json.dumps(data, indent=1, sort_keys=True, default=str))
        os.replace(tmp, self._p(name))

    def _drop(self, *names: str) -> None:
        for n in names:
            self._p(n).unlink(missing_ok=True)

    def _disk_fingerprint(self) -> tuple:
        return tuple(self._p(n).stat().st_mtime_ns if self._p(n).exists() else 0
                     for n in ("task.json", "team.json", "state.json"))

    def _event(self, event: str, **data: Any) -> None:
        hist = self._read("history.json", [])
        hist.append({"t": round(time.time(), 3), "kind": event, **data})
        self._write("history.json", hist[-200:])

    @property
    def is_host(self) -> bool:
        return self.backend_name == "host"

    def llm(self) -> LLMBackend:
        if self._llm is None:
            cfg = LLMConfig.from_env()
            self._llm = make_backend(cfg, override_provider=self.backend_name, override_model=self.model)
        return self._llm

    def task(self) -> Task:
        d = self._read("task.json")
        if d is None:
            raise AutoWorkspaceError("no task yet: call auto_task_set with the Python skeleton to implement")
        return Task.from_dict(d)

    def team_json(self) -> dict | None:
        return self._read("team.json")

    # ------------------------------------------------------------------
    # the running session (rebuilt from disk when the files changed)
    # ------------------------------------------------------------------

    def _new_session(self, team_json: dict, task: Task, state: dict | None) -> Session:
        try:
            team = parse_team(normalize(team_json))
            ct = compile_team(team, task, self.dir / "compiled")
        except (TeamFormatError, CompileError) as exc:
            raise AutoWorkspaceError(f"the stored team does not compile: {exc}") from exc
        wb = PyWorkbench(task)
        ctx = RunContext(wb)
        llm = HostBackend() if self.is_host else self.llm()
        s = Session(ct, llm, ctx, k=self.k, temperature=self.temperature, max_passes=self.max_passes)
        wb.bind(s.current_bodies)
        if state:
            if state.get("version") != STATE_VERSION:
                raise AutoWorkspaceError(f"unsupported auto state version {state.get('version')!r}")
            roots = load_models(state.get("models", {}), ct.team.views)
            for v, root in roots.items():
                ct.team.roots[v] = root
            for hname, payload in (state.get("traces") or {}).items():
                if hname in ct.team.traces:
                    tm = TraceModel(handoff=hname)
                    for d in payload.get("links", []):
                        tm.put(TraceLink.from_dict(d))
                    ct.team.traces[hname] = tm
            ctx.last_rejected.update(state.get("last_rejected") or {})
        return s

    def _require(self) -> Session:
        if self._session is not None and self._fingerprint == self._disk_fingerprint():
            return self._session
        team_json = self.team_json()
        if team_json is None:
            raise AutoWorkspaceError("no admitted team yet: get a builder prompt with auto_propose, then submit the "
                                     "typed team with auto_submit_team")
        self._session = self._new_session(team_json, self.task(), self._read("state.json"))
        self._fingerprint = self._disk_fingerprint()
        return self._session

    def _save(self, s: Session) -> None:
        self._write("team.json", s.ct.typed.raw)
        self._write("state.json", {
            "version": STATE_VERSION,
            "models": dump_models(s.ct.team.roots),
            "traces": {n: {"handoff": n, "links": [l.to_dict() for l in tm.links()]}
                       for n, tm in s.ct.team.traces.items()},
            "last_rejected": dict(s.ctx.last_rejected),
        })
        self._session = s
        self._fingerprint = self._disk_fingerprint()

    # ------------------------------------------------------------------
    # task
    # ------------------------------------------------------------------

    def set_task(self, source: str, description: str = "") -> dict:
        """Lift a Python skeleton (a class with stub methods, or a stub function)
        into the goal model. Replacing the task keeps the team but discards
        the run state (it was derived from the old goal model)."""
        with self._lock:
            try:
                task = task_from_source(source, description=description)
            except TaskSourceError as exc:
                raise AutoWorkspaceError(str(exc)) from None
            self._write("task.json", task.to_dict())
            self._drop("state.json")
            self._session = None
            self._event("task", entry=task.entry, task_kind=task.kind, methods=[m.name for m in task.methods])
            return {"task": self._task_summary(task), "team": self.team_json() is not None,
                    "next": "auto_propose to get the builder prompt" if self.team_json() is None else "auto_run"}

    @staticmethod
    def _task_summary(task: Task) -> dict:
        return {"entry": task.entry, "kind": task.kind, "description": task.description[:300],
                "methods": [{"name": m.name, "signature": m.signature, "examples": bool(m.examples)} for m in task.methods]}

    # ------------------------------------------------------------------
    # Steps A–C: propose, check, admit
    # ------------------------------------------------------------------

    def check(self, team_json: Any = None, *, naive: bool = False) -> dict:
        with self._lock:
            if team_json is None:
                team_json = self.team_json() or self._read("proposal.json")
                if team_json is None:
                    raise AutoWorkspaceError("no team to check: pass team_json, or submit a team first")
            return check_team_json(team_json, naive=naive, python_goal=True)

    def builder_prompt(self, mode: str = "auto") -> dict:
        """The prompt for the builder (the host, in host mode): propose a team,
        revise a rejected one from its diagnostics, or a delta after φ failed."""
        with self._lock:
            task = self.task()
            proposal = self._read("proposal.json")
            team = self.team_json()
            if mode == "auto":
                mode = "revise" if proposal else ("delta" if team else "propose")
            if mode == "propose":
                prompt = propose_prompt(task.prompt, task.kind)
            elif mode == "revise":
                if not proposal:
                    raise AutoWorkspaceError("nothing to revise: no rejected proposal")
                prompt = revise_prompt(json.dumps(proposal["team"]), proposal["diagnostics"])
            elif mode == "delta":
                if not team:
                    raise AutoWorkspaceError("no admitted team to change")
                faults = self._read("faults.json", [])
                if not faults:
                    att = self.attribute()
                    faults = [f["summary"] for f in att["faults"]]
                prompt = delta_prompt(json.dumps(team), faults or ["(no located fault; φ fails: see auto_status)"])
            else:
                raise AutoWorkspaceError(f"unknown mode {mode!r} (auto|propose|revise|delta)")
            return {
                "mode": mode,
                "prompt": prompt,
                "instructions": "Act as the team builder: answer the prompt with ONE typed-team JSON object and pass it "
                                "to auto_submit_team. The deterministic checker (W1–W6) decides admission.",
            }

    def submit_team(self, team_json: Any) -> dict:
        """Check a proposed team; admit it (fresh run state) or, when a team
        is already running, apply it as a checked delta (in-place / hot /
        rebuild, keeping accepted values where possible)."""
        with self._lock:
            if isinstance(team_json, str):
                try:
                    team_json = json.loads(team_json)
                except json.JSONDecodeError as exc:
                    team_json = None
                    err = f"the team is not valid JSON: {exc}"
            if not isinstance(team_json, dict):
                rep = check_report(CheckResult(_format_diag(locals().get("err") or "the team must be a JSON object")))
                return {**rep, "next": "fix the JSON and resubmit"}
            task = self.task()
            team_json = normalize(team_json)
            rep = check_team_json(team_json, python_goal=True)
            rounds = len([e for e in self._read("history.json", []) if e["kind"] == "admission"])
            if not rep["admitted"]:
                self._write("proposal.json", {"team": team_json, "diagnostics": [d["cond"] + "  " + d["message"]
                                                                                  for d in rep["diagnostics"]]})
                self._event("admission", admitted=False, round=rounds + 1, violated=rep["violated"])
                return {**rep, "next": "fix every diagnostic and resubmit (auto_propose gives a revise prompt)"}
            new_team = parse_team(team_json)
            try:
                if self.team_json() is None:
                    ct_dir = self.dir / "compiled"
                    shutil.rmtree(ct_dir, ignore_errors=True)
                    s = self._new_session(team_json, task, None)
                    mode = "admitted"
                    kept = 0
                else:
                    cur = self._require()
                    if json.dumps(normalize(cur.ct.typed.raw), sort_keys=True) == json.dumps(team_json, sort_keys=True):
                        self._drop("proposal.json")
                        return {**rep, "mode": "noop", "next": "unchanged team; auto_run"}
                    ar = apply_delta(cur, new_team, task)
                    s, mode, kept = ar.session, ar.mode, ar.kept
            except (CompileError, AutoWorkspaceError) as exc:
                self._event("admission", admitted=False, round=rounds + 1, violated=["W1"])
                return {**check_report(CheckResult(_format_diag(f"the team does not compile: {str(exc)[:300]}"))),
                        "next": "fix and resubmit"}
            self._save(s)
            self._drop("proposal.json", "faults.json")
            self._event("admission", admitted=True, round=rounds + 1, mode=mode, kept=kept)
            return {**rep, "mode": mode, "kept_values": kept,
                    "agents": list(new_team.agents), "handoffs": [h.name for h in new_team.handoffs],
                    "next": "auto_run, then fill bindings with auto_next_bindings / auto_submit_binding" if self.is_host
                    else "auto_run"}

    # ------------------------------------------------------------------
    # Step D: run, bindings, φ
    # ------------------------------------------------------------------

    def _split_pending(self, s: Session) -> tuple[list, list]:
        """Host mode: (ready, blocked) pending bindings. Generated footprints are
        rendered text, so the engine cannot see that one reads a not-yet-filled
        upstream value; a binding is blocked when a footprint path navigates
        from a source object that still has an unaccepted LLM value."""
        if not s.last_report:
            return [], []
        incomplete = s.rt.unstamped_targets()
        ready, blocked = [], list(s.last_report.blocked)
        for p in s.last_report.pending:
            meta = s.ct.bindings.get((p.rule, p.binding))
            try:
                match = s.rt._locate(p.target_key, p.binding)[4]
            except KeyError:
                match = None
            srcs = match.bindings if match is not None else {}
            waits = meta is not None and any(
                "." in path and getattr(srcs.get(path.split(".")[0]), "_amt_target_key", None) in incomplete
                for path in meta.footprint)
            (blocked if waits else ready).append(p)
        return ready, blocked

    def _result(self, s: Session, res) -> dict:
        pending, blocked = self._split_pending(s)
        return {
            "backend": self.backend_name,
            "phi": res.phi,
            "passes": res.passes,
            "accepted_values": res.accepted_values,
            "pending": len(pending),
            "blocked_on_upstream": len(blocked),
            "pending_preview": [{"target_key": p.target_key, "binding": p.binding, "rule": p.rule,
                                 "agent": self._owner(s, p.rule, p.binding)} for p in pending[:10]],
            "failures": [self._failure(s, f) for f in res.failures[:20]],
            "open_clauses": sorted({f.clause for f in res.failures}),
            "deliverables_accepted": res.accepted,
        }

    @staticmethod
    def _owner(s: Session, rule: str, binding: str) -> str | None:
        meta = s.ct.bindings.get((rule, binding))
        return meta.owner if meta else None

    def _failure(self, s: Session, f) -> dict:
        return {"clause": f.clause, "handoff": f.handoff, "rule": f.rule, "target_key": f.target_key,
                "binding": f.binding, "goal_element": f.goal_element, "reason": (f.reason or "")[:300],
                "agent": self._owner(s, f.rule, f.binding) if f.rule and f.binding else None}

    def run(self) -> dict:
        """Run every hand-off to a fixpoint and evaluate φ. In host mode no LLM
        is called: open bindings come back as pending."""
        with self._lock:
            s = self._require()
            res = s.run()
            self._save(s)
            out = self._result(s, res)
            self._event("run", phi=res.phi, pending=out["pending"], clauses=out["open_clauses"])
            if res.phi:
                out["next"] = "done: auto_deliverable returns the assembled code"
            elif out["pending"]:
                out["next"] = "fill the pending bindings: auto_next_bindings -> auto_submit_binding"
            else:
                out["next"] = "φ fails: auto_attribute locates the fault, auto_propose(mode='delta') proposes a repair"
            return out

    def next_bindings(self, agent: str | None = None, limit: int = 5) -> dict:
        with self._lock:
            if not self.is_host:
                raise AutoWorkspaceError(f"backend is {self.backend_name!r}: bindings are sampled by the engine; "
                                         "use auto_run")
            s = self._require()
            s.run()
            self._save(s)
            items = []
            ready, blocked = self._split_pending(s)
            for p in ready:
                owner = self._owner(s, p.rule, p.binding)
                if agent and owner != agent:
                    continue
                items.append(p.to_dict() | {"agent": owner, "max_attempts": self.k})
            return {
                "bindings": items[: max(1, limit)],
                "remaining": max(0, len(items) - limit),
                "blocked_on_upstream": len(blocked),
                "instructions": "Answer each binding ONLY from its prompt (it holds the whole allowed footprint), in the "
                                "format the prompt asks for, then call auto_submit_binding with its target_key, binding "
                                "and footprint_version.",
            }

    def submit_binding(self, target_key: str, binding: str, value: str, footprint_version: str | None = None) -> dict:
        with self._lock:
            s = self._require()
            toks = s._enter()
            try:
                result = s.rt.submit_binding(target_key, binding, value, footprint_version)
            except KeyError as exc:
                raise AutoWorkspaceError(str(exc.args[0] if exc.args else exc)) from None
            finally:
                s._exit(toks)
            self._save(s)
            return result

    # ------------------------------------------------------------------
    # Steps E–F: attribution and repair
    # ------------------------------------------------------------------

    def attribute(self, limit: int = 2) -> dict:
        """Locate each failed φ clause (rule, binding, agent). With an engine
        backend, also classify it by bounded replay; in host mode replays
        would need an LLM, so faults are located but left unclassified."""
        with self._lock:
            s = self._require()
            res = s.run()
            if res.phi:
                return {"phi": True, "faults": []}
            if self.is_host:
                faults = [self._failure(s, f) | {"fault_class": "unclassified",
                                                 "summary": f"{f.clause} at {f.handoff}/{f.rule}.{f.binding} "
                                                            f"(agent {self._owner(s, f.rule, f.binding)}): {f.reason[:200]}"}
                          for f in res.failures[: max(1, limit) * 4]]
                note = "host mode: located only; classification replays need an engine LLM"
            else:
                reports, calls = attribute_all(s, res.failures, r=1, limit=limit)
                faults = [r.to_dict() | {"summary": r.summary()} for r in reports]
                note = f"{calls} replay call(s)"
            self._save(s)
            self._write("faults.json", [f["summary"] for f in faults])
            self._event("attribution", faults=[f.get("fault_class") for f in faults])
            return {"phi": False, "faults": faults, "note": note}

    def apply_delta(self, team_json: Any) -> dict:
        return self.submit_team(team_json)

    # ------------------------------------------------------------------
    # results, status, unattended solve
    # ------------------------------------------------------------------

    def deliverable(self) -> dict:
        with self._lock:
            s = self._require()
            task = self.task()
            accepted = s.current_bodies()
            best = dict(accepted)
            for name, val in s.ctx.last_rejected.items():
                if name not in best:
                    fn = s.ctx.bench.extract_function(val, name)
                    if fn:
                        best[name] = fn
            return {
                "entry": task.entry,
                "code": assemble(task, best),
                "accepted": {m.name: m.name in accepted for m in task.methods},
                "complete": all(m.name in accepted for m in task.methods),
            }

    def status(self) -> dict:
        with self._lock:
            out: dict[str, Any] = {"project_dir": str(self.project_dir), "backend": self.backend_name,
                                   "dir": str(self.dir)}
            task = self._read("task.json")
            out["task"] = self._task_summary(Task.from_dict(task)) if task else None
            proposal = self._read("proposal.json")
            out["rejected_proposal"] = {"diagnostics": proposal["diagnostics"]} if proposal else None
            team = self.team_json()
            if team is None:
                out["team"] = None
                out["next"] = "auto_task_set" if task is None else "auto_propose -> auto_submit_team"
            else:
                out["team"] = {"name": team.get("name"), "agents": [a.get("name") for a in team.get("agents", [])],
                               "views": [v for v in team.get("views", {}) if v != "Goal"],
                               "writes": team.get("writes"),
                               "handoffs": [h.get("name") for h in team.get("handoffs", [])]}
                if task is not None:
                    s = self._require()
                    res = s.run() if self.is_host else s.evaluate(s.last_report)
                    out["run"] = self._result(s, res)
                    out["phi"] = res.phi
            hist = self._read("history.json", [])
            out["history"] = hist[-15:]
            out["admission_rounds"] = len([e for e in hist if e["kind"] == "admission"])
            return out

    def solve(self, source: str | None = None, *, k_adm: int = 3, k_rep: int = 2) -> dict:
        """Algorithms 1 and 2 unattended, with an engine LLM as builder and
        binding sampler. Starts from the stored team if there is one."""
        with self._lock:
            if self.is_host:
                raise AutoWorkspaceError("solve needs an engine LLM (backend is 'host'); in host mode drive the loop "
                                         "with auto_propose / auto_submit_team / auto_run / auto_next_bindings")
            if source:
                self.set_task(source)
            task = self.task()
            loop = AutoM2M(self.llm(), k_adm=k_adm, k_rep=k_rep, k=self.k, temperature=self.temperature,
                           workdir=self.dir / "runs")
            out = loop.solve(task, task.prompt, PyWorkbench(task), initial_team=self.team_json())
            if loop.session is not None:
                self._save(loop.session)
            self._event("solve", status=out.status, phi=out.phi, rounds=out.admission_rounds,
                        repairs=[r.get("mode") for r in out.repairs])
            d = out.to_dict()
            d.pop("first_team", None)
            d["code"] = assemble(task, {n: f for n, v in out.deliverables.items()
                                        if (f := PyWorkbench(task).extract_function(v, n))})
            return d

    def reset(self) -> dict:
        with self._lock:
            shutil.rmtree(self.dir, ignore_errors=True)
            self._session = None
            return {"reset": True}
