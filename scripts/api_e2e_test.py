#!/usr/bin/env python3
"""End-to-end test of the verifier HTTP API against a running service.

Requires the HTTP service to be up (docker compose up -d) and the backend
to be reachable. Hits every endpoint with the fixed good/bad pair:

    GET  /health
    POST /v1/compare
    POST /v1/select
    POST /v1/track
    POST /v1/score-pairs
    POST /v1/compare (validation error path)

Usage:
    docker compose up -d
    python3 scripts/api_e2e_test.py        # from the host (pure stdlib)

The test hits the service on the host's mapped port (VERIFIER_BASE_URL,
default http://localhost:8010), so it runs from the host rather than
inside a run container.
"""

import json
import os
import sys
import urllib.error
import urllib.request

BASE_URL = os.environ.get("VERIFIER_BASE_URL", "http://localhost:8010")

PROBLEM = "Write a function that returns 42."
GOOD = "def foo(): return 42"
BAD = "def foo(): return 0"

CRITERIA = [{
    "id": "correctness",
    "name": "Correctness",
    "description": "Does the output correctly solve the task?",
}]


def _post(path, body):
    req = urllib.request.Request(
        BASE_URL + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=600) as resp:
        return resp.status, json.loads(resp.read())


def _get(path):
    with urllib.request.urlopen(BASE_URL + path, timeout=30) as resp:
        return resp.status, json.loads(resp.read())


def _check(name, cond, detail=""):
    if not cond:
        print(f"FAIL: {name} {detail}")
        sys.exit(1)
    print(f"PASS: {name}")


# 1. Health
status, health = _get("/health")
_check("health", status == 200 and health["status"] == "ok", str(health))

# 2. Compare: good must beat bad
status, cmp = _post("/v1/compare", {
    "problem": PROBLEM,
    "trace_a": GOOD,
    "trace_b": BAD,
    "criteria": CRITERIA,
    "n_evaluations": 1,
})
_check("compare status", status == 200)
_check("compare scores in [0,1]",
       0.0 <= cmp["score_a"] <= 1.0 and 0.0 <= cmp["score_b"] <= 1.0,
       str(cmp))
_check("compare good > bad", cmp["score_a"] > cmp["score_b"], str(cmp))
_check("compare accepted is bool", isinstance(cmp["accepted"], bool))
print(f"  compare: good={cmp['score_a']:.3f} bad={cmp['score_b']:.3f} "
      f"accepted={cmp['accepted']}")

# 3. Select: must pick the good candidate (index 0)
status, sel = _post("/v1/select", {
    "problem": PROBLEM,
    "candidates": [GOOD, BAD],
    "criteria": CRITERIA,
    "n_evaluations": 1,
    "pivots": 2,
})
_check("select status", status == 200)
_check("select picks good", sel["index"] == 0, str(sel))
_check("select scores in [0,1]",
       all(0.0 <= s <= 1.0 for s in sel["scores"]), str(sel))
print(f"  select: index={sel['index']} scores={[round(s, 3) for s in sel['scores']]}")

# 4. Track: progress should rise toward the correct answer
steps = ["Read the problem", "Wrote def foo(): return 0", "Fixed to def foo(): return 42"]
status, trk = _post("/v1/track", {
    "problem": PROBLEM,
    "steps": steps,
    "checkpoint_steps": [1, 2, 3],
    "n_evaluations": 1,
})
_check("track status", status == 200)
_check("track score count", len(trk["scores"]) == 3, str(trk))
_check("track scores in [0,1]",
       all(0.0 <= s <= 1.0 for s in trk["scores"]), str(trk))
_check("track scores are real (not 0.5 fallback)",
       any(s is not None for rep in trk["per_rep_scores"] for s in rep),
       str(trk))
print(f"  track: scores={[round(s, 3) for s in trk['scores']]}")

# 5. Score pairs: batch comparison
status, pairs = _post("/v1/score-pairs", {
    "pairs": [{"problem": PROBLEM, "trace_a": GOOD, "trace_b": BAD}],
    "criteria": CRITERIA,
    "n_reps": 1,
})
_check("score-pairs status", status == 200)
_check("score-pairs count", len(pairs["scores"]) == 1, str(pairs))
sa, sb = pairs["scores"][0]["score_a"], pairs["scores"][0]["score_b"]
_check("score-pairs in [0,1]", 0.0 <= sa <= 1.0 and 0.0 <= sb <= 1.0, str(pairs))
_check("score-pairs good > bad", sa > sb, str(pairs))
print(f"  score-pairs: good={sa:.3f} bad={sb:.3f}")

# 6. Validation error path: missing problem must be rejected (422)
try:
    _post("/v1/compare", {"trace_a": GOOD, "trace_b": BAD})
except urllib.error.HTTPError as exc:
    _check("validation 422", exc.code == 422, f"got {exc.code}")
else:
    _check("validation 422", False, "missing problem was accepted")

# 7. Usage: works without any backend interaction
status, usage = _get("/v1/usage")
_check("usage status", status == 200, str(usage))
_check("usage has model", "model" in usage, str(usage))
_check("usage counts >= 0",
       usage["prompt_tokens"] >= 0 and usage["completion_tokens"] >= 0,
       str(usage))
