#!/usr/bin/env python3
"""Verify llm-verifier can score and select an obviously better answer."""

import math

from llm_verifier import compare, select


criteria = [
    {
        "id": "correctness",
        "name": "Correctness",
        "description": "Does the code solve the task?",
    }
]
problem = "Write a function that returns 42."
good = "def answer():\n    return 42"
bad = "def answer():\n    return 0"

good_score, bad_score = compare(
    problem=problem,
    trace_a=good,
    trace_b=bad,
    criteria=criteria,
    n_evaluations=1,
    max_workers=1,
)
assert all(math.isfinite(score) and 0 <= score <= 1 for score in (good_score, bad_score))
assert good_score > bad_score, f"better answer did not win: {good_score} <= {bad_score}"

result = select(
    problem=problem,
    candidates=[good, bad],
    criteria=criteria,
    n_evaluations=1,
    pivots=1,
    max_workers=1,
)
assert result.index == 0 and result.best == good, f"selected candidate {result.index}, expected 0"

print(f"compare: good={good_score:.3f}, bad={bad_score:.3f}")
print(f"select: index={result.index}, scores={result.scores}")
print("PASS: verifier scored and selected the better answer")
