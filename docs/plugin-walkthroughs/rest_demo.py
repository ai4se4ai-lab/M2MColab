"""Drive the hosted service's REST API in-process (FastAPI TestClient), with a throw-away data dir.

Usage: python rest_demo.py   (needs the [serve] extra and httpx)
"""
import json, os, copy, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
os.environ["AGENTM2M_LLM"] = "host"
from fastapi.testclient import TestClient
from agentm2m.server.app import create_app
from agentm2m.auto.examples import EXAMPLE_TYPED
from agentm2m.auto.compile import GOAL_VIEW
DATA = tempfile.mkdtemp(prefix="am2m_svc_")
TASK = open(os.path.join(HERE, "wallet.py")).read()
app = create_app(data_dir=DATA)
c = TestClient(app)
def show(t, r, n=1500):
    body = r.json() if r.headers.get("content-type","").startswith("application/json") else r.text
    print(f"\n===== {t}  -> HTTP {r.status_code}\n" + json.dumps(body, indent=1, default=str)[:n])
h = c.get("/api/health").json()
print("HEALTH", json.dumps({k: h[k] for k in ("status","version","mcp","llm","execution")}, indent=1)[:1500])
r = c.post("/api/keys", json={"label": "student"}); show("create key", r); key = r.json()["key"]
H = {"Authorization": f"Bearer {key}", "X-AgentM2M-Project": "wallet"}
show("task without key", c.post("/api/auto/task", json={"source": TASK}))
show("task", c.post("/api/auto/task", json={"source": TASK}, headers=H))
r = c.get("/api/auto/propose", headers=H); print("\n===== propose -> HTTP", r.status_code, "mode", r.json()["mode"], "prompt chars", len(r.json()["prompt"]))
team = copy.deepcopy(EXAMPLE_TYPED); team["views"] = {"Goal": GOAL_VIEW, **{k:v for k,v in EXAMPLE_TYPED["views"].items() if k!="Goal"}}
show("team", c.post("/api/auto/team", json={"team": team}, headers=H), 600)
show("run", c.post("/api/auto/run", headers=H), 500)
b = c.get("/api/auto/bindings", headers=H, params={"agent":"Tester","limit":1}).json()["bindings"][0]
show("submit tests (needs exec)", c.post("/api/auto/bindings", headers=H, json={"target_key": b["target_key"], "binding": b["binding"], "value": "```python\nimport unittest\nclass T(unittest.TestCase):\n    def test_a(self):\n        self.assertEqual(Wallet().deposit(5), 5)\n```", "footprint_version": b["footprint_version"]}))
show("solve", c.post("/api/auto/solve", headers=H, json={}))
show("mcp without key", c.post("/mcp", json={"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}))
print("\nDATA", sorted(os.listdir(DATA)), os.listdir(os.path.join(DATA, "tenants")))
