"""RQ3: fault injection into runs of an admitted typed team, trace-based
attribution versus transcript-based attribution, and checked repair.

Each run injects one fault into the hand-written admitted reference team
(teams/classeval_reference.json: Tester -> MethodTest.code, Developer ->
MethodImpl.code) on one ClassEval task. Ground truth is known by construction:

  upstream       Method2Test.code of one method gets a wrong value, accepted by a
                 weakened ("weak") upstream validator; responsible: Tester
  footprint      Method2Impl.code's footprint narrowed to the method signature
                 (one hop too narrow); responsible: Developer
  specification  Method2Impl.code gets an unsatisfiable validator; responsible: Developer
  sampling       the Developer's sampler is degraded for one method during the run
                 (answers carry no code); responsible: Developer
  validator      Method2Impl.code gets a nondeterministic validator; responsible:
                 the validator, not an agent

    python -m evaluation.rq3.inject --model qwen2.5-coder:7b --tasks first:60 --workers 4
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OLLAMA = os.environ.get("AM2M_OLLAMA", "http://127.0.0.1:11435")
FAULTS = ["upstream", "footprint", "specification", "sampling", "validator"]
WRONG_TESTS = (
    "```python\nimport unittest\n\nclass InjectedTest(unittest.TestCase):\n"
    "    def test_{name}(self):\n        # injected upstream fault\n        self.assertEqual('{name}', 'expected-value')\n```"
)
DEGRADED = "I am not able to write this method."


def injected_team(fault: str) -> dict:
    team = json.loads((ROOT / "teams" / "classeval_reference.json").read_text())
    test_rule = team["handoffs"][0]["rules"][0]
    impl = team["handoffs"][1]["rules"][0]["llm"][0]
    if fault == "upstream":
        test_rule["llm"][0]["validator"] = [{"id": "weak"}]
    elif fault == "footprint":
        impl["footprint"] = ["m.signature"]
    elif fault == "specification":
        impl["validator"] = impl["validator"] + [{"id": "unsat"}]
    elif fault == "validator":
        impl["validator"] = impl["validator"] + [{"id": "flaky"}]
    return team


class Injector:
    """LLM wrapper that injects the sampling / upstream fault during the run
    phase only (attribution replays and repairs see the real model)."""

    def __init__(self, inner, fault: str, method: str, registry_ref):
        self.inner = inner
        self.fault = fault
        self.method = method
        self.registry_ref = registry_ref
        self.active = True
        self.injected = 0

    def __getattr__(self, k):
        return getattr(self.inner, k)

    @property
    def role(self):
        return self.inner.role

    @role.setter
    def role(self, v):
        self.inner.role = v

    def _is(self, prompt: str, key: str) -> bool:
        head = self.registry_ref().get(key, "\0")
        return prompt.startswith(head) and (f"[m.name]\n{self.method}\n" in prompt
                                            or f"[m.signature]\ndef {self.method}(" in prompt)

    def generate(self, prompt: str, **kw):
        if self.active and self.inner.role == "binding":
            if self.fault == "upstream" and self._is(prompt, "Method2Test.code") and not self.injected:
                self.injected += 1
                out = WRONG_TESTS.format(name=self.method)
                self.inner.texts.append({"role": "binding", "prompt": prompt, "output": out, "injected": True})
                return out
            if self.fault == "sampling" and self._is(prompt, "Method2Impl.code"):
                self.injected += 1
                self.inner.texts.append({"role": "binding", "prompt": prompt, "output": DEGRADED, "injected": True})
                return DEGRADED
        return self.inner.generate(prompt, **kw)


def run_injected(task_id: str, fault: str, model: str, seed: int = 1, r: int = 1) -> dict:
    from agentm2m.auto.attribution import attribute_all
    from agentm2m.auto.compile import Session, compile_team
    from agentm2m.auto.repair import normalize
    from agentm2m.auto.typed_team import parse_team
    from agentm2m.auto.vlib import RunContext
    from agentm2m.llm.metered import MeteredBackend
    from agentm2m.llm.ollama_backend import OllamaBackend
    from evaluation.benchmarks import tasks as T
    from evaluation.harness.workbench import TaskWorkbench

    task = {t.task_id: t for t in T.load("classeval")}[task_id]
    method = task.methods[0].name
    base = MeteredBackend(OllamaBackend(OLLAMA, model, seed=seed, num_ctx=16384, timeout=900, max_tokens=3072), keep_text=True)
    team = parse_team(normalize(injected_team(fault)))
    workdir = Path(os.environ.get("TMPDIR", "/tmp")) / f"rq3_{model.replace(':', '_')}_{fault}_{task_id}"
    ct = compile_team(team, task, workdir)
    llm = Injector(base, fault, method, lambda: ct.registry)
    wb = TaskWorkbench(task)
    ctx = RunContext(wb)
    events = base.texts  # chronological: LLM calls and validator verdicts

    import agentm2m.auto.rt_helpers as RH

    orig_record = RH._recording

    session = Session(ct, llm, ctx, k=3)
    wb.bind(session.current_bodies)
    base.role = "binding"
    t0 = time.time()
    # record validator verdicts into the same event list
    from agentm2m.auto import vlib

    wrapped = {}
    for vid, fn in vlib.IMPLEMENTATIONS.items():
        def mk(fn=fn, vid=vid):
            def w(value, *args):
                v = fn(value, *args)
                goal = next((getattr(a, "name", None) for a in args if hasattr(a, "eClass")), None)
                events.append({"role": "check", "validator": vid, "goal": goal, "ok": bool(v),
                               "reason": getattr(v, "reason", "")})
                return v
            return w
        wrapped[vid] = mk()
    for vid, w in wrapped.items():
        setattr(RH, f"v_{vid}", orig_record(w))
    try:
        res = session.run()
        run_tokens = base.totals()
        row = {"task": task_id, "fault": fault, "model": model, "method": method, "phi": res.phi,
               "manifested": not res.phi, "injected": llm.injected}
        if res.phi:
            row["run_tokens"] = run_tokens
            return row
        llm.active = False
        base.role = "attribution"
        n_before = len(base.calls)
        reports, calls = attribute_all(session, res.failures, r=r, limit=1)
        rep = reports[0] if reports else None
        row["attribution"] = rep.to_dict() if rep else None
        row["attribution_calls"] = calls
        row["transcript"] = [e for e in events if e.get("role") in ("binding", "check")]
        row["registry"] = dict(ct.registry)
        row["owners"] = {"Method2Test.code": "Tester", "Method2Impl.code": "Developer"}
        row["failures"] = [f.__dict__ for f in res.failures][:20]
        row["run_tokens"] = run_tokens
        row["attr_tokens"] = base.totals("attribution")
        _ = n_before
        # ---- RQ3c: one checked repair, applied in place vs. rebuilt from scratch
        try:
            from agentm2m.auto.loop import AutoM2M

            am = AutoM2M(base, workdir=workdir / "repair")
            base.role = "binding"
            t_before = base.totals()["out_tokens"]
            session2, info = am._repair(session, reports, task)
            repaired_team = session2.ct.typed
            res2 = session2.run()
            ip_tok = base.totals()["out_tokens"] - t_before
            code_ip = T.assemble(task, {k: (T.extract_function(v, k) or v) for k, v in res2.deliverables.items()})
            row["repair"] = {"info": info, "inplace": {"phi": res2.phi, "success": T.score(task, code_ip)["success"],
                                                        "out_tokens": ip_tok}}
            # rebuild: same repaired team, compiled and run from scratch (no kept values)
            ct_rb = compile_team(repaired_team, task, workdir / "rebuild")
            ctx_rb = RunContext(TaskWorkbench(task))
            s_rb = Session(ct_rb, base, ctx_rb, k=3)
            ctx_rb.bench.bind(s_rb.current_bodies)
            t1 = base.totals()["out_tokens"]
            res_rb = s_rb.run()
            code_rb = T.assemble(task, {k: (T.extract_function(v, k) or v) for k, v in res_rb.deliverables.items()})
            row["repair"]["rebuild"] = {"phi": res_rb.phi, "success": T.score(task, code_rb)["success"],
                                        "out_tokens": base.totals()["out_tokens"] - t1}
        except Exception as exc:  # noqa: BLE001
            row["repair_error"] = f"{type(exc).__name__}: {exc}"
        return row
    finally:
        for vid in wrapped:
            setattr(RH, f"v_{vid}", orig_record(vlib.IMPLEMENTATIONS[vid]))
        row_time = time.time() - t0
        if "row" in locals():
            row["seconds"] = round(row_time, 1)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--tasks", default="first:50")
    ap.add_argument("--faults", default=",".join(FAULTS))
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args(argv)
    from evaluation.benchmarks import tasks as T

    ids = [t.task_id for t in T.load("classeval")]
    if a.tasks.startswith("first:"):
        ids = ids[: int(a.tasks.split(":")[1])]
    elif a.tasks.startswith("file:"):
        ids = Path(a.tasks[5:]).read_text().split()
    out = ROOT / "results" / "rq3" / f"inject__{a.model.replace(':', '_')}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if out.exists():
        for l in out.read_text().splitlines():
            r = json.loads(l)
            if "error" not in r:
                done.add((r["task"], r["fault"]))
    jobs = [(t, f) for f in a.faults.split(",") for t in ids if (t, f) not in done]
    print(f"{len(jobs)} injected runs", flush=True)
    with ProcessPoolExecutor(a.workers) as ex, out.open("a") as fh:
        futs = {ex.submit(run_injected, t, f, a.model, a.seed): (t, f) for t, f in jobs}
        for i, fu in enumerate(as_completed(futs), 1):
            t, f = futs[fu]
            try:
                row = fu.result()
            except Exception as exc:  # noqa: BLE001
                row = {"task": t, "fault": f, "model": a.model, "error": f"{exc}", "trace": traceback.format_exc()[-1500:]}
            fh.write(json.dumps(row, default=str) + "\n")
            fh.flush()
            att = (row.get("attribution") or {})
            print(f"[{i}/{len(jobs)}] {t} {f} manifested={row.get('manifested')} -> {att.get('fault_class')} "
                  f"{att.get('rule')}.{att.get('binding')} agent={att.get('agent')} {row.get('error', '')[:80]}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
