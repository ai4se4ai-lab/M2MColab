"""Loads the Python module named by a rule module's `uses "file.py";`
declaration and exposes its public functions as OCL-callable helpers --
our substitute for ATL `helper context ... def: ...` blocks (see
engine/expr.py's module docstring).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

from .expr import Helpers


def load_helpers(uses_path: str | None, base_dir: Path) -> Helpers:
    if not uses_path:
        return {}
    full_path = (base_dir / uses_path).resolve()
    spec = importlib.util.spec_from_file_location(full_path.stem, full_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load helpers module at {full_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return {name: obj for name, obj in vars(module).items() if callable(obj) and not name.startswith("_")}
