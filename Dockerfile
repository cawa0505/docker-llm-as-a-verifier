FROM python:3.11-slim

WORKDIR /app

# Install llm-verifier and HTTP server dependencies
RUN pip install --no-cache-dir llm-verifier==0.2.0 fastapi uvicorn[standard]

# Entrypoint and scripts
COPY scripts/ /app/scripts/
RUN chmod +x /app/scripts/*.sh

# Verifier score cache and result output directories
RUN mkdir -p /app/cache /app/results

ENTRYPOINT ["/app/scripts/entrypoint.sh"]