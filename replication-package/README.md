# Frozen reviewer evidence

`autom2m-reviewer-2026-11-07-431600a.zip` is the reviewer handoff. Send its
adjacent `.sha256` checksum with it. The ZIP includes a reviewer guide, all
paper-related run evidence, source history, datasets, environment records, and
per-file SHA-256 checksums. `coverage-audit.json` and `package-summary.json`
record validation and archive size outside the ZIP for quick inspection.

Read `reviewer-guide.md` for the claim-to-data map and reproduction commands.

`build_package.py` creates a new snapshot from this checkout and the original
HPC run locations. It refuses to replace an existing ZIP; an existing snapshot
should stay frozen. Rebuilding requires access to the project and scratch data.
It does not run new experiments or modify existing run data.
