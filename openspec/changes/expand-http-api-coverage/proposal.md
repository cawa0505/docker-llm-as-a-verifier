## Why

The HTTP API currently wraps 4 of the 13 public `llm-verifier` functions. Two
valuable capabilities are missing: token usage reporting (the server already
records usage via `fgr.USAGE.record()` but never exposes it — users cannot
track verifier cost) and directed comparison (`score_directed_pairs` +
`directed_reward`), which scores non-symmetric (task, a, b) pairs and computes
directed rewards (R_a, R_b) — the primitive behind preference-based
optimization workflows.

## What Changes

- Add `GET /v1/usage` — returns accumulated token usage (prompt/completion
  tokens, cached tokens, per-call count) since service start, via upstream
  `token_usage()` + `format_usage()`.
- Add `POST /v1/directed` — scores directed (task, a, b) pairs with
  slot-swap rep averaging and computes directed rewards (R_a, R_b) via
  upstream `score_directed_pairs` + `directed_reward`.
  - Request: tasks + needed pairs + criteria + ground truth + n_reps
  - Response: per-pair directed rewards (R_a, R_b) and raw scores
  - Disk cache: persists scored pairs to the mounted `/app/cache` volume so
    repeat calls avoid re-scoring (upstream `cache_file` support)
- Update `docs/API-ROADMAP.md` — mark `score_pair_criterion` as wrapped (it
  already is via `/v1/score-pairs`), promote `directed_reward` /
  `score_directed_pairs` from "待包裝" to planned/wrapped, add `token_usage` /
  `format_usage`.
- Image input for `compare`/`select`/`track`: **[待討論]** — the server's
  patched `call_openai` already handles `image_url`, but the current backend
  (Qwen3.5-9B, text-only) cannot verify it. Deferred until a VLM backend is
  available; no schema changes now.

## Capabilities

### New Capabilities

- `usage-api`: Token usage reporting — exposes accumulated verifier token
  consumption over the service lifetime.
- `directed-comparison`: Directed (task, a, b) comparison scoring with
  slot-swap rep averaging, disk cache persistence, and directed reward
  (R_a, R_b) computation.

### Modified Capabilities

None — existing endpoints (`/health`, `/v1/compare`, `/v1/select`,
`/v1/track`, `/v1/score-pairs`) keep their current behavior.

## Impact

- **Code**: `app/server.py` (two new endpoints, `score_directed_pairs`
  wrapped via `asyncio.to_thread`, cache dir wired to `/app/cache`),
  `scripts/api_e2e_test.py` (new endpoint coverage), `docs/API-ROADMAP.md`.
- **Dependencies**: none new — both features use functions already in
  `llm-verifier==0.2.0`.
- **Backend contract**: unchanged; both endpoints reuse the existing
  llama.cpp-compatible monkey-patched `call_openai`.
- **Ops**: `GET /v1/usage` is stateless-over-lifetime (resets on restart);
  `POST /v1/directed` writes to the cache volume only.
