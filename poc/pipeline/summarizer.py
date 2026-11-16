"""
Map-reduce summarization using the cheap model (llama-3.1-8b-instant).

Uses a semaphore to cap concurrent Groq requests at 2, plus a post-call hold —
identical throttle pattern to task_generator.py — to stay under the free-tier
rate limit when many chunks are processed in parallel. Includes retry with
exponential backoff on 429 rate-limit responses.

Doc-type-aware: spec docs (FRS/SOW) use extraction-preserving bullet mode
(max 15 bullets, 1000 tokens); transcripts/meeting notes use compact summary
mode (2-3 sentences, 300 tokens). Both modes preserve UI/design details.
"""

import asyncio
import json
import logging
import random
import re

from openai import BadRequestError, RateLimitError

from config import LLM_THROTTLE_DELAY_S, MODEL_CHEAP, REASONING_LOW
from pipeline.llm_utils import extract_usage, get_llm_client
from pipeline.metrics_common import UsageTotals

_client = get_llm_client()
_logger = logging.getLogger(__name__)

# Keywords in the filename that identify transcript/meeting docs.
# Everything else is treated as a spec document (FRS, SOW, requirements, etc.)
# — safer to extract more rather than less.
_TRANSCRIPT_KEYWORDS = re.compile(
    r"(transcript|meeting|notes|call|interview|discussion|minutes)",
    re.IGNORECASE,
)

# Groq dashboard confirms the real ceiling for gpt-oss-20b: 8K TPM (not just
# 30 req/min). Spec mode calls run ~1500-1800 tokens (prompt + up to 1000
# output) each. Semaphore(2) + 12s hold still averaged ~8-9 calls/min ≈
# 12-16K TPM — over budget, matches the observed 173%-consumed spike.
# Serialize to Semaphore(1) + 15s hold: ~3-3.5 calls/min * ~1800 tok ≈
# 5.4-6.3K TPM, ~70-80% of the 8K cap — same safety margin Stage 4 uses
# (see STAGE4_THROTTLE_DELAY_S in config.py) for the same underlying limit.
# LLM_THROTTLE_DELAY_S overrides this to a short value on OpenRouter (no TPM
# cap there — see config.py) since the 15s Groq-tuned hold may be long
# enough to let a free/shared model instance go idle and cold-start.
_SEMAPHORE_SIZE = 1
_THROTTLE_DELAY_S = LLM_THROTTLE_DELAY_S if LLM_THROTTLE_DELAY_S is not None else 15.0

_SPEC_SYSTEM_PROMPT = (
    "You are a requirements analyst. Extract ALL requirements, constraints, "
    "and design details from this document chunk as a bullet list. "
    "Include exact numbers, time limits, field names, UI/screen/branding details, "
    "measurable thresholds, and any named system components. "
    "Do not summarise or paraphrase — preserve specific values verbatim. "
    "Return ONLY valid JSON matching this schema:\n"
    '{"key_points": ["string"]}\n'
    "Max 15 bullets."
)

_TRANSCRIPT_SYSTEM_PROMPT = (
    "You are a requirements analyst assistant. Summarise the provided document "
    "chunk in 2-3 concise sentences, preserving all functional requirements, "
    "constraints, and key decisions. Include any UI, screen, design, or branding "
    "details mentioned. Then list up to 5 key points as short phrases. "
    "Return ONLY valid JSON matching this schema:\n"
    '{"summary": "string", "key_points": ["string"]}'
)


def _detect_doc_mode(doc_name: str) -> str:
    """Return 'transcript' or 'spec' based on the document filename."""
    if _TRANSCRIPT_KEYWORDS.search(doc_name):
        return "transcript"
    return "spec"


