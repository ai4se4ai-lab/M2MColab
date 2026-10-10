"""RQ4 ground truth: fault injection with known class and responsible binding.

For each ClassEval task, a team (the hand-written Typed-Ref team, or a
builder-generated admitted team from the AutoM2M runs) is run once cleanly.
Each fault class is then injected into a copy of that run (a snapshot), and
only what the injection makes stale is re-sampled (in-place semantics):

  upstream       the tests of one method get an extra wrong assertion; the
                 test validator accepts it (it is too weak to see the error);
                 responsible: the producer of the tests
  footprint      the deliverable binding's footprint is narrowed to the
                 method signature; responsible: that binding
  specification  the deliverable binding gets an unsatisfiable validator
  sampling       the owner's LLM is degraded for one method (each answer is
                 replaced by a broken one with probability 0.6, also during
                 replays), giving pass rates of roughly 0.3-0.6
  validator      the deliverable binding gets a flaky validator; responsible:
                 the validator, not an agent

A fault counts when it manifests (phi fails at the injected binding). Then
trace-based attribution runs in exhaustive mode, so the classification under
one replay and under the adaptive budget come from the same draws; the full
run (every LLM call with its input, output and check verdict) is stored for
the transcript-based methods (transcript.py).

    python -m evaluation.rq4.inject --team ref --per-class 40
    python -m evaluation.rq4.inject --team builder --per-class 40
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import random
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FAULTS = ["upstream", "footprint", "specification", "sampling", "validator"]
BROKEN = "```python\ndef {name}(*args, **kwargs):\n    return None\n```"


def _wrong(expected: str) -> str | None:
    import ast

    try:
        v = ast.literal_eval(expected.strip())
    except Exception:  # noqa: BLE001
        return None
    if isinstance(v, bool):
        return repr(not v)
    if isinstance(v, (int, float)):
        return repr(v + 1)
    if isinstance(v, str):
        return repr(v + "_x")
    if isinstance(v, (list, tuple)):
        return repr(type(v)(list(v) + [0]))
    if isinstance(v, dict):
        return repr({**v, "__injected__": 1})
    return repr(None) if v is not None else "1"


class Degrade:
    """LLM wrapper: the sampling fault, a degraded model on one agent. Its
    calls for the deliverable binding (one method, or every method when
    `method` is None) are replaced by a broken answer with probability p,
    during the run and during replays."""

    def __init__(self, inner, method: str | None, marker: str, p: float = 0.6, seed: int = 0) -> None:
        self.inner = inner
        self.method = method
        self.marker = marker
        self.p = p
        self.rng = random.Random(seed)
        self.injected = 0

    def __getattr__(self, k):
        return getattr(self.inner, k)

    @property
    def role(self):
        return self.inner.role

    @role.setter
    def role(self, v):
        self.inner.role = v

    def generate(self, prompt: str, **kw):
        import re as _re

        name = method_of_prompt(prompt)
        if self.marker in prompt and (self.method is None or name == self.method) and self.rng.random() < self.p:
            self.injected += 1
            out = BROKEN.format(name=self.method or name or "f")
            if getattr(self.inner, "keep_text", False):
                self.inner.texts.append({"role": self.inner.role, "prompt": prompt, "output": out, "injected": True})
            return out
        return self.inner.generate(prompt, **kw)


class Replay:
    """LLM wrapper for the validator fault: the deliverable binding's answers
    are the clean run's accepted values, so a rejection can only come from
    the (flaky) check, as for a correct value rejected by an unreliable test."""

    def __init__(self, inner, marker: str, accepted: dict[str, str]) -> None:
        self.inner = inner
        self.marker = marker
        self.accepted = accepted

    def __getattr__(self, k):
        return getattr(self.inner, k)

    @property
    def role(self):
        return self.inner.role

    @role.setter
    def role(self, v):
        self.inner.role = v

    def generate(self, prompt: str, **kw):
        if self.marker in prompt:
            val = self.accepted.get(method_of_prompt(prompt) or "")
            if val is not None:
                if getattr(self.inner, "keep_text", False):
                    self.inner.texts.append({"role": self.inner.role, "prompt": prompt, "output": val})
                return val
        return self.inner.generate(prompt, **kw)


def method_of_prompt(prompt: str) -> str | None:
    """The method a binding prompt is for: its footprint's signature line."""
    import re as _re

    m = _re.search(r"signature = (?:@\S+\s+)*(?:async\s+)?def (\w+)\(", prompt) or _re.search(r"def (\w+)\(", prompt)
    return m.group(1) if m else None


