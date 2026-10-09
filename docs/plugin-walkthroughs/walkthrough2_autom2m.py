"""Walkthrough 2 of docs/autom2m-plugin-explained-v0.tex: AutoM2M in host mode (Wallet task).

We play the builder and the agents; the checker, engine and validators are real.
Usage: python walkthrough2_autom2m.py
"""
import copy, json, os, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
os.environ["AGENTM2M_LLM"] = "host"
from agentm2m.auto.workspace import AutoWorkspace, check_team_json
from agentm2m.auto.examples import EXAMPLE_TYPED

TASK = open(os.path.join(HERE, "wallet.py")).read()

d = tempfile.mkdtemp(prefix="cap_auto_")
aw = AutoWorkspace(d)
def show(title, obj, n=5000):
    print(f"\n===== {title}")
    s = obj if isinstance(obj, str) else json.dumps(obj, indent=1, default=str)
    print(s[:n])

show("auto_check devteam_proposal", check_team_json(open(os.path.join(REPO, "teams", "devteam_proposal.json")).read()))
show("auto_task_set", aw.set_task(TASK))
bp = aw.builder_prompt("auto")
show("auto_propose mode", bp["mode"])
show("auto_propose prompt (head)", bp["prompt"], 3500)
print("PROMPT LENGTH chars", len(bp["prompt"]))

team = copy.deepcopy(EXAMPLE_TYPED)
team["name"] = "wallet_team"
team.pop("views")
from agentm2m.auto.compile import GOAL_VIEW
team["views"] = {"Goal": GOAL_VIEW, **{k: v for k, v in EXAMPLE_TYPED["views"].items() if k != "Goal"}}

# a broken first proposal: Tester has no exec (W6), Developer also writes Test (W2), footprint typo (W1), says-clause (W5)
bad = copy.deepcopy(team)
bad["agents"][1]["tools"] = []
bad["writes"]["Developer"] = ["Code", "Test"]
bad["handoffs"][2]["rules"][0]["llm"][0]["footprint"].append("d.method.returnType")
bad["done"] = ["cover(G)", "valid", "fresh", "noObl", "Tester.says('ALL TESTS PASS')"]
show("auto_submit_team (bad)", aw.submit_team(bad))
bp = aw.builder_prompt("auto")
show("auto_propose after rejection: mode", bp["mode"])
show("revise prompt tail", bp["prompt"][-1500:])


show("auto_submit_team (good)", aw.submit_team(team))
show("auto_run #1", aw.run())
nb = aw.next_bindings(None, 10)
show("auto_next_bindings #1", nb, 9000)

DESIGN = {"deposit": "- add cents to self.cents\n- return the new balance",
          "withdraw": "- if cents > self.cents raise ValueError\n- subtract and return the new balance"}
TESTS = {"deposit": "```python\nimport unittest\n\nclass TestDeposit(unittest.TestCase):\n    def test_adds(self):\n        w = Wallet()\n        self.assertEqual(w.deposit(250), 250)\n        self.assertEqual(w.deposit(50), 300)\n```",
         "withdraw": "```python\nimport unittest\n\nclass TestWithdraw(unittest.TestCase):\n    def test_subtracts(self):\n        w = Wallet(); w.deposit(300)\n        self.assertEqual(w.withdraw(120), 180)\n\n    def test_overdraw_rejected(self):\n        w = Wallet(); w.deposit(100)\n        with self.assertRaises(ValueError):\n            w.withdraw(1000)\n```"}
GOOD = {"deposit": "```python\ndef deposit(self, cents):\n    self.cents += cents\n    return self.cents\n```",
        "withdraw": "```python\ndef withdraw(self, cents):\n    if cents > self.cents:\n        raise ValueError('insufficient funds')\n    self.cents -= cents\n    return self.cents\n```"}
WRONG = "```python\ndef withdraw(self, cents):\n    self.cents -= cents\n    return self.cents\n```"

def meth(p):
    return "deposit" if "deposit" in p["target_key"] else "withdraw"

shown_wrong = False
log = []
for rnd in range(8):
    nb = aw.next_bindings(None, 20)
    if not nb["bindings"]:
        break
    for p in nb["bindings"]:
        m = meth(p)
        if p["binding"] == "plan": v = DESIGN[m]
        elif p["rule"] == "Method2Test": v = TESTS[m]
        else:
            if m == "withdraw":
                v = WRONG   # escalate withdraw on purpose
            else:
                v = GOOD[m]
        res = aw.submit_binding(p["target_key"], p["binding"], v, p["footprint_version"])
        log.append((p["target_key"], p["binding"], res["status"]))
        if res["status"] in ("rejected", "escalated") and not shown_wrong:
            show("auto_submit_binding rejected", res, 3000); shown_wrong = True
        if p["rule"] == "Method2Impl" and m == "withdraw" and not any(x[0]=="impl-prompt" for x in log):
            log.append(("impl-prompt", p["prompt"], ""))
for l in log:
    if l[0] == "impl-prompt":
        show("Method2Impl prompt", l[1], 4000)
    else:
        print("submit", *l)
show("auto_run after fills", aw.run())
show("auto_status", aw.status(), 5000)
show("auto_attribute", aw.attribute())
bp = aw.builder_prompt("delta")
show("delta prompt tail", bp["prompt"][-1200:])
# delta: widen footprint of Method2Impl with the examples -> new footprint, new budget
team2 = copy.deepcopy(team)
team2["handoffs"][2]["rules"][0]["llm"][0]["footprint"].append("d.method.examples")
show("auto_submit_team (delta)", aw.submit_team(team2))
for rnd in range(5):
    nb = aw.next_bindings(None, 20)
    if not nb["bindings"]:
        break
    for p in nb["bindings"]:
        m = meth(p)
        v = DESIGN[m] if p["binding"] == "plan" else (TESTS[m] if p["rule"] == "Method2Test" else GOOD[m])
        res = aw.submit_binding(p["target_key"], p["binding"], v, p["footprint_version"])
        print("submit", p["target_key"], p["binding"], res["status"])
show("auto_run final", aw.run())
show("auto_deliverable", aw.deliverable())
show("history", aw.status()["history"], 3000)
print("DIR", d)
