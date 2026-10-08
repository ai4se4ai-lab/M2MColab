#!/usr/bin/env bash
# Release the engine to (Test)PyPI and prepare the plugin for publication.
#
#   release.sh                                  # dry run (default): validate, build, write entries; no upload
#   release.sh --publish --target testpypi --repo-url https://github.com/org/repo
#   release.sh --publish --target pypi     --repo-url https://github.com/org/repo
#
# Publishing is refused unless AGENTM2M_ALLOW_PUBLIC=1: the paper describing
# this tool is under double-anonymous review, and a public package or repo
# under the authors' names would de-anonymize it. Set it only after the
# review (or when publishing under an anonymous account).
#
# The official Anthropic directory has no upload API: submission is a form
# (https://clau.de/plugin-directory-submission) reviewed by Anthropic. This
# script produces the entry to paste there (dist/submission-entry.json).
source "$(dirname "$0")/_common.sh"

publish=0; target=testpypi; repo_url="${AGENTM2M_REPO_URL:-https://github.com/OWNER/REPO}"
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) publish=0 ;;
    --publish) publish=1 ;;
    --target) target="$2"; shift ;;
    --repo-url) repo_url="$2"; shift ;;
    -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
    *) die "unknown option $1" ;;
  esac
  shift
done
case "$target" in testpypi|pypi) ;; *) die "--target must be testpypi or pypi" ;; esac

if [ "$publish" = 1 ]; then
  [ "${AGENTM2M_ALLOW_PUBLIC:-0}" = 1 ] || die "refusing to publish: set AGENTM2M_ALLOW_PUBLIC=1 once the double-anonymous review is over"
  case "$repo_url" in *OWNER/REPO*) die "--repo-url is required to publish" ;; esac
  [ -z "$(git -C "$REPO_ROOT" status --porcelain)" ] || die "working tree is not clean"
  git -C "$REPO_ROOT" rev-parse "v$VERSION" >/dev/null 2>&1 && die "tag v$VERSION already exists"
fi

log "release $VERSION ($([ "$publish" = 1 ] && echo "PUBLISH to $target" || echo "dry run"))"
"$SCRIPTS_DIR/validate.sh"
"$SCRIPTS_DIR/build.sh"

if [ "$publish" = 1 ]; then
  log "uploading to $target"
  "$PY" -m twine upload --repository "$target" "$REPO_ROOT"/dist/agentm2m-"$VERSION"*.whl "$REPO_ROOT"/dist/agentm2m-"$VERSION".tar.gz
  if [ "$target" = testpypi ]; then
    log "smoke test from TestPyPI"
    uvx --isolated --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ \
      --from "agentm2m==$VERSION" --with "mcp>=1.2" agentm2m-mcp --version
    log "TestPyPI OK. Re-run with --target pypi for the real release."
    exit 0
  fi
  log "root marketplace + tag"
  "$PY" "$SCRIPTS_DIR/submission_entry.py" --repo-url "$repo_url" --write-root-marketplace --sha "$(git -C "$REPO_ROOT" rev-parse HEAD)"
  claude plugin validate --strict "$REPO_ROOT"
  git -C "$REPO_ROOT" add .claude-plugin/marketplace.json
  git -C "$REPO_ROOT" commit -m "Publish agentm2m plugin $VERSION marketplace entry"
  git -C "$REPO_ROOT" tag -a "v$VERSION" -m "agentm2m $VERSION"
  git -C "$REPO_ROOT" push origin HEAD "v$VERSION"
  # the tag now exists: pin the submission entry to exactly that commit
  "$PY" "$SCRIPTS_DIR/submission_entry.py" --repo-url "$repo_url" --ref "v$VERSION" --sha "$(git -C "$REPO_ROOT" rev-parse "v$VERSION^{commit}")"
else
  "$PY" "$SCRIPTS_DIR/submission_entry.py" --repo-url "$repo_url"
fi

cat <<MSG

Done ($([ "$publish" = 1 ] && echo published || echo "dry run: nothing uploaded, tagged, or pushed")).
Artifacts in dist/: wheel, sdist, plugin zip, submission-entry.json, marketplace preview.

Users install from your repo (after it is public):
  /plugin marketplace add ${repo_url#https://github.com/}
  /plugin install agentm2m@agentm2m

To list it in Anthropic's official directory, submit dist/submission-entry.json via
  https://clau.de/plugin-directory-submission
MSG
