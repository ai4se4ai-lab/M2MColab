"""Untyped conditions of Table 6, re-implemented in one harness with the same
model, tools and worked examples (rendered in each condition's own format).

  single       one agent with code execution and two self-repair rounds; done = the agent stops
  single_gate  Single with best-of-n sampling up to AutoM2M's median token budget; a candidate is
               accepted when the public examples pass; done = examples pass
  free         CaptainAgent-style: the builder writes prose roles (with tools) and a plan; agents talk in a
               group chat; only agents given `exec` run code; done = TERMINATE
  critic       free + an LLM critic reviews the team's JSON-rendered specification for D1-D5; one revision
  schema       PatchBoard-style: the same roles exchange JSON patches to one shared, schema-validated board;
               done = an agent sets status 'done' (the board's TERMINATE)

Temperatures: 0.2 for agents, 0.6 for builders (paper Sec. 4.2).
"""
from __future__ import annotations

import json

from agenthot.llm.base import LLMError
from autom2m.examples import prose_examples_text
from evaluation.benchmarks import tasks as T

from .common import RunRecord, answer_format, clip, execute, find_solution, find_tests, json_from, task_text, with_imports

MAX_TURNS = 8
FIX_ROUNDS = 2
AGENT_T = 0.2
BUILDER_T = 0.6
TOOLS = ("exec", "search")


def _set_role(llm, role: str) -> None:
    if hasattr(llm, "role"):
        llm.role = role


def _out_tokens(llm) -> int:
    return llm.totals()["out_tokens"] if hasattr(llm, "totals") else 0


def examples_pass(task: T.Task, code: str | None) -> tuple[bool, int, int]:
    """(all public examples pass, passed, total) for an assembled solution."""
    if not code:
        return False, 0, 0
    ex = T.run_examples(task, with_imports(task, code), [m.name for m in task.methods])
    if ex.get("error"):
        return False, 0, max(ex.get("n", 0), 1)
    n, bad = ex.get("n", 0), len(ex.get("failed", []))
    return bad == 0 and not ex.get("pending"), n - bad, n


# --------------------------------------------------------------------------
# single agent (+ execution gate)
# --------------------------------------------------------------------------


def run_single(task: T.Task, llm, *, temperature: float = AGENT_T) -> RunRecord:
    rec = RunRecord()
    _set_role(llm, "agent")
    msgs = [{"role": "system", "content": "You are an expert Python developer."},
            {"role": "user", "content": f"Implement the following.\n\n{task_text(task)}\n\n{answer_format(task)}"}]
    code = None
    stopped = False
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
        if code and "Loading the code failed" not in report and " expected " not in report:
            stopped = True  # the agent has nothing left to fix: it stops
            break
        if rnd == FIX_ROUNDS:
            break
        msgs += [{"role": "assistant", "content": out},
                 {"role": "user", "content": f"Execution result:\n{report}\n\nFix the code if the result shows a real error "
                                              f"(documented examples may be informal). {answer_format(task)}"}]
    rec.code = code or ""
    rec.declared_done = stopped
    rec.extra["rounds"] = sum(1 for m in rec.transcript if m["kind"] == "message")
    return rec


def run_single_gate(task: T.Task, llm, *, budget_out: int, temperature: float = AGENT_T) -> RunRecord:
    """Best-of-n Single runs until a candidate passes the public examples or
    the output-token budget (AutoM2M's median) is spent."""
    rec = RunRecord()
    best, best_score, n = None, (-1, -1), 0
    start = _out_tokens(llm)
    while True:
        n += 1
        cand = run_single(task, llm, temperature=temperature)
        rec.transcript += cand.transcript
        if cand.status == "budget":
            rec.status = "budget"
        ok, passed, total = examples_pass(task, cand.code)
        rec.transcript.append({"agent": "Gate", "content": f"candidate {n}: public examples {passed}/{total} "
                                                           f"{'pass' if ok else 'fail'}", "kind": "tool"})
        if cand.code and (passed, -n) > best_score:
            best, best_score = cand.code, (passed, -n)
        if ok and cand.code:
            best = cand.code
            rec.declared_done = True
            break
        if _out_tokens(llm) - start >= budget_out or cand.status == "budget":
            break
    rec.code = best or ""
    rec.extra.update(candidates=n, budget_out=budget_out)
    return rec


# --------------------------------------------------------------------------
# free-form builder + group chat
# --------------------------------------------------------------------------

BUILDER_PROMPT = """You are a team builder. Design a small team of LLM expert agents (2-4) that will collaborate in a
group chat to solve the programming task below. Give each agent the tools it needs from {tools}: an agent with
"exec" has its ```python blocks executed by a Computer_terminal (which runs the task's documented examples and any
unittest tests posted in the chat).

Worked examples:
{examples}

TASK:
{task}

Output only a JSON object {{"agents": [{{"name", "role", "tools"}}], "plan": [steps]}}."""

