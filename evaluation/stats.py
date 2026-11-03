"""Small statistics helpers for the evaluation scripts (no third-party dependencies).

- ``wilson_interval``: 95 % confidence interval of a share (right/wrong counts), better than +-1/sqrt(n) for small n.
- ``mcnemar_exact``: exact two-sided paired test of two systems on the same questions; only the questions where
  they disagree carry information.
"""

from __future__ import annotations

from math import comb, sqrt

Z_95 = 1.96


def wilson_interval(successes: int, n: int, z: float = Z_95) -> tuple[float, float]:
    """Wilson score interval for ``successes`` out of ``n`` (``(0.0, 1.0)`` when ``n`` is 0)."""
    if n <= 0:
        return 0.0, 1.0
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def mcnemar_exact(only_a: int, only_b: int) -> float:
    """Two-sided exact p-value for ``only_a`` questions that only system A got right and ``only_b`` only B got right."""
    n = only_a + only_b
    if n == 0:
        return 1.0
    tail = sum(comb(n, k) for k in range(min(only_a, only_b) + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def paired_counts(a: dict[object, bool], b: dict[object, bool]) -> tuple[int, int]:
    """(only A right, only B right) over the questions both systems answered."""
    shared = a.keys() & b.keys()
    return (sum(1 for q in shared if a[q] and not b[q]), sum(1 for q in shared if b[q] and not a[q]))


def percentile(values: list[float], q: float) -> float:
    """The ``q``-th percentile (0-100) by linear interpolation; 0.0 for an empty list."""
    if not values:
        return 0.0
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q / 100
    low, high = int(pos), min(int(pos) + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)
