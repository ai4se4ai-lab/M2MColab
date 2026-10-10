#!/usr/bin/env python3
"""Keep one version across the engine and the plugin.

    bump_version.py 0.3.0     # rewrite every location
    bump_version.py --check   # exit 1 if any location disagrees with pyproject.toml

Locations: pyproject.toml, agenthot.__version__, plugin.json, the dev and
(if present) root marketplace entries, and the `autom2m==X` engine pins in
.mcp.json and the validate hook.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PLUGIN = REPO / "plugin" / "autom2m"
SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:[-.]?(?:a|b|rc|dev)\d+)?$")

TEXT = [  # (path, regex with one group around the version)
    (REPO / "pyproject.toml", r'(?m)^version = "([^"]+)"'),
    (REPO / "src/agenthot/__init__.py", r'__version__ = "([^"]+)"'),
    (REPO / "src/autom2m/__init__.py", r'__version__ = "([^"]+)"'),
    (PLUGIN / ".mcp.json", r"autom2m==([0-9A-Za-z.\-]+)"),
    (PLUGIN / "hooks/validate-on-edit.sh", r"autom2m==([0-9A-Za-z.\-]+)"),
]
JSON_MANIFESTS = [PLUGIN / ".claude-plugin/plugin.json"]
MARKETPLACES = [REPO / "plugin/.claude-plugin/marketplace.json", REPO / ".claude-plugin/marketplace.json"]


def current() -> str:
    return re.search(TEXT[0][1], TEXT[0][0].read_text()).group(1)


def found_versions() -> dict[str, str]:
    out: dict[str, str] = {}
    for path, rx in TEXT:
        for i, m in enumerate(re.finditer(rx, path.read_text())):
            out[f"{path.relative_to(REPO)}#{i}"] = m.group(1)
    for path in JSON_MANIFESTS:
        out[str(path.relative_to(REPO))] = json.loads(path.read_text())["version"]
    for path in MARKETPLACES:
        if path.is_file():
            for p in json.loads(path.read_text())["plugins"]:
                if p["name"] == "autom2m" and "version" in p:
                    out[f"{path.relative_to(REPO)}:autom2m"] = p["version"]
    return out


def set_version(new: str) -> None:
    for path, rx in TEXT:
        text = path.read_text()
        text = re.sub(rx, lambda m: m.group(0).replace(m.group(1), new), text)
        path.write_text(text)
    for path in JSON_MANIFESTS:
        d = json.loads(path.read_text())
        d["version"] = new
        path.write_text(json.dumps(d, indent=2) + "\n")
    for path in MARKETPLACES:
        if path.is_file():
            d = json.loads(path.read_text())
            for p in d["plugins"]:
                if p["name"] == "autom2m":
                    p["version"] = new
            path.write_text(json.dumps(d, indent=2) + "\n")


def main(argv: list[str]) -> int:
    if argv == ["--check"]:
        want = current()
        bad = {k: v for k, v in found_versions().items() if v != want}
        if bad:
            print(f"version mismatch (pyproject.toml says {want}):", file=sys.stderr)
            for k, v in bad.items():
                print(f"  {k}: {v}", file=sys.stderr)
            return 1
        if f"## {want}" not in (PLUGIN / "CHANGELOG.md").read_text():
            print(f"warning: CHANGELOG.md has no '## {want}' section", file=sys.stderr)
        print(f"ok: {want} in {len(found_versions())} places")
        return 0
    if len(argv) != 1 or not SEMVER.match(argv[0]):
        print(__doc__, file=sys.stderr)
        return 2
    set_version(argv[0])
    print(f"set version {argv[0]}; add a '## {argv[0]}' section to plugin/autom2m/CHANGELOG.md")
    return main(["--check"])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
