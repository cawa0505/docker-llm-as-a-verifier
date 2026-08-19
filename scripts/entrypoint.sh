#!/bin/sh
# entrypoint.sh — LLM-as-a-Verifier Docker container entrypoint.
#
# Default mode: start the long-running HTTP verifier service (for
# `docker compose up -d`).
#
# Pass a command to run one-off tasks instead:
#   docker compose run --rm verifier python scripts/smoke_test.py
#   docker compose run --rm verifier python scripts/verifier_smoke_test.py
#
# Required env: OPENAI_BASE_URL (OpenAI-compatible backend with logprobs)
# Optional env: MODEL_ALIAS (default: qwen3.5-9b)
#               OPENAI_API_KEY (default: EMPTY)
#               VERIFIER_PORT (default: 8010)
#               VERIFIER_MIN_SCORE (default: 0.8)

set -e

if [ $# -gt 0 ]; then
    exec "$@"
fi

echo "=== LLM-as-a-Verifier HTTP Service ==="
echo "Backend: ${OPENAI_BASE_URL:-not set}"
echo "Model:   ${MODEL_ALIAS:-qwen3.5-9b}"
echo "Port:    ${VERIFIER_PORT:-8010}"

if [ -z "$OPENAI_BASE_URL" ]; then
    echo ""
    echo "ERROR: OPENAI_BASE_URL is not set."
    echo "Set it in .env or pass -e OPENAI_BASE_URL=http://host:port/v1"
    echo ""
    echo "One-off commands:"
    echo "  python scripts/smoke_test.py        — test backend connectivity"
    echo "  python scripts/verifier_smoke_test.py  — verify compare()/select()"
    echo ""
    exit 1
fi

exec python /app/app/server.py