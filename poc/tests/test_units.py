"""Unit tests for pure pipeline logic. No database, no network."""

import os
import subprocess
import sys

import pytest
from fastapi import HTTPException

from config import CHUNK_OVERLAP_WORDS, CHUNK_TARGET_WORDS, MODEL_TIER, PRICING
from api.routes import validate_project_id
from pipeline.chunker import chunk
from pipeline.metrics_common import UsageTotals, opus_reprice, step_cost, tier_for_model
from pipeline.parser import parse
from pipeline.runner import _jaccard
from pipeline.utils import text_array_literal

POC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── Chunker ──────────────────────────────────────────────────────────────────

def _paragraphs(n: int, words_each: int = 60) -> str:
    return "\n\n".join(" ".join(f"p{i}w{j}" for j in range(words_each)) for i in range(n))


def test_short_text_is_one_chunk_without_overlap_marker():
    chunks = chunk("A single short paragraph about appointment booking.")
    assert len(chunks) == 1
    assert not chunks[0].startswith("[...]")


def test_long_text_splits_near_target_size():
    chunks = chunk(_paragraphs(20))  # 1,200 words
    assert len(chunks) > 1
    for c in chunks:
        core = c.split("\n\n", 1)[1] if c.startswith("[...]") else c
        assert len(core.split()) <= CHUNK_TARGET_WORDS * 1.2


def test_chunks_carry_tail_overlap_from_previous_chunk():
    chunks = chunk(_paragraphs(20))
    prev_core = chunks[0].split()
    expected_tail = " ".join(prev_core[-CHUNK_OVERLAP_WORDS:])
    assert chunks[1].startswith(f"[...] {expected_tail}")


def test_chunking_keeps_every_word():
    text = _paragraphs(20)
    cores = [c.split("\n\n", 1)[1] if c.startswith("[...]") else c for c in chunk(text)]
    assert " ".join(cores).split() == text.split()


# ── Parser ───────────────────────────────────────────────────────────────────

def test_parse_txt_returns_text():
    assert "booking" in parse(b"Patients need online booking.\n\nSMS reminders.", "txt")


# ── Dedup similarity ─────────────────────────────────────────────────────────

def test_jaccard_identical_and_disjoint():
    assert _jaccard(["book", "appointment"], ["book", "appointment"]) == 1.0
    assert _jaccard(["book"], ["invoice"]) == 0.0


def test_jaccard_partial_overlap():
    assert _jaccard(["a", "b", "c"], ["b", "c", "d"]) == pytest.approx(0.5)


# ── pg8000 TEXT[] literal ────────────────────────────────────────────────────

def test_text_array_literal_escapes_quotes_backslashes_newlines():
    assert text_array_literal([]) == "{}"
    assert text_array_literal(['say "hi"', "a\\b", "line1\nline2"]) == (
        '{"say \\"hi\\"","a\\\\b","line1 line2"}'
    )


# ── Cost math ────────────────────────────────────────────────────────────────

def test_step_cost_uses_per_million_rates():
    assert step_cost(1_000_000, 0, "sonnet") == pytest.approx(PRICING["sonnet"]["input"])
    assert step_cost(0, 1_000_000, "haiku") == pytest.approx(PRICING["haiku"]["output"])


def test_opus_reprice_is_same_tokens_at_opus_rates():
    assert opus_reprice(2_000, 500) == pytest.approx(step_cost(2_000, 500, "opus"))


def test_unknown_model_defaults_to_sonnet_tier():
    assert tier_for_model("some-unknown-model") == "sonnet"


def test_ollama_models_are_not_priced_as_anthropic_tiers():
    # Local runs must not produce fake "savings" numbers.
    assert "qwen2.5-coder:3b" not in MODEL_TIER


def test_usage_totals_add_is_immutable():
    empty = UsageTotals()
    one = empty.add(100, 20, 0)
    assert empty.calls == 0
    assert (one.input_tokens, one.output_tokens, one.calls, one.total_tokens) == (100, 20, 1, 120)


# ── Path traversal guard ─────────────────────────────────────────────────────

def test_validate_project_id_rejects_paths():
    with pytest.raises(HTTPException) as err:
        validate_project_id("../../etc/passwd")
    assert err.value.status_code == 400


def test_validate_project_id_accepts_uuid():
    validate_project_id("8f14e45f-ceea-467f-a8a8-3f3b6b1b2c3d")


# ── Provider config (regression: every provider used to send the Groq key) ───

@pytest.mark.parametrize(
    "provider, expected_url, expected_key",
    [
        ("groq", "api.groq.com", "g-key"),
        ("openrouter", "openrouter.ai", "o-key"),
        ("ollama", "localhost:11434", "ollama"),
    ],
)
def test_each_provider_gets_its_own_url_and_key(provider, expected_url, expected_key):
    env = {**os.environ, "LLM_PROVIDER": provider, "GROQ_API_KEY": "g-key", "OPENROUTER_API_KEY": "o-key"}
    out = subprocess.run(
        [sys.executable, "-c",
         "from pipeline.llm_utils import get_llm_client; c = get_llm_client(); print(c.base_url, c.api_key)"],
        cwd=POC_DIR, env=env, capture_output=True, text=True, check=True,
    ).stdout
    assert expected_url in out
    assert out.strip().endswith(expected_key)
