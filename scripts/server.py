#!/usr/bin/env python3
"""LLM-as-a-Verifier HTTP service.

StateMachineMcp calls POST /v1/compare to verify patch quality before applying.

Endpoints:
  GET  /health       — Health check
  POST /v1/compare   — Compare two traces (original vs patched)
  POST /v1/select    — Select best from N candidates
"""

import asyncio
import logging
import os

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from llm_verifier import compare, select

# ── Config ──────────────────────────────────────────────────────────
MODEL_ALIAS = os.environ.get("MODEL_ALIAS", "qwen3.5-9b")
VERIFIER_PORT = int(os.environ.get("VERIFIER_PORT", "8010"))
MIN_SCORE = float(os.environ.get("VERIFIER_MIN_SCORE", "0.8"))

# ── Logging ────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("verifier")

# ── Request/Response schemas ───────────────────────────────────────

class CompareRequest(BaseModel):
    problem: str
    trace_a: str
    trace_b: str
    criteria: list[dict] = Field(
        default=[
            {
                "id": "correctness",
                "name": "Correctness",
                "description": "Does the output correctly solve the task?",
            }
        ]
    )
    model: str | None = None
    n_evaluations: int = 1


class CompareResponse(BaseModel):
    score_a: float
    score_b: float
    accepted: bool
    model: str


class SelectRequest(BaseModel):
    problem: str
    candidates: list[str]
    criteria: list[dict] = Field(
        default=[
            {
                "id": "correctness",
                "name": "Correctness",
                "description": "Does the output correctly solve the task?",
            }
        ]
    )
    model: str | None = None
    n_evaluations: int = 1
    pivots: int = 2


class SelectResponse(BaseModel):
    index: int
    scores: list[float]
    n_comparisons: int
    model: str


class HealthResponse(BaseModel):
    status: str
    model: str
    backend: str | None = None


# ── FastAPI app ────────────────────────────────────────────────────

app = FastAPI(title="LLM-as-a-Verifier", version="0.1.0")


@app.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse(
        status="ok",
        model=MODEL_ALIAS,
        backend=os.environ.get("OPENAI_BASE_URL"),
    )


@app.post("/v1/compare", response_model=CompareResponse)
async def v1_compare(req: CompareRequest):
    model = req.model or MODEL_ALIAS
    try:
        score_a, score_b = await asyncio.to_thread(
            compare,
            problem=req.problem,
            trace_a=req.trace_a,
            trace_b=req.trace_b,
            criteria=req.criteria,
            n_evaluations=req.n_evaluations,
            max_workers=1,
            model=model,
        )
    except Exception as exc:
        log.error("compare failed: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc))

    accepted = score_b >= MIN_SCORE
    log.info(
        "compare: score_a=%.3f score_b=%.3f accepted=%s model=%s",
        score_a, score_b, accepted, model,
    )
    return CompareResponse(
        score_a=score_a, score_b=score_b, accepted=accepted, model=model,
    )


@app.post("/v1/select", response_model=SelectResponse)
async def v1_select(req: SelectRequest):
    model = req.model or MODEL_ALIAS
    try:
        result = await asyncio.to_thread(
            select,
            problem=req.problem,
            candidates=req.candidates,
            criteria=req.criteria,
            n_evaluations=req.n_evaluations,
            pivots=req.pivots,
            max_workers=1,
            model=model,
        )
    except Exception as exc:
        log.error("select failed: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc))

    log.info(
        "select: index=%d scores=%s n_comparisons=%d model=%s",
        result.index, result.scores, result.n_comparisons, model,
    )
    return SelectResponse(
        index=result.index,
        scores=result.scores,
        n_comparisons=result.n_comparisons,
        model=model,
    )


# ── Entrypoint ─────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    log.info("Starting verifier HTTP service on port %d (model=%s)", VERIFIER_PORT, MODEL_ALIAS)
    uvicorn.run("server:app", host="0.0.0.0", port=VERIFIER_PORT, log_level="info")