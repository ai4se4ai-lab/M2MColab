"""Codebook coding of failed runs (RQ1 and RQ3): the decisive cause of each
failure, plus an optional secondary code.

A cause is *decisive* if the run would have succeeded had that cause alone
been absent. Two coders label every failure independently; agreeing labels
are kept, disagreements are resolved by a third pass with a fixed tie-break
rule (the adjudicator chooses between the two labels; if it answers neither,
the first coder's label stands). Who&When items are coded twice: first
without the dataset's annotation (agent, step, reason), then with it.

Every run is rendered into one transcript format; typed runs appear as
messages, one per accepted or rejected value, and the condition is not named.
"""
from __future__ import annotations

import json
from typing import Any

CODES = ["D1", "D2", "D3", "D4", "D5", "reasoning", "tool", "other"]
COMPOSITION = {"D1", "D2", "D3", "D4", "D5"}

CODEBOOK = """Codebook. Choose the ONE code for the DECISIVE cause of the failure: the cause such that, had it
alone been absent, the run would most likely have succeeded. Optionally add one SECONDARY code.
D1 coverage gap: part of the task (a method, a requirement, an example, a plan step) is owned by no agent, or a
   requirement is never checked against anything that read it.
D2 hand-off mismatch: what one agent passed on was not what the next agent needed (missing details, wrong form,
   misread or dangling reference), or an agent built on a wrong intermediate result it was handed as fact.
D3 ownership conflict: two agents worked on or overwrote the same artefact, or nobody took responsibility for it.
D4 unverifiable completion: the team declared success (TERMINATE, "verified", status done) although nothing
   checked the result, or a failing check was ignored.
D5 capability mismatch: work went to an agent lacking the tool or skill it needed (e.g. code to check, no execution).
reasoning: one agent's own reasoning or coding error although its inputs were adequate (wrong algorithm, bug,
   misread specification).
tool: execution environment or tool failure (timeout, missing library, sandbox, web access).
other: none of the above.
D1-D5 are composition defects: properties of the team's specification, not of one agent's answer."""

VARIANT_HINT = [
    "",
    "Before answering, ask yourself for each candidate cause: if only this were fixed, would the run have succeeded?",
]


def _clip(text: str, n: int) -> str:
    text = str(text or "")
    return text if len(text) <= n else text[: n // 2] + "\n...[clipped]...\n" + text[-n // 2:]


def _norm(code: Any) -> str | None:
    c = str(code or "").strip()
    for k in CODES:
        if c.lower() == k.lower() or c.lower().startswith(k.lower() + " ") or c.lower().startswith(k.lower() + ":"):
            return k
    if c.lower().startswith("agent"):
        return "reasoning"
    return None


def code_one(llm, text: str, *, variant: int = 0, annotation: str = "") -> dict:
    from autom2m.loop import json_from

    prompt = (f"You analyse why a team of LLM agents failed its task.\n{CODEBOOK}\n\n{text}\n\n"
              + (f"DATASET ANNOTATION: {annotation}\n\n" if annotation else "")
              + VARIANT_HINT[variant % len(VARIANT_HINT)]
              + "\nAnswer JSON {\"code\": one of " + json.dumps(CODES) + ", \"secondary\": one of the codes or null, "
              "\"why\": one sentence}.")
    try:
        r = json_from(llm.generate(prompt, temperature=0.0, format="json", max_tokens=200)) or {}
    except Exception as exc:  # noqa: BLE001
        r = {"code": "other", "why": f"coder error {exc}"}
    code = _norm(r.get("code")) or "other"
    sec = _norm(r.get("secondary"))
    return {"code": code, "secondary": sec if sec != code else None, "why": str(r.get("why", ""))[:300]}


def adjudicate(llm, text: str, a: dict, b: dict) -> dict:
    from autom2m.loop import json_from

    prompt = (f"Two analysts disagree on the decisive cause of this failure.\n{CODEBOOK}\n\n{text}\n\n"
              f"Analyst A: {a['code']} ({a['why']})\nAnalyst B: {b['code']} ({b['why']})\n"
              f"Choose A or B. Answer JSON {{\"choice\": \"A\" | \"B\", \"why\": one sentence}}.")
    try:
        r = json_from(llm.generate(prompt, temperature=0.0, format="json", max_tokens=150)) or {}
    except Exception:  # noqa: BLE001
        r = {}
    choice = str(r.get("choice", "A")).strip().upper()[:1]
    return b if choice == "B" else a  # tie-break: the first coder's label stands


def code_item(llms: list, text: str, *, annotation: str = "") -> dict:
    """Two coders, then adjudication on disagreement (by the first coder's model)."""
    a = code_one(llms[0], text, variant=0)
    b = code_one(llms[1], text, variant=1)
    out = {"coder_a": a, "coder_b": b}
    if annotation:  # second pass, after seeing the dataset annotation
        a2 = code_one(llms[0], text, variant=0, annotation=annotation)
        b2 = code_one(llms[1], text, variant=1, annotation=annotation)
        out.update(coder_a_annot=a2, coder_b_annot=b2)
        a, b = a2, b2
    final = a if a["code"] == b["code"] else adjudicate(llms[0], text, a, b)
    out.update(final=final["code"], secondary=final.get("secondary") or (b["code"] if b["code"] != final["code"] else None),
               agree=a["code"] == b["code"])
    return out


# --------------------------------------------------------------------------
# rendering our runs into one transcript format
# --------------------------------------------------------------------------

def render_run(d: dict) -> str:
    det = d.get("detail") or {}
    sc = d.get("score") or {}
    outcome = (f"OUTCOME: hidden tests {sc.get('tests_passed')}/{sc.get('tests_run')} passed"
               + (f"; {str(sc.get('error'))[:200]}" if sc.get("error") else "") + ".")
    if "transcript" in det:  # untyped conditions: the team and the conversation
        team = json.dumps(det.get("team"), indent=1) if det.get("team") else "(one agent)"
        tr = "\n\n".join(f"[{i}] {m['agent']}: {_clip(m['content'], 900)}" for i, m in enumerate(det["transcript"]))
        return f"TEAM SPECIFICATION:\n{_clip(team, 2500)}\n\nRUN:\n{_clip(tr, 9000)}\n\n{outcome}"
    team = det.get("final_team") or det.get("first_team") or {}
    agents = "\n".join(f"- {a.get('name')} (tools {a.get('tools')}): {_clip(a.get('role'), 200)}"
                       for a in team.get("agents", []) if isinstance(a, dict))
    spec = json.dumps({k: team.get(k) for k in ("writes", "handoffs", "goal", "deliverable", "done")}, separators=(",", ":"))
    msgs = []
    for i, e in enumerate(det.get("events") or []):
        what = f"{e.get('binding')} for {str(e.get('target', '')).split('::')[-1]}"
        tail = f" -- {e.get('status')}" + (f": {e.get('reason')}" if e.get("reason") else "")
        msgs.append(f"[{i}] {e.get('agent')}: ({what}) {_clip(e.get('value'), 600)}{tail}")
    done = "the team declared completion" if det.get("phi") else "the team did not declare completion"
    return (f"TEAM SPECIFICATION:\nagents:\n{agents}\nspecification: {_clip(spec, 3000)}\n\n"
            f"RUN (one message per produced value):\n{_clip(chr(10).join(msgs), 9000)}\n\n{done}. {outcome}")
