"""Run untrusted, LLM-written Python in a subprocess.

Each run gets a fresh temporary working directory, CPU / address-space /
file-size / process limits and a wall-clock timeout. Network access is not
blocked (no unprivileged namespaces on the experiment machine); this is
reported as a threat to validity.

The interpreter is `AM2M_SANDBOX_PY` if set, else the repository's
`.venv-sbx` when running from a checkout, else the current interpreter.
"""
from __future__ import annotations

import json
import os
import resource
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

    def tail(self, n: int = 1200) -> str:
        text = (self.stderr or "") + ("\n" + self.stdout if self.stdout else "")
        return text[-n:]


def _limits(cpu: int, mem_mb: int) -> None:
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
    resource.setrlimit(resource.RLIMIT_AS, (mem_mb << 20, mem_mb << 20))
    resource.setrlimit(resource.RLIMIT_FSIZE, (64 << 20, 64 << 20))
    os.setsid()


def run_python(source: str, *, timeout: float = 20.0, mem_mb: int = 2048) -> ExecResult:
    with tempfile.TemporaryDirectory(prefix="am2m_sbx_") as d:
        path = os.path.join(d, "main.py")
        with open(path, "w") as fh:
            fh.write(source)
        env = {"PATH": os.environ.get("PATH", ""), "PYTHONHASHSEED": "0", "HOME": d,
               "OMP_NUM_THREADS": "1", "MPLBACKEND": "Agg",
               "NLTK_DATA": os.path.expanduser("~/.cache/nltk_data")}
        try:
            p = subprocess.run(
                [PY, "-I", path], cwd=d, capture_output=True, text=True, timeout=timeout, env=env,
                preexec_fn=lambda: _limits(int(timeout) + 2, mem_mb),
            )
        except subprocess.TimeoutExpired as e:
            return ExecResult(False, (e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or ""),
                              "TIMEOUT", timeout=True)
        return ExecResult(p.returncode == 0, p.stdout[-20000:], p.stderr[-20000:])


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
