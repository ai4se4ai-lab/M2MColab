# HPC experiments (Digital Research Alliance of Canada)

All experiments of the paper run on Alliance GPU clusters (fir and rorqual: H100; narval: A100), one
model per job, with a node-local Ollama server. Everything that comes back is kept here.

## Connection

Open persistent SSH ControlMaster sessions once (MFA is the only manual step; no credential is stored):

    bash hpc/scripts/ssh-session.sh connect all
    bash hpc/scripts/ssh-session.sh status all

`hpc/scripts/fleet.py` then drives the clusters through these sockets.

## Workflow

    python hpc/scripts/fleet.py probe                      # Ollama binaries, model stores, queues
    python hpc/scripts/fleet.py setup                      # venv (harness) + venv-sbx (sandbox), nltk data
    python hpc/scripts/fleet.py shards --n 4               # task lists per shard (hpc/shards/)
    python hpc/scripts/fleet.py deploy                     # code, teams, exported benchmarks, shards
    python hpc/scripts/fleet.py pull-model --site fir --model qwen2.5-coder:32b
    python hpc/scripts/fleet.py submit --site fir --model qwen2.5-coder:7b --bench classeval \
        --conditions autom2m,autom2m_base --seeds 1 --shard 0/4 --tag smoke --time 3:00:00
    python hpc/scripts/fleet.py status
    python hpc/scripts/fleet.py fetch --tag smoke          # -> hpc/raw/<site>/<tag>/
    python hpc/scripts/fleet.py merge --tag smoke          # -> hpc/processed/<tag>/raw/<bench>__<model>.jsonl

A job (`hpc/slurm/am2m_matrix.sbatch`) starts Ollama on the node-local disk, requires the model to be
fully GPU-resident, and runs `evaluation.run_matrix` on one shard. Runs are resumable: a resubmitted
shard skips every (task, condition, seed) already in its raw file. Single-Gate is compute-matched to
AutoM2M, so it runs after AutoM2M with `--gate-budget` set to AutoM2M's median output tokens over all
shards of the same model and benchmark.

## Layout

| Path | Content |
|---|---|
| `config/clusters.json` | user, clusters, accounts, GPU types, work directories |
| `scripts/ssh-session.sh` | ControlMaster sessions |
| `scripts/fleet.py` | probe, setup, deploy, submit, status, fetch, merge |
| `slurm/am2m_matrix.sbatch` | one experiment job |
| `shards/` | task ids per shard |
| `jobs.jsonl` | every submission (site, job id, model, benchmark, conditions, seeds, shard) |
| `raw/<site>/<tag>/` | results as produced on the cluster: `raw/*.jsonl` (one row per run) and `runs/` (full records) |
| `raw/<site>/logs/` | Slurm output of every job |
| `processed/<tag>/` | merged raw rows per benchmark and model, and the analysis outputs |
