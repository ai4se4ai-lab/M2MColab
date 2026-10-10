"""Algorithm 1: build, admit, run, attribute, repair.

  M0 <- Lift(x); Theta <- Propose(x, M0); diag <- Admit(Theta)          (A, B)
  for i = 1..k_adm while diag != {}: Theta <- Revise(Theta, diag); ...  (C)
  if diag != {}: return NotAdmitted(diag)
  for j = 0..k_rep:
      (phi, F) <- RunTeam(Theta, M0)                                     (D)
      if phi: return Done(deliverables)
      if j < k_rep: Delta <- Repair(Theta, {Attribute(f) | f in F})    (E)
                    if Admit(Theta (+) Delta) = {}: Theta <- Apply(Theta, Delta)   (F)
  return Failed(fault reports)

Only Propose, Revise, the delta proposal and the bindings inside the runtime
call an LLM; admission, execution structure, attribution lookup and
completion are deterministic.

Switches give the ablations of the evaluation: `check_enabled=False` is
Typed-NC (the builder's typed team runs on AgentHOT without checker or
repair; re-prompted only if the JSON does not parse; ill-typed bindings
escalate; agent claims in phi are ignored; cycles stop after three passes);
`initial_team` with checker and repair is Typed-Ref.
"""
from __future__ import annotations

import json
import re
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agenthot.compiler import CompileError, compile_team
from agenthot.session import Session

from .attribution import FaultReport, attribute_all
from .checker import CheckResult, check
from .lift import normalize
from .prompts import delta_prompt, propose_prompt, revise_prompt
from .repair import apply_delta, plan, resample_upstream, retry_bindings, widen_delta
from .typed_team import TeamFormatError, TypedTeam, parse_team
from .vlib import RunContext


@dataclass
class AutoOutcome:
    status: str  # done | failed | not_admitted | compile_error
    deliverables: dict[str, str] = field(default_factory=dict)
    accepted: dict[str, bool] = field(default_factory=dict)
    phi: bool = False
    clauses: dict[str, bool] = field(default_factory=dict)
    admission_rounds: int = 0
    admitted_round: int | None = None  # 0 = first proposal
    diagnostics: list[list[str]] = field(default_factory=list)  # per admission round
    first_violation: str | None = None
    unparsable: int = 0  # proposals that were not a JSON object
    checks: int = 0
    check_seconds: float = 0.0
    first_team: dict | None = None
    final_team: dict | None = None
    repairs: list[dict] = field(default_factory=list)
    fault_reports: list[dict] = field(default_factory=list)
    attribution_calls: int = 0
    timing: dict[str, float] = field(default_factory=dict)
    events: list[dict] = field(default_factory=list)  # accepted / rejected values (transcript for coding)
    unchecked_diagnostics: list[str] = field(default_factory=list)  # Typed-NC: what the checker would have said
    escalations: int = 0
    accepted_values: int = 0
    seconds: float = 0.0
    error: str = ""

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def json_from(text: str) -> dict | None:
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


_json_from = json_from  # older name


