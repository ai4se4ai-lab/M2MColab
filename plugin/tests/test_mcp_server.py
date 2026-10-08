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
}


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
