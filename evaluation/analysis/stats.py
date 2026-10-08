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
