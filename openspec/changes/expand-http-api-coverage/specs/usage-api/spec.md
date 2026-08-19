## Purpose

Exposes the verifier service's accumulated token consumption so operators can
track and budget LLM cost without inspecting server logs.

## ADDED Requirements

### Requirement: Usage reporting endpoint

The service SHALL expose `GET /v1/usage` returning the token usage accumulated
since the service started: prompt tokens, completion tokens, cached tokens,
and the number of backend requests made.

#### Scenario: Report accumulated usage

WHEN a client requests `GET /v1/usage`
THEN the response SHALL contain JSON with prompt/completion/cached token
counts, a backend request count, and the model alias in use.

#### Scenario: Counts accumulate across calls

WHEN at least one `/v1/compare`, `/v1/select`, `/v1/track`, or
`/v1/score-pairs` request was served before the usage request
THEN the reported token counts SHALL be greater than or equal to the counts
reported before those requests.

#### Scenario: Reset on restart

WHEN the service restarts
THEN a subsequent `GET /v1/usage` SHALL report counts starting from zero.

### Requirement: Usage endpoint availability

The usage endpoint SHALL be available without any backend interaction and
SHALL NOT fail when the backend is unreachable.

#### Scenario: Usage works while backend is down

WHEN the backend is unreachable AND a client requests `GET /v1/usage`
THEN the request SHALL succeed with HTTP 200 and the last accumulated counts.

#### Scenario: Non-verifier traffic does not distort usage

WHEN a client requests `GET /v1/health` between verifier calls
THEN the usage counts SHALL NOT include the health request.
