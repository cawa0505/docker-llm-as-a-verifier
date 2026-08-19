## Context

The service (`app/server.py`) wraps `llm-verifier==0.2.0` behind FastAPI with
a llama.cpp-compatible monkey-patched `call_openai`. All verifier calls run
through `asyncio.to_thread` with `max_workers=1` and a shared
`_VERIFIER_CLIENT` (timeout 300s, `max_retries=0`). Usage is already recorded
on every call via `fgr.USAGE.record(response)` but never exposed. The cache
volume mounts at `/app/cache`. See proposal.md for motivation.

## Goals / Non-Goals

**Goals:**
- Expose accumulated token usage via a stateless-over-lifetime endpoint.
- Wrap `score_directed_pairs` + `directed_reward` as `POST /v1/directed`
  with disk-cache persistence on the cache volume.
- Keep the llama.cpp compatibility patch untouched (both features reuse it).

**Non-Goals:**
- No image input (deferred — see proposal `[待討論]`).
- No concurrency increase (`max_workers=1` stays; upstream
  `score_directed_pairs` accepts `max_workers` but the server serializes).
- No auth, no multi-tenant isolation.

## Decisions

**D1: `GET /v1/usage` reads `fgr.USAGE` directly.**
`token_usage()` returns the accumulated counts; `format_usage()` renders a
human-readable report. The endpoint returns structured JSON (counts +
request count) and includes the formatted string for convenience.
Alternative considered: tracking usage in server.py itself — rejected, the
package already records it; duplicating would drift.

**D2: `POST /v1/directed` wraps `score_directed_pairs` with a cache file.**
Upstream signature: `score_directed_pairs(lazy_client, tasks, needed_pairs,
criteria, ground_truth_note, n_reps, max_workers, cache_file, model, ...)`.
The endpoint maps request fields onto it, passes `cache_file` under
`/app/cache` (env `VERIFIER_CACHE_DIR`, default `/app/cache`), and computes
`directed_reward` per pair for the response. `on_error` stays `'tie'`
(default) — matches the existing `/v1/score-pairs` behavior.
Alternative considered: reimplementing cache logic in server.py — rejected,
upstream already merges scored pairs into the cache file.

**D3: Request schema mirrors upstream's `needed_pairs` map.**
`tasks` is a list of `{id, problem}`; `pairs` is a list of
`{task_id, a, b}`. The server groups pairs by task into the `needed_pairs`
map. This keeps the endpoint generic (multiple tasks per call) without
inventing a new shape.
Alternative considered: flat `{problem, a, b}` list like `/v1/score-pairs` —
rejected, upstream's cache keying is per-task; a flat shape would force
task-name synthesis.

**D4: Response includes raw scores and rewards.**
Per pair: `{task_id, a, b, reward_a, reward_b, score_a, score_b}`.
`directed_reward` needs `criteria_ids` and `n_reps` — both known from the
request, so rewards are computed server-side.

## Risks / Trade-offs

- [Cache file growth] → bounded by distinct (task, a, b, criterion) pairs;
  cache volume is operator-managed, documented in README.
- [`score_directed_pairs` progress bar in server logs] → upstream prints a
  progress bar; harmless in container logs, noted as cosmetic.
- [Usage resets on restart] → documented in spec (Scenario: Reset on
  restart); operators wanting cumulative cost should scrape `/v1/usage`
  periodically.

## Migration Plan

- Additive endpoints only; existing routes unchanged. Deploy = rebuild +
  restart container. Rollback = revert image; no data migration (cache file
  is additive and reusable).

## Open Questions

None — deferred items (image input) are explicitly out of scope in the
proposal and do not affect these specs.