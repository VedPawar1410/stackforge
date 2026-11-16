"""Shared configuration loaded from .env."""

import os
from dotenv import load_dotenv

load_dotenv(override=True)

DATABASE_URL: str = os.environ["DATABASE_URL"]

GROQ_API_KEY: str = os.environ.get("GROQ_API_KEY", "")
OPENROUTER_API_KEY: str = os.environ.get("OPENROUTER_API_KEY", "")

# Set LLM_PROVIDER=groq or LLM_PROVIDER=openrouter in .env to switch providers
LLM_PROVIDER: str = os.environ.get("LLM_PROVIDER", "groq")

if LLM_PROVIDER == "openrouter":
    LLM_API_KEY: str = OPENROUTER_API_KEY
    LLM_BASE_URL: str = "https://openrouter.ai/api/v1"
    # Override via .env; defaults are free-tier NVIDIA Nemotron models — both
    # confirmed to support structured_outputs (response_format=json_object),
    # unlike Groq's gpt-oss models which intermittently truncated/malformed
    # JSON under the same setting. No per-model TPM cap on OpenRouter free
    # tier either (only a global 20 req/min + 50-or-1000 req/day account cap).
    MODEL_CHEAP: str = os.environ.get("OPENROUTER_MODEL_CHEAP", "nvidia/nemotron-nano-9b-v2:free")
    MODEL_CAPABLE: str = os.environ.get("OPENROUTER_MODEL_CAPABLE", "nvidia/nemotron-3-super-120b-a12b:free")
elif LLM_PROVIDER == "ollama":
    # Local dev/testing only — no rate limits, no cost, but NOT a stand-in for
    # any Anthropic pricing tier. Deliberately excluded from MODEL_TIER below
    # so stage4_metrics_calculator.py can flag these runs as non-representative
    # rather than silently reporting fake savings numbers.
    LLM_API_KEY = "ollama"  # dummy — Ollama's OpenAI-compat endpoint ignores auth
    LLM_BASE_URL = "http://localhost:11434/v1"
    MODEL_CHEAP: str = os.environ.get("OLLAMA_MODEL_CHEAP", "qwen2.5-coder:3b")
    MODEL_CAPABLE: str = os.environ.get("OLLAMA_MODEL_CAPABLE", "qwen2.5-coder:3b")
else:  # groq
    LLM_API_KEY = GROQ_API_KEY
    LLM_BASE_URL = "https://api.groq.com/openai/v1"
    MODEL_CHEAP   = "openai/gpt-oss-20b"    # replaces deprecated llama-3.1-8b-instant
    MODEL_CAPABLE = "openai/gpt-oss-120b"   # replaces deprecated llama-3.3-70b-versatile

# Reasoning-effort request shape differs by provider. Groq/OpenAI use a flat
# "reasoning_effort" string; OpenRouter's unified API requires a nested
# {"reasoning": {"effort": ...}} object and silently ignores the flat form
# (confirmed via OpenRouter docs) — no error, it just leaves the model to
# pick its own reasoning behavior, which can route the whole answer into a
# hidden reasoning field and leave message.content empty/None.
#
# On OpenRouter, "low" still wasn't low enough — NVIDIA Nemotron is a hybrid
# reasoning model that burns real tokens on internal reasoning even at "low"
# effort, eating into max_tokens before it writes the visible JSON (observed
# as frequent empty content / truncated "Unterminated string" JSON errors).
# These pipeline tasks are pure classification/extraction — no chain-of-
# thought needed — so effort="none" is used instead, which OpenRouter
# documents as fully disabling reasoning token spend.
if LLM_PROVIDER == "openrouter":
    REASONING_LOW: dict = {"reasoning": {"effort": "none"}}
    REASONING_MEDIUM: dict = {"reasoning": {"effort": "none"}}
elif LLM_PROVIDER == "ollama":
    # qwen2.5-coder is not a reasoning model — send nothing rather than risk
    # Ollama's OpenAI-compat layer choking on an unrecognized field.
    REASONING_LOW = {}
    REASONING_MEDIUM = {}
else:
    REASONING_LOW = {"reasoning_effort": "low"}
    REASONING_MEDIUM = {"reasoning_effort": "medium"}

# Per-call post-success throttle hold. Groq's gpt-oss models cap at a tight
# 8K TPM — each pipeline file hand-tunes its own hold (15-35s) around that
# ceiling and those values are left as-is below. OpenRouter's free tier has
# NO token-per-minute cap at all, only a flat 20 req/min account limit — a
# 15-35s hold there is pure overhead, and may be long enough to let a
# free/shared model instance go idle and cold-start on the next call (empty
# content on first attempt, succeeds on retry — observed repeatedly).
# LLM_THROTTLE_DELAY_S is None on Groq so every file keeps its existing
# hand-tuned constant; set to a real value only for OpenRouter, where one
# shared number is safe for every file since there's no TPM budget to size
# per max_tokens. 4s comfortably clears the 60/20=3s minimum RPM spacing.
LLM_THROTTLE_DELAY_S: float | None = 4.0 if LLM_PROVIDER == "openrouter" else None

# Chunking
CHUNK_TARGET_WORDS: int = 275  # target ~250-300 words per chunk
CHUNK_OVERLAP_WORDS: int = 35  # tail words from previous chunk prepended to next

# Extraction batching — summaries are sent to the extractor in groups of this
# size to avoid truncating large requirement sets at max_tokens=2048.
EXTRACTOR_BATCH_SIZE: int = 10

# Embedding model (local, 384-dim) — disabled on Python 3.14 (no wheels)
EMBED_MODEL: str = "all-MiniLM-L6-v2"

