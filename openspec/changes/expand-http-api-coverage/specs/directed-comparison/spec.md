## Purpose

Provides directed (task, a, b) comparison scoring over the HTTP API: score
non-symmetric candidate pairs, persist scored pairs to a cache volume so
repeat calls are free, and compute directed rewards (R_a, R_b) averaged over
criteria and repeats.

## ADDED Requirements

### Requirement: Directed comparison endpoint

The service SHALL expose `POST /v1/directed` that accepts a task description,
a set of directed (a, b) pairs, criteria, ground-truth note, and a repeat
count, and SHALL return per-pair directed rewards (R_a, R_b) in [0, 1].

#### Scenario: Score directed pairs

WHEN a client posts a task with two directed pairs and `n_reps=1`
THEN the response SHALL contain one reward pair (R_a, R_b) per requested
pair, each value a finite number in [0, 1].

#### Scenario: Slot-bias cancellation across reps

WHEN a client posts a pair with `n_reps >= 2`
THEN odd repeats SHALL swap the a/b prompt slots and the final rewards SHALL
be averaged over all repeats in candidate order.

#### Scenario: Missing pairs are rejected

WHEN a client posts a request with an empty pairs set or no tasks
THEN the service SHALL reject the request with HTTP 422.

### Requirement: Cache persistence

The service SHALL persist scored pairs to the cache directory so a repeated
request for the same (task, a, b, criterion) pair does not re-score it.

#### Scenario: Repeat request hits cache

WHEN the same directed pair is requested twice
THEN the second response SHALL be produced from the cache without additional
backend calls, and the token usage SHALL NOT increase for the second call.

#### Scenario: Cache survives restart

WHEN the service restarts and the same directed pair is requested again
THEN the pair SHALL be served from the persisted cache on the cache volume.

### Requirement: Failure behavior

The service SHALL fail closed on backend errors: a request whose scoring
fails SHALL return an error response rather than fabricated scores.

#### Scenario: Backend unreachable

WHEN the backend is unreachable AND a client posts a directed pair not in
cache
THEN the service SHALL return HTTP 502 with a descriptive error.
