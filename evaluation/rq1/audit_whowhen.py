"""Structural audit of Who&When team specifications (RQ1, specification-level
evidence) and the runtime addressing contrast.

    python -m evaluation.rq1.audit_whowhen data/Agents_Failure_Attribution/Who\\&When

Asks only what each specification *states*. All patterns are below; every
match is written to results/rq1/audit_matches.jsonl for manual inspection.
"""
from __future__ import annotations

import glob
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

OUTPUT_PAT = re.compile(
    r"\b(you (must|should|will) (output|produce|return|deliver|hand over|send|pass))|"
    r"\b(your (output|deliverable|result) (is|must|should))|\b(output format\s*:)|\b(format of your (answer|output))",
    re.I)
HANDOFF_PAT = re.compile(
    r"\b(colleague|teammate|team member|other (agents|members|experts)|hand[- ]?off|pass (it|them|the result)|"
    r"forward|receive[sd]? from|deliver(ed)? to|send (it|them|the) )", re.I)
VERIFIER_PAT = re.compile(r"verif|valid", re.I)


def wb(name: str) -> re.Pattern:
    return re.compile(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])")


def plan_steps(brief: str) -> list[str]:
    m = re.search(r"## Plan for solving the task\s*\n(.*?)(\n## |\Z)", brief, re.S)
    if not m:
        return []
    return [l.strip() for l in m.group(1).splitlines() if re.match(r"\s*\d+[.)]", l)]


def audit_algorithm_generated(folder: str, matches: list) -> dict:
    files = sorted(glob.glob(os.path.join(folder, "*.json")), key=lambda p: int(Path(p).stem))
    n_roles = names_teammate = states_output = handoff = 0
    plan_assigns = has_format = has_verifier = 0
    terminated = term_by_verifier = mistake_verifier = 0
    msgs = msgs_naming = runs_naming = 0
    for f in files:
        d = json.load(open(f))
        roles = d.get("system_prompt") or {}
        names = [n for n in roles if n != "Computer_terminal"]
        for n, prompt in roles.items():
            if n == "Computer_terminal":
                continue
            n_roles += 1
            others = [o for o in names if o != n]
            if any(wb(o).search(prompt or "") for o in others):
                names_teammate += 1
                matches.append({"file": Path(f).name, "kind": "names_teammate", "role": n})
            if OUTPUT_PAT.search(prompt or ""):
                states_output += 1
                matches.append({"file": Path(f).name, "kind": "states_output", "role": n,
                                "text": OUTPUT_PAT.search(prompt).group(0)})
            hm = HANDOFF_PAT.search(prompt or "")
            if hm:
                handoff += 1
                s = max(0, hm.start() - 80)
                matches.append({"file": Path(f).name, "kind": "handoff_phrase", "role": n,
                                "text": (prompt or "")[s:hm.end() + 80]})
        hist = d.get("history") or []
        brief = hist[0]["content"] if hist else ""
        steps = plan_steps(brief)
        if any(wb(n).search(s) for s in steps for n in names):
            plan_assigns += 1
        if "## Output format" in brief:
            has_format += 1
        if any(VERIFIER_PAT.search(n) for n in names):
            has_verifier += 1
        term = [m for m in hist if "TERMINATE" in (m.get("content") or "") and m.get("name") != "Computer_terminal"]
        if term:
            terminated += 1
            if VERIFIER_PAT.search(term[-1].get("name") or ""):
                term_by_verifier += 1
        if VERIFIER_PAT.search(d.get("mistake_agent") or ""):
            mistake_verifier += 1
        named_any = False
        for m in hist[1:]:
            who = m.get("name")
            if who in (None, "Computer_terminal"):
                continue
            msgs += 1
            if any(wb(o).search(m.get("content") or "") for o in names if o != who):
                msgs_naming += 1
                named_any = True
        runs_naming += named_any
    n = len(files)
    return {
        "teams": n, "roles": n_roles,
        "roles_naming_teammate": names_teammate, "roles_stating_output": states_output,
        "roles_with_handoff_phrase": handoff, "plans_assigning_step_to_role": plan_assigns,
        "teams_with_output_format": has_format, "teams_with_verifier_role": has_verifier,
        "runs_terminated_by_agent": terminated, "runs_terminated_by_verifier": term_by_verifier,
        "runs_mistake_agent_verifier": mistake_verifier,
        "agent_messages": msgs, "agent_messages_naming_teammate": msgs_naming, "runs_with_any_naming": runs_naming,
    }


def audit_hand_crafted(folder: str) -> dict:
    files = sorted(glob.glob(os.path.join(folder, "*.json")))
    team = ["Orchestrator", "WebSurfer", "FileSurfer", "Coder", "ComputerTerminal", "Assistant"]
    msgs = naming = runs = 0
    for f in files:
        d = json.load(open(f))
        any_ = False
        for m in d.get("history") or []:
            who = (m.get("role") or "").split(" ")[0]
            if who in ("human", "", "ComputerTerminal"):
                continue
            msgs += 1
            text = (m.get("role") or "") + " " + (m.get("content") or "")
            if any(wb(o).search(text) for o in team if o != who):
                naming += 1
                any_ = True
        runs += any_
    return {"runs": len(files), "agent_messages": msgs, "agent_messages_naming_teammate": naming, "runs_with_any_naming": runs}


def main(argv=None) -> int:
    root = (argv or sys.argv[1:] or ["data/Agents_Failure_Attribution/Who&When"])[0]
    matches: list = []
    ag = audit_algorithm_generated(os.path.join(root, "Algorithm-Generated"), matches)
    hc = audit_hand_crafted(os.path.join(root, "Hand-Crafted"))
    out = Path("results/rq1")
    out.mkdir(parents=True, exist_ok=True)
    (out / "audit_whowhen.json").write_text(json.dumps({"algorithm_generated": ag, "hand_crafted": hc}, indent=1))
    with (out / "audit_matches.jsonl").open("w") as fh:
        for m in matches:
            fh.write(json.dumps(m) + "\n")
    print(json.dumps({"algorithm_generated": ag, "hand_crafted": hc}, indent=1))
    print(Counter(m["kind"] for m in matches))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
