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
from llm_verifier.fine_grained_reward import (
    score_pair_criterion,
    score_directed_pairs,
    directed_reward,
)

# ── Config ──────────────────────────────────────────────────────────
MODEL_ALIAS = os.environ.get("MODEL_ALIAS", "qwen3.5-9b")
VERIFIER_PORT = int(os.environ.get("VERIFIER_PORT", "8010"))
MIN_SCORE = float(os.environ.get("VERIFIER_MIN_SCORE", "0.8"))
BACKEND_TIMEOUT = int(os.environ.get("VERIFIER_BACKEND_TIMEOUT", "300"))
VERIFIER_CACHE_DIR = os.environ.get("VERIFIER_CACHE_DIR", "/app/cache")

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


class _LazyClient:
    """Wrapper with .get() for upstream APIs expecting a lazy client."""
    def get(self):
        return _VERIFIER_CLIENT

# ── Logging ────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("verifier")

# ── Helpers ─────────────────────────────────────────────────────────

def _validate_images(images: list[str] | None, path: str = "images"
                     ) -> list[str | bytes] | None:
    """Validate and convert image references.

    Accepts data URIs (decoded to ``bytes``) and HTTP(S) URLs (kept as
    ``str``).  Rejects file paths — the server never reads local files.

    Returns the converted list suitable for upstream ``load_image``.
    """
    if not images:
        return None
    out: list[str | bytes] = []
    for i, img in enumerate(images):
        if img.startswith("data:image/") and ";base64," in img:
            import base64
            b64 = img.split(";base64,", 1)[1]
            out.append(base64.b64decode(b64))
        elif img.startswith(("http://", "https://")):
            out.append(img)
        else:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"{path}[{i}]: only data URI (data:image/*;base64,...) "
                    f"and HTTP(S) URLs are allowed, got: {img[:60]!r}"
                ),
            )
    return out


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
    images: list[str] | None = None


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
    images: list[str] | None = None


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
    images: list[str] | None = None


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
        description="List of dicts, each with 'problem', 'trace_a', 'trace_b', "
                    "and optionally 'images'",
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


class UsageResponse(BaseModel):
    """Token usage accumulated since service start."""
    model: str
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int
    backend_requests: int
    formatted: str


class DirectedPair(BaseModel):
    """One directed (a, b) pair to score."""
    task_id: str
    a: str
    b: str
    images: list[str] | None = None


class DirectedRequest(BaseModel):
    """Score directed (task, a, b) pairs.

    tasks: list of {id, problem}
    pairs: list of {task_id, a, b} referencing task ids
    criteria: list of {id, name, description}
    ground_truth_note: optional ground truth
    n_reps: repeats (odd reps swap a/b slots to cancel slot bias)
    model: optional model override
    """
    tasks: list[dict] = Field(min_length=1)
    pairs: list[DirectedPair] = Field(min_length=1)
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


class DirectedResult(BaseModel):
    """One directed pair result with scores and rewards."""
    task_id: str
    a: str
    b: str
    score_a: float
    score_b: float
    reward_a: float
    reward_b: float


class DirectedResponse(BaseModel):
    """Results for all directed pairs."""
    results: list[DirectedResult]
    cached: bool
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
    images = _validate_images(req.images, "images")
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
            images=images,
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
    images = _validate_images(req.images, "images")
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
            images=images,
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
    images = _validate_images(req.images, "images")
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
                images=images,
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
        pair_images = _validate_images(pair.get("images"), f"pairs[{idx}].images")
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
                    images=pair_images,
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


# ── Usage endpoint ────────────────────────────────────────────────


@app.get("/v1/usage", response_model=UsageResponse)
async def v1_usage():
    """Return accumulated token usage since service start.

    No backend interaction — always succeeds (even when backend is down).
    Health check requests are NOT counted in usage.
    """
    usage = fgr.token_usage()
    return UsageResponse(
        model=MODEL_ALIAS,
        prompt_tokens=usage.get("input_tokens", 0),
        completion_tokens=usage.get("output_tokens", 0),
        cached_tokens=usage.get("cached_input_tokens", 0),
        backend_requests=usage.get("calls", 0),
        formatted="\n".join(fgr.format_usage(usage)),
    )


# ── Directed comparison endpoint ──────────────────────────────────