class AutoM2M:
    def __init__(self, llm, *, k_adm: int = 3, k_rep: int = 2, k: int = 3, n_rep: int = 3, h: int = 2,
                 builder_temperature: float = 0.6, temperature: float = 0.2, builder_max_tokens: int = 4096,
                 check_enabled: bool = True, repair_enabled: bool = True, n_examples: int = 3,
                 attribution_limit: int = 2, builder: str = "v2", k_prop: int = 3, k_rev: int = 2,
                 workdir: Path | None = None, log=None) -> None:
        self.llm = llm
        # builder "v1": one proposal, full-team revisions (the paper's first description);
        # builder "v2": schema-constrained, checker-ranked proposals, localized repair (builder.py)
        self.builder, self.k_prop, self.k_rev = builder, k_prop, k_rev
        self.k_adm, self.k_rep, self.k = k_adm, k_rep, k
        self.n_rep, self.h = n_rep, h
        self.builder_temperature = builder_temperature
        self.temperature = temperature
        self.builder_max_tokens = builder_max_tokens
        self.check_enabled = check_enabled
        self.repair_enabled = repair_enabled
        self.n_examples = n_examples
        self.attribution_limit = attribution_limit
        self.workdir = Path(workdir or tempfile.mkdtemp(prefix="am2m_run_"))
        self.log = log or (lambda *a, **k: None)
        self.session: Session | None = None  # the last run's session (kept for persistence/inspection)
        self._out: AutoOutcome | None = None

    # ---- helpers ------------------------------------------------------
    def _role(self, role: str) -> None:
        if hasattr(self.llm, "role"):
            self.llm.role = role

    def _tick(self, key: str, t0: float) -> None:
        if self._out is not None:
            self._out.timing[key] = self._out.timing.get(key, 0.0) + (time.perf_counter() - t0)

    def _builder(self, prompt: str, step: str = "propose", schema: dict | None = None) -> dict | None:
        prev = getattr(self.llm, "role", None)
        self._role("builder")
        t0 = time.perf_counter()
        try:
            out = self.llm.generate(prompt, temperature=self.builder_temperature, format=schema or "json",
                                    max_tokens=self.builder_max_tokens)
        except Exception as exc:  # noqa: BLE001
            self.log("builder error", exc)
            return None
        finally:
            self._tick(step, t0)
            if prev is not None:
                self._role(prev)
        return json_from(out)

    def _check(self, team: TypedTeam | dict) -> CheckResult:
        res = check(team)
        if self._out is not None:
            self._out.checks += 1
            self._out.check_seconds += res.seconds
        return res

    def _admit(self, team_json: dict | None, task) -> tuple[TypedTeam | None, list[str], Any, str | None]:
        """Parse, check (Alg. 3) and compile. Returns (team, diagnostics, compiled, first violated condition)."""
        if team_json is None:
            if self._out is not None:
                self._out.unparsable += 1
            return None, ["W1  the builder output is not a JSON object"], None, "parse"
        try:
            team = parse_team(normalize(team_json))
        except TeamFormatError as exc:
            return None, [f"W1  {exc}"], None, "W1"
        first = None
        diags: list[str] = []
        if self.check_enabled:
            res = self._check(team)
            diags = [d.with_hint() for d in res.diagnostics]
            first = res.first_cond()
            if diags:
                return team, diags, None, first
        t0 = time.perf_counter()
        try:
            ct = compile_team(team, task, self.workdir / f"team{int(time.time() * 1000) % 10 ** 8}",
                              lenient=not self.check_enabled)
        except (CompileError, Exception) as exc:  # noqa: BLE001
            return team, [f"W1  the team does not compile: {str(exc)[:300]}"], None, "compile"
        finally:
            self._tick("compile", t0)
        return team, [], ct, None

    def _events(self, session: Session) -> list[dict]:
        out = []
        for hname, trace in session.ct.team.traces.items():
            for link in trace.links():
                obj = session.object_by_target_key(link.target_key)
                for (r, bn), meta in session.ct.bindings.items():
                    if r != link.rule:
                        continue
                    if bn in link.stamps and obj is not None:
                        out.append({"agent": meta.owner, "handoff": hname, "binding": f"{r}.{bn}", "target": link.target_key,
                                    "status": "accepted", "value": str(getattr(obj, bn, "") or "")[:1500]})
                    elif bn in link.rejections or bn in link.failed_stamps:
                        rj = link.rejections.get(bn) or {}
                        out.append({"agent": meta.owner, "handoff": hname, "binding": f"{r}.{bn}", "target": link.target_key,
                                    "status": "escalated" if bn in link.failed_stamps else "rejected",
                                    "value": str(rj.get("value", ""))[:1500], "reason": str(rj.get("reason", ""))[:400]})
        return out

    def _run(self, session: Session):
        self._role("binding")
        t0 = time.perf_counter()
        exec_before = session.ctx.exec_seconds
        res = session.run()
        self._tick("run", t0)
        if self._out is not None:
            self._out.timing["validators"] = self._out.timing.get("validators", 0.0) + session.ctx.exec_seconds - exec_before
        return res

    # ---- Algorithm 1 --------------------------------------------------
    def solve(self, task, task_text: str, bench, *, initial_team: dict | None = None,
              admission_only: bool = False) -> AutoOutcome:
        t0 = time.time()
        out = AutoOutcome(status="not_admitted")
        self._out = out
        if initial_team is None and self.check_enabled and self.builder == "v2":
            team_json, team, diags, ct, first = self._build_v2(task, task_text, out)
            out.first_team = out.first_team or team_json
        else:
            team_json = initial_team if initial_team is not None else \
                self._builder(propose_prompt(task_text, task.kind, n_examples=self.n_examples), "propose")
            out.first_team = team_json
            team, diags, ct, first = self._admit(team_json, task)
            out.diagnostics.append(diags)
            out.first_violation = first
        while diags and out.admission_rounds < self.k_adm and not (self.builder == "v2" and initial_team is None
                                                                    and self.check_enabled):
            if not self.check_enabled and team_json is not None:
                break  # Typed-NC re-prompts only when the JSON does not parse
            out.admission_rounds += 1
            prev = json.dumps(team_json) if team_json else "(no valid JSON)"
            if self.check_enabled:
                team_json = self._builder(revise_prompt(prev, diags), "revise") or team_json
            else:
                team_json = self._builder(propose_prompt(task_text, task.kind, n_examples=self.n_examples), "revise")
            team, diags, ct, _first = self._admit(team_json, task)
            out.diagnostics.append(diags)
        out.final_team = team_json
        if not self.check_enabled and team is not None:
            out.unchecked_diagnostics = [str(d) for d in check(team).diagnostics]
        if ct is None:
            out.status = "not_admitted" if self.check_enabled else "compile_error"
            out.error = "; ".join(diags)[:500]
            out.seconds = time.time() - t0
            return out
        out.admitted_round = out.admission_rounds
        if admission_only:
            out.status = "admitted"
            out.seconds = time.time() - t0
            return out
        ctx = RunContext(bench)
        session = Session(ct, self.llm, ctx, k=self.k, temperature=self.temperature,
                          max_passes=4 if self.check_enabled else 3)
        self.session = session
        bench.bind(session.current_bodies)
        res = self._run(session)
        for j in range(self.k_rep if self.repair_enabled else 0):
            if res.phi:
                break
            self._role("attribution")
            ta = time.perf_counter()
            reports, calls = attribute_all(session, res.failures, n_rep=self.n_rep, h=self.h, limit=self.attribution_limit)
            self._tick("attribution", ta)
            out.attribution_calls += calls
            out.fault_reports += [rep.to_dict() for rep in reports]
            self._role("repair")
            tr = time.perf_counter()
            session, info = self._repair(session, reports, task)
            self._tick("repair", tr)
            info["round"] = j + 1
            out.repairs.append(info)
            if info["mode"] == "none":
                break
            self.session = session
            res = self._run(session)
        out.deliverables, out.accepted, out.phi, out.clauses = res.deliverables, res.accepted, res.phi, res.clauses
        out.escalations = len(res.escalations)
        out.accepted_values = res.accepted_values
        out.final_team = session.ct.typed.raw
        out.events = self._events(session)
        out.status = "done" if res.phi else "failed"
        out.seconds = time.time() - t0
        return out

    # ---- the checked builder (v2) --------------------------------------
    def _assess(self, team_json: dict | None):
        """(score, team, diagnostics as Diagnostic objects) without compiling."""
        if team_json is None:
            return (10 ** 6, None, None)
        try:
            team = parse_team(normalize(team_json))
        except TeamFormatError as exc:
            from .checker import Diagnostic

            return (10 ** 5, None, [Diagnostic("W1", "format", str(exc))])
        res = self._check(team)
        return (len(res.diagnostics), team, res.diagnostics)

    def _build_v2(self, task, task_text: str, out: AutoOutcome):
        """Propose k candidates (schema-constrained), keep the best by the
        checker; repair it locally from its diagnostics for up to k_adm
        rounds (k_rev repair samples per round). Returns the admitted
        (compiled) team or the last best candidate with its diagnostics."""
        from .builder import FIX_SCHEMA, TEAM_SCHEMA, apply_fixes, fragments_for, path_catalogue, repair_prompt
        from .prompts import FORMAT, RULES
        from .vlib import catalogue

        prompt = propose_prompt(task_text, task.kind, n_examples=self.n_examples)
        cands = []
        for _ in range(max(1, self.k_prop)):
            tj = self._builder(prompt, "propose", TEAM_SCHEMA)
            if tj is None:
                out.unparsable += 1
            score, team, diags = self._assess(tj)
            cands.append((score, tj, team, diags))
            if score == 0:
                break
        out.first_team = cands[0][1]
        best = min(cands, key=lambda c: c[0])
        out.first_violation = (best[3][0].cond if best[3] else None) if best[1] is not None else "parse"
        out.diagnostics.append([d.with_hint() for d in best[3] or []] if best[1] is not None
                               else ["W1  the builder output is not a JSON object"])
        while best[0] > 0 and out.admission_rounds < self.k_adm and best[1] is not None:
            out.admission_rounds += 1
            frags = fragments_for(best[1], best[3])
            rule_names = [str(v.get("name")) for k, v in frags.items() if "/rules/" in k and isinstance(v, dict)]
            rp = repair_prompt(best[1], best[3], frags, path_catalogue(best[1], rule_names),
                               RULES, FORMAT, catalogue())
            trials = []
            for _ in range(max(1, self.k_rev)):
                fx = self._builder(rp, "revise", FIX_SCHEMA)
                if fx is None:
                    continue
                tj = apply_fixes(best[1], fx.get("fixes"), set(frags))
                score, team, diags = self._assess(tj)
                trials.append((score, tj, team, diags))
                if score == 0:
                    break
            if not trials or min(t[0] for t in trials) >= best[0]:
                # localized repair made no progress: one full-team revision
                tj = self._builder(revise_prompt(json.dumps(best[1]), [d.with_hint() for d in best[3]]), "revise",
                                   TEAM_SCHEMA)
                score, team, diags = self._assess(tj)
                trials.append((score, tj, team, diags))
            cand = min(trials, key=lambda c: c[0])
            if cand[0] <= best[0]:
                best = cand
            out.diagnostics.append([d.with_hint() for d in best[3] or []])
        team_json = best[1]
        if best[0] != 0:
            diags = [d.with_hint() for d in best[3] or []] or ["W1  the builder output is not a JSON object"]
            return team_json, best[2], diags, None, out.first_violation
        team, diags, ct, _first = self._admit(team_json, task)  # compile (the checker already admitted it)
        out.checks -= 1 if self.check_enabled else 0
        return team_json, team, diags, ct, out.first_violation

    # ---- Repair (step F) ----------------------------------------------
    def _repair(self, session: Session, reports: list[FaultReport], task) -> tuple[Session, dict]:
        """The planner chooses the cheapest repair per fault class; every
        change of the team model passes the checker before it is applied."""
        p = plan(reports)
        info: dict = {"mode": "none", "faults": [r.fault_class for r in reports], "checked": True,
                      "source": "mechanical" if not p.builder else "builder"}
        cur = session.ct.typed.raw
        new_json = widen_delta(cur, p.widen) if p.widen else None
        if p.builder:
            proposed = self._builder(delta_prompt(json.dumps(new_json or cur), [r.summary() for r in p.builder]), "delta")
            if proposed is not None:
                new_json = proposed
            info["builder_unchanged"] = proposed is None or \
                json.dumps(normalize(proposed), sort_keys=True) == json.dumps(normalize(cur), sort_keys=True)
        if new_json is not None:
            try:
                new_team = parse_team(normalize(new_json))
                res = self._check(new_team) if self.check_enabled else None
                diags = [str(d) for d in res.diagnostics] if res else []
            except TeamFormatError as exc:
                new_team, diags = None, [f"W1  {exc}"]
            info["delta_diagnostics"] = diags
            if new_team is not None and not diags:
                try:
                    ar = apply_delta(session, new_team, task)
                    session = ar.session
                    if ar.mode != "noop":
                        info.update(mode=ar.mode, kept=ar.kept, before=ar.before, invalidated=ar.invalidated)
                    else:
                        info["noop_delta"] = True
                except Exception as exc:  # noqa: BLE001
                    info["apply_error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
            elif diags:
                info["checked"] = False  # Theta (+) Delta not admitted: Theta unchanged
        n_retry = retry_bindings(session, p.retry)
        n_up = sum(resample_upstream(session, u["handoff"], u["target_key"], u["binding"]) for u in p.resample)
        if info["mode"] == "none" and (n_retry or n_up):
            info["mode"] = "retry" if n_retry and not n_up else "resample"
            info.setdefault("kept", sum(len(l.stamps) for t in session.ct.team.traces.values() for l in t.links()))
        info.update(retried=n_retry, upstream_resampled=n_up)
        if info["mode"] == "none" and not p.builder and not p.widen:
            # nothing actionable (e.g. an unlocated failure): one more run with fresh budgets
            n = retry_bindings(session, [(h, l.target_key, b) for h, t in session.ct.team.traces.items()
                                         for l in t.links() for b in list(l.failed_stamps)])
            info.update(mode="retry" if n else "none", retried=n)
        return session, info
