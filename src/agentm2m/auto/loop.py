"""The AutoM2M loop (Algorithm 1: admission; Algorithm 2: run, attribute, repair).

Only Propose, Revise, ProposeDelta and the bindings inside the runtime call
an LLM; admission, execution structure, attribution lookup and completion
are deterministic.
"""
from __future__ import annotations

import json
import re
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .attribution import FaultReport, attribute_all
from .checker import Diagnostic, check
from .compile import CompileError, Session, compile_team
from .prompts import delta_prompt, propose_prompt, revise_prompt
from .repair import apply_delta, normalize, resample_upstream, retry_bindings
from .typed_team import TeamFormatError, TypedTeam, parse_team
from .vlib import RunContext


@dataclass
class AutoOutcome:
    status: str  # done | failed | not_admitted | compile_error
    deliverables: dict[str, str] = field(default_factory=dict)
    accepted: dict[str, bool] = field(default_factory=dict)
    phi: bool = False
    admission_rounds: int = 0
    diagnostics: list[list[str]] = field(default_factory=list)  # per admission round
    first_team: dict | None = None
    final_team: dict | None = None
    repairs: list[dict] = field(default_factory=list)
    fault_reports: list[dict] = field(default_factory=list)
    attribution_calls: int = 0
    seconds: float = 0.0
    error: str = ""

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        return d


def _json_from(text: str) -> dict | None:
    t = (text or "").strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", t, re.S)
    if m:
        t = m.group(1)
    try:
        v = json.loads(t)
        return v if isinstance(v, dict) else None
    except json.JSONDecodeError:
        i, j = t.find("{"), t.rfind("}")
        if 0 <= i < j:
            try:
                v = json.loads(t[i:j + 1])
                return v if isinstance(v, dict) else None
            except json.JSONDecodeError:
                return None
    return None


