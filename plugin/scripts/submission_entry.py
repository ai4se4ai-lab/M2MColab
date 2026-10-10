#!/usr/bin/env python3
"""Produce the marketplace entries needed to publish the plugin.

    submission_entry.py --repo-url https://github.com/<org>/<repo> [--ref v0.2.0] [--sha <commit>]
        [--write-root-marketplace] [--out dist/]

Writes:
  submission-entry.json   the entry for Anthropic's official directory
                          (source: git-subdir pinned to --ref and --sha), to paste into the
                          submission form at https://clau.de/plugin-directory-submission
  marketplace.json        with --write-root-marketplace: <repo>/.claude-plugin/marketplace.json,
                          so users can `/plugin marketplace add <org>/<repo>` and
                          `/plugin install autom2m@autom2m`
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PLUGIN = REPO / "plugin" / "autom2m"
PLUGIN_SUBDIR = "plugin/autom2m"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-url", required=True, help="public git URL, e.g. https://github.com/org/repo")
    ap.add_argument("--ref", default=None, help="tag to pin (default: v<version>)")
    ap.add_argument("--sha", default=None, help="commit to pin (default: git rev-parse HEAD)")
    ap.add_argument("--homepage", default=None)
    ap.add_argument("--out", default=str(REPO / "dist"))
    ap.add_argument("--write-root-marketplace", action="store_true")
    args = ap.parse_args()

    manifest = json.loads((PLUGIN / ".claude-plugin/plugin.json").read_text())
    version = manifest["version"]
    ref = args.ref or f"v{version}"
    sha = args.sha or subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    url = args.repo_url.rstrip("/")
    git_url = url if url.endswith(".git") else url + ".git"
    homepage = args.homepage or f"{url.removesuffix('.git')}/tree/{ref}/{PLUGIN_SUBDIR}"

    entry = {
        "name": manifest["name"],
        "description": manifest["description"],
        "author": manifest["author"],
        "category": "development",
        "source": {"source": "git-subdir", "url": git_url, "path": PLUGIN_SUBDIR, "ref": ref, "sha": sha},
        "homepage": homepage,
    }
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "submission-entry.json").write_text(json.dumps(entry, indent=2) + "\n")
    print(f"wrote {out / 'submission-entry.json'}")

    root_market = {
        "name": "autom2m",
        "owner": manifest["author"],
        "metadata": {"description": "autom2m: agent teams with model-to-model hand-offs for Claude Code."},
        "plugins": [{
            "name": manifest["name"],
            "source": f"./{PLUGIN_SUBDIR}",
            "description": manifest["description"],
            "version": version,
            "category": "development",
            "homepage": homepage,
        }],
    }
    target = REPO / ".claude-plugin/marketplace.json" if args.write_root_marketplace else out / "marketplace.root.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(root_market, indent=2) + "\n")
    print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
