#!/usr/bin/env bash
# Compact status of the "full" jobs per site, plus run counts and error counts on the cluster.
cd "$(dirname "$0")/../.."
for s in fir narval rorqual; do
  ssh -o ControlPath=hpc/.ssh/$s -o BatchMode=yes -o ConnectTimeout=20 mbabaei@$s.alliancecan.ca '
    st=$(squeue -u $USER -h -o "%T %j" | grep am2m-full | awk "{print \$1}" | sort | uniq -c | tr -s " " | tr "\n" ","); 
    rows=0; errs=0
    for f in /scratch/mbabaei/autom2m/results/full/raw/*.jsonl; do [ -f "$f" ] || continue; rows=$((rows+$(wc -l < $f))); errs=$((errs+$(grep -c "\"status\": \"error\"" $f))); done
    fails=$(sacct -u $USER -S now-3days -n -X -o JobName%40,State | grep am2m-full | grep -cE "FAILED|TIMEOUT|OUT_OF_ME|NODE_FAIL")
    echo "'$s': queue[$st] rows=$rows errors=$errs badjobs=$fails"' 2>/dev/null || echo "$s: ssh failed"
done