@app.post("/v1/directed", response_model=DirectedResponse)
async def v1_directed(req: DirectedRequest):
    """Score directed (task, a, b) pairs with cache and reward computation.

    Groups pairs by task into the upstream `needed_pairs` map, calls
    `score_directed_pairs` via `asyncio.to_thread`, then computes
    `directed_reward` per pair. Failed backend calls return 502.
    """
    model = req.model or MODEL_ALIAS

    # Build tasks dict: task_name -> {candidate_key -> {trace, problem, images}}
    # and needed_pairs: task_name -> [(candidate_a, candidate_b), ...]
    tasks_dict: dict[str, dict] = {}
    needed_pairs: dict[str, list[tuple[str, str]]] = {}
    task_problems: dict[str, str] = {t["id"]: t["problem"] for t in req.tasks}

    # Validate and convert images from tasks and pairs
    task_images_conv: dict[str, list[str | bytes] | None] = {
        t["id"]: _validate_images(t.get("images"), f"tasks[{t['id']}].images")
        for t in req.tasks
    }

    for p in req.pairs:
        tid = p.task_id
        if tid not in tasks_dict:
            tasks_dict[tid] = {}
            needed_pairs[tid] = []
        # Assign candidate names per pair using an incrementing key
        key_a = f"a_{len(tasks_dict[tid]) // 2}"
        key_b = f"b_{len(tasks_dict[tid]) // 2}"
        pair_images = _validate_images(p.images, f"pairs[{tid}].images")
        img = pair_images or task_images_conv.get(tid)
        tasks_dict[tid][key_a] = {
            "trace": p.a,
            "problem": task_problems.get(tid, ""),
            "images": img,
        }
        tasks_dict[tid][key_b] = {
            "trace": p.b,
            "problem": task_problems.get(tid, ""),
            "images": img,
        }
        needed_pairs[tid].append((key_a, key_b))

    cache_file = os.path.join(VERIFIER_CACHE_DIR, "directed_cache.json")

    # Check cache state before calling backend
    calls_before = fgr.token_usage().get("calls", 0)

    try:
        scores = await asyncio.to_thread(
            score_directed_pairs,
            lazy_client=_LazyClient(),
            tasks=tasks_dict,
            needed_pairs=needed_pairs,
            criteria=req.criteria,
            ground_truth_note=req.ground_truth_note,
            n_reps=req.n_reps,
            max_workers=1,
            cache_file=cache_file,
            model=model,
            progress=False,
            on_error="tie",
        )
    except Exception as exc:
        log.error("directed failed: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc))

    calls_after = fgr.token_usage().get("calls", 0)
    was_cached = calls_after == calls_before

    # Compute directed rewards per pair
    criteria_ids = [c["id"] for c in req.criteria]
    results = []
    pair_index = 0
    for tid in needed_pairs:
        for key_a, key_b in needed_pairs[tid]:
            p = req.pairs[pair_index]
            reward_a, reward_b = directed_reward(
                scores=scores,
                task_name=tid,
                a=key_a,
                b=key_b,
                criteria_ids=criteria_ids,
                n_reps=req.n_reps,
            )
            # Average raw scores across ALL criteria (not just the first one)
            all_raw_a, all_raw_b = [], []
            for cid in criteria_ids:
                entry = scores.get(fgr.cache_key(cid, tid, key_a, key_b, 0), {})
                all_raw_a.append(entry.get("score_A", 0.5))
                all_raw_b.append(entry.get("score_B", 0.5))
            raw_a = sum(all_raw_a) / len(all_raw_a) if all_raw_a else 0.5
            raw_b = sum(all_raw_b) / len(all_raw_b) if all_raw_b else 0.5
            results.append(DirectedResult(
                task_id=tid,
                a=p.a,
                b=p.b,
                score_a=raw_a,
                score_b=raw_b,
                reward_a=reward_a,
                reward_b=reward_b,
            ))
            pair_index += 1

    log.info(
        "directed: %d pairs scored cached=%s model=%s",
        len(results), was_cached, model,
    )
    return DirectedResponse(results=results, cached=was_cached, model=model)


# ── Entrypoint ─────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    log.info("Starting verifier HTTP service on port %d (model=%s)", VERIFIER_PORT, MODEL_ALIAS)
    uvicorn.run("server:app", host="0.0.0.0", port=VERIFIER_PORT, log_level="info")