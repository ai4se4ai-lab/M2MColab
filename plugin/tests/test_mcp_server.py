"""End-to-end over MCP stdio: the exact process Claude Code launches.

Spawns `agentm2m-mcp` as a subprocess (the same entry point `.mcp.json` runs
through uvx), drives a full host-mode team run to phi through tool calls,
and checks that user errors surface as readable tool errors.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import anyio
import pytest
from conftest import REPO, good_value

pytest.importorskip("mcp")
from mcp import StdioServerParameters  # noqa: E402
from mcp.client.session import ClientSession  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

EXPECTED_TOOLS = {
    "team_init", "team_status", "team_validate", "model_show", "model_edit", "impact", "run",
    "next_bindings", "submit_binding", "trace_query", "team_evolve", "acceptance",
    "auto_task_set", "auto_check", "auto_propose", "auto_submit_team", "auto_run", "auto_next_bindings",
    "auto_submit_binding", "auto_status", "auto_attribute", "auto_deliverable", "auto_solve", "auto_reset",
}

TEAMS = REPO / "teams"
SKELETON = 'def add(a, b):\n    """Return a + b.\n    >>> add(1, 2)\n    3\n    """\n'
TESTS = "```python\nimport unittest\nclass T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2, 2), 4)\n```"
CODE = "```python\ndef add(a, b):\n    return a + b\n```"


def _server_cmd() -> list[str]:
    exe = shutil.which("agentm2m-mcp") or str(Path(sys.executable).with_name("agentm2m-mcp"))
    if os.path.exists(exe):
        return [exe]
    return [sys.executable, "-m", "agentm2m.mcp_server"]


def _params(project: Path, **env) -> StdioServerParameters:
    cmd = _server_cmd()
    return StdioServerParameters(
        command=cmd[0], args=cmd[1:],
        env={"AGENTM2M_PROJECT_DIR": str(project), "PATH": os.environ.get("PATH", ""),
             "PYTHONPATH": str(REPO / "src"), **env},
    )


def _is_error(res) -> bool:
    return bool(getattr(res, "is_error", None) or getattr(res, "isError", None))


async def _call(s: ClientSession, name: str, args: dict | None = None) -> dict:
    res = await s.call_tool(name, args or {})
    text = res.content[0].text if res.content else ""
    assert not _is_error(res), f"{name} failed: {text}"
    return json.loads(text)


async def _call_err(s: ClientSession, name: str, args: dict) -> str:
    res = await s.call_tool(name, args)
    assert _is_error(res), f"{name} unexpectedly succeeded"
    return res.content[0].text


def test_full_host_workflow_over_stdio(project: Path):
    async def main():
        async with stdio_client(_params(project)) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                tools = {t.name for t in (await s.list_tools()).tools}
                assert tools == EXPECTED_TOOLS

                st = await _call(s, "team_status")
                assert st["workspace"] is None and "devteam" in st["templates"]

                init = await _call(s, "team_init", {"template": "devteam"})
                assert init["team"] == "devteam" and init["backend"] == "host"
                assert (await _call(s, "team_validate"))["ok"] is True

                run = await _call(s, "run")
                assert run["pending"] == 5 and run["blocked"] == 2

                accepted = 0
                for _ in range(10):
                    batch = (await _call(s, "next_bindings", {"limit": 50}))["bindings"]
                    if not batch:
                        break
                    for b in batch:
                        res = await _call(s, "submit_binding", {"target_key": b["target_key"], "binding": b["binding"], "value": good_value(b), "footprint_version": b["footprint_version"]})
                        assert res["status"] == "accepted", res
                        accepted += 1
                assert accepted == 7
                assert (await _call(s, "acceptance"))["phi"] is True

                ops = [{"op": "set", "key": "Criterion#S2.1", "values": {"text": "returns HTTP 409"}}]
                imp = await _call(s, "impact", {"view": "Req", "ops": ops, "as_agent": "Analyst"})
                assert len(imp["obligations"]) == 3
                await _call(s, "model_edit", {"view": "Req", "ops": ops, "as_agent": "Analyst"})
                assert (await _call(s, "acceptance"))["phi"] is False

                shown = await _call(s, "model_show", {"view": "Req", "key": "Criterion#S2.1"})
                assert shown["element"]["text"] == "returns HTTP 409"
                tq = await _call(s, "trace_query", {"key": "Criterion#S2.1"})
                assert tq["downstream"][0]["target"] == "Test:TestCase#S2.1"

                evo = await _call(s, "team_evolve", {
                    "agent": "SecurityReviewer", "view": "Sec", "handoff": "Arch2Sec",
                    "rule": "rules/extra/Arch2Sec.agentm2m", "view_spec_file": "rules/extra/SecurityReviewer.view.yaml"})
                assert evo["existing_matches"] == 2
                sec = await _call(s, "next_bindings", {"agent": "SecurityReviewer", "limit": 10})
                assert len(sec["bindings"]) == 4

                # errors are readable tool errors, not crashes
                msg = await _call_err(s, "model_edit", {"view": "Req", "ops": ops, "as_agent": "Tester"})
                assert "write rights" in msg
                msg = await _call_err(s, "team_init", {"template": "devteam"})
                assert "already exists" in msg
                msg = await _call_err(s, "model_show", {"view": "Nope"})
                assert "unknown view" in msg
                msg = await _call_err(s, "team_evolve", {"agent": "a", "view": "V", "handoff": "h", "rule": "r", "view_spec_file": "../../etc/passwd"})
                assert "inside .agentm2m" in msg

    anyio.run(main)


def test_engine_backend_over_stdio(project: Path):
    async def main():
        async with stdio_client(_params(project, AGENTM2M_LLM="mock")) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                await _call(s, "team_init", {"template": "research"})
                run = await _call(s, "run")
                assert run["backend"] == "mock" and run["pending"] == 0
                assert run["handoffs"]["Lit2Plan"]["created"] == 4
                # n:m hand-off: latency has 2 claims x 2 plans = 4 sections, +1 each for the other two topics
                assert run["handoffs"]["ExpLit2Report"]["created"] == 6
                assert run["phi"] is True
                msg = await _call_err(s, "next_bindings", {})
                assert "sampled by the engine" in msg

    anyio.run(main)


def test_autom2m_host_workflow_over_stdio(project: Path):
    """Claude as builder and value-filler: task -> rejected proposal -> admitted team -> phi -> code."""
    async def main():
        async with stdio_client(_params(project)) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                st = await _call(s, "auto_status")
                assert st["task"] is None and st["team"] is None
                msg = await _call_err(s, "auto_propose", {})
                assert "no task yet" in msg

                t = await _call(s, "auto_task_set", {"source": SKELETON})
                assert t["task"]["entry"] == "add"
                p = await _call(s, "auto_propose", {})
                assert p["mode"] == "propose" and "typed team" in p["prompt"].lower()

                bad = json.loads((TEAMS / "devteam_proposal.json").read_text())
                chk = await _call(s, "auto_check", {"team_json": bad})
                assert chk["admitted"] is False and chk["violated"] == ["W1", "W2", "W4", "W5", "W6"]
                sub = await _call(s, "auto_submit_team", {"team_json": bad})
                assert sub["admitted"] is False
                assert (await _call(s, "auto_propose", {}))["mode"] == "revise"

                good = (TEAMS / "classeval_reference.json").read_text()
                sub = await _call(s, "auto_submit_team", {"team_json": good})
                assert sub["admitted"] is True and sub["mode"] == "admitted"
                assert (await _call(s, "auto_check", {}))["admitted"] is True

                run = await _call(s, "auto_run")
                assert run["phi"] is False and run["pending"] == 1 and run["blocked_on_upstream"] == 1
                for expected_agent, value in (("Tester", TESTS), ("Developer", CODE)):
                    nb = await _call(s, "auto_next_bindings", {})
                    (b,) = nb["bindings"]
                    assert b["agent"] == expected_agent
                    res = await _call(s, "auto_submit_binding", {"target_key": b["target_key"], "binding": b["binding"],
                                                                 "value": value, "footprint_version": b["footprint_version"]})
                    assert res["status"] == "accepted", res
                assert (await _call(s, "auto_run"))["phi"] is True
                st = await _call(s, "auto_status")
                assert st["phi"] is True and st["team"]["agents"] == ["Tester", "Developer"]
                d = await _call(s, "auto_deliverable")
                assert d["complete"] and "return a + b" in d["code"]
                assert (await _call(s, "auto_attribute"))["faults"] == []
                msg = await _call_err(s, "auto_solve", {})
                assert "no engine LLM" in msg
                assert (await _call(s, "auto_reset"))["reset"] is True
                assert (await _call(s, "auto_status"))["task"] is None

    anyio.run(main)


# ---------------------------------------------------------------------------
# remote mode: the plugin's stdio server forwards to a hosted service
# ---------------------------------------------------------------------------


@pytest.fixture
def hosted(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    import socket
    import threading
    import time

    import uvicorn

    from agentm2m.server.app import create_app

    monkeypatch.setenv("AGENTM2M_LLM", "host")
    monkeypatch.delenv("AGENTM2M_SANDBOX", raising=False)
    monkeypatch.setenv("AGENTM2M_ALLOW_UNSANDBOXED_EXEC", "1")
    with socket.socket() as so:
        so.bind(("127.0.0.1", 0))
        port = so.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(data_dir=tmp_path / "svc", web_dist=tmp_path / "none"),
                                           host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    import requests

    url = f"http://127.0.0.1:{port}"
    key = requests.post(f"{url}/api/keys", json={"label": "plugin"}, timeout=10).json()["key"]
    yield url, key
    server.should_exit = True
    th.join(timeout=10)
    from agentm2m.auto import sandbox

    sandbox.require_isolation(False)


def test_plugin_server_forwards_to_hosted_service(project: Path, hosted):
    import requests

    url, key = hosted

    async def main():
        env = {"AGENTM2M_URL": url, "AGENTM2M_API_KEY": key, "AGENTM2M_PROJECT": "plug"}
        async with stdio_client(_params(project, **env)) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                assert (await _call(s, "auto_task_set", {"source": SKELETON}))["task"]["entry"] == "add"
                assert (await _call(s, "auto_submit_team", {"team_json": (TEAMS / "classeval_reference.json").read_text()}))["admitted"]
                await _call(s, "auto_run")
                for value in (TESTS, CODE):
                    (b,) = (await _call(s, "auto_next_bindings"))["bindings"]
                    res = await _call(s, "auto_submit_binding", {"target_key": b["target_key"], "binding": b["binding"],
                                                                 "value": value, "footprint_version": b["footprint_version"]})
                    assert res["status"] == "accepted"
                st = await _call(s, "auto_status")
                assert st["phi"] is True
                # the state lives on the service, under the key's project; nothing local
                assert "/tenants/" in st["project_dir"] and st["project_dir"].endswith("/plug")
                # hosted tool errors come back as readable tool errors
                assert "write rights" not in await _call_err(s, "model_show", {"view": "Nope"})

    anyio.run(main)
    assert not (project / ".agentm2m").exists()
    st = requests.get(f"{url}/api/auto/status?project=plug", headers={"Authorization": f"Bearer {key}"}, timeout=30).json()
    assert st["phi"] is True


def test_plugin_server_reports_a_bad_key(project: Path, hosted):
    url, _key = hosted

    async def main():
        async with stdio_client(_params(project, AGENTM2M_URL=url, AGENTM2M_API_KEY="am2m_wrong")) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                msg = await _call_err(s, "auto_status", {})
                assert "rejected the API key" in msg

    anyio.run(main)