async def _summarize_with_retry(
    raw_text: str,
    semaphore: asyncio.Semaphore,
    mode: str = "spec",
    retries: int = 6,
) -> tuple[str, int, int, int]:
    """Call the cheap model with exponential backoff on rate-limit errors.

    Returns (summary_text, input_tokens, output_tokens, thinking_tokens) so the
    caller can aggregate token usage for the metrics report.
    """
    if mode == "transcript":
        system_prompt = _TRANSCRIPT_SYSTEM_PROMPT
        max_tokens = 300
    else:
        system_prompt = _SPEC_SYSTEM_PROMPT
        max_tokens = 1000

    for attempt in range(retries):
        # The semaphore wraps the ENTIRE attempt — call plus any backoff
        # sleep on failure — not just the network call. If the backoff sleep
        # sat outside this block, a failed attempt would release the slot
        # immediately and the next queued chunk would fire its own request
        # right away, defeating Semaphore(1): N chunks would round-robin
        # failed requests against Groq with near-zero spacing between them,
        # looking identical to unthrottled concurrency even with size 1.
        async with semaphore:
            response = None
            try:
                response = await _client.chat.completions.create(
                    model=MODEL_CHEAP,
                    max_tokens=max_tokens,
                    response_format={"type": "json_object"},
                    extra_body=REASONING_LOW,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": raw_text},
                    ],
                )
                # Hold the slot after the call completes so releases spread
                # out over time instead of every waiter firing at once.
                await asyncio.sleep(_THROTTLE_DELAY_S)
                in_tok, out_tok, think_tok = extract_usage(response)
                # content can be None (model routed everything into a hidden
                # reasoning field, or truncated with zero visible output) —
                # json.loads(None) raises TypeError, not JSONDecodeError, so
                # both must be caught to treat this as a retryable failure
                # instead of an unhandled crash that kills the whole pipeline.
                data = json.loads(response.choices[0].message.content)

                if mode == "spec":
                    key_points = data.get("key_points", [])
                    text = "; ".join(key_points) if key_points else ""
                else:
                    summary = data.get("summary", "")
                    key_points = data.get("key_points", [])
                    if key_points:
                        text = f"{summary} Key points: {'; '.join(key_points)}"
                    else:
                        text = summary
                return text, in_tok, out_tok, think_tok

            except RateLimitError:
                if attempt == retries - 1:
                    raise
                # Longer backoff — Groq's per-minute window needs real time
                # to free up capacity, not a fast retry. Still inside the
                # semaphore, so no other chunk can sneak a request in here.
                wait = min(60.0, (2 ** attempt) * 5) + random.uniform(0, 2)
                await asyncio.sleep(wait)
            except BadRequestError as exc:
                # gpt-oss reasoning models occasionally emit non-JSON (or get
                # truncated mid-JSON by max_tokens) — Groq rejects it server-side
                # with a 400 before any content is even parseable. Retry same as
                # rate-limit; a fresh sample usually produces valid JSON.
                if attempt == retries - 1:
                    raise
                _logger.warning(
                    "Chunk summarization: Groq rejected model output as invalid JSON "
                    "(attempt %d/%d): %s", attempt + 1, retries, exc,
                )
                wait = (2 ** attempt) + random.uniform(0, 1)
                await asyncio.sleep(wait)
            except (TypeError, json.JSONDecodeError) as exc:
                # 200 OK but content is None/malformed — same retryable class
                # as BadRequestError above, just a provider that returns 200
                # instead of 400 for an empty/unparseable completion.
                if attempt == retries - 1:
                    raise
                # Diagnostic dump — finish_reason tells us WHY content was
                # empty (length = truncated by max_tokens, content_filter =
                # refused, etc.) instead of us guessing from symptoms alone.
                choice = response.choices[0] if response and response.choices else None
                finish_reason = getattr(choice, "finish_reason", "<no response>")
                reasoning_field = getattr(choice.message, "reasoning", None) if choice else None
                _logger.warning(
                    "Chunk summarization: empty or malformed content "
                    "(attempt %d/%d): %s | finish_reason=%s | reasoning_present=%s",
                    attempt + 1, retries, exc, finish_reason, bool(reasoning_field),
                )
                wait = (2 ** attempt) + random.uniform(0, 1)
                await asyncio.sleep(wait)

    raise RuntimeError("summarize_chunk: all retries exhausted without a result")


# Shared across ALL concurrent summarize_all() calls so the semaphore cap is
# a true global rate-limit gate, not per-document. Safe in Python 3.10+ because
# Semaphore binds to the running event loop on first await, not at construction.
_semaphore = asyncio.Semaphore(_SEMAPHORE_SIZE)


async def summarize_all(chunks: list[str], doc_name: str = "") -> tuple[list[str], UsageTotals]:
    """Summarise all chunks with rate-limit-safe concurrency.

    doc_name is used to detect whether this is a spec document (FRS/SOW)
    or a transcript/meeting note, selecting the appropriate extraction mode.

    Returns (summaries, usage) where usage aggregates token counts across every
    chunk call — one call per chunk — for the Stage 1 metrics report.
    """
    mode = _detect_doc_mode(doc_name)
    _logger.info("Summarizer mode: %s (doc: %s)", mode, doc_name or "<unknown>")
    tasks = [_summarize_with_retry(c, _semaphore, mode=mode) for c in chunks]
    results = await asyncio.gather(*tasks)

    summaries: list[str] = []
    usage = UsageTotals()
    for text, in_tok, out_tok, think_tok in results:
        summaries.append(text)
        usage = usage.add(in_tok, out_tok, think_tok)
    return summaries, usage
