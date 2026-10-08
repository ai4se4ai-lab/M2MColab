"""Baseline conditions, re-implemented in one harness with the same models,
tool access, worked example and turn budget.

  single  one agent writes the solution, then up to 2 fix rounds driven by the execution tool
  free    CaptainAgent-style: a builder writes prose roles and a plan; agents talk in a
          group chat (round-robin, shared transcript); the run ends on TERMINATE or the turn cap
  critic  free + an LLM critic reviews the team for composition defects D1-D5; the builder revises once
  schema  PatchBoard-style: same roles, but agents exchange JSON patches to a shared board
          validated against one schema (invalid patches are re-sampled)
"""
from __future__ import annotations

import json

from agentm2m.auto.examples import EXAMPLE_TASK, prose_example_text
from agentm2m.llm.base import LLMError
from evaluation.benchmarks import tasks as T

from .common import RunRecord, answer_format, clip, execute, find_solution, find_tests, json_from, task_text

MAX_TURNS = 8
FIX_ROUNDS = 2


def _set_role(llm, role: str) -> None:
    if hasattr(llm, "role"):
        llm.role = role


# --------------------------------------------------------------------------
# single agent
# --------------------------------------------------------------------------


def run_single(task: T.Task, llm, *, temperature: float = 0.6) -> RunRecord:
    rec = RunRecord()
    _set_role(llm, "agent")
    msgs = [{"role": "system", "content": "You are an expert Python developer."},
            {"role": "user", "content": f"Implement the following.\n\n{task_text(task)}\n\n{answer_format(task)}"}]
    code = None
    for rnd in range(FIX_ROUNDS + 1):
        try:
            out = llm.chat(msgs, temperature=temperature)
        except LLMError:
            rec.status = "budget"
            break
        rec.transcript.append({"agent": "Developer", "content": out, "kind": "message"})
        code = find_solution(task, out) or code
        report = execute(task, code)
        rec.transcript.append({"agent": "Computer_terminal", "content": report, "kind": "tool"})
        if rnd == FIX_ROUNDS or ("Loading the code failed" not in report and " expected " not in report and code):
            break
        msgs += [{"role": "assistant", "content": out},
                 {"role": "user", "content": f"Execution result:\n{report}\n\nFix the code if the result shows a real error "
                                              f"(documented examples may be informal). {answer_format(task)}"}]
    rec.code = code or ""
    return rec


# --------------------------------------------------------------------------
# free-form builder + group chat
# --------------------------------------------------------------------------

BUILDER_PROMPT = """You are a team builder. Design a small team of LLM expert agents (2-4) that will collaborate in a
group chat to solve the programming task below. A Computer_terminal executes any ```python block an agent writes
(it runs the task's documented examples and any unittest tests posted in the chat).

Example task:
{example_task}
Example team for the example task:
{example}

TASK:
{task}

Output only a JSON object {{"agents": [{{"name", "role"}}], "plan": [steps]}}."""

CRITIC_PROMPT = """You review a team of LLM agents designed to solve a programming task, BEFORE it runs.
Look for composition defects:
D1 coverage gap: part of the task is owned by no agent, or a requirement is never checked;
D2 hand-off mismatch: what an agent produces is not what the next agent needs;
D3 ownership conflict: two agents work on the same artefact, or nobody knows it is theirs;
D4 unverifiable completion: "done" is only a claim, nothing checks it;
D5 capability mismatch: work goes to an agent that cannot do it.

TASK:
{task}

TEAM:
{team}

Output only JSON {{"defects": [{{"class": "D1".."D5", "where": text, "fix": text}}]}} (empty list if none)."""

REVISE_PROMPT = """You are a team builder. A reviewer found these problems in your team:
{critique}

TASK:
{task}

YOUR TEAM:
{team}

Output only the corrected JSON object {{"agents": [{{"name", "role"}}], "plan": [steps]}}."""


