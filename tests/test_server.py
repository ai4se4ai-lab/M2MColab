"""The hosted service: keys, REST API, tenant isolation, and MCP over
streamable HTTP through a real uvicorn server."""
from __future__ import annotations

import json

from autom2m import __version__
import socket
import threading
import time
from pathlib import Path

import anyio
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("mcp")
import uvicorn
from starlette.testclient import TestClient

from autom2m.server.app import create_app

TEAMS = Path(__file__).resolve().parents[1] / "teams"
REF = json.loads((TEAMS / "pair_team.json").read_text())
FUNC_SRC = 'def add(a, b):\n    """Return a + b.\n    >>> add(1, 2)\n    3\n    """\n'
TESTS = "```python\nimport unittest\nclass T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(1, 2), 3)\n        self.assertEqual(add(2, 2), 4)\n```"
CODE = "```python\ndef add(a, b):\n    return a + b\n```"


@pytest.fixture(autouse=True)
def _isolation_policy_reset():
    yield
    from agenthot import sandbox

    sandbox.require_isolation(False)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENTHOT_SOLVE_LLM", raising=False)
    monkeypatch.delenv("AGENTHOT_SANDBOX", raising=False)
    monkeypatch.setenv("AGENTHOT_LLM", "host")
    # these tests execute validator code: opt in as a trusted single-tenant deployment would
    monkeypatch.setenv("AGENTHOT_ALLOW_UNSANDBOXED_EXEC", "1")
    app = create_app(data_dir=tmp_path / "data", web_dist=tmp_path / "nodist")
    with TestClient(app) as c:
        yield c


@pytest.fixture
def locked_client(tmp_path, monkeypatch):
    """The default hosted configuration: no isolating sandbox, no opt-in."""
    monkeypatch.delenv("AGENTHOT_SANDBOX", raising=False)
    monkeypatch.delenv("AGENTHOT_ALLOW_UNSANDBOXED_EXEC", raising=False)
    monkeypatch.setenv("AGENTHOT_LLM", "host")
    app = create_app(data_dir=tmp_path / "data", web_dist=tmp_path / "nodist")
    with TestClient(app) as c:
        yield c


def test_hosted_service_refuses_unsandboxed_code_execution(locked_client):
    c = locked_client
    ex = c.get("/api/health").json()["execution"]
    assert ex == {"sandbox": "process", "isolated": False, "enabled": False, "reason": ex["reason"]}
    assert "no isolating sandbox" in ex["reason"]
    h = {"Authorization": f"Bearer {_key(c)}"}
    # everything that does not execute code still works
    assert c.post("/api/auto/check", json={"team": REF}).json()["admitted"] is True
    c.post("/api/auto/task", json={"source": FUNC_SRC}, headers=h)
    assert c.post("/api/auto/team", json={"team": REF}, headers=h).json()["admitted"] is True
    assert c.post("/api/auto/run", headers=h).json()["pending"] == 1
    (b,) = c.get("/api/auto/bindings", headers=h).json()["bindings"]
    # submitting a value whose validator executes code is refused, with the reason
    r = c.post("/api/auto/bindings", headers=h, json={"target_key": b["target_key"], "binding": b["binding"],
                                                       "value": TESTS, "footprint_version": b["footprint_version"]})
    assert r.status_code == 403 and "no isolating sandbox" in r.json()["detail"]


def _key(c, label="test") -> str:
    r = c.post("/api/keys", json={"label": label})
    assert r.status_code == 201
    return r.json()["key"]


def test_health_pricing_and_docs_are_public(client):
    h = client.get("/api/health").json()
    assert h["status"] == "ok" and h["version"] == __version__
    assert h["mcp"]["endpoint"] == "/mcp" and "auto_check" in h["mcp"]["tools"] and h["mcp"]["tool_count"] == 24
    assert h["llm"] == {"mode": "host", "solve_backend": None}
    tiers = client.get("/api/pricing").json()["tiers"]
    assert [t["id"] for t in tiers] == ["free", "pro"]
    assert client.get("/api/docs").status_code == 200
    spec = client.get("/api/openapi.json").json()
    assert "/api/auto/team" in spec["paths"]
    assert {s["name"] for s in client.get("/api/teams/samples").json()["samples"]} >= {"devteam_proposal", "classeval_reference"}


def test_key_lifecycle(client):
    r = client.post("/api/keys", json={"label": "laptop", "email": "a@b.c"}).json()
    key = r["key"]
    assert key.startswith("am2m_") and r["record"]["tier"] == "free" and r["record"]["label"] == "laptop"
    assert client.get("/api/keys/me").status_code == 401
    assert client.get("/api/keys/me", headers={"Authorization": "Bearer am2m_nope"}).status_code == 401
    me = client.get("/api/keys/me", headers={"X-API-Key": key}).json()
    assert me["id"] == r["record"]["id"] and me["request_count"] >= 1
    assert client.delete("/api/keys/me", headers={"Authorization": f"Bearer {key}"}).json()["revoked"] is True
    assert client.get("/api/keys/me", headers={"Authorization": f"Bearer {key}"}).status_code == 401
    assert client.get("/api/health").json()["keys"] == {"active": 0, "total": 1}