def _deliverable_rule(team: dict) -> tuple[int, int, int] | None:
    d = team.get("deliverable") or {}
    for hi, h in enumerate(team["handoffs"]):
        for ri, r in enumerate(h["rules"]):
            tv, tc = r["to"]["type"].split("!")
            if tv == d.get("view") and tc == d.get("class"):
                for bi, b in enumerate(r.get("llm", [])):
                    if b["feature"] == d.get("feature"):
                        return hi, ri, bi
    return None


def injected_team(team: dict, fault: str) -> dict:
    t = copy.deepcopy(team)
    loc = _deliverable_rule(t)
    if loc is None:
        return t
    hi, ri, bi = loc
    b = t["handoffs"][hi]["rules"][ri]["llm"][bi]
    if fault == "footprint":
        sig = [p for p in b["footprint"] if p.endswith("signature")]
        b["footprint"] = sig[:1] or b["footprint"][:1]
    elif fault == "specification":
        b["validator"] = list(b["validator"]) + [{"id": "unsat"}]
    elif fault == "validator":
        b["validator"] = list(b["validator"]) + [{"id": "flaky"}]
    return t


def record_checks(events: list):
    """Wrap every validator so its verdicts join the call log (the transcript)."""
    from agenthot import rt_helpers as RH
    from autom2m import vlib

    originals = {vid: getattr(RH, f"v_{vid}") for vid in vlib.IMPLEMENTATIONS}

    def mk(vid, fn):
        def w(value, target=None, *args):
            v = fn(value, target, *args)
            events.append({"role": "check", "validator": vid, "target": getattr(target, "_amt_target_key", None),
                           "ok": bool(v), "reason": getattr(v, "reason", "")})
            return v
        w.__name__ = f"v_{vid}"
        return w

    for vid, fn in originals.items():
        setattr(RH, f"v_{vid}", mk(vid, fn))
    return lambda: [setattr(RH, f"v_{vid}", fn) for vid, fn in originals.items()]


def load_builder_team(task_id: str, model: str) -> dict | None:
    from evaluation.run_matrix import _safe

    for seed in (1, 2, 3):
        f = ROOT / "results" / "runs" / "classeval" / _safe(model) / "autom2m" / f"{_safe(task_id)}__s{seed}.json"
        if f.exists():
            det = json.loads(f.read_text()).get("detail") or {}
            if det.get("admitted_round") is not None and det.get("final_team"):
                return det["final_team"]
    return None


