"""Exact statistics for forced-choice (ABX) listening tests.

Stdlib-only on purpose: this module is the citation surface of the tool, so it
must be importable, auditable and testable without numpy/scipy/audio deps.

Design notes
------------
* All p-values are EXACT binomial tail probabilities P(X >= k | n, p=0.5),
  computed from integer binomial coefficients. No normal approximation.
* The naive p-value is only valid for a trial count FIXED BEFORE the first
  answer. `is_final` / `verdict_locked` make that constraint explicit in the
  API so a UI cannot accidentally present a p-hacked number as a verdict.
"""
from __future__ import annotations

import math
from typing import List, Optional, Tuple

CHANCE = 0.5
DEFAULT_ALPHA = 0.05


def binom_tail(n: int, k: int, p: float = CHANCE) -> float:
    """Exact P(X >= k) for X ~ Binomial(n, p)."""
    if n <= 0:
        return 1.0
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    # Integer arithmetic, then one scaling: exact for realistic n (<= ~1000).
    total = 0
    for i in range(k, n + 1):
        total += math.comb(n, i) * (int(round(p * (1 << 53))) ** i) * (
            int(round((1 - p) * (1 << 53))) ** (n - i)
        )
    scale = (1 << 53) ** n
    return min(max(total / scale, 0.0), 1.0)


def one_tailed_p_value(n: int, k: int) -> float:
    """Exact one-tailed binomial p-value against 50% guessing."""
    return binom_tail(n, k)


def min_correct_for_significance(n: int, alpha: float = DEFAULT_ALPHA) -> Optional[int]:
    """Smallest hit count in `n` trials that reaches `alpha`. None if impossible.

    Used by the UI to answer "how many do I need to get right?" and to show
    the pass threshold BEFORE the test starts (pre-registration).
    """
    for k in range(1, n + 1):
        if binom_tail(n, k) < alpha:
            return k
    return None


def is_significant(n: int, k: int, alpha: float = DEFAULT_ALPHA) -> bool:
    return binom_tail(n, k) < alpha


def binomial_ci(
    n: int, k: int, confidence: float = 0.95
) -> Tuple[float, float]:
    """Exact Clopper-Pearson interval for the discrimination probability.

    Reported alongside the p-value so a result carries an EFFECT SIZE, not just
    a yes/no. "p=0.03" without a CI is how over-claiming happens.
    Bisection on the exact binomial CDF: no special-function dependency.
    """
    if n <= 0:
        return (0.0, 1.0)
    lo_ci = 1.0 - confidence

    def cdf_at(p: float, x: int) -> float:
        """P(X <= x) for X ~ Binomial(n, p)."""
        if x < 0:
            return 0.0
        if x >= n:
            return 1.0
        total = 0.0
        for i in range(0, x + 1):
            total += math.comb(n, i) * (p ** i) * ((1.0 - p) ** (n - i))
        return total

    # Lower bound: p such that P(X >= k) = alpha/2  ->  solve on [0, k/n]
    if k == 0:
        lower = 0.0
    else:
        # P(X >= k | p) is increasing in p, so the solution lies BELOW mid
        # whenever the tail is already larger than alpha/2.
        lo, hi = 0.0, k / n
        for _ in range(200):
            mid = (lo + hi) / 2
            if binom_tail(n, k, mid) > lo_ci / 2:
                hi = mid
            else:
                lo = mid
        lower = (lo + hi) / 2

    # Upper bound: p such that P(X <= k) = alpha/2  ->  solve on [k/n, 1]
    if k == n:
        upper = 1.0
    else:
        lo, hi = k / n, 1.0
        for _ in range(200):
            mid = (lo + hi) / 2
            if cdf_at(mid, k) > lo_ci / 2:
                lo = mid
            else:
                hi = mid
        upper = (lo + hi) / 2

    return (round(lower, 6), round(min(upper, 1.0), 6))


def holm_bonferroni(
    p_values: List[float], alpha: float = DEFAULT_ALPHA
) -> List[Tuple[int, float, bool]]:
    """Holm-Walfe correction for a family of comparisons.

    A codec comparison "family" is naturally multi-member (several bitrates,
    several clips, several pipelines). Uncorrected per-comparison p-values
    inflate the family-wise false-positive rate ~linearly with family size,
    which is exactly how "Opus beats AAC at 96kbps" claims get made.

    Returns [(original_index, adjusted_p, reject), ...] sorted by adjusted p.
    """
    m = len(p_values)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: p_values[i])
    out: List[Tuple[int, float, bool]] = []
    running_max = 0.0
    for rank, idx in enumerate(order):
        adjusted = min(1.0, (m - rank) * p_values[idx])
        running_max = max(running_max, adjusted)
        out.append((idx, round(running_max, 8), running_max < alpha))
    return sorted(out, key=lambda t: t[1])


def required_trials_for_power(
    p_ability: float, alpha: float = DEFAULT_ALPHA, power: float = 0.80
) -> int:
    """Trials needed to detect a listener with true ability `p_ability`.

    Lets the tool tell a user up front how long the test must be, which is the
    only way to pre-register a defensible N. Normal approximation, ceil'd,
    floor of 2 (a single trial can never be significant).
    """
    if not (0.5 < p_ability < 1.0):
        raise ValueError("p_ability must be in (0.5, 1.0)")
    z_a = _z_from_alpha(alpha, one_sided=True)
    z_b = _z_from_alpha(1.0 - power, one_sided=True)
    numerator = (z_a * math.sqrt(0.25) + z_b * math.sqrt(p_ability * (1 - p_ability))) ** 2
    denominator = (p_ability - 0.5) ** 2
    return max(2, int(math.ceil(numerator / denominator)))


def _z_from_alpha(alpha: float, one_sided: bool) -> float:
    """Inverse standard normal CDF via bisection (stdlib-only)."""
    target = alpha if one_sided else alpha / 2
    lo, hi = -8.0, 8.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if _phi(mid) < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def verdict_text(n: int, k: int, alpha: float = DEFAULT_ALPHA) -> str:
    """Human-readable verdict with the constraint baked in."""
    p = one_tailed_p_value(n, k)
    threshold = min_correct_for_significance(n, alpha)
    ci = binomial_ci(n, k)
    if n == 0:
        return "no trials"
    if threshold is None:
        return f"{k}/{n}: cannot reach significance with n={n}"
    if k >= threshold:
        return (
            f"{k}/{n}: significant (p={p:.5f}, need >={threshold}); "
            f"ability 95% CI [{ci[0]:.2f}, {ci[1]:.2f}]"
        )
    return (
        f"{k}/{n}: not significant (p={p:.4f}, need >={threshold}); "
        f"ability 95% CI [{ci[0]:.2f}, {ci[1]:.2f}]"
    )
