"""Shared fixtures for the Claude Code plugin tests.

`GOOD` answers pass the devteam template's validators; `fill_all` plays the
role of the binding-worker subagent: it drains `next_bindings` and submits a
value per binding, exactly as Claude Code does through MCP.
"""
from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PLUGIN = REPO / "plugin" / "autom2m"
sys.path.insert(0, str(REPO / "src"))

from agenthot.workspace import Workspace  # noqa: E402

GOOD_SIGNATURE = "markTaskDone(id: TaskId) -> Task"
GOOD_BODY = "def op(task_id):\n    return {'id': task_id, 'done': True}\n"
GOOD_ORACLE = "def test_oracle():\n    result = implementation('t1')\n    assert result['done'] is True\n"
GOOD_NOTES = "Requires an authenticated caller; only the task owner may modify it."
GOOD_RISK = "medium"


def good_value(binding: dict) -> str:
    return {
        "signature": GOOD_SIGNATURE,
        "body": GOOD_BODY,
        "oracle": GOOD_ORACLE,
        "notes": GOOD_NOTES,
        "risk": GOOD_RISK,
    }[binding["binding"]]


def fill_all(ws: Workspace, answer: Callable[[dict], str] = good_value, *, agent: str | None = None, rounds: int = 20) -> list[dict]:
    """Drain every pending binding (re-running so blocked ones unblock)."""
    results: list[dict] = []
    for _ in range(rounds):
        batch = ws.next_bindings(agent=agent, limit=50)["bindings"]
        if not batch:
            return results
        for b in batch:
            results.append(ws.submit_binding(b["target_key"], b["binding"], answer(b), b["footprint_version"]))
    raise AssertionError("bindings did not drain")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    d = tmp_path / "project"
    d.mkdir()
    return d


@pytest.fixture
def host_ws(project: Path) -> Workspace:
    ws = Workspace(project, backend="host", max_resamples=3)
    ws.init("devteam")
    return ws


@pytest.fixture
def uvx() -> str:
    exe = shutil.which("uvx") or str(REPO / ".venv" / "bin" / "uvx")
    if not os.path.exists(exe):
        pytest.skip("uvx not available")
    return exe