def run_task(task_id: str, team_kind: str, model: str, faults: list[str], seed: int = 1) -> list[dict]:
    from agenthot.compiler import compile_team
    from agenthot.session import Session
    from autom2m.attribution import Attributor, derive
    from autom2m.lift import normalize
    from autom2m.repair import apply_delta
    from autom2m.typed_team import parse_team
    from autom2m.vlib import RunContext
    from evaluation.benchmarks import tasks as T
    from evaluation.harness.workbench import TaskWorkbench
    from evaluation.run_matrix import make_llm

    task = {t.task_id: t for t in T.load("classeval")}[task_id]
    base_json = json.loads((ROOT / "teams" / "classeval_reference.json").read_text()) if team_kind == "ref" \
        else load_builder_team(task_id, model)
    if base_json is None:
        return []
    base_json = normalize(base_json)
    loc = _deliverable_rule(base_json)
    if loc is None:
        return []
    hi, ri, bi = loc
    rule = base_json["handoffs"][hi]["rules"][ri]
    drule, dfeat = rule["name"], rule["llm"][bi]["feature"]
    workdir = Path(os.environ.get("TMPDIR", "/tmp")) / f"rq4_{team_kind}_{task_id.replace('/', '_')}_{seed}"
    llm = make_llm(model, seed, keep_text=True, budget=None)
    events = llm.texts  # LLM calls and (below) check verdicts, in order
    restore_checks = record_checks(events)
    rows = []
    try:
        team = parse_team(base_json)
        ct = compile_team(team, task, workdir / "clean")
        wb = TaskWorkbench(task)
        s0 = Session(ct, llm, RunContext(wb), k=3)
        wb.bind(s0.current_bodies)
        llm.role = "binding"
        clean = s0.run()
        snap = s0.snapshot()
        clean_events = len(events)
        clean_tokens = llm.totals()["out_tokens"]
        accepted_impl, accepted_vals = [], {}
        for link in s0.ct.team.traces[base_json["handoffs"][hi]["name"]].links():
            if dfeat in link.stamps:
                accepted_impl.append(link.target_key)
                obj = s0.object_by_target_key(link.target_key)
                meth = next((m.name for m in task.methods if f"Method#{m.name}" in link.target_key), None)
                if obj is not None and meth:
                    accepted_vals[meth] = getattr(obj, dfeat)
        for fault in faults:
            t0 = time.time()
            row = {"task": task_id, "team": team_kind, "model": model, "fault": fault, "deliverable": f"{drule}.{dfeat}",
                   "clean_phi": clean.phi}
            ev_start = len(events)
            try:
                tj = injected_team(base_json, fault)
                ct_f = compile_team(parse_team(base_json), task, workdir / fault)
                wb_f = TaskWorkbench(task)
                run_llm = llm
                s = Session(ct_f, run_llm, RunContext(wb_f, rng=random.Random(seed)), k=3)
                wb_f.bind(s.current_bodies)
                s.restore(snap)
                truth = {"class": fault, "binding": f"{drule}.{dfeat}", "agent": s.ct.bindings[(drule, dfeat)].owner,
                         "target": None}
                if fault in ("footprint", "specification", "validator"):
                    ar = apply_delta(s, parse_team(tj), task)
                    s = ar.session
                    if fault == "validator":
                        truth["agent"] = "(validator)"
                        if not accepted_vals:
                            continue
                        marker = s.ct.registry.get(s.ct.bindings[(drule, dfeat)].prompt_key, "")[:60]
                        run_llm = Replay(llm, marker, accepted_vals)
                        s.llm = run_llm
                        s.rt.llm = run_llm
                elif fault == "sampling":
                    if not accepted_impl:
                        continue
                    marker = s.ct.registry.get(s.ct.bindings[(drule, dfeat)].prompt_key, "")[:60]
                    run_llm = Degrade(llm, None, marker, seed=seed)
                    s.llm = run_llm
                    s.rt.llm = run_llm
                    accepted_set = set(accepted_impl)
                    for link in s.ct.team.traces[base_json["handoffs"][hi]["name"]].links():
                        if link.target_key in accepted_set:
                            link.stamps.pop(dfeat, None)  # the degraded agent re-samples every value it accepted
                elif fault == "upstream":
                    up = _upstream_target(s, base_json, hi, ri, bi, task)
                    if up is None:
                        continue
                    truth.update(binding=up["binding"], agent=up["agent"], target=up["target"], method=up["method"],
                                 symptom_target=up["downstream"])
                llm.role = "binding"
                res = s.run()
                row["phi"] = res.phi
                failing = [f for f in res.failures if f.clause in ("noEsc", "valid") and f.rule == drule and f.binding == dfeat]
                if fault in ("sampling", "validator"):
                    # only values the clean run had accepted fail because of the injection alone
                    failing = [f for f in failing if f.target_key in set(accepted_impl)]
                if fault == "upstream":
                    failing = [f for f in failing if f.target_key == truth.get("symptom_target")]
                row["manifested"] = bool(failing)
                row["truth"] = truth
                if failing:
                    llm.role = "attribution"
                    before = llm.totals()["calls"] + (getattr(run_llm, "injected", 0) if run_llm is not llm else 0)
                    att = Attributor(s, exhaustive=True, minimize=False)
                    rep = att.attribute(failing[0])
                    row["symptom"] = {"rule": failing[0].rule, "binding": failing[0].binding, "target": failing[0].target_key}
                    row["report"] = rep.to_dict()
                    row["responsible"] = rep.responsible()
                    row["exhaustive_calls"] = att.calls
                    for n in (1, 2, 3):
                        cls, calls = derive(rep.steps, n) if rep.fault_class not in ("validator", "unlocated") else \
                            (rep.fault_class, 0)
                        row[f"class_n{n}"] = cls
                        row[f"calls_n{n}"] = calls
                    _ = before
                    row["registry"] = dict(s.ct.registry)
                    row["owners"] = {f"{r}.{b}": m.owner for (r, b), m in s.ct.bindings.items()}
                    row["clean_summary"] = _summary(events[:clean_events])
                    row["transcript"] = [e for e in events[ev_start:] if e.get("role") in ("binding", "check")]
                row["seconds"] = round(time.time() - t0, 1)
                row["clean_out_tokens"] = clean_tokens
            except Exception as exc:  # noqa: BLE001
                row["error"] = f"{type(exc).__name__}: {exc}"
                row["trace"] = traceback.format_exc()[-1500:]
            rows.append(row)
    finally:
        restore_checks()
    return rows


