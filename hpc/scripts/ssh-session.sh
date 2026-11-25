#!/usr/bin/env bash
# Open (or check/close) persistent SSH ControlMaster sessions to the Alliance clusters.
# Interactive login with MFA is the only manual step; no credentials are stored.
#   bash hpc/scripts/ssh-session.sh connect all     # complete MFA for fir, narval, rorqual
#   bash hpc/scripts/ssh-session.sh status all
#   bash hpc/scripts/ssh-session.sh close all
set -euo pipefail
root=$(cd "$(dirname "$0")/../.." && pwd)
if [[ $# -eq 0 ]]; then sed -n 2,6p "$0"; exit 0; fi
cluster=${2:-all}
user=${ALLIANCE_USER:-mbabaei}
mkdir -p "$root/hpc/.ssh"; chmod 700 "$root/hpc/.ssh"
case "$cluster" in fir|narval|rorqual) clusters=("$cluster");; all) clusters=(fir narval rorqual);;
  *) echo 'cluster must be fir, narval, rorqual or all' >&2; exit 2;; esac
failed=0
for site in "${clusters[@]}"; do
  socket="$root/hpc/.ssh/$site"
  host="$user@$site.alliancecan.ca"
  opts=(-o StrictHostKeyChecking=accept-new -o ConnectTimeout=20 -o ServerAliveInterval=60
        -o ServerAliveCountMax=3 -o ControlPath="$socket")
  case "$1" in
    connect) echo "== $site"; ssh "${opts[@]}" -o ControlMaster=auto -o ControlPersist=72h -MNf "$host" || failed=1 ;;
    status)  echo -n "$site: "; ssh "${opts[@]}" -O check "$host" 2>&1 || failed=1 ;;
    close)   ssh "${opts[@]}" -O exit "$host" || failed=1 ;;
    *) echo 'usage: ssh-session.sh connect|status|close [fir|narval|rorqual|all]' >&2; exit 2 ;;
  esac
done
exit "$failed"
