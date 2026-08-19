# docker-llm-as-a-verifier

[Docker](https://docker.com) deployment for [LLM-as-a-Verifier](https://github.com/llm-as-a-verifier/llm-as-a-verifier) — a fine-grained LLM verification framework that reads token-level logprobs to produce continuous rewards for best-of-N selection, progress tracking, and preference comparison.

This repository packages the upstream project for reproducible Docker-based
deployment. It is not the research implementation and does not claim
ownership of the underlying method.

## Upstream project

This deployment is based on the following upstream research and source code:

- **Paper:** [LLM-as-a-Verifier](https://arxiv.org/abs/2607.05391)
- **Source repository:** [llm-as-a-verifier/llm-as-a-verifier](https://github.com/llm-as-a-verifier/llm-as-a-verifier)
- **Documentation:** [llm-as-a-verifier.com/docs](https://llm-as-a-verifier.com/docs/)

Please refer to the upstream repository and paper for the verification
algorithm, evaluation methodology, and research results.

## Prerequisites

- Docker & Docker Compose (or Podman with compose plugin)
- An **OpenAI-compatible backend** that returns token-level logprobs (llama.cpp, vLLM, SGLang, DeepSeek API, etc.)

## Quick start

```bash
# 1. Clone
git clone https://github.com/cawa0505/docker-llm-as-a-verifier.git
cd docker-llm-as-a-verifier

# 2. Configure your backend
cp .env.example .env
# Edit .env — set OPENAI_BASE_URL to your backend's /v1 endpoint

# 3. Build and start the verifier service
docker compose build
docker compose up -d
```

The service listens on port `8010` by default. Verify it is running:

```bash
curl http://localhost:8010/health
```

Response:

```json
{"status": "ok", "model": "qwen3.5-9b", "backend": "http://host:port/v1"}
```

### One-off checks

You can also run a smoke test or core verifier test as a one-off command:

```bash
# Backend connectivity + logprobs
docker compose run --rm verifier python scripts/smoke_test.py

# Core verifier compare()/select() with assertions
docker compose run --rm verifier python scripts/verifier_smoke_test.py

# End-to-end HTTP API test (service must be up; runs from the host)
python3 scripts/api_e2e_test.py
```

The verifier cache and result volumes are mounted at `/app/cache` and `/app/results`
for commands that use them.

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check — returns status, model alias, backend URL |
| `/v1/compare` | POST | Compare two traces (A vs B) → scores + accepted flag |
| `/v1/select` | POST | Select best from N candidates → index + scores |
| `/v1/track` | POST | Score an agent trajectory's progress after each step |
| `/v1/score-pairs` | POST | Batch score multiple (A, B) comparisons |
| `/v1/usage` | GET | Accumulated token usage since service start |
| `/v1/directed` | POST | Score directed (task, a, b) pairs with cache + rewards |

All endpoints return JSON. Error responses:

- `422` — request validation failed (missing/invalid fields)
- `502` — backend request failed (unreachable backend, timeout, or verifier error)

### `POST /v1/compare`

Compare two candidate traces (A vs B) for a given problem. Returns fine-grained
scores in [0, 1] and whether `score_b` meets the `VERIFIER_MIN_SCORE` threshold.

```json
{
  "problem": "Fix the bug in foo()",
  "trace_a": "initial code...",
  "trace_b": "patched code...",
  "criteria": [{"id": "correctness", "name": "Correctness", "description": "..."}],
  "n_evaluations": 1
}
→
{"score_a": 0.12, "score_b": 0.95, "accepted": true, "model": "qwen3.5-9b"}
```

Optionally pass `images` (array of base64 data URIs or HTTP URLs) for
multimodal evaluation. File paths are rejected with 422.

```json
{
  "problem": "Fix the bug in foo()",
  "trace_a": "initial code...",
  "trace_b": "patched code...",
  "criteria": [{"id": "correctness", "name": "Correctness", "description": "..."}],
  "n_evaluations": 1,
  "images": ["data:image/png;base64,iVBOR..."]
}
→
{"score_a": 0.12, "score_b": 0.95, "accepted": true, "model": "qwen3.5-9b"}
```

The `images` field is also accepted on `POST /v1/select`, `POST /v1/track`,
`POST /v1/score-pairs`, and `POST /v1/directed` (via `tasks` entries).

### `POST /v1/select`

Select the best candidate from N options using the Probabilistic Pivot Tournament.

```json
{
  "problem": "Implement a sorting function",
  "candidates": ["def sort(x): return x", "def sort(x): return sorted(x)", "..."],
  "criteria": [{"id": "correctness", "name": "Correctness", "description": "..."}],
  "n_evaluations": 1,
  "pivots": 2
}
→
{"index": 1, "scores": [0.12, 0.95, 0.45], "n_comparisons": 3, "model": "qwen3.5-9b"}
```

### `POST /v1/track`

Score an agent trajectory's progress at multiple checkpoints after each step.
The verifier evaluates all checkpoints in a single call (O(K) cost regardless of
trajectory length).

```json
{
  "problem": "Write a function that returns 42",
  "steps": [
    "def foo(): return 0",
    "def foo(): return 42"
  ],
  "checkpoint_steps": [1, 2],
  "n_evaluations": 1
}
→
{
  "steps": [1, 2],
  "scores": [0.58, 0.58],
  "per_rep_scores": [[0.58, 0.58]],
  "model": "qwen3.5-9b"
}
```

The `steps` array contains the agent's actions (one string per step).
`checkpoint_steps` are 1-indexed step numbers to score (defaults to interior
steps when omitted). Each score is the expected value of the A–T letter scale
mapped to [0, 1].

The model sometimes fails to emit the required `<c{i}>LETTER</c{i}>` answer
format (reasoning-mode models may return empty content). The service retries
up to 3 times and returns `502` if no parseable scores are produced — it never
silently returns the neutral 0.5 fallback.

### `POST /v1/score-pairs`

Score multiple (A, B) comparisons in one batch. Each pair is scored
independently and averaged over `n_reps` reps. Odd reps swap the prompt slots
to cancel slot bias.

```json
{
  "pairs": [
    {"problem": "Write a function that returns 42", "trace_a": "def foo(): return 0", "trace_b": "def foo(): return 42"},
    {"problem": "Write a function that returns 42", "trace_a": "def foo(): return 42", "trace_b": "def foo(): return 0"}
  ],
  "ground_truth_note": "The correct answer is a function that returns the integer 42.",
  "n_reps": 2
}
→
{
  "scores": [
    {"score_a": 0.04, "score_b": 0.999},
    {"score_a": 0.999, "score_b": 0.04}
  ],
  "model": "qwen3.5-9b"
}
```

### `GET /v1/usage`

Return accumulated token usage since the service started. No backend
interaction — always succeeds (even when the backend is down). Health check
requests are NOT counted.

```json
→
{
  "model": "qwen3.5-9b",
  "input_tokens": 1234,
  "output_tokens": 567,
  "cached_input_tokens": 0,
  "backend_requests": 8,
  "formatted": "Verifier tokens (8 verifier calls)\n  input ..."
}
```

### `POST /v1/directed`

Score directed (task, a, b) pairs with disk-cache persistence. Groups pairs by
task, calls `score_directed_pairs` for cache-aware scoring, then computes
`directed_reward` (R_a, R_b) per pair. Odd `n_reps` swap a/b slots to cancel
slot bias. Repeat requests hit the cache without additional backend calls.

```json
{
  "tasks": [{"id": "task-1", "problem": "Write a function that returns 42."}],
  "pairs": [{"task_id": "task-1", "a": "def foo(): return 0", "b": "def foo(): return 42"}],
  "criteria": [{"id": "correctness", "name": "Correctness", "description": "Does the output correctly solve the task?"}],
  "ground_truth_note": "The correct answer is a function that returns the integer 42.",
  "n_reps": 2
}
→
{
  "results": [
    {
      "task_id": "task-1",
      "a": "def foo(): return 0",
      "b": "def foo(): return 42",
      "score_a": 0.04,
      "score_b": 0.999,
      "reward_a": 0.04,
      "reward_b": 0.999
    }
  ],
  "cached": false,
  "model": "qwen3.5-9b"
}
```

## Configuration

All configuration is done through environment variables (set in `.env` or passed with `-e`):

| Variable | Required | Default | Description |
|---|---|---|---|
| `OPENAI_BASE_URL` | **Yes** | — | URL of an OpenAI-compatible backend (e.g. `http://host:port/v1`) |
| `MODEL_ALIAS` | No | `qwen3.5-9b` | Model name the backend serves |
| `OPENAI_API_KEY` | No | `EMPTY` | API key; most local servers accept any value |
| `VERIFIER_PORT` | No | `8010` | HTTP service port |
| `VERIFIER_MIN_SCORE` | No | `0.8` | Minimum score threshold for `accepted` field |
| `VERIFIER_BACKEND_TIMEOUT` | No | `300` | Backend request timeout in seconds (increase for long prompts) |
| `VERIFIER_CACHE_DIR` | No | `/app/cache` | Directory for directed pair cache (persisted via volume) |

### Example `.env`

```env
OPENAI_BASE_URL=http://host.docker.internal:8080/v1
MODEL_ALIAS=qwen3.5-9b
OPENAI_API_KEY=EMPTY
VERIFIER_PORT=8010
VERIFIER_MIN_SCORE=0.8
VERIFIER_BACKEND_TIMEOUT=300
```

## How it works

The container installs `llm-verifier` from PyPI and wraps it with:

- **`entrypoint.sh`** — starts the HTTP service by default, or runs any command you pass
- **`app/server.py`** — FastAPI HTTP service with `/health`, `/v1/compare`, `/v1/select`, `/v1/track`, `/v1/score-pairs`, `/v1/usage`, `/v1/directed`
- **`smoke_test.py`** — standalone script that verifies the backend returns logprobs (no `llm-verifier` package dependency)
- **`verifier_smoke_test.py`** — checks `compare()` and `select()` with assertions
- **`api_e2e_test.py`** — end-to-end test of all 38 HTTP test cases against a running service (compare, select, track, score-pairs, usage, directed, images, error handling)

The verifier itself connects to your external OpenAI-compatible backend (llama.cpp, vLLM, etc.) — it is **not** served inside this container.

## Files

```
├── Dockerfile                  # Builds the verifier image
├── docker-compose.yml          # HTTP service with port mapping + volumes
├── .env.example                # Configuration template (safe to commit)
├── .gitignore
├── app/
│   └── server.py               # HTTP verifier service (FastAPI)
├── scripts/
│   ├── entrypoint.sh           # Container entrypoint
│   ├── smoke_test.py           # Backend connectivity + logprobs test
│   ├── verifier_smoke_test.py  # Core verifier behavior test
│   └── api_e2e_test.py         # End-to-end HTTP API test
├── docs/
│   └── API-ROADMAP.md          # API coverage roadmap
└── openspec/                   # OpenSpec change artifacts (private)
```

## License

See the upstream [llm-as-a-verifier](https://github.com/llm-as-a-verifier/llm-as-a-verifier)
repository for the research project and its license. This repository contains only
the Docker deployment wrapper.
