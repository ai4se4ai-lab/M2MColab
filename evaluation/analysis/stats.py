"""Statistics used throughout the evaluation."""
from __future__ import annotations

import math
import random
from collections.abc import Sequence

from scipy import stats as S


def bootstrap_ci(values: Sequence[float], *, b: int = 5000, alpha: float = 0.05, seed: int = 0) -> tuple[float, float, float]:
    """Mean and percentile bootstrap CI over the given (per-task) values."""
    vals = list(values)
    if not vals:
        return (float("nan"), float("nan"), float("nan"))
    rng = random.Random(seed)
    n = len(vals)
    means = sorted(sum(vals[rng.randrange(n)] for _ in range(n)) / n for _ in range(b))
    return (sum(vals) / n, means[int(alpha / 2 * b)], means[int((1 - alpha / 2) * b) - 1])


def paired_bootstrap_diff(a: Sequence[float], b_: Sequence[float], *, b: int = 5000, seed: int = 0):
    d = [x - y for x, y in zip(a, b_)]
    return bootstrap_ci(d, b=b, seed=seed)


def wilcoxon(a: Sequence[float], b: Sequence[float]) -> float:
    d = [x - y for x, y in zip(a, b)]
    if all(abs(x) < 1e-12 for x in d):
        return 1.0
    return float(S.wilcoxon(a, b, zero_method="wilcox").pvalue)


def mcnemar(a: Sequence[bool], b: Sequence[bool]) -> tuple[int, int, float]:
    """Exact McNemar test on paired binary outcomes: (a-only, b-only, p)."""
    n01 = sum(1 for x, y in zip(a, b) if x and not y)
    n10 = sum(1 for x, y in zip(a, b) if y and not x)
    n = n01 + n10
    p = 1.0 if n == 0 else float(S.binomtest(n01, n, 0.5).pvalue)
    return n01, n10, p


def holm(pvals: dict) -> dict:
    items = sorted(pvals.items(), key=lambda kv: kv[1])
    m = len(items)
    out, running = {}, 0.0
    for i, (k, p) in enumerate(items):
        adj = min(1.0, (m - i) * p)
        running = max(running, adj)
        out[k] = running
    return out


def cliffs_delta(a: Sequence[float], b: Sequence[float]) -> float:
    gt = sum(1 for x in a for y in b if x > y)
    lt = sum(1 for x in a for y in b if x < y)
    n = len(a) * len(b)
    return (gt - lt) / n if n else float("nan")


def cohen_kappa(x: Sequence[str], y: Sequence[str]) -> float:
    n = len(x)
    if n == 0:
        return float("nan")
    labels = sorted(set(x) | set(y))
    po = sum(1 for a, b in zip(x, y) if a == b) / n
    pe = sum((sum(1 for a in x if a == l) / n) * (sum(1 for b in y if b == l) / n) for l in labels)
    return (po - pe) / (1 - pe) if pe < 1 else 1.0


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


def cochran_q(columns: Sequence[Sequence[bool]]) -> tuple[float, float]:
    """Cochran's Q over k paired binary conditions (columns: one list per
    condition, aligned by subject). Returns (Q, p)."""
    k = len(columns)
    n = len(columns[0]) if columns else 0
    if k < 2 or n == 0:
        return float("nan"), float("nan")
    col = [sum(bool(x) for x in c) for c in columns]
    row = [sum(bool(columns[j][i]) for j in range(k)) for i in range(n)]
    total = sum(row)
    denom = k * total - sum(r * r for r in row)
    if denom == 0:
        return 0.0, 1.0
    q = (k - 1) * (k * sum(c * c for c in col) - total * total) / denom
    return q, float(S.chi2.sf(q, k - 1))


def cluster_bootstrap(groups: dict, stat, *, b: int = 1000, seed: int = 0, alpha: float = 0.05):
    """Percentile CI of `stat(list_of_items)` resampling clusters (e.g. tasks):
    `groups` maps cluster -> list of items."""
    keys = list(groups)
    if not keys:
        return float("nan"), float("nan"), float("nan")
    rng = random.Random(seed)
    point = stat([x for k in keys for x in groups[k]])
    vals = []
    for _ in range(b):
        sample = [x for _ in keys for x in groups[keys[rng.randrange(len(keys))]]]
        v = stat(sample)
        if v == v:
            vals.append(v)
    vals.sort()
    if not vals:
        return point, float("nan"), float("nan")
    return point, vals[int(alpha / 2 * len(vals))], vals[min(len(vals) - 1, int((1 - alpha / 2) * len(vals)))]


def cliff_magnitude(d: float) -> str:
    a = abs(d)
    return "N" if a < 0.147 else "S" if a < 0.33 else "M" if a < 0.474 else "L"


def gee_logit(rows: list[dict], *, outcome: str, condition: str, cluster: str, reference: str,
              extra: list[str] | None = None) -> dict:
    """Logistic GEE (exchangeable working correlation, clustered) of the
    outcome on condition dummies (+ optional categorical covariates).
    Returns {level: (OR, lo, hi, p)} against the reference condition."""
    import numpy as np
    import pandas as pd
    import statsmodels.api as sm
    import statsmodels.formula.api as smf

    df = pd.DataFrame(rows)
    if df.empty or df[condition].nunique() < 2:
        return {}
    df["_y"] = df[outcome].astype(int)
    terms = [f"C({condition}, Treatment(reference={reference!r}))"] + [f"C({e})" for e in (extra or []) if df[e].nunique() > 1]
    model = smf.gee(f"_y ~ {' + '.join(terms)}", groups=df[cluster], data=df, family=sm.families.Binomial(),
                    cov_struct=sm.cov_struct.Exchangeable())
    res = model.fit()
    out = {}
    ci = res.conf_int()
    for name in res.params.index:
        if name.startswith(f"C({condition}"):
            level = name.split("[T.")[-1].rstrip("]")
            out[level] = (float(np.exp(res.params[name])), float(np.exp(ci.loc[name, 0])), float(np.exp(ci.loc[name, 1])),
                          float(res.pvalues[name]))
    return out


def gee_interaction(rows: list[dict], *, outcome: str, condition: str, group: str, cluster: str) -> tuple[float, int, float]:
    """Wald test of the condition x group interaction in a logistic GEE:
    (chi2, df, p)."""
    import pandas as pd
    import statsmodels.api as sm
    import statsmodels.formula.api as smf

    df = pd.DataFrame(rows)
    if df[group].nunique() < 2:
        return float("nan"), 0, float("nan")
    df["_y"] = df[outcome].astype(int)
    full = smf.gee(f"_y ~ C({condition}) * C({group})", groups=df[cluster], data=df, family=sm.families.Binomial(),
                   cov_struct=sm.cov_struct.Exchangeable()).fit()
    names = [n for n in full.params.index if ":" in n]
    if not names:
        return float("nan"), 0, float("nan")
    import numpy as np

    idx = [list(full.params.index).index(n) for n in names]
    r = np.zeros((len(idx), len(full.params)))
    for i, j in enumerate(idx):
        r[i, j] = 1
    w = full.wald_test(r, scalar=True)
    return float(w.statistic), len(idx), float(w.pvalue)