_check("usage backend_requests >= 5",
       usage["backend_requests"] >= 5,  # health is NOT counted, 5 verifier calls
       f"got {usage['backend_requests']}")
_check("usage has formatted", len(usage["formatted"]) > 0, str(usage))

# Re-read usage — counts should be identical (no new verifier call)
status, usage2 = _get("/v1/usage")
_check("usage idempotent", usage2["backend_requests"] == usage["backend_requests"],
       f"before={usage['backend_requests']} after={usage2['backend_requests']}")

# 8. Directed comparison
status, directed = _post("/v1/directed", {
    "tasks": [{"id": "task-1", "problem": PROBLEM}],
    "pairs": [{"task_id": "task-1", "a": GOOD, "b": BAD}],
    "criteria": CRITERIA,
    "n_reps": 1,
})
_check("directed status", status == 200, str(directed))
_check("directed results count", len(directed["results"]) == 1, str(directed))
r = directed["results"][0]
_check("directed reward_a in [0,1]", 0.0 <= r["reward_a"] <= 1.0, str(r))
_check("directed reward_b in [0,1]", 0.0 <= r["reward_b"] <= 1.0, str(r))
_check("directed good > bad", r["reward_a"] > r["reward_b"], str(r))
_check("directed raw scores match",
       r["score_a"] == r["reward_a"] and r["score_b"] == r["reward_b"],
       f"score_a={r['score_a']} reward_a={r['reward_a']}")
print(f"  directed: reward_a={r['reward_a']:.3f} reward_b={r['reward_b']:.3f} "
      f"score_a={r['score_a']:.3f} score_b={r['score_b']:.3f}")

# 9. Directed: repeat request hits cache
status2, directed2 = _post("/v1/directed", {
    "tasks": [{"id": "task-1", "problem": PROBLEM}],
    "pairs": [{"task_id": "task-1", "a": GOOD, "b": BAD}],
    "criteria": CRITERIA,
    "n_reps": 1,
})
_check("directed repeat status", status2 == 200, str(directed2))
_check("directed cache hit", directed2.get("cached", False),
       f"cached={directed2.get('cached')}")
r2 = directed2["results"][0]
_check("directed repeat reward matches",
       r2["reward_a"] == r["reward_a"], f"first={r['reward_a']} repeat={r2['reward_a']}")
# Usage should not increase (cached hit)
status3, usage3 = _get("/v1/usage")
_check("directed cache no usage increase",
       usage3["backend_requests"] == usage2["backend_requests"],
       f"before={usage2['backend_requests']} after={usage3['backend_requests']}")
print(f"  directed cached={directed2.get('cached')}")

# 10. Directed: empty pairs → 422
try:
    _post("/v1/directed", {
        "tasks": [{"id": "t", "problem": PROBLEM}],
        "pairs": [],
    })
except urllib.error.HTTPError as exc:
    _check("directed empty pairs 422", exc.code == 422, f"got {exc.code}")
else:
    _check("directed empty pairs 422", False, "empty pairs was accepted")

# 10. Directed: empty tasks → 422
try:
    _post("/v1/directed", {
        "tasks": [],
        "pairs": [{"task_id": "t", "a": GOOD, "b": BAD}],
    })
except urllib.error.HTTPError as exc:
    _check("directed empty tasks 422", exc.code == 422, f"got {exc.code}")
else:
    _check("directed empty tasks 422", False, "empty tasks was accepted")

# 11. Image input: compare with data URI
PNG_1x1 = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAA"
           "fFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
status, img_cmp = _post("/v1/compare", {
    "problem": "What color is this image?",
    "trace_a": "Red",
    "trace_b": "Blue",
    "criteria": CRITERIA,
    "n_evaluations": 1,
    "images": [PNG_1x1],
})
_check("compare images status", status == 200, str(img_cmp))
_check("compare images scores in [0,1]",
       0.0 <= img_cmp["score_a"] <= 1.0 and 0.0 <= img_cmp["score_b"] <= 1.0,
       str(img_cmp))
print(f"  compare+images: good={img_cmp['score_a']:.3f} bad={img_cmp['score_b']:.3f} "
      f"accepted={img_cmp['accepted']}")

# 12. Image input: select with data URI
status, img_sel = _post("/v1/select", {
    "problem": "What color is this image?",
    "candidates": ["Red", "Blue"],
    "criteria": CRITERIA,
    "n_evaluations": 1,
    "pivots": 2,
    "images": [PNG_1x1],
})
_check("select images status", status == 200, str(img_sel))
_check("select images scores in [0,1]",
       all(0.0 <= s <= 1.0 for s in img_sel["scores"]), str(img_sel))
print(f"  select+images: index={img_sel['index']} scores={[round(s, 3) for s in img_sel['scores']]}")

# 13. Image input: validation rejects file paths
try:
    _post("/v1/compare", {
        "problem": "test",
        "trace_a": "a",
        "trace_b": "b",
        "images": ["/etc/passwd"],
    })
except urllib.error.HTTPError as exc:
    _check("compare images reject file path", exc.code == 422,
           f"got {exc.code}")
else:
    _check("compare images reject file path", False, "file path was accepted")

print("\nALL E2E CHECKS PASSED")