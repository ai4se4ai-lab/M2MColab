#!/usr/bin/env python3
"""Build an immutable reviewer ZIP from existing project and HPC evidence."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import zipfile

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
RUNS = ('am2m-20261014-015948', 'am2m-pilot-v3', 'am2m-exp1-pilot', 'am2m-exp1-confirm')
SKIP = {'.git', '__pycache__', '.pytest_cache', '.venv', 'node_modules', '.env'}

def git(*args):
    return subprocess.check_output(['git', '-C', str(REPO), *args]).decode().strip()

def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--work', type=Path, default=Path('/scratch/mbabaei/autom2m_hpc'))
    ap.add_argument('--output', type=Path)
    args = ap.parse_args()
    commit = git('rev-parse', 'HEAD')
    stamp = dt.datetime.now(dt.timezone.utc)
    label = f'autom2m-reviewer-{stamp:%Y-%m-%d}-{commit[:7]}'
    out = args.output or HERE / f'{label}.zip'
    partial = out.with_suffix('.zip.partial')
    if out.exists() or partial.exists():
        raise SystemExit(f'Refusing to overwrite snapshot: {out} (or its partial file)')
    sources = {}
    def add(path, name):
        path = Path(path)
        if name in sources:
            raise ValueError(f'Duplicate archive name: {name}')
        sources[name] = path
    def tree(path, dest, exclude=()):
        path = Path(path)
        if not path.is_dir():
            raise FileNotFoundError(path)
        for base, dirs, files in os.walk(path):
            dirs[:] = sorted(d for d in dirs if d not in SKIP and d not in exclude)
            for file in sorted(files):
                if file in SKIP or file.endswith(('.pyc', '.lock', '.tmp')):
                    continue
                p = Path(base) / file
                add(p, (Path(dest) / p.relative_to(path)).as_posix())
    tracked = subprocess.check_output(['git', '-C', str(REPO), 'ls-files', '-z']).decode().split('\0')
    for rel in tracked:
        if rel and not rel.startswith('docs/package/'):
            add(REPO / rel, rel)
    tree(REPO / 'docs/figures', 'docs/figures')
    for file in ('README.md', 'reviewer-guide.md', 'build_package.py', 'audit_results.py'):
        add(HERE / file, f'packaging/{file}')
    add(HERE / 'reviewer-guide.md', 'README-reviewer.md')
    add(HERE / 'verify_package.py', 'verify_package.py')
    tree(REPO / f'hpc/logs/{RUNS[0]}/results', 'results')
    # Scratch is authoritative for evidence updated since the collected archive.
    differences = []
    scratch_main = args.work / f'runs/{RUNS[0]}/results'
    for base, dirs, files in os.walk(scratch_main):
        if Path(base) == scratch_main:
            dirs[:] = sorted(d for d in dirs if d != 'runs')
        for file in sorted(files):
            p = Path(base) / file
            name = 'results/' + p.relative_to(scratch_main).as_posix()
            if name in sources:
                if sha(p) != sha(sources[name]):
                    differences.append({'path': name, 'selected': 'scratch'})
                sources[name] = p
            else:
                add(p, name)
    for run in RUNS:
        tree(REPO / f'hpc/logs/{run}', f'hpc/logs/{run}', exclude=('results',))
        if run != RUNS[0]:
            print(f'Indexing full experiment records: {run}', flush=True)
            tree(args.work / f'runs/{run}', f'experiments/{run}')
    tree(args.work / f'runs/{RUNS[0]}/setup', 'evidence/main-setup')
    add(args.work / f'runs/{RUNS[0]}/run_manifest.json', 'evidence/main-run-manifest.json')
    tree(args.work / 'data/Agents_Failure_Attribution', 'data/Agents_Failure_Attribution')
    tree(args.work / 'hf_cache/datasets', 'benchmark-cache/datasets')
    for dataset in ('datasets--FudanSELab--ClassEval', 'datasets--evalplus--humanevalplus'):
        tree(args.work / f'hf_cache/hub/{dataset}', f'benchmark-cache/hub/{dataset}')
    for name in ('reliability.csv', 'reliability_by_model.csv'):
        add(args.work / 'paper_numbers' / name, f'paper-analysis/{name}')
    for name in ('reliability.py', 'coder_sensitivity.py', 'verify_sample.py', 'exp1_analyze_job.sh', 'exp1_to_dash.py'):
        add(args.work / 'tools' / name, f'paper-analysis/tools/{name}')
    with tempfile.TemporaryDirectory(prefix='autom2m-freeze-') as td:
        tmp = Path(td)
        # Verify the actual sources selected for packaging, without modifying them.
        (tmp / 'results').symlink_to(scratch_main)
        (tmp / 'hpc').symlink_to(REPO / 'hpc')
        (tmp / 'experiments').mkdir()
        for run in RUNS[2:]:
            (tmp / 'experiments' / run).symlink_to(args.work / 'runs' / run)
        spec = importlib.util.spec_from_file_location('audit', HERE / 'audit_results.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        coverage = module.audit(tmp)
        (tmp / 'coverage-audit.json').write_text(json.dumps(coverage, indent=2) + '\n')
        add(tmp / 'coverage-audit.json', 'coverage-audit.json')
        (HERE / 'coverage-audit.json').write_text((tmp / 'coverage-audit.json').read_text())
        print('Coverage audit passed. Preserving Git history.', flush=True)
        subprocess.run(['git', '-C', str(REPO), 'bundle', 'create', str(tmp / 'source-history.bundle'), '--all'], check=True)
        add(tmp / 'source-history.bundle', 'source-history.bundle')
        tarpath = sources['results/runs.tar.gz']
        print('Checking archived main-study run records.', flush=True)
        archive_members = 0
        archive_bytes = 0
        with tarfile.open(tarpath, 'r:gz') as tar:
            for member in tar:
                if member.isfile():
                    assert not Path(member.name).is_absolute() and '..' not in Path(member.name).parts
                    archive_members += 1
                    archive_bytes += member.size
        assert archive_members >= 44352, archive_members
        freeze = {'frozen_utc': stamp.isoformat(), 'repository': 'https://github.com/ai4se4ai-lab/M2MColab',
                  'commit': commit, 'branch': git('branch', '--show-current'),
                  'working_tree_at_freeze': git('status', '--short'), 'runs': list(RUNS),
                  'manuscript_sha256': sha(REPO / 'docs/emse-autom2m.tex'),
                  'main_runs_archive_files': archive_members, 'main_runs_archive_expanded_bytes': archive_bytes,
                  'collected_vs_scratch_differences': differences,
                  'provenance_limit': 'Current source and available Git history retained; historical dirty job states are not reconstructed.',
                  'excluded': ['model weights', 'installed environments and binaries', 'unrelated setup/smoke/sizing runs',
                               'quarantined experiments', '.git directories', '.env files', 'Python and LaTeX build caches']}
        (tmp / 'FREEZE.json').write_text(json.dumps(freeze, indent=2) + '\n')
        add(tmp / 'FREEZE.json', 'FREEZE.json')
        manifest = []
        total = 0
        print(f'Writing {len(sources)} payload files to {out.name}', flush=True)
        def prepare(item):
            name, p = item
            before = p.stat()
            if before.st_size > 8 * 1024 * 1024:
                return name, p, before, None
            data = p.read_bytes()
            after = p.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or len(data) != before.st_size:
                raise RuntimeError(f'Source changed while freezing: {p}')
            return name, p, before, data
        items = sorted(sources.items())
        with ThreadPoolExecutor(max_workers=8) as pool, zipfile.ZipFile(
                partial, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as z:
            index = 0
            for batch_start in range(0, len(items), 64):
                # Prefetch independent small records; all ZIP writes remain sequential.
                for name, p, before, data in pool.map(prepare, items[batch_start:batch_start + 64]):
                    index += 1
                    digest = hashlib.sha256()
                    info = zipfile.ZipInfo(f'{label}/{name}', dt.datetime.fromtimestamp(before.st_mtime).timetuple()[:6])
                    info.external_attr = (before.st_mode & 0xFFFF) << 16
                    info.compress_type = zipfile.ZIP_STORED if p.suffix in {'.gz', '.zip', '.png', '.jpg'} else zipfile.ZIP_DEFLATED
                    info._compresslevel = 6
                    length = 0
                    with z.open(info, 'w', force_zip64=True) as dst:
                        if data is not None:
                            dst.write(data); digest.update(data); length = len(data)
                        else:
                            with p.open('rb') as src:
                                for chunk in iter(lambda: src.read(1024 * 1024), b''):
                                    dst.write(chunk); digest.update(chunk); length += len(chunk)
                            after = p.stat()
                            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or length != before.st_size:
                                raise RuntimeError(f'Source changed while freezing: {p}')
                    manifest.append({'path': name, 'bytes': length, 'sha256': digest.hexdigest(),
                                     'source': ('generated-at-freeze/' + p.name) if p.is_relative_to(tmp) else str(p)})
                    total += length
                    if index % 500 == 0:
                        print(f'  {index}/{len(sources)} files; {total / 1024**3:.2f} GiB read', flush=True)
            mbytes = (json.dumps(manifest, indent=2) + '\n').encode()
            z.writestr(f'{label}/MANIFEST.json', mbytes)
            sums = ''.join(f'{x["sha256"]}  {x["path"]}\n' for x in manifest)
            sums += f'{hashlib.sha256(mbytes).hexdigest()}  MANIFEST.json\n'
            z.writestr(f'{label}/SHA256SUMS', sums)
        print('Verifying SHA-256 and ZIP CRC for every packaged file.', flush=True)
        with zipfile.ZipFile(partial) as z:
            for line in sums.splitlines():
                expected, rel = line.split('  ', 1)
                h = hashlib.sha256()
                with z.open(f'{label}/{rel}') as f:
                    for chunk in iter(lambda: f.read(1024 * 1024), b''):
                        h.update(chunk)
                assert h.hexdigest() == expected, rel
        partial.rename(out)
        digest = sha(out)
        out.with_suffix('.zip.sha256').write_text(f'{digest}  {out.name}\n')
        summary = {'archive': out.name, 'sha256': digest, 'archive_bytes': out.stat().st_size,
                   'payload_files': len(manifest), 'uncompressed_payload_bytes': total,
                   'main_run_records_in_nested_archive': archive_members,
                   'all_payload_hashes_and_zip_crcs_verified': True, 'coverage_audit_passed': True,
                   'collected_vs_scratch_differences': differences, 'freeze': freeze}
        (HERE / 'package-summary.json').write_text(json.dumps(summary, indent=2) + '\n')
        print(json.dumps({k: v for k, v in summary.items() if k != 'freeze'}, indent=2), flush=True)

if __name__ == '__main__':
    main()
