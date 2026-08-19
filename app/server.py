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
from openai import OpenAI
from pydantic import BaseModel, Field

import llm_verifier.fine_grained_reward as fgr
from llm_verifier import compare, select, track
from llm_verifier.fine_grained_reward import score_pair_criterion

# ── Config ──────────────────────────────────────────────────────────
MODEL_ALIAS = os.environ.get("MODEL_ALIAS", "qwen3.5-9b")
VERIFIER_PORT = int(os.environ.get("VERIFIER_PORT", "8010"))
MIN_SCORE = float(os.environ.get("VERIFIER_MIN_SCORE", "0.8"))
BACKEND_TIMEOUT = int(os.environ.get("VERIFIER_BACKEND_TIMEOUT", "300"))

# ── Monkey-patch call_openai for llama.cpp compatibility ────────────
# llama.cpp doesn't support vLLM/SGLang-specific extra_body params:
#   - chat_template_kwargs (enable_thinking)
#   - continue_final_message + structured_outputs (prefill trick)
# These requests hang forever. We patch call_openai to:
#   1. Skip the extra_body on first call (no enable_thinking)
#   2. Preserve original tokens/position_logprobs when prefill fails

_original_call_openai = fgr.call_openai

def _patched_call_openai(client, prompt, model=fgr.DEFAULT_MODEL, top_logprobs=20, images=None):
    """Patched call_openai: skip vLLM-only extra_body, preserve original logprobs."""
    content = prompt
    imgs = fgr.as_image_list(images) if images else []
    if imgs:
        content = [{"type": "text", "text": prompt}]
        for img in imgs:
            data, mime = fgr.load_image(img)
            b64 = __import__("base64").b64encode(data).decode("ascii")
            content.append({"type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{b64}"}})
    params = dict(
        model=model,
        messages=[{"role": "user", "content": content}],
        max_tokens=4096,
        temperature=1.0,
        logprobs=True,
        top_logprobs=top_logprobs,
    )
    # Skip the vLLM-only extra_body — llama.cpp hangs on it.
    response = client.chat.completions.create(**params)
    fgr.USAGE.record(response)

    choice = response.choices[0]
    text = choice.message.content or ""
    tokens = None
    position_logprobs = None

    if choice.logprobs and choice.logprobs.content:
        tokens, position_logprobs = [], []
        for pos in choice.logprobs.content:
            tokens.append(pos.token)
            alts = [(alt.token, alt.logprob)
                    for alt in (pos.top_logprobs or [])]
            if not alts:
                alts = [(pos.token, pos.logprob)]
            position_logprobs.append(alts)

    # Save originals — prefill may overwrite with None on failure.
    orig_tokens = tokens
    orig_position_logprobs = position_logprobs

    # Try prefill trick for score tags, but preserve originals on failure.
    tags = [t for t in ("<score_A>", "<score_B>") if t in prompt]
    if tags and not getattr(client, "_llm_verifier_deepseek", False):
        idx = min([text.find(t) for t in tags if t in (text or "")]
                  or [len(text or "")])
        analysis = (text or "")[:idx].rstrip()
        text, tokens, position_logprobs = fgr._score_tags_by_prefill(
            client, params["model"], params["messages"], analysis, tags,
            top_logprobs)
        # If prefill failed (returned None logprobs), restore originals.
        if tokens is None or position_logprobs is None:
            tokens = orig_tokens
            position_logprobs = orig_position_logprobs

    return text, tokens, position_logprobs

fgr.call_openai = _patched_call_openai

# ── Create a client with timeout ────────────────────────────────────
def _create_verifier_client():
    """Create OpenAI client with timeout to prevent hanging on unsupported params."""
    from dotenv import load_dotenv
    load_dotenv()
    base_url = os.environ.get("OPENAI_BASE_URL")
    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("DEEPSEEK_API_KEY", "EMPTY")
    return OpenAI(base_url=base_url, api_key=api_key, timeout=BACKEND_TIMEOUT, max_retries=0)

_VERIFIER_CLIENT = _create_verifier_client()

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


class TrackRequest(BaseModel):
    problem: str
    steps: list[str] = Field(min_length=1)
    checkpoint_steps: list[int] | None = None
    n_evaluations: int = 1
    model: str | None = None


class TrackResponse(BaseModel):
    steps: list[int]
    scores: list[float]
    per_rep_scores: list[list[float | None]]
    model: str


