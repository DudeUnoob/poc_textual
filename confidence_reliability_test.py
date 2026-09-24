"""
Confidence Reliability Hypothesis Test
========================================
Implements the statistical test proposed by Jayden McCree (IUPRA, CTX Database
project) to determine whether the LLM's high-confidence transcriptions can be
trusted on data with no ground truth available.

Background
----------
For each cell in a ground-truth-checked sample, define:
    Incorrect_i = True if the LLM's transcription does NOT match ground truth
    Confident_i = True if the LLM reported high confidence for that cell

The "bad scenario" is (Incorrect=True, Confident=True): the LLM is wrong AND
confident. This is the dangerous case because high-confidence cells are NOT
sent to human review, so an error here is never caught.

We treat the number of bad scenarios K across N ground-truth cells as
K ~ Binomial(N, p), where p is the true (unknown) proportion of bad
scenarios if the LLM were run on all of Bastrop County.

We test:
    H0: p <= p0   (error rate is within a tolerable threshold p0)
    H1: p > p0    (error rate exceeds the tolerable threshold)

by computing P(K >= k_observed | p = p0). A LOW p-value (conventionally
< 0.05) is evidence AGAINST H0, i.e., evidence that high-confidence cells
are unreliable at a rate worse than tolerable.

Usage
-----
    from confidence_reliability_test import run_confidence_test

    # cells: list of (incorrect: bool, high_confidence: bool)
    cells = [(False, True), (False, True), (True, True), ...]

    result = run_confidence_test(cells, tolerance=0.02)
    print(result)
"""

from dataclasses import dataclass
from scipy.stats import binomtest


@dataclass
class ConfidenceTestResult:
    n_cells: int                  # total ground-truth cells examined
    n_bad_scenarios: int          # count where Incorrect=True AND Confident=True
    observed_rate: float          # n_bad_scenarios / n_cells
    tolerance: float              # p0, the tolerable "bad scenario" rate
    p_value: float                # P(K >= observed | p = p0)
    reject_null: bool             # True if p_value < alpha (evidence of unreliability)
    alpha: float

    def __str__(self):
        verdict = (
            "REJECT H0 — evidence that high-confidence cells are unreliable "
            "beyond the tolerable rate. Recommend NOT trusting high-confidence "
            "cells on uncleaned data without further review."
            if self.reject_null else
            "FAIL TO REJECT H0 — no statistical evidence that the bad-scenario "
            "rate exceeds tolerance. High-confidence cells may reasonably be "
            "trusted, subject to independence caveats (see docstring)."
        )
        return (
            f"Cells examined:      {self.n_cells}\n"
            f"Bad scenarios found: {self.n_bad_scenarios} "
            f"(Incorrect=True AND Confident=True)\n"
            f"Observed bad rate:   {self.observed_rate:.4f}\n"
            f"Tolerance (p0):      {self.tolerance:.4f}\n"
            f"p-value:             {self.p_value:.6f}\n"
            f"Alpha:               {self.alpha}\n"
            f"Verdict: {verdict}"
        )


def run_confidence_test(cells, tolerance: float, alpha: float = 0.05) -> ConfidenceTestResult:
    """
    Run Jayden's hypothesis test on a set of ground-truth-checked cells.

    Parameters
    ----------
    cells : list of (incorrect: bool, high_confidence: bool)
        One entry per ground-truth cell examined during testing.
        incorrect=True means the LLM's value did NOT match ground truth.
        high_confidence=True means the LLM reported high confidence for that cell.
    tolerance : float
        p0 — the maximum "bad scenario" proportion you're willing to accept
        if the LLM were run on all of Bastrop County. E.g. 0.02 = tolerate
        up to a 2% silent-error rate among high-confidence cells.
    alpha : float
        Significance level for rejecting H0 (default 0.05, standard convention).

    Returns
    -------
    ConfidenceTestResult
    """
    n = len(cells)
    if n == 0:
        raise ValueError("Need at least one ground-truth cell to run this test.")

    bad_count = sum(1 for incorrect, high_conf in cells if incorrect and high_conf)

    # One-sided binomial test: P(K >= bad_count | p = tolerance)
    test = binomtest(bad_count, n, tolerance, alternative="greater")
    p_value = test.pvalue

    return ConfidenceTestResult(
        n_cells=n,
        n_bad_scenarios=bad_count,
        observed_rate=bad_count / n,
        tolerance=tolerance,
        p_value=p_value,
        reject_null=(p_value < alpha),
        alpha=alpha,
    )


def scenario_breakdown(cells):
    """
    Break down all four (Incorrect, Confident) scenarios from Jayden's
    proposal, useful for a sanity-check / descriptive summary alongside
    the formal test.
    """
    counts = {
        "BAD: Incorrect=True, Confident=True (wrong & confident, UNCAUGHT)": 0,
        "OK: Incorrect=False, Confident=True (correct & confident)": 0,
        "OK: Incorrect=True, Confident=False (wrong, but flagged for review)": 0,
        "OK: Incorrect=False, Confident=False (correct, flagged for review anyway)": 0,
    }
    for incorrect, high_conf in cells:
        if incorrect and high_conf:
            counts["BAD: Incorrect=True, Confident=True (wrong & confident, UNCAUGHT)"] += 1
        elif not incorrect and high_conf:
            counts["OK: Incorrect=False, Confident=True (correct & confident)"] += 1
        elif incorrect and not high_conf:
            counts["OK: Incorrect=True, Confident=False (wrong, but flagged for review)"] += 1
        else:
            counts["OK: Incorrect=False, Confident=False (correct, flagged for review anyway)"] += 1
    return counts


if __name__ == "__main__":
    # ------------------------------------------------------------------
    # SANITY-CHECK EXAMPLE using synthetic data — replace with real
    # (incorrect, high_confidence) pairs pulled from actual ground-truth
    # comparison output once available (from Damodar's results/ or a
    # fresh run against data/ground_truth/).
    # ------------------------------------------------------------------
    import random
    random.seed(42)

    # Simulate 500 cells: 90% high confidence, and among high-confidence
    # cells, a 3% true error rate (worse than a 2% tolerance) to show
    # what a "reject H0" result looks like.
    example_cells = []
    for _ in range(500):
        high_conf = random.random() < 0.90
        if high_conf:
            incorrect = random.random() < 0.03
        else:
            incorrect = random.random() < 0.20
        example_cells.append((incorrect, high_conf))

    print("=== Scenario breakdown ===")
    for label, count in scenario_breakdown(example_cells).items():
        print(f"  {label}: {count}")

    print("\n=== Hypothesis test (tolerance = 2% bad-scenario rate) ===")
    result = run_confidence_test(example_cells, tolerance=0.02)
    print(result)
