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
```

The verifier cache and result volumes are mounted at `/app/cache` and `/app/results`
for commands that use them.

## Configuration

All configuration is done through environment variables (set in `.env` or passed with `-e`):

| Variable | Required | Default | Description |
|---|---|---|---|---|
| `OPENAI_BASE_URL` | **Yes** | — | URL of an OpenAI-compatible backend (e.g. `http://host:port/v1`) |
| `MODEL_ALIAS` | No | `qwen3.5-9b` | Model name the backend serves |
| `OPENAI_API_KEY` | No | `EMPTY` | API key; most local servers accept any value |
| `VERIFIER_PORT` | No | `8010` | HTTP service port |
| `VERIFIER_MIN_SCORE` | No | `0.8` | Minimum score threshold for `accepted` field |

### Example `.env`

```env
OPENAI_BASE_URL=http://host.docker.internal:8080/v1
MODEL_ALIAS=qwen3.5-9b
OPENAI_API_KEY=EMPTY
VERIFIER_PORT=8010
VERIFIER_MIN_SCORE=0.8
```

## How it works

The container installs `llm-verifier` from PyPI and wraps it with:

- **`entrypoint.sh`** — starts the HTTP service by default, or runs any command you pass
- **`server.py`** — FastAPI HTTP service with `/health`, `/v1/compare`, `/v1/select`
- **`smoke_test.py`** — standalone script that verifies the backend returns logprobs (no `llm-verifier` package dependency)
- **`verifier_smoke_test.py`** — checks `compare()` and `select()` with assertions

The verifier itself connects to your external OpenAI-compatible backend (llama.cpp, vLLM, etc.) — it is **not** served inside this container.

## Files

```
├── Dockerfile                  # Builds the verifier image
├── docker-compose.yml          # HTTP service with port mapping + volumes
├── .env.example                # Configuration template (safe to commit)
├── .gitignore
└── scripts/
    ├── entrypoint.sh           # Container entrypoint
    ├── server.py               # HTTP verifier service (FastAPI)
    ├── smoke_test.py           # Backend connectivity + logprobs test
    └── verifier_smoke_test.py  # Core verifier behavior test
```

## License

See the upstream [llm-as-a-verifier](https://github.com/llm-as-a-verifier/llm-as-a-verifier)
repository for the research project and its license. This repository contains only
the Docker deployment wrapper.
