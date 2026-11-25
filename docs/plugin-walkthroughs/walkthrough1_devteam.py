"""Walkthrough 1 of docs/autom2m-plugin-explained-v0.tex: the DevTeam template in host mode.

Runs the same Workspace methods the MCP tools call, typing the values an LLM would write
(some deliberately wrong). Usage: python walkthrough1_devteam.py
"""
import json, os, sys, tempfile
os.environ["AGENTHOT_LLM"] = "host"
from agenthot.workspace import Workspace

d = tempfile.mkdtemp(prefix="cap_am2m_")
ws = Workspace(d)
def show(title, obj, n=4000):
    print(f"\n===== {title}")
    s = json.dumps(obj, indent=1, default=str)
    print(s[:n])

r = ws.init("devteam")
show("team_init", {k: r[k] for k in ("team", "backend", "agents", "views", "handoffs", "bindings", "phi", "open_total")})
show("team_validate", ws.validate())
show("run#1", ws.run())
nb = ws.next_bindings(None, 10)
show("next_bindings (all)", nb, 9000)

# answer the bindings with plausible values, showing one rejection first
good = {
  "signature": {"Story2Operation::op::S1": "create_task(title: str) -> Task",
                "Story2Operation::op::S2": "mark_done(task_id: int) -> Task"},
}
def answer(p):
    tk, b = p["target_key"], p["binding"]
    if b == "signature":
        return "create_task(title: str) -> Task" if "S1" in tk else "mark_done(task_id: int) -> Task"
    if b == "oracle":
        crit = tk.split("::")[-1]
        if "S1.1" in crit:
            return ("def test_oracle():\n    try:\n        implementation('')\n    except ValueError:\n        return\n"
                    "    raise AssertionError('empty title accepted')")
        if "S2.1" in crit:
            return ("def test_oracle():\n    t = implementation(1)\n    try:\n        implementation(1)\n    except ValueError:\n        return\n"
                    "    raise AssertionError('done twice')")
        return "def test_oracle():\n    assert implementation() is not None"
    if b == "body":
        return "def f(x):\n    return x"
    return "x"

# first: a bad signature to show rejection
p0 = next(p for p in nb["bindings"] if p["binding"] == "signature")
bad = ws.submit_binding(p0["target_key"], p0["binding"], "Here is the signature: create_task", p0["footprint_version"])
show("submit_binding (bad)", bad)
for _ in range(10):
    nb = ws.next_bindings(None, 20)
    if not nb["bindings"]:
        break
    for p in nb["bindings"]:
        res = ws.submit_binding(p["target_key"], p["binding"], answer(p), p["footprint_version"])
        print("submit", p["target_key"], p["binding"], res["status"], res.get("reason", ""))
show("run#2", ws.run())
show("acceptance", ws.acceptance())
show("status", ws.status(), 3000)
show("trace_query Criterion#S2.1", ws.trace_query("Criterion#S2.1"))
ops = [{"op": "set", "key": "Criterion#S2.1", "values": {"text": "completing a done task returns HTTP 409"}}]
show("impact preview", ws.impact("Req", ops, "Analyst"))
try:
    ws.edit("Req", ops, "Architect")
except Exception as e:
    print("\n===== edit as wrong agent\n", e)
try:
    ws.edit("Arch", [{"op": "set", "key": "Operation#op_s2", "values": {"name": "x"}}], "Architect")
except Exception as e:
    print("\n===== edit engine-owned\n", e)
show("model_edit", ws.edit("Req", ops, "Analyst"))
nb = ws.next_bindings(None, 10)
show("next_bindings after change", nb, 6000)
# evolve
import yaml
from importlib import resources
spec = yaml.safe_load(open(os.path.join(d, ".agenthot/rules/extra/SecurityReviewer.view.yaml")))
show("team_evolve", ws.evolve("SecurityReviewer", "Sec", spec, "Arch2Sec", "rules/extra/Arch2Sec.agenthot"))
show("run after evolve", ws.run())
print("\nDIR", d)

show("trace_query UserStory#S2", ws.trace_query("UserStory#S2"))
show("one PendingBinding (raw)", ws.next_bindings("SecurityReviewer", 1)["bindings"][0])
for _ in range(6):
    nb = ws.next_bindings(None, 20)
    if not nb["bindings"]: break
    for p in nb["bindings"]:
        b = p["binding"]
        if b == "risk": v = "medium"
        elif b == "notes": v = "The operation changes task state; only the task owner should be allowed to call it."
        elif b == "signature": v = "mark_done(task_id: int) -> Task"
        elif b == "oracle": v = ("def test_oracle():\n    implementation(1)\n    try:\n        implementation(1)\n    except Exception as e:\n        assert '409' in str(e)\n        return\n    raise AssertionError('no 409')")
        else: v = "def mark_done(task_id):\n    raise ValueError('HTTP 409')"
        r = ws.submit_binding(p["target_key"], b, v, p["footprint_version"])
        print("submit", p["target_key"], b, r["status"], r.get("reason",""))
show("run final", ws.run())
show("status final", {k: ws.status()[k] for k in ("bindings","evolutions","phi")})
