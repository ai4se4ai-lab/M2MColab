"""The one place untrusted code runs: modes, fail-closed policy, and the
paths that used to bypass it."""
from __future__ import annotations

import shutil

import pytest

from agenthot import sandbox
from agenthot.engine.validators import run_pytest_oracle
from agenthot.workspace import Workspace, WorkspaceError


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.delenv("AGENTHOT_SANDBOX", raising=False)
    monkeypatch.delenv("AGENTHOT_SANDBOX_CMD", raising=False)
    sandbox._probes.clear()
    yield
    sandbox.require_isolation(False)
    sandbox._probes.clear()


def test_process_mode_runs_and_reports_exit_code():
    r = sandbox.run_python("import sys; print('hi'); sys.exit(3)")
    assert (r.ok, r.stdout.strip(), r.returncode) == (False, "hi", 3)
    assert sandbox.execution_status()["enabled"] is True


def test_isolation_required_refuses_process_mode():
    sandbox.require_isolation()
    st = sandbox.execution_status()
    assert st["enabled"] is False and not st["isolated"]
    with pytest.raises(sandbox.SandboxRefused, match="no isolating sandbox"):
        sandbox.run_python("print(1)")
    # the AgentHOT engine's executable oracle goes through the same gate
    with pytest.raises(sandbox.SandboxRefused):
        run_pytest_oracle("print('OK')", "")


def test_off_and_unknown_modes_fail_closed(monkeypatch):
    for m in ("off", "nonsense"):
        monkeypatch.setenv("AGENTHOT_SANDBOX", m)
        assert sandbox.execution_status()["enabled"] is False
        with pytest.raises(sandbox.SandboxRefused):
            sandbox.run_python("print(1)")


def test_command_mode_wraps_every_run(monkeypatch, tmp_path):
    log = tmp_path / "wrapped"
    wrapper = tmp_path / "wrap.sh"
    wrapper.write_text(f'#!/bin/sh\necho wrapped >> "{log}"\nexec "$@"\n')
    wrapper.chmod(0o755)
    monkeypatch.setenv("AGENTHOT_SANDBOX", "command")
    monkeypatch.setenv("AGENTHOT_SANDBOX_CMD", str(wrapper))
    sandbox.require_isolation()  # an isolating mode is allowed under the policy
    assert sandbox.run_python("print(6 * 7)").stdout.strip() == "42"
    assert log.read_text() == "wrapped\n" * 2  # the one-off start-up probe, then the run
    monkeypatch.delenv("AGENTHOT_SANDBOX_CMD")
    with pytest.raises(sandbox.SandboxRefused, match="AGENTHOT_SANDBOX_CMD"):
        sandbox.run_python("print(1)")


def test_a_sandbox_that_cannot_start_fails_closed(monkeypatch, tmp_path):
    broken = tmp_path / "broken.sh"
    broken.write_text("#!/bin/sh\necho 'cannot create namespace' >&2\nexit 1\n")
    broken.chmod(0o755)
    monkeypatch.setenv("AGENTHOT_SANDBOX", "command")
    monkeypatch.setenv("AGENTHOT_SANDBOX_CMD", str(broken))
    st = sandbox.execution_status()
    assert st["enabled"] is False and "cannot create namespace" in st["reason"]
    with pytest.raises(sandbox.SandboxRefused, match="cannot start a run"):
        sandbox.run_python("print(1)")


def test_bwrap_argv_hides_home_and_network(tmp_path):
    argv = sandbox._bwrap_argv(str(tmp_path))
    assert argv[:2] == ["bwrap", "--unshare-all"]
    assert ["--bind", str(tmp_path), str(tmp_path)] == argv[-5:-2]
    mounted = {argv[i + 1] for i, a in enumerate(argv) if a in ("--ro-bind", "--ro-bind-try", "--bind")}
    assert not any(m == "/home" or m.startswith(str(sandbox.os.path.expanduser("~")) + "/.agenthot") for m in mounted)


@pytest.mark.skipif(shutil.which("bwrap") is None, reason="bubblewrap not installed")
def test_bwrap_mode_isolates_when_namespaces_are_available(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTHOT_SANDBOX", "bwrap")
    if not sandbox.execution_status()["enabled"]:
        pytest.skip("bubblewrap cannot create namespaces on this host: " + sandbox.execution_status()["reason"])
    secret = tmp_path / "secret.txt"
    secret.write_text("tenant data")
    r = sandbox.run_python(f"import os, socket\nprint(os.path.exists({str(secret)!r}))\n"
                           "s = socket.socket()\ns.settimeout(2)\n"
                           "try:\n    s.connect(('1.1.1.1', 80)); print('net')\nexcept OSError:\n    print('nonet')\n")
    assert r.stdout.split() == ["False", "nonet"], r.stderr


def test_rule_text_cannot_create_python(tmp_path):
    ws = Workspace(tmp_path, backend="host")
    ws.init("devteam")
    with pytest.raises(WorkspaceError, match=r"only create a \.agenthot"):
        ws.evolve("Evil", "Ev", {"classes": {"X": {"attributes": {"a": "string"}}}}, "Ev", "rules/evil.py",
                  "import os\nos.system('touch /tmp/pwned')\n")
    assert not (ws.dir / "rules/evil.py").exists()


def test_rule_expressions_cannot_reach_python_internals():
    from agenthot.rt_helpers import nav
    from agenthot.engine.expr import OCLEvalError, eval_expr
    from agenthot.rules.parser import parse_module

    def expr(text):
        mod = parse_module(f"module M; create O : X from I : Y;\nrule R {{ from s : Y!Z to t : X!Z ( v <- {text} ) }}")
        return mod.rules[0].to_clause.patterns[0].bindings[0].expr

    class Obj:
        name = "ok"

    assert eval_expr(expr("s.name"), {"s": Obj()}, {}) == "ok"
    for bad in ("s.__class__", "s.__init__.__globals__", "'{0.__class__}'.format(s)", "s._secret"):
        with pytest.raises(OCLEvalError, match="not accessible"):
            eval_expr(expr(bad), {"s": Obj()}, {})
    with pytest.raises(ValueError):
        nav(Obj(), "name.__class__")