# Reranker model (local) — disabled on Python 3.14
RERANK_MODEL: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"

# RAG retrieval
BM25_TOP_K: int = 20
SEMANTIC_TOP_K: int = 20
RERANK_TOP_K: int = 5

# Pinecone vector search
# Set PINECONE_API_KEY in .env to enable semantic search alongside BM25.
# If not set, system falls back to BM25-only retrieval.
PINECONE_API_KEY: str = os.environ.get("PINECONE_API_KEY", "")
PINECONE_INDEX_NAME: str = os.environ.get("PINECONE_INDEX_NAME", "stackforge-poc")
PINECONE_EMBED_MODEL: str = "multilingual-e5-large"  # 1024-dim, available in Pinecone Inference
PINECONE_DIMENSION: int = 1024
PINECONE_CLOUD: str = os.environ.get("PINECONE_CLOUD", "aws")
PINECONE_REGION: str = os.environ.get("PINECONE_REGION", "us-east-1")
PINECONE_ENABLED: bool = bool(PINECONE_API_KEY)

# Google Stitch design generation (optional — required only for Stitch integration)
# Get your key at stitch.withgoogle.com → Settings → API Keys
STITCH_API_KEY: str = os.environ.get("STITCH_API_KEY", "")

# ── Stage 2: Azure DevOps ──────────────────────────────────────────────────────
# Set these in poc/.env to enable pushing epics and user stories to ADO.
# ADO_ORG:     your Azure DevOps organization name (e.g. "stackular")
# ADO_PROJECT: the project name inside that org (e.g. "StackForge")
# ADO_PAT:     Personal Access Token with Work Items (Read & Write) scope
ADO_ORG: str = os.environ.get("ADO_ORG", "")
ADO_PROJECT: str = os.environ.get("ADO_PROJECT", "")
ADO_PAT: str = os.environ.get("ADO_PAT", "")
ADO_API_VERSION: str = "7.1"

# Anthropic pricing reference (USD per 1 million tokens).
# Used by the metrics calculator to compare actual vs naive approach cost.
# These rates are used even when running on Groq — the calculator shows what
# the same calls would cost on the production Anthropic API.
PRICING: dict = {
    "haiku":  {"input": 0.25,  "output": 1.25},   # claude-haiku-4-5
    "sonnet": {"input": 3.00,  "output": 15.00},   # claude-sonnet-4-6
    "opus":   {"input": 15.00, "output": 75.00},   # claude-opus-4-8
}

# Map Groq/OpenRouter model names → Anthropic pricing tier for cost calculations
MODEL_TIER: dict = {
    # Groq (fallback provider — see LLM_PROVIDER in .env)
    "openai/gpt-oss-20b":   "haiku",    # cheap tier → haiku pricing
    "openai/gpt-oss-120b":  "sonnet",   # capable tier → sonnet pricing
    # OpenRouter (active provider)
    "nvidia/nemotron-nano-9b-v2:free":          "haiku",
    "nvidia/nemotron-3-super-120b-a12b:free":   "sonnet",
    # OpenRouter legacy defaults (kept in case .env still references them)
    "meta-llama/llama-3.1-8b-instruct:free": "haiku",
    "meta-llama/llama-3.3-70b-instruct":     "sonnet",
}

# ── Stage 3: Sprint & Task Planning ──────────────────────────────────────────
# Default sprint capacity in story points. Can be overridden per-project via
# the API (generate-sprints?sprint_capacity=N).
SPRINT_CAPACITY_DEFAULT: int = 20
SPRINT_DURATION_WEEKS: int = 2

# ── Stage 4: AI-Assisted Development ─────────────────────────────────────────
# Root directory for per-task workspaces. Each task gets an isolated
# subdirectory at {STAGE4_WORKSPACE_ROOT}/{task_id}/.
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STAGE4_WORKSPACE_ROOT: str = os.path.join(_BASE_DIR, "workspaces")

# Maximum concurrent Groq code-gen calls. Default 1 — Groq free tier caps at
# 8K TPM and each Stage 4 call uses ~2800 tokens, so Semaphore(1) + 30s hold
# keeps us at ~5600 TPM which is safely under the limit.
STAGE4_MAX_PARALLEL: int = int(os.environ.get("STAGE4_MAX_PARALLEL", "1"))

# Seconds to hold the semaphore slot after each Groq call completes. This
# spreads releases across time so concurrent waiters don't burst the TPM window.
# For Ollama (no rate limit) set STAGE4_THROTTLE_DELAY_S=0 in .env.
STAGE4_THROTTLE_DELAY_S: float = float(os.environ.get("STAGE4_THROTTLE_DELAY_S", "30.0"))

# Ollama's default context window (2048-4096) truncates Stage 4's
# issue_context.md prompt + generated-code response. Only read when
# LLM_PROVIDER == "ollama"; harmless to define otherwise.
OLLAMA_NUM_CTX: int = int(os.environ.get("OLLAMA_NUM_CTX", "8192"))

# SDLC topic taxonomy used for requirement classification
SDLC_TOPICS: list[str] = [
    "requirements",     # functional requirements, features, user needs, scope
    "design",           # UI/UX, wireframes, brand, components, flows, styling
    "technical",        # architecture, stack, APIs, DB, infra, security
    "timeline",         # phases, milestones, deadlines, dependencies
    "budget",           # costs, payment schedule, resource allocation
    "testing",          # test requirements, UAT, acceptance criteria, coverage
    "integrations",     # third-party services, ERP, payment gateways, external APIs
    "team_and_process", # roles, responsibilities, change management, communication
]
