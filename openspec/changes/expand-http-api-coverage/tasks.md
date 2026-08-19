## 1. Usage endpoint

- [x] 1.1 Add `GET /v1/usage` handler in `app/server.py` returning structured
      counts from `fgr.USAGE` (prompt/completion/cached tokens, request
      count, model) plus the `format_usage()` string
- [x] 1.2 Add `UsageResponse` pydantic schema

## 2. Directed comparison endpoint

- [x] 2.1 Add `DirectedRequest` / `DirectedResponse` pydantic schemas
      (tasks list, pairs list with task_id/a/b, criteria, ground_truth_note,
      n_reps, model)
- [x] 2.2 Add `POST /v1/directed` handler wrapping `score_directed_pairs`
      via `asyncio.to_thread`, grouping pairs into the upstream
      `needed_pairs` map, `cache_file` under `VERIFIER_CACHE_DIR`
      (default `/app/cache`)
- [x] 2.3 Compute `directed_reward` per pair and include raw scores in the
      response
- [x] 2.4 Map backend errors to HTTP 502 (fail closed, no fabricated scores)

## 3. Verification

- [x] 3.1 Extend `scripts/api_e2e_test.py` with `/v1/usage` checks (counts
      accumulate, works while backend down, health not counted)
- [x] 3.2 Extend `scripts/api_e2e_test.py` with `/v1/directed` checks
      (rewards in [0,1], repeat request hits cache without usage increase,
      empty pairs → 422)
- [x] 3.3 Run full E2E suite against the rebuilt container and record
      results

## 4. Documentation

- [x] 4.1 Update `docs/API-ROADMAP.md` (score_pair_criterion → wrapped,
      directed_reward/score_directed_pairs → wrapped, token_usage/
      format_usage → wrapped)
- [x] 4.2 Update README API section with `/v1/usage` and `/v1/directed`
      request/response examples