def build_free_team(task: T.Task, llm, *, temperature: float) -> dict:
    _set_role(llm, "builder")
    out = llm.generate(BUILDER_PROMPT.format(example_task=EXAMPLE_TASK, example=prose_example_text(), task=task_text(task)),
                       temperature=temperature, format="json")
    team = json_from(out) or {}
    agents = [a for a in team.get("agents", []) if isinstance(a, dict) and a.get("name")][:4]
    if not agents:
        agents = [{"name": "Developer", "role": "Expert Python developer."}]
    plan = [str(p) for p in team.get("plan", [])] if isinstance(team.get("plan"), list) else []
    return {"agents": [{"name": str(a["name"]).replace(" ", "_"), "role": str(a.get("role", ""))} for a in agents], "plan": plan}


def critic_revise(task: T.Task, team: dict, llm, *, temperature: float) -> tuple[dict, dict]:
    _set_role(llm, "critic")
    crit = json_from(llm.generate(CRITIC_PROMPT.format(task=task_text(task), team=json.dumps(team, indent=1)),
                                  temperature=temperature, format="json")) or {"defects": []}
    defects = crit.get("defects") or []
    if not defects:
        return team, crit
    _set_role(llm, "builder")
    rev = json_from(llm.generate(REVISE_PROMPT.format(critique=json.dumps(defects, indent=1), task=task_text(task),
                                                      team=json.dumps(team, indent=1)), temperature=temperature, format="json"))
    if rev and rev.get("agents"):
        agents = [a for a in rev["agents"] if isinstance(a, dict) and a.get("name")][:4]
        if agents:
            team = {"agents": [{"name": str(a["name"]).replace(" ", "_"), "role": str(a.get("role", ""))} for a in agents],
                    "plan": [str(p) for p in rev.get("plan", [])] if isinstance(rev.get("plan"), list) else team["plan"]}
    return team, crit


def brief(task: T.Task, team: dict) -> str:
    plan = "\n".join(f"{i+1}. {p}" for i, p in enumerate(team["plan"]))
    names = ", ".join(a["name"] for a in team["agents"])
    return (f"## Task\n{task_text(task)}\n\n## Plan for solving the task\n{plan}\n\n## Team\n{names} (+ Computer_terminal)\n\n"
            f"## Output format\n{answer_format(task)} Reply TERMINATE when the task is solved.")


def group_chat(task: T.Task, team: dict, llm, *, temperature: float) -> RunRecord:
    rec = RunRecord(team=team)
    rec.transcript.append({"agent": "Manager", "content": brief(task, team), "kind": "brief"})
    code, tests = None, None
    _set_role(llm, "agent")
    agents = team["agents"]
    for turn in range(MAX_TURNS):
        a = agents[turn % len(agents)]
        history = "\n\n".join(f"[{m['agent']}]\n{m['content']}" for m in clip(rec.transcript))
        msgs = [{"role": "system", "content": f"You are {a['name']} in a group chat. {a['role']}\n"
                                              "Write only your own contribution. Use ```python blocks for code."},
                {"role": "user", "content": f"{history}\n\nIt is your turn, {a['name']}."}]
        try:
            out = llm.chat(msgs, temperature=temperature)
        except LLMError:
            rec.status = "budget"
            break
        rec.transcript.append({"agent": a["name"], "content": out, "kind": "message"})
        new_code, new_tests = find_solution(task, out), find_tests(out)
        if new_code:
            code = new_code
        if new_tests and not new_code:
            tests = new_tests
        if new_code or new_tests:
            rec.transcript.append({"agent": "Computer_terminal", "content": execute(task, code, tests), "kind": "tool"})
        if "TERMINATE" in out:
            rec.extra["terminated_by"] = a["name"]
            break
    rec.extra["turns"] = sum(1 for m in rec.transcript if m["kind"] == "message")
    rec.code = code or ""
    return rec


def run_free(task: T.Task, llm, *, temperature: float = 0.6) -> RunRecord:
    team = build_free_team(task, llm, temperature=temperature)
    return group_chat(task, team, llm, temperature=temperature)