CRITIC_PROMPT = """You review the specification of a team of LLM agents designed to solve a programming task, BEFORE it runs.
Look for composition defects:
D1 coverage gap: part of the task is owned by no agent, or a requirement is never checked;
D2 hand-off mismatch: what an agent produces is not what the next agent needs;
D3 ownership conflict: two agents work on the same artefact, or nobody knows it is theirs;
D4 unverifiable completion: "done" is only a claim, nothing checks it;
D5 capability mismatch: work goes to an agent that lacks the tool or skill it needs.

TASK:
{task}

TEAM SPECIFICATION (JSON):
{team}

Output only JSON {{"defects": [{{"class": "D1".."D5", "where": text, "fix": text}}]}} (an empty list if none)."""

REVISE_PROMPT = """You are a team builder. A reviewer found these problems in your team:
{critique}

TASK:
{task}

YOUR TEAM:
{team}

Output only the corrected JSON object {{"agents": [{{"name", "role", "tools"}}], "plan": [steps]}}."""


def _clean_team(team: dict | None) -> dict:
    team = team or {}
    agents = [a for a in team.get("agents", []) if isinstance(a, dict) and a.get("name")][:4]
    if not agents:
        agents = [{"name": "Developer", "role": "Expert Python developer.", "tools": ["exec"]}]
    out = []
    for a in agents:
        tools = a.get("tools") if isinstance(a.get("tools"), list) else []
        out.append({"name": str(a["name"]).replace(" ", "_"), "role": str(a.get("role", "")),
                    "tools": [str(t) for t in tools if str(t) in TOOLS]})
    plan = [str(p) for p in team.get("plan", [])] if isinstance(team.get("plan"), list) else []
    return {"agents": out, "plan": plan}


def build_free_team(task: T.Task, llm, *, temperature: float = BUILDER_T, n_examples: int = 3) -> dict:
    _set_role(llm, "builder")
    out = llm.generate(BUILDER_PROMPT.format(tools=list(TOOLS), examples=prose_examples_text(n_examples),
                                             task=task_text(task)), temperature=temperature, format="json")
    return _clean_team(json_from(out))


def critic_revise(task: T.Task, team: dict, llm, *, temperature: float = AGENT_T) -> tuple[dict, dict]:
    _set_role(llm, "critic")
    crit = json_from(llm.generate(CRITIC_PROMPT.format(task=task_text(task), team=json.dumps(team, indent=1)),
                                  temperature=temperature, format="json")) or {"defects": []}
    defects = crit.get("defects") if isinstance(crit.get("defects"), list) else []
    if not defects:
        return team, crit
    _set_role(llm, "builder")
    rev = json_from(llm.generate(REVISE_PROMPT.format(critique=json.dumps(defects, indent=1), task=task_text(task),
                                                      team=json.dumps(team, indent=1)), temperature=BUILDER_T, format="json"))
    if rev and rev.get("agents"):
        team = _clean_team(rev)
    return team, crit


def brief(task: T.Task, team: dict) -> str:
    plan = "\n".join(f"{i+1}. {p}" for i, p in enumerate(team["plan"]))
    names = ", ".join(f"{a['name']} (tools: {', '.join(a['tools']) or 'none'})" for a in team["agents"])
    return (f"## Task\n{task_text(task)}\n\n## Plan for solving the task\n{plan}\n\n## Team\n{names}, Computer_terminal\n\n"
            f"## Output format\n{answer_format(task)} Reply TERMINATE when the task is solved.")


def group_chat(task: T.Task, team: dict, llm, *, temperature: float = AGENT_T) -> RunRecord:
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
        if (new_code or new_tests) and "exec" in a["tools"]:
            rec.transcript.append({"agent": "Computer_terminal", "content": execute(task, code, tests), "kind": "tool"})
        if "TERMINATE" in out:
            rec.extra["terminated_by"] = a["name"]
            rec.declared_done = True
            break
    rec.extra["turns"] = sum(1 for m in rec.transcript if m["kind"] == "message")
    rec.code = code or ""
    return rec


def run_free(task: T.Task, llm, *, temperature: float = AGENT_T) -> RunRecord:
    team = build_free_team(task, llm)
    return group_chat(task, team, llm, temperature=temperature)


def run_critic(task: T.Task, llm, *, temperature: float = AGENT_T) -> RunRecord:
    team = build_free_team(task, llm)
    team2, crit = critic_revise(task, team, llm)
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


def run_schema(task: T.Task, llm, *, temperature: float = AGENT_T) -> RunRecord:
    team = build_free_team(task, llm)
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
            if rec.status == "budget":
                break
            continue
        for k, v in patch.items():
            if k == "design":
                board["design"].update({str(x): str(y) for x, y in v.items()})
            else:
                board[k] = v
        if ("code" in patch or "tests" in patch) and "exec" in a["tools"]:
            code = find_solution(task, f"```python\n{T.extract_code(board['code'])}\n```") if board["code"] else None
            board["terminal"] = execute(task, code, T.extract_code(board["tests"]) if board["tests"] else None)
            rec.transcript.append({"agent": "Computer_terminal", "content": board["terminal"], "kind": "tool"})
        if rec.status == "budget":
            break
        if board.get("status") == "done":
            rec.extra["terminated_by"] = a["name"]
            rec.declared_done = True
            break
    rec.code = T.extract_code(board["code"]) if board["code"] else ""
    rec.extra["board_keys"] = sorted(k for k, v in board.items() if v)
    return rec
