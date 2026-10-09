"""Run untrusted, LLM-written Python in a subprocess.

Each run gets a fresh temporary working directory, CPU / address-space /
file-size / process limits and a wall-clock timeout. Network access is not
blocked (no unprivileged namespaces on the experiment machine); this is
reported as a threat to validity.

The interpreter is `AM2M_SANDBOX_PY` if set, else the repository's
`.venv-sbx` when running from a checkout, else the current interpreter.

Isolation (`AGENTM2M_SANDBOX`):

  process  (default) a same-user subprocess with resource limits. NOT an
           isolation boundary: the code can read anything the caller can and
           use the network. Fine for the local CLI / plugin (you run your own
           team's code), not for a shared service.
  bwrap    bubblewrap: no network, own PID/IPC/UTS/user namespaces, a fresh
           /proc, only the system directories (and the interpreter's prefix)
           mounted read-only plus the run's temp dir. The caller's home, data
           directory and other tenants' files are not visible. Needs
           unprivileged user namespaces (or a container started with them).
  command  prefix every run with `AGENTM2M_SANDBOX_CMD` (e.g. an nsjail or
           firejail invocation); the wrapper is responsible for isolation.
  off      never execute: behaviour validators reject with a clear reason.

A multi-tenant host calls `require_isolation()`: `process` is then refused
too (fail closed), so code from one API key can never run with the
service's own privileges.
"""
from __future__ import annotations

import json
import os
import resource
import shlex
import subprocess
import sys
import tempfile
from dataclasses import dataclass

PY = os.environ.get("AM2M_SANDBOX_PY") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", ".venv-sbx", "bin", "python")
if not os.path.exists(PY):
    PY = sys.executable


@dataclass
class ExecResult:
    ok: bool
    stdout: str
    stderr: str
    timeout: bool = False
    returncode: int | None = None

    def tail(self, n: int = 1200) -> str:
        text = (self.stderr or "") + ("\n" + self.stdout if self.stdout else "")
        return text[-n:]


ISOLATING = ("bwrap", "command")
_require_isolation = False


class SandboxRefused(RuntimeError):
    """Code execution is not allowed with the configured sandbox."""


def mode() -> str:
    m = (os.getenv("AGENTM2M_SANDBOX") or "process").strip().lower()
    return m if m in ("process", "bwrap", "command", "off") else "off"  # unknown -> fail closed


def require_isolation(flag: bool = True) -> None:
    """Refuse non-isolating modes (set by the hosted, multi-tenant service)."""
    global _require_isolation
    _require_isolation = flag


_probes: dict[tuple, str] = {}


def _wrapper(m: str, workdir: str) -> list[str]:
    if m == "bwrap":
        return _bwrap_argv(workdir)
    if m == "command":
        return shlex.split(os.environ.get("AGENTM2M_SANDBOX_CMD", ""))
    return []


def _probe(m: str) -> str:
    """'' if the isolating sandbox can actually start a run, else why not (cached)."""
    key = (m, os.environ.get("AGENTM2M_SANDBOX_CMD", ""), PY)
    if key not in _probes:
        if m == "command" and not os.environ.get("AGENTM2M_SANDBOX_CMD", "").strip():
            _probes[key] = "AGENTM2M_SANDBOX=command needs AGENTM2M_SANDBOX_CMD"
        else:
            with tempfile.TemporaryDirectory(prefix="am2m_probe_") as d:
                try:
                    p = subprocess.run(_wrapper(m, d) + [PY, "-I", "-c", "print('am2m-ok')"], cwd=d, capture_output=True,
                                       text=True, timeout=30, check=False)
                    ok = p.returncode == 0 and "am2m-ok" in p.stdout
                    _probes[key] = "" if ok else (p.stderr.strip()[-300:] or f"exit code {p.returncode}")
                except (OSError, subprocess.TimeoutExpired) as exc:
                    _probes[key] = str(exc)[:300]
    return _probes[key]