def test_public_checker(client):
    bad = json.loads((TEAMS / "devteam_proposal.json").read_text())
    r = client.post("/api/auto/check", json={"team": bad}).json()
    assert r["admitted"] is False and r["violated"] == ["W1", "W2", "W4", "W5", "W6"]
    assert client.post("/api/auto/check", json={"team": REF}).json()["admitted"] is True
    assert client.post("/api/auto/check", json={"team": "{oops"}).json()["admitted"] is False


def test_rest_host_flow_and_tenant_isolation(client):
    k1, k2 = _key(client, "one"), _key(client, "two")
    h1 = {"Authorization": f"Bearer {k1}"}
    assert client.post("/api/auto/task", json={"source": FUNC_SRC}).status_code == 401
    assert client.post("/api/auto/task", json={"source": FUNC_SRC}, headers=h1).json()["task"]["entry"] == "add"
    assert client.get("/api/auto/propose", headers=h1).json()["mode"] == "propose"
    assert client.post("/api/auto/team", json={"team": REF}, headers=h1).json()["admitted"] is True
    assert client.post("/api/auto/run", headers=h1).json()["pending"] == 1
    for value in (TESTS, CODE):
        (b,) = client.get("/api/auto/bindings", headers=h1).json()["bindings"]
        res = client.post("/api/auto/bindings", headers=h1, json={
            "target_key": b["target_key"], "binding": b["binding"], "value": value,
            "footprint_version": b["footprint_version"]}).json()
        assert res["status"] == "accepted"
    assert client.get("/api/auto/status", headers=h1).json()["phi"] is True
    assert "return a + b" in client.get("/api/auto/deliverable", headers=h1).json()["code"]
    # another key sees nothing; another project of the same key is empty too
    h2 = {"Authorization": f"Bearer {k2}"}
    assert client.get("/api/auto/status", headers=h2).json()["task"] is None
    assert client.get("/api/auto/status", headers={**h1, "X-AgentHOT-Project": "other"}).json()["task"] is None
    assert client.get("/api/auto/status?project=../x", headers=h1).status_code == 400
    # errors are 409 with the actionable message; solve needs a server LLM
    r = client.get("/api/auto/propose", headers=h2)
    assert r.status_code == 409 and "no task yet" in r.json()["detail"]
    assert client.post("/api/auto/solve", json={}, headers=h1).status_code == 501
    assert client.delete("/api/auto", headers=h1).json()["reset"] is True


def test_mcp_requires_a_key(client):
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401 and "API key" in r.json()["detail"]
    assert client.get("/api/health").json()["metrics"]["unauthorized"] >= 1


# ---------------------------------------------------------------------------
# MCP over streamable HTTP against a real server
# ---------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live_server(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENTHOT_SOLVE_LLM", raising=False)
    monkeypatch.delenv("AGENTHOT_SANDBOX", raising=False)
    monkeypatch.setenv("AGENTHOT_LLM", "host")
    monkeypatch.setenv("AGENTHOT_ALLOW_UNSANDBOXED_EXEC", "1")
    port = _free_port()
    app = create_app(data_dir=tmp_path / "data", web_dist=tmp_path / "nodist")
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    th.join(timeout=10)


def test_mcp_over_http_with_key(live_server):
    import httpx2
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    with httpx2.Client() as h:
        key = h.post(f"{live_server}/api/keys", json={"label": "mcp"}).json()["key"]

    async def call(s, name, args=None):
        res = await s.call_tool(name, args or {})
        assert not (getattr(res, "is_error", None) or getattr(res, "isError", None)), res.content[0].text
        return json.loads(res.content[0].text)

    async def main():
        headers = {"Authorization": f"Bearer {key}", "X-AgentHOT-Project": "demo"}
        async with httpx2.AsyncClient(headers=headers, timeout=60) as hc:
            async with streamable_http_client(f"{live_server}/mcp", http_client=hc) as (r, w, *_):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    tools = {t.name for t in (await s.list_tools()).tools}
                    assert {"auto_check", "auto_submit_team", "team_init"} <= tools
                    await call(s, "auto_task_set", {"source": FUNC_SRC})
                    assert (await call(s, "auto_submit_team", {"team_json": REF}))["admitted"] is True
                    await call(s, "auto_run")
                    for value in (TESTS, CODE):
                        (b,) = (await call(s, "auto_next_bindings"))["bindings"]
                        res = await call(s, "auto_submit_binding", {"target_key": b["target_key"], "binding": b["binding"],
                                                                    "value": value, "footprint_version": b["footprint_version"]})
                        assert res["status"] == "accepted"
                    assert (await call(s, "auto_status"))["phi"] is True
                    # the AgentHOT tools are tenant-scoped too
                    assert (await call(s, "team_status"))["workspace"] is None

    anyio.run(main)
    with httpx2.Client() as h:
        m = h.get(f"{live_server}/api/health").json()["metrics"]
        assert m["tool_calls"]["auto_submit_binding"] == 2 and m["mcp_requests"] > 0
        # REST sees the same project state as MCP
        st = h.get(f"{live_server}/api/auto/status?project=demo", headers={"Authorization": f"Bearer {key}"}).json()
        assert st["phi"] is True
        # a forged tenant header without a key is still refused
        assert h.post(f"{live_server}/mcp", headers={"x-agenthot-tenant": "x"},
                      json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).status_code == 401
