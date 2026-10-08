"""The plugin package itself: manifests, cross-references, hooks, versions.

Catches the mistakes that only show up after install: an agent allowlisting
a tool name the server doesn't expose, a skill without frontmatter, a hook
script that isn't executable or fails on real input, versions out of sync
between the engine and the plugin pin.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import PLUGIN, REPO

from agentm2m import __version__
from agentm2m.workspace import Workspace

SERVER_PREFIX = "mcp__plugin_agentm2m_agentm2m__"


def _frontmatter(path: Path) -> dict:
    text = path.read_text()
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    assert m, f"{path} has no YAML frontmatter"
    import yaml

    return yaml.safe_load(m.group(1))


def _server_tools() -> set[str]:
    src = (REPO / "src/agentm2m/mcp_server.py").read_text()
    return set(re.findall(r"@mcp\.tool\(\)\ndef (\w+)\(", src))


def test_manifests_are_valid_json_and_consistent():
    manifest = json.loads((PLUGIN / ".claude-plugin/plugin.json").read_text())
    market = json.loads((REPO / "plugin/.claude-plugin/marketplace.json").read_text())
    mcp = json.loads((PLUGIN / ".mcp.json").read_text())
    assert manifest["name"] == "agentm2m"
    entry = next(p for p in market["plugins"] if p["name"] == "agentm2m")
    assert (REPO / "plugin" / entry["source"]).resolve() == PLUGIN.resolve()
    # one version everywhere: engine, plugin, marketplace entry, uvx pin, hook pin
    assert manifest["version"] == entry["version"] == __version__
    assert f"agentm2m=={__version__}" in json.dumps(mcp)
    assert f"agentm2m=={__version__}" in (PLUGIN / "hooks/validate-on-edit.sh").read_text()
    pyproject = (REPO / "pyproject.toml").read_text()
    assert f'version = "{__version__}"' in pyproject


def test_mcp_config_launches_the_engine_entry_point():
    cfg = json.loads((PLUGIN / ".mcp.json").read_text())["agentm2m"]
    assert cfg["command"] == "uvx"
    assert cfg["args"][-1] == "agentm2m-mcp"
    assert "agentm2m-mcp = \"agentm2m.mcp_server:main\"" in (REPO / "pyproject.toml").read_text()
    assert cfg["env"]["AGENTM2M_LLM"].startswith("${AGENTM2M_LLM")


def test_skills_have_frontmatter_and_name_real_tools():
    tools = _server_tools()
    assert len(tools) == 12
    skills = sorted((PLUGIN / "skills").glob("*/SKILL.md"))
    assert {p.parent.name for p in skills} == {"init", "run", "change", "evolve", "status", "author-handoff", "agentm2m-concepts"}
    for p in skills:
        fm = _frontmatter(p)
        assert fm["name"] == p.parent.name
        assert 40 < len(fm["description"]) < 400
        body = p.read_text()
        for used in re.findall(r"`(\w+)`", body):
            if used.endswith(("_init", "_status", "_validate", "_show", "_edit", "_binding", "_bindings", "_query", "_evolve")):
                assert used in tools, f"{p.parent.name} mentions unknown tool {used}"


def test_agents_only_allowlist_existing_tools():
    tools = _server_tools()
    agents = {p.stem: _frontmatter(p) for p in (PLUGIN / "agents").glob("*.md")}
    assert set(agents) == {"binding-worker", "handoff-architect"}
    for name, fm in agents.items():
        for t in [x.strip() for x in fm["tools"].split(",")]:
            if t.startswith("mcp__"):
                assert t.startswith(SERVER_PREFIX), t
                assert t[len(SERVER_PREFIX):] in tools, f"{name}: {t} is not a server tool"
    # footprint discipline: the worker cannot read the repository
    worker_tools = {x.strip() for x in agents["binding-worker"]["tools"].split(",")}
    assert worker_tools == {SERVER_PREFIX + "next_bindings", SERVER_PREFIX + "submit_binding"}


def test_hooks_reference_executable_scripts():
    hooks = json.loads((PLUGIN / "hooks/hooks.json").read_text())["hooks"]
    for event in ("SessionStart", "PostToolUse"):
        for group in hooks[event]:
            for h in group["hooks"]:
                script = h["command"].split('"${CLAUDE_PLUGIN_ROOT}/')[1].split('"')[0]
                assert os.access(PLUGIN / script, os.X_OK), script


def _hook(script: str, stdin: str, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(PLUGIN / "hooks" / script)], input=stdin, capture_output=True, text=True,
                          env={"PATH": os.environ["PATH"], **env}, timeout=300)


def test_session_start_hook(project: Path):
    r = _hook("session-start.sh", "{}", {"CLAUDE_PROJECT_DIR": str(project)})
    assert r.returncode == 0 and r.stdout == ""
    Workspace(project, backend="mock").init("research")
    r = _hook("session-start.sh", "{}", {"CLAUDE_PROJECT_DIR": str(project)})
    assert r.returncode == 0 and "team: research" in r.stdout


def test_validate_on_edit_hook(project: Path, uvx: str):
    ws = Workspace(project, backend="host")
    ws.init("devteam")
    env = {"CLAUDE_PROJECT_DIR": str(project), "AGENTM2M_ENGINE": str(REPO),
           "PATH": f"{Path(uvx).parent}:{os.environ['PATH']}"}
    rule = ws.dir / "rules/Req2Arch.agentm2m"

    def payload(p: Path) -> str:
        return json.dumps({"tool_name": "Edit", "tool_input": {"file_path": str(p)}})

    # unrelated file: ignored instantly
    assert _hook("validate-on-edit.sh", payload(project / "README.md"), env).returncode == 0
    # valid rule edit: passes
    r = _hook("validate-on-edit.sh", payload(rule), env)
    assert r.returncode == 0, r.stderr
    # broken rule: exit 2 with the reason on stderr (fed back to Claude)
    rule.write_text(rule.read_text().replace("rule Epic2Component {", "rule Epic2Component { oops"))
    r = _hook("validate-on-edit.sh", payload(rule), env)
    assert r.returncode == 2
    assert "INVALID" in r.stderr and "does not parse" in r.stderr
    # an edit to state/ is never validated by the hook
    assert _hook("validate-on-edit.sh", payload(ws.dir / "state/state.json"), env).returncode == 0


def test_validate_on_edit_hook_without_uvx_is_silent(project: Path):
    Workspace(project, backend="host").init("devteam")
    payload = json.dumps({"tool_input": {"file_path": str(project / ".agentm2m/team.yaml")}})
    r = subprocess.run(["/bin/bash", str(PLUGIN / "hooks/validate-on-edit.sh")], input=payload, capture_output=True, text=True,
                       env={"PATH": "/nonexistent", "CLAUDE_PROJECT_DIR": str(project)})
    assert r.returncode == 0


@pytest.mark.skipif(shutil.which("claude") is None, reason="claude CLI not installed")
def test_claude_plugin_validate_strict():
    for target in (PLUGIN, REPO / "plugin"):
        r = subprocess.run(["claude", "plugin", "validate", "--strict", str(target)], capture_output=True, text=True, timeout=120)
        assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.skipif(shutil.which("claude") is None, reason="claude CLI not installed")
def test_claude_sees_all_components():
    r = subprocess.run(["claude", "--plugin-dir", str(PLUGIN), "plugin", "details", "agentm2m"],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Skills (7)" in r.stdout and "Agents (2)" in r.stdout
    assert "Hooks (2)" in r.stdout and "MCP servers (1)" in r.stdout