def run_critic(task: T.Task, llm, *, temperature: float = 0.6) -> RunRecord:
    team = build_free_team(task, llm, temperature=temperature)
    team2, crit = critic_revise(task, team, llm, temperature=temperature)
    rec = group_chat(task, team2, llm, temperature=temperature)
    rec.extra.update(critique=crit, initial_team=team)
    return rec


# --------------------------------------------------------------------------
# shared-schema board (PatchBoard-style)
# --------------------------------------------------------------------------

BOARD_KEYS = {"design": dict, "tests": str, "code": str, "status": str, "note": str}


def _valid_patch(p: dict | None) -> tuple[bool, str]:
    if not isinstance(p, dict) or not p:
        return False, "patch must be a non-empty JSON object"
    for k, v in p.items():
        if k not in BOARD_KEYS:
            return False, f"unknown key {k!r}; allowed: {sorted(BOARD_KEYS)}"
        if not isinstance(v, BOARD_KEYS[k]):
            return False, f"key {k!r} must be a {BOARD_KEYS[k].__name__}"
    if "status" in p and p["status"] not in ("in_progress", "done"):
        return False, "status must be 'in_progress' or 'done'"
    return True, ""


def run_schema(task: T.Task, llm, *, temperature: float = 0.6) -> RunRecord:
    team = build_free_team(task, llm, temperature=temperature)
    rec = RunRecord(team=team)
    board: dict = {"design": {}, "tests": "", "code": "", "status": "in_progress", "note": "", "terminal": ""}
    plan = "\n".join(f"{i+1}. {p}" for i, p in enumerate(team["plan"]))
    _set_role(llm, "agent")
    agents = team["agents"]
    rec.transcript.append({"agent": "Manager", "content": brief(task, team), "kind": "brief"})
    for turn in range(MAX_TURNS):
        a = agents[turn % len(agents)]
        prompt = (f"You are {a['name']}. {a['role']}\n\nTASK:\n{task_text(task)}\n\nPLAN:\n{plan}\n\n"
                  f"SHARED BOARD (JSON):\n{json.dumps(board, indent=1)[-9000:]}\n\n"
                  "Contribute by returning ONE JSON patch object updating the board. Allowed keys: "
                  "design (object: method name -> approach), tests (string: complete unittest code), "
                  f"code (string: the complete solution code; {answer_format(task)[len('The final answer is '):]}), "
                  "status ('in_progress' or 'done'), note (string to the team). Output only the JSON patch.")
        patch, why = None, ""
        for _attempt in range(3):
            try:
                out = llm.generate(prompt + (f"\nYour previous patch was invalid: {why}" if why else ""),
                                   temperature=temperature, format="json")
            except LLMError:
                rec.status = "budget"
                out = ""
                break
            patch = json_from(out)
            ok, why = _valid_patch(patch)
            if ok:
                break
            patch = None
        rec.transcript.append({"agent": a["name"], "content": json.dumps(patch) if patch else f"(invalid patch: {why})",
                               "kind": "message"})
        if patch is None:
            continue
        for k, v in patch.items():
            if k == "design":
                board["design"].update({str(x): str(y) for x, y in v.items()})
            else:
                board[k] = v
        if "code" in patch or "tests" in patch:
            code = find_solution(task, f"```python\n{T.extract_code(board['code'])}\n```") if board["code"] else None
            board["terminal"] = execute(task, code, T.extract_code(board["tests"]) if board["tests"] else None)
            rec.transcript.append({"agent": "Computer_terminal", "content": board["terminal"], "kind": "tool"})
        if rec.status == "budget":
            break
        if board.get("status") == "done":
            rec.extra["terminated_by"] = a["name"]
            break
    code = T.extract_code(board["code"]) if board["code"] else ""
    rec.code = code
    rec.extra["board_keys"] = sorted(k for k, v in board.items() if v)
    return rec
