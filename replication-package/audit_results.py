#!/usr/bin/env python3
"""Audit frozen experiment coverage using only the Python standard library."""
import argparse
import collections
import csv
import json
from pathlib import Path

CONDITIONS = {'single', 'free', 'critic', 'schema', 'typed_nc', 'autom2m', 'typed_ref', 'single_gate'}

def records(path):
    good = {}
    errors = 0
    lines = 0
    for line in path.open():
        if not line.strip():
            continue
        row = json.loads(line)
        lines += 1
        if row.get('status') == 'error':
            errors += 1
            continue
        key = (row['bench'], row['model'], row['task'], row['condition'], row['seed'])
        good[key] = row
    return good, errors, lines


def audit(root):
    root = Path(root)
    results = root / 'results'
    rows = {}
    errors = 0
    for path in sorted((results / 'raw').glob('*.jsonl')):
        good, nerrors, _ = records(path)
        rows.update(good)
        errors += nerrors
    assert rows, 'No main-study records found'
    main = {k: r for k, r in rows.items() if r['condition'] in CONDITIONS}
    extra = {k: r for k, r in rows.items() if r['condition'] not in CONDITIONS}
    assert len(main) == 44352, len(main)
    assert len(extra) == 100, len(extra)
    counts = collections.Counter((r['bench'], r['model'], r['condition']) for r in main.values())
    assert len(counts) == 2 * 7 * 8, len(counts)
    for (bench, model, cond), n in counts.items():
        assert n == (300 if bench == 'classeval' else 492), (bench, model, cond, n)
    csv_rows = list(csv.DictReader((results / 'data/runs.csv').open()))
    csv_keys = {(r['bench'], r['model'], r['task'], r['condition'], int(r['seed'])) for r in csv_rows}
    assert csv_keys == set(rows), 'Frozen CSV coverage differs from raw JSONL'
    for r in csv_rows:
        raw = rows[(r['bench'], r['model'], r['task'], r['condition'], int(r['seed']))]
        assert int(r['success']) == int(bool(raw['success']))
        assert int(r['declared_done']) == int(bool(raw.get('declared_done')))
    report = {'main_study_runs': len(main), 'additional_1example_ablation_runs': len(extra),
              'main_raw_error_rows_preserved_but_excluded': errors,
              'frozen_csv_matches_raw_success_and_completion': True, 'budget_sweeps': {}}
    mutants = json.loads((results / 'rq2/natural_mutants.json').read_text())
    mutation_count = sum(op['n'] for op in mutants['operators'].values())
    assert mutants['teams'] == 3379 and mutation_count == 151516
    independent = [json.loads(l) for l in (results / 'rq2/independent.jsonl').open() if l.strip()]
    independent_counts = collections.Counter(r['kind'] for r in independent)
    assert independent_counts['seeded'] == 275
    assert independent_counts['clean'] == 60
    injections = []
    for path in sorted((results / 'rq4').glob('inject_*.jsonl')):
        injections.extend(json.loads(l) for l in path.open() if l.strip())
    manifested = sum(bool(r.get('manifested')) for r in injections)
    assert len(injections) == 1108 and manifested == 993, (len(injections), manifested)
    ww = json.loads((results / 'rq1/audit_whowhen.json').read_text())['algorithm_generated']
    assert ww['teams'] == 126 and ww['roles'] == 356 and ww['runs_terminated_by_agent'] == 82
    report['other_paper_evidence'] = {'mutation_teams': mutants['teams'], 'natural_mutants': mutation_count,
                                     'independent_seeded_defects': independent_counts['seeded'],
                                     'independent_clean_controls': independent_counts['clean'],
                                     'injected_faults': len(injections), 'manifested_faults': manifested,
                                     'whowhen_failed_teams': ww['teams'], 'whowhen_roles': ww['roles']}
    for name, split in [('am2m-exp1-confirm', 'heldout'), ('am2m-exp1-pilot', 'dev')]:
        sweep = root / 'experiments' / name
        curves = list(csv.DictReader((sweep / 'analysis/exp1_curves.csv').open()))
        info = []
        for k in (1, 2, 4, 8):
            paths = sorted((sweep / f'results/k{k}/raw').glob('*.jsonl'))
            assert len(paths) == 4, (name, k, len(paths))
            for path in paths:
                good, nerrors, lines = records(path)
                for cond in ('autom2m', 'single_gate'):
                    sub = [r for r in good.values() if r['condition'] == cond]
                    bench = sub[0]['bench']
                    model = sub[0]['model'].replace(':', '_').replace('/', '_')
                    tasks = set((root / f'hpc/config/{split}_tasks_{bench}.txt').read_text().split())
                    assert {r['task'] for r in sub} == tasks
                    assert {(r['task'], r['seed']) for r in sub} == {(t, s) for t in tasks for s in (1, 2, 3)}
                    pct = round(100 * sum(bool(r['success']) for r in sub) / len(sub), 1)
                    match = [r for r in curves if r['bench'] == bench and r['model'] == model
                             and r['condition'] == cond and int(r['k']) == k and r['size'] == 'all']
                    assert len(match) == 1, (name, bench, model, cond, k)
                    assert int(match[0]['runs']) == len(sub)
                    assert float(match[0]['success_pct']) == pct, (name, match[0], pct)
                    info.append({'k': k, 'bench': bench, 'model': model, 'condition': cond,
                                 'tasks': len(tasks), 'runs': len(sub), 'success_pct': pct})
        report['budget_sweeps'][name] = info
    return report

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('root', nargs='?', default=Path(__file__).resolve().parents[1])
    parser.add_argument('--out')
    args = parser.parse_args()
    result = audit(args.root)
    output = json.dumps(result, indent=2) + '\n'
    if args.out:
        Path(args.out).write_text(output)
    else:
        print(output)