def _summary(events: list) -> list[dict]:
    """One compact line per LLM call of the clean run, with its verdicts."""
    import re as _re

    out: list[dict] = []
    for e in events:
        if e.get("role") == "binding":
            m = _re.search(r"def (\w+)\(", e.get("prompt", ""))
            out.append({"head": e.get("prompt", "")[:80], "method": m.group(1) if m else None,
                        "output": str(e.get("output", ""))[:300], "checks": []})
        elif e.get("role") == "check" and out:
            out[-1]["checks"].append("accepted" if e.get("ok") else f"rejected: {str(e.get('reason'))[:120]}")
    return out


def _upstream_target(s, team: dict, hi: int, ri: int, bi: int, task) -> dict | None:
    """Give one method's upstream test (or text) value an extra wrong element,
    keeping its stamp: its validator accepted the original and is too weak."""
    from autom2m.typed_team import parse_team, rule_env, type_path

    from autom2m.typed_team import check_paths

    typed = parse_team(team)
    rule = next(r for _h, r in typed.rules() if r.name == team["handoffs"][hi]["rules"][ri]["name"])
    b = rule.llm[bi]
    env = rule_env(rule)
    producers = {(f"{r.target_view}.{r.target_cls}", x.feature): (h, r, x) for h, r in typed.rules() for x in r.llm}
    # the wrong value must sit where the validator reads it (a wrong test), else in the footprint
    paths = list(dict.fromkeys(check_paths(typed, rule, b) + [p for p in b.footprint]))
    for p in paths:
        pt = type_path(typed, p, env, object_reads_all=False)
        if not pt.ok or not pt.reads or pt.reads[-1] not in producers:
            continue
        uh, ur, ux = producers[pt.reads[-1]]
        is_test = any(v.id in ("test_valid",) for v in ux.validators)
        for link in s.ct.team.traces[uh.name].links():
            if ux.feature not in link.stamps:
                continue
            obj = s.object_by_target_key(link.target_key)
            meth = next((m for m in task.methods if f"Method#{m.name}" in link.target_key), None)
            if obj is None or meth is None:
                continue
            from autom2m.lift import split_doctests

            _doc, exs = split_doctests(meth.docstring)
            idx = next((i for i, (c, e) in enumerate(exs) if e.strip() and _wrong(e) is not None), None)
            val = getattr(obj, ux.feature) or ""
            if is_test:
                if idx is None:
                    continue
                call, expected = exs[idx]
                # earlier examples of the docstring are its setup, as in doctest
                lines = [c for c, _e in exs[:idx]] + call.split("\n")
                setup, expr = "\n        ".join(l for l in lines[:-1]), lines[-1]
                extra = (f"\n\nimport unittest as _ut\nclass InjectedTest(_ut.TestCase):\n    def test_injected(self):\n"
                         + (f"        {setup}\n" if setup else "") + f"        self.assertEqual({expr}, {_wrong(expected)})\n")
                code = val.split("```python", 1)[-1].split("```", 1)[0] if "```" in val else val
                setattr(obj, ux.feature, f"```python\n{code.rstrip()}{extra}```")
            else:
                setattr(obj, ux.feature, str(val) + f"\nNote: {meth.name} must always return None.")
            downstream = next((l.target_key for l in s.ct.team.traces[team["handoffs"][hi]["name"]].links()
                               if f"Method#{meth.name}" in l.target_key), None)
            return {"binding": f"{ur.name}.{ux.feature}", "agent": s.ct.bindings[(ur.name, ux.feature)].owner,
                    "target": link.target_key, "method": meth.name, "downstream": downstream}
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--team", choices=["ref", "builder"], default="ref")
    ap.add_argument("--model", default="qwen2.5-coder:7b")
    ap.add_argument("--per-class", type=int, default=40)
    ap.add_argument("--max-tasks", type=int, default=100)
    ap.add_argument("--faults", default=",".join(FAULTS))
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args(argv)
    from evaluation.benchmarks import tasks as T

    ids = [t.task_id for t in T.load("classeval")][: a.max_tasks]
    out = ROOT / "results" / "rq4" / f"inject_{a.team}__{a.model.replace(':', '_')}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    done, counts = set(), {f: 0 for f in FAULTS}
    if out.exists():
        for l in out.read_text().splitlines():
            r = json.loads(l)
            done.add(r["task"])
            if r.get("manifested"):
                counts[r["fault"]] += 1
    faults = a.faults.split(",")
    todo = [t for t in ids if t not in done]
    print(f"{len(todo)} tasks to inject ({a.team} team); manifested so far {counts}", flush=True)
    with ProcessPoolExecutor(a.workers) as ex, out.open("a") as fh:
        futs = {}
        it = iter(todo)

        def submit():
            need = [f for f in faults if counts[f] < a.per_class]
            if not need:
                return False
            t = next(it, None)
            if t is None:
                return False
            futs[ex.submit(run_task, t, a.team, a.model, need, a.seed)] = t
            return True

        for _ in range(a.workers):
            submit()
        while futs:
            for fu in as_completed(list(futs)):
                t = futs.pop(fu)
                try:
                    rows = fu.result()
                except Exception as exc:  # noqa: BLE001
                    rows = [{"task": t, "error": str(exc)[:300]}]
                for row in rows:
                    fh.write(json.dumps(row, default=str) + "\n")
                    if row.get("manifested"):
                        counts[row["fault"]] = counts.get(row["fault"], 0) + 1
                if not rows:
                    fh.write(json.dumps({"task": t, "skipped": True}) + "\n")
                fh.flush()
                print(f"{t}: {[(r.get('fault'), r.get('manifested'), r.get('report', {}).get('fault_class')) for r in rows]} "
                      f"counts={counts}", flush=True)
                submit()
                break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
