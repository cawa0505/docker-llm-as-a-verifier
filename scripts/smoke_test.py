#!/usr/bin/env python3
"""Smoke test: verify the verifier backend is reachable and returns logprobs.
Uses OPENAI_BASE_URL / MODEL_ALIAS from the environment or .env file.

Usage:
    python scripts/smoke_test.py
"""

import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

# Try loading .env from the working directory (optional; env vars take
# precedence because load_dotenv uses setdefault).
_env_path = os.path.join(ROOT_DIR, ".env")
if os.path.exists(_env_path):
    for line in open(_env_path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

base_url = os.environ.get("OPENAI_BASE_URL")
model_alias = os.environ.get("MODEL_ALIAS", "qwen3.5-9b")

if not base_url:
    print("FAIL: OPENAI_BASE_URL not set")
    sys.exit(1)

print(f"Backend: {base_url}")
print(f"Model alias: {model_alias}")

from openai import OpenAI

client = OpenAI(
    base_url=base_url,
    api_key=os.environ.get("OPENAI_API_KEY", "EMPTY"),
)

# Verify the requested alias is actually served. llama.cpp silently serves
# its loaded model for ANY requested name, so an unknown MODEL_ALIAS would
# otherwise produce a false PASS.
served = []
try:
    for m in client.models.list():
        mid = getattr(m, "id", None)
        if mid:
            served.append(mid)
        mname = getattr(m, "name", None)
        if mname:
            served.append(mname)
except Exception as exc:
    print(f"FAIL: cannot resolve model list from backend: {exc}")
    sys.exit(1)

if model_alias not in served:
    print(f"FAIL: MODEL_ALIAS {model_alias!r} is not served by the backend "
          f"(served: {sorted(set(served)) or 'none'})")
    sys.exit(1)

print(f"Model alias resolved: {model_alias}")

# Minimal chat call with logprobs to confirm the backend works end-to-end.
response = client.chat.completions.create(
    model=model_alias,
    messages=[{"role": "user", "content": "Say hello."}],
    max_tokens=16,
    temperature=0.0,
    logprobs=True,
    top_logprobs=5,
)

text = response.choices[0].message.content
has_logprobs = bool(
    response.choices[0].logprobs
    and response.choices[0].logprobs.content
)

print(f"Response: {text!r}")
print(f"Logprobs returned: {has_logprobs}")

if not has_logprobs:
    print("FAIL: backend did not return token-level logprobs")
    sys.exit(1)

print("PASS: backend reachable, logprobs present")