def execution_status() -> dict:
    m = mode()
    isolated = m in ISOLATING
    if m == "off":
        reason = "code execution is disabled on this server (AGENTM2M_SANDBOX=off)"
    elif isolated:
        err = _probe(m)
        reason = f"code execution is disabled: the {m} sandbox cannot start a run ({err})" if err else ""
    elif _require_isolation:
        reason = ("code execution is disabled: this server has no isolating sandbox (set AGENTM2M_SANDBOX=bwrap, or "
                  "AGENTM2M_SANDBOX=command with AGENTM2M_SANDBOX_CMD)")
    else:
        reason = ""
    return {"sandbox": m, "isolated": isolated, "enabled": not reason, "reason": reason}


def ensure_allowed() -> None:
    st = execution_status()
    if not st["enabled"]:
        raise SandboxRefused(st["reason"])


def _bwrap_argv(workdir: str) -> list[str]:
    argv = ["bwrap", "--unshare-all", "--die-with-parent", "--new-session", "--clearenv",
            "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"]
    for d in ("/usr", "/lib", "/lib64", "/lib32", "/bin", "/sbin", "/etc/alternatives", "/etc/ld.so.cache",
              "/etc/ssl", "/etc/localtime"):
        if os.path.lexists(d):
            argv += ["--ro-bind-try", d, d] if not os.path.islink(d) else ["--symlink", os.readlink(d), d]
    real = os.path.realpath(PY)
    for prefix in {os.path.dirname(os.path.dirname(os.path.abspath(PY))), os.path.dirname(os.path.dirname(real)),
                   sys.base_prefix}:
        if prefix and prefix not in ("/", "/usr") and os.path.isdir(prefix):
            argv += ["--ro-bind", prefix, prefix]
    argv += ["--bind", workdir, workdir, "--chdir", workdir]
    return argv


def _limits(cpu: int, mem_mb: int) -> None:
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
    resource.setrlimit(resource.RLIMIT_AS, (mem_mb << 20, mem_mb << 20))
    resource.setrlimit(resource.RLIMIT_FSIZE, (64 << 20, 64 << 20))
    os.setsid()


def run_python(source: str, *, timeout: float = 20.0, mem_mb: int = 2048) -> ExecResult:
    ensure_allowed()
    m = mode()
    with tempfile.TemporaryDirectory(prefix="am2m_sbx_") as d:
        path = os.path.join(d, "main.py")
        with open(path, "w") as fh:
            fh.write(source)
        env = {"PATH": os.environ.get("PATH", ""), "PYTHONHASHSEED": "0", "HOME": d,
               "OMP_NUM_THREADS": "1", "MPLBACKEND": "Agg",
               "NLTK_DATA": os.path.expanduser("~/.cache/nltk_data")}
        argv = _wrapper(m, d)
        if m == "bwrap":
            argv += [a for k, v in env.items() if k != "NLTK_DATA" for a in ("--setenv", k, v)]
        argv += [PY, "-I", path]
        try:
            p = subprocess.run(  # noqa: PLW1510  (the exit code is inspected below)
                argv, cwd=d, capture_output=True, text=True, timeout=timeout, env=env,
                preexec_fn=lambda: _limits(int(timeout) + 2, mem_mb),
            )
        except subprocess.TimeoutExpired as e:
            return ExecResult(False, (e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or ""),
                              "TIMEOUT", timeout=True)
        return ExecResult(p.returncode == 0, p.stdout[-20000:], p.stderr[-20000:], returncode=p.returncode)


_RESULT_MARK = "@@AM2M_RESULT@@"


def run_python_json(source: str, *, timeout: float = 20.0) -> tuple[dict | None, ExecResult]:
    """Run a script that prints one JSON object after _RESULT_MARK."""
    res = run_python(source, timeout=timeout)
    for line in reversed(res.stdout.splitlines()):
        if line.startswith(_RESULT_MARK):
            try:
                return json.loads(line[len(_RESULT_MARK):]), res
            except json.JSONDecodeError:
                break
    return None, res


RESULT_MARK = _RESULT_MARK