class ScorePairsRequest(BaseModel):
    """Score multiple (A, B) comparisons in one batch.

    Each pair is scored independently and averaged over `n_reps` reps.
    Odd reps swap the prompt slots to cancel slot bias.
    """
    pairs: list[dict] = Field(
        description="List of dicts, each with 'problem', 'trace_a', 'trace_b'",
    )
    criteria: list[dict] = Field(
        default=[
            {
                "id": "correctness",
                "name": "Correctness",
                "description": "Does the output correctly solve the task?",
            }
        ]
    )
    ground_truth_note: str = (
        "The correct answer is a function that returns the integer 42."
    )
    n_reps: int = 2
    model: str | None = None


class ScorePairsItem(BaseModel):
    score_a: float
    score_b: float


class ScorePairsResponse(BaseModel):
    scores: list[ScorePairsItem]
    model: str


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
            client=_VERIFIER_CLIENT,
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
            client=_VERIFIER_CLIENT,
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


@app.post("/v1/track", response_model=TrackResponse)
async def v1_track(req: TrackRequest):
    model = req.model or MODEL_ALIAS
    result = None
    for attempt in range(1, 4):
        try:
            result = await asyncio.to_thread(
                track,
                problem=req.problem,
                steps=req.steps,
                checkpoint_steps=req.checkpoint_steps,
                n_evaluations=req.n_evaluations,
                max_workers=1,
                model=model,
                client=_VERIFIER_CLIENT,
            )
        except Exception as exc:
            log.error("track failed: %s", exc)
            raise HTTPException(status_code=502, detail=str(exc))
        # The model sometimes fails to emit the <c{i}>LETTER</c{i}> answer
        # format (reasoning mode returns empty content). Retry instead of
        # silently returning the 0.5 fallback.
        if result.per_rep_scores and all(
                all(s is None for s in rep) for rep in result.per_rep_scores):
            log.warning("track extraction failed (attempt %d/3), retrying", attempt)
            continue
        break
    else:
        raise HTTPException(
            status_code=502,
            detail="track: verifier returned no parseable scores after 3 attempts",
        )

    log.info(
        "track: steps=%s scores=%s model=%s",
        result.steps, result.scores, model,
    )
    return TrackResponse(
        steps=result.steps,
        scores=result.scores,
        per_rep_scores=result.per_rep_scores,
        model=model,
    )


@app.post("/v1/score-pairs", response_model=ScorePairsResponse)
async def v1_score_pairs(req: ScorePairsRequest):
    """Score multiple (A, B) comparisons in one batch.

    Each pair is scored independently via `score_pair_criterion` and
    averaged over `n_reps` reps. Odd reps swap the prompt slots to
    cancel slot bias.
    """
    model = req.model or MODEL_ALIAS
    scores: list[ScorePairsItem] = []

    for idx, pair in enumerate(req.pairs):
        problem = pair["problem"]
        trace_a = pair["trace_a"]
        trace_b = pair["trace_b"]
        rep_scores_a, rep_scores_b = [], []

        for rep in range(req.n_reps):
            swap = rep % 2 == 1
            ta, tb = (trace_b, trace_a) if swap else (trace_a, trace_b)
            try:
                sa, sb = await asyncio.to_thread(
                    score_pair_criterion,
                    client=_VERIFIER_CLIENT,
                    problem=problem,
                    trace_a=ta,
                    trace_b=tb,
                    criterion=req.criteria[0],
                    ground_truth_note=req.ground_truth_note,
                    model=model,
                )
            except Exception as exc:
                log.error("score_pairs[%d] rep %d failed: %s", idx, rep, exc)
                sa, sb = 0.5, 0.5  # tie on error

            if swap:
                sa, sb = sb, sa
            rep_scores_a.append(sa)
            rep_scores_b.append(sb)

        avg_a = sum(rep_scores_a) / len(rep_scores_a) if rep_scores_a else 0.5
        avg_b = sum(rep_scores_b) / len(rep_scores_b) if rep_scores_b else 0.5
        scores.append(ScorePairsItem(score_a=avg_a, score_b=avg_b))

    log.info(
        "score_pairs: %d pairs scored model=%s", len(scores), model,
    )
    return ScorePairsResponse(scores=scores, model=model)


# ── Entrypoint ─────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    log.info("Starting verifier HTTP service on port %d (model=%s)", VERIFIER_PORT, MODEL_ALIAS)
    uvicorn.run("server:app", host="0.0.0.0", port=VERIFIER_PORT, log_level="info")