class AutoM2M:
    def __init__(self, llm, *, k_adm: int = 3, k_rep: int = 2, r: int = 1, k: int = 3, temperature: float = 0.6,
                 builder_max_tokens: int = 6000, check_enabled: bool = True, repair_enabled: bool = True,
                 workdir: Path | None = None, log=None) -> None:
        self.llm = llm
        self.k_adm, self.k_rep, self.r, self.k = k_adm, k_rep, r, k
        self.temperature = temperature
        self.builder_max_tokens = builder_max_tokens
        self.check_enabled = check_enabled
        self.repair_enabled = repair_enabled
        self.workdir = Path(workdir or tempfile.mkdtemp(prefix="am2m_run_"))
        self.log = log or (lambda *a, **k: None)

    # ---- LLM steps ----------------------------------------------------
    def _builder(self, prompt: str) -> dict | None:
        prev = getattr(self.llm, "role", None)
        if hasattr(self.llm, "role"):
            self.llm.role = "builder"
        try:
            out = self.llm.generate(prompt, temperature=self.temperature, format="json",
                                    max_tokens=self.builder_max_tokens)
        except Exception as exc:  # noqa: BLE001
            self.log("builder error", exc)
            return None
        finally:
            if hasattr(self.llm, "role"):
                self.llm.role = prev
        return _json_from(out)

    def _admit(self, team_json: dict | None, task) -> tuple[TypedTeam | None, list[str], Any]:
        """Check + compile. Returns (team, diagnostics, compiled)."""
        if team_json is None:
            return None, ["W1  the builder output is not a JSON object"], None
        try:
            team = parse_team(normalize(team_json))
        except TeamFormatError as exc:
            return None, [f"W1  {exc}"], None
        diags = [str(d) for d in check(team).diagnostics] if self.check_enabled else []
        if diags:
            return team, diags, None
        try:
            ct = compile_team(team, task, self.workdir / f"team{int(time.time()*1000)%10**8}")
        except (CompileError, Exception) as exc:  # noqa: BLE001
            return team, [f"W1  the team does not compile: {str(exc)[:300]}"], None
        return team, [], ct

    # ---- Algorithm 1 + 2 ----------------------------------------------
    def solve(self, task, task_text: str, bench, *, initial_team: dict | None = None) -> AutoOutcome:
        t0 = time.time()
        out = AutoOutcome(status="not_admitted")
        team_json = initial_team if initial_team is not None else self._builder(propose_prompt(task_text, task.kind))
        out.first_team = team_json
        team, diags, ct = self._admit(team_json, task)
        out.diagnostics.append(diags)
        while diags and self.check_enabled and out.admission_rounds < self.k_adm:
            out.admission_rounds += 1
            team_json = self._builder(revise_prompt(json.dumps(team_json) if team_json else "(no valid JSON)", diags)) or team_json
            team, diags, ct = self._admit(team_json, task)
            out.diagnostics.append(diags)
        out.final_team = team_json
        if ct is None:
            out.status = "not_admitted" if self.check_enabled else "compile_error"
            out.error = "; ".join(diags)[:500]
            out.seconds = time.time() - t0
            return out
        ctx = RunContext(bench)
        session = Session(ct, self.llm, ctx, k=self.k, temperature=self.temperature)
        bench.bind(session.current_bodies)
        res = session.run()
        for j in range(self.k_rep if self.repair_enabled else 0):
            if res.phi:
                break
            if hasattr(self.llm, "role"):
                self.llm.role = "attribution"
            reports, calls = attribute_all(session, res.failures, r=self.r)
            out.attribution_calls += calls
            out.fault_reports += [rep.to_dict() for rep in reports]
            if hasattr(self.llm, "role"):
                self.llm.role = "binding"
            session, info = self._repair(session, reports, task)
            info["round"] = j + 1
            out.repairs.append(info)
            if info["mode"] == "none":
                break
            res = session.run()
        out.deliverables, out.accepted, out.phi = res.deliverables, res.accepted, res.phi
        out.final_team = session.ct.typed.raw
        out.status = "done" if res.phi else "failed"
        out.seconds = time.time() - t0
        return out

    def _repair(self, session: Session, reports: list[FaultReport], task) -> tuple[Session, dict]:
        """ProposeDelta: mechanical remedies where the fault class has one,
        the builder otherwise; every team change passes the checker first."""
        retry, upstream, needs_builder, widen = [], [], [], {}
        for rep in reports:
            if rep.fault_class == "sampling":
                retry.append((rep.handoff, rep.target_key, rep.binding))
            elif rep.fault_class == "upstream" and rep.upstream:
                upstream.append(rep.upstream)
            elif rep.fault_class == "footprint" and rep.widened:
                widen[(rep.rule, rep.binding)] = rep.widened
            elif rep.fault_class in ("specification", "coverage", "validator"):
                needs_builder.append(rep)
        info: dict = {"mode": "none", "faults": [r.fault_class for r in reports], "checked": True}
        cur = session.ct.typed.raw
        new_json = None
        if widen:
            new_json = json.loads(json.dumps(cur))
            for h in new_json.get("handoffs", []):
                for r in h.get("rules", []):
                    for b in r.get("llm", []):
                        add = widen.get((r.get("name"), b.get("feature")))
                        if add:
                            b["footprint"] = list(b.get("footprint", [])) + [p for p in add if p not in b.get("footprint", [])]
        if needs_builder:
            proposed = self._builder(delta_prompt(json.dumps(new_json or cur), [r.summary() for r in needs_builder]))
            if proposed is not None:
                new_json = proposed
        if new_json is not None:
            try:
                new_team = parse_team(normalize(new_json))
                diags = check(new_team).diagnostics
            except TeamFormatError as exc:
                diags = [Diagnostic("W1", "format", str(exc))]
            info["delta_diagnostics"] = [str(d) for d in diags]
            if json.dumps(normalize(new_json), sort_keys=True) == json.dumps(normalize(cur), sort_keys=True):
                info["noop_delta"] = True
            elif not diags:
                try:
                    ar = apply_delta(session, new_team, task)
                    session = ar.session
                    info.update(mode=ar.mode, kept=ar.kept, before=ar.before, invalidated=ar.invalidated)
                except Exception as exc:  # noqa: BLE001
                    info["apply_error"] = str(exc)[:300]
            else:
                info["checked"] = False  # delta rejected by the checker: not applied
        n_retry = retry_bindings(session, retry)
        n_up = sum(resample_upstream(session, u["handoff"], u["target_key"], u["binding"]) for u in upstream)
        if info["mode"] == "none" and (n_retry or n_up):
            info["mode"] = "retry"
        info.update(retried=n_retry, upstream_resampled=n_up)
        if info["mode"] == "none" and not needs_builder and not widen:
            # nothing actionable (e.g. a validator fault): rerun once with fresh budgets
            n = retry_bindings(session, [(h, l.target_key, b) for h, t in session.ct.team.traces.items()
                                         for l in t.links() for b in list(l.failed_stamps)])
            info.update(mode="retry" if n else "none", retried=n)
        return session, info
