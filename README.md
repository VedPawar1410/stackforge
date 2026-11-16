# StackForge POC: client documents to sprint-ready work, on a token budget

An AI pipeline that reads a client's raw documents (SOW, BRD, technical spec, call
transcript, emails), extracts structured requirements, turns them into epics, user
stories, sprints and tasks, and drafts code for the first sprint. Every stage is
built around one research question: **how cheaply can each step run without losing
quality?**

**Live:** not deployed. It runs locally by design (bring your own LLM key and
Postgres). A hosted demo comes with the successor product (see [Successor](#successor)).

| Headline (MediBook sample docs, Groq free tier, Nov 2026) | |
|---|---|
| Requirements a correct extraction must contain, found | **43 / 46 (93.5%)**, up from 25 / 46 (54.3%) |
| Full run, Stages 1-4 | 63 LLM calls, 126k tokens, ~25 min |
| Same tokens priced at Anthropic rates | **$0.48** routed vs $1.20 all-Opus (**60% less**) |
| Output | 110 requirements, 6 epics, 30 stories, 9 sprints, 142 tasks, 25 code files |
| Tests | 26 (pytest, LLM faked) |

Source: [`poc/reports/`](poc/reports/README.md). How to read these numbers honestly is
spelled out there and under [Limitations](#limitations).

---

## How it works

```
 client docs (.pdf .docx .txt)
        │
        ▼
 STAGE 1  Ingestion ───────────────────────────────────────────────────────────
   parse (tables included) → chunk ~275 words, 35-word overlap
   → summarise each chunk ............ cheap model, in parallel
   → extract requirements (JSON) ..... capable model, from summaries only
   → dedup across docs (Jaccard) ..... no LLM
   → BM25 (+ optional Pinecone) index, clarification questions (cheap model)
   → 8 SDLC topic docs (requirements.md, design.md, ...) ..... no LLM
        │
        ▼
 STAGE 2  Epics & stories ─────────────────────────────────────────────────────
   requirement titles only → epics ... cheap model
   per epic: scoped requirements + project-wide constraints → stories (capable model)
   → optional push to Azure DevOps (Epic / User Story)
        │
        ▼
 STAGE 3  Sprints & tasks ─────────────────────────────────────────────────────
   bin-pack stories into sprints ..... rule-based, no LLM
   per story: story + 3 similar past items (BM25) → 3-5 tasks (capable model)
   → optional push to Azure DevOps (Iterations / Tasks)
        │
        ▼
 STAGE 4  Code drafts ─────────────────────────────────────────────────────────
   per task: isolated workspace + issue_context.md → files as JSON (capable model)
        │
        ▼
 every call logs input / output / reasoning tokens per model → metrics per stage
```

FastAPI serves the pipeline (36 endpoints, background jobs), Streamlit is the 8-tab
UI, Postgres stores everything.

### Why these choices

- **Route by task, not by habit.** Classification, summarising and grouping go to the
  cheap model; generation goes to the capable one; nothing needs the premium tier.
  This is where the measured 60% saving comes from.
- **Summaries, not documents.** The capable model never sees raw text, only chunk
  summaries. It keeps each call small enough for cheap models and free-tier limits.
- **Scope the context per call.** Epics see titles only; each story call sees one
  epic's requirements; each task call sees one story. No call gets the whole backlog.
- **No LLM where rules work.** Dedup, sprint packing and doc writing are plain code.
- **Strict JSON with a `max_tokens` cap** on every generation call, so models fill
  fields instead of narrating.
- **One provider switch.** `LLM_PROVIDER` = `groq` | `gemini` | `openrouter` | `ollama`,
  all through the OpenAI-compatible SDK. Moving to Anthropic is a config change.
- **Measure, don't assume.** Token usage is stored per call, and a 46-item checklist
  scores extraction quality, so a change can be judged on cost and quality together.

## Data

[`sample_client_docs/`](sample_client_docs/): five documents written for this project
for a fictional client, Apex Health Group, commissioning "MediBook", a patient booking
and clinic management platform for five clinics.

| Document | Words parsed | Notes |
|---|---|---|
| BRD (.docx) | 1,250 | 30 numbered functional requirements, NFRs, open questions, mostly in tables |
| SOW (.docx) | 735 | scope, timeline, commercials |
| Technical spec (.pdf) | 1,030 | architecture, integrations, security, performance |
| Discovery call transcript (.txt) | 1,186 | how the client talks about the problem |
| Client email (.txt) | 615 | late changes, including one that contradicts the SOW (referral tracking) |

The checklist in [`poc/evals/medibook_checklist.json`](poc/evals/medibook_checklist.json)
was built by reading these documents: the BRD's 30 requirements plus 16 items from the
other four (Telugu support, <3 s dashboard, Razorpay, 40,000-record migration, offline
mode...).

## Results

### Before vs after this rebuild

| | June 2026 POC | Nov 2026 rebuild |
|---|---|---|
| Fresh install following the README | fails (`rag_chunks` never created) | works (`docker compose up`, one migration command) |
| BRD words reaching the model | 300 (tables dropped) | 1,250 |
| Checklist coverage | 54.3% (25/46) | **93.5% (43/46)** |
| Stage 2 at 110 requirements | fails (empty JSON) | 6 epics, 30 stories |
| Cost claim | "87% savings" on an outdated price table | 60% at current prices |
| Upload API | returns an id that doesn't exist | returns the stored document |
| Retry after a failed upload | duplicate rows, project stuck "failed" | same row reused, project recovers |
| Providers | every provider sent the Groq key | Groq, Gemini, OpenRouter, Ollama each correct |
| Dependencies | unpinned (`openai` drifted 1.56 → 3.51) | pinned, Python 3.12 |
| Tests | 0 | 26 |
| Public repo | Stage 2 only; Stages 3-4 on a local branch | Stages 1-4 |

### Full run, per stage (fixed parser)

| Stage | Output | Tokens in / out | Routed | All Opus |
|---|---|---|---|---|
| 1 Ingestion | 110 requirements, 7 clarifications | 29,649 / 14,350 | $0.097 | $0.406 |
| 2 Epics & stories | 6 epics, 30 stories | 20,652 / 6,850 | $0.098 | $0.220 |
| 3 Sprints & tasks | 9 sprints, 142 tasks | 26,888 / 9,310 | $0.147 | $0.294 |
| 4 Code (sprint 1) | 10/10 tasks, 25 files, all 13 `.py` files parse | 6,014 / 12,674 | $0.139 | $0.278 |
| **Total** | | **83,203 / 43,184** | **$0.48** | **$1.20** |

Priced at Haiku 5.5 / Sonnet 5.5 / Opus 5.5 rates ($0.10/$0.50, $2/$10, $4/$20 per
million tokens). The run itself was free.

## Project structure

```
sample_client_docs/          MediBook sample documents (the demo input)
architecture.md              full 6-stage target architecture (research)
CLAUDE.md                    context file for AI coding sessions on this repo
docs/                        POC architecture write-ups; docs/codebase/ = generated snapshots
Exiladraw/                   Excalidraw architecture and cost-comparison diagrams
poc/
├── config.py                every setting: provider switch, models, chunking, pricing
├── db.py                    pg8000 connection helper
├── docker-compose.yml       Postgres 16 + pgvector
├── requirements*.txt        pinned runtime / dev dependencies
├── run_demo.py              older Stage 1 terminal demo (see Limitations)
├── api/                     FastAPI app, Pydantic models, routes per stage
├── pipeline/
│   ├── parser.py chunker.py summarizer.py extractor.py clarifier.py embedder.py
│   ├── runner.py                    Stage 1 orchestration, cross-doc dedup
│   ├── doc_writer.py doc_editor.py  SDLC topic markdown files (no LLM)
│   ├── epic_generator.py story_generator.py stage2_runner.py ado_client.py
│   ├── sprint_planner.py task_generator.py stage3_runner.py
│   ├── workspace_builder.py workspace_prep.py stage4_runner.py
│   ├── stitch_designer.py           optional Google Stitch UI screens
│   ├── llm_utils.py                 client factory, retry, usage extraction
│   └── *metrics_calculator.py metrics_common.py metrics_terminal.py
├── rag/                     BM25 + optional Pinecone search, re-ranker
├── db/                      schema (init.sql + stage SQL), migrate_all.py, seed.py
├── ui/app.py                Streamlit UI, 8 tabs
├── evals/                   checklist, scorer, run_medibook.py, build_report.py
├── reports/                 measured run reports (JSON + README)
├── tests/                   unit tests + API tests with a fake LLM
└── scripts/                 ADO bulk delete, Stitch debug helper
```

## Run locally

Needs Docker, Python 3.12, and a free [Groq](https://console.groq.com) key (the
verified provider; see Limitations for Gemini).

```bash
cd poc
cp .env.example .env          # add GROQ_API_KEY
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
docker compose up -d          # Postgres + pgvector on :5432
python db/migrate_all.py      # full schema, safe to re-run
```

Already running Postgres on 5432? Use `STACKFORGE_DB_PORT=5433 docker compose up -d`
and put `5433` in `DATABASE_URL`.

Getting "invalid API key" with a key that's correct in `.env`? Variables exported in
your shell (e.g. an old `GEMINI_API_KEY` in `~/.zshrc`) take precedence over `.env`.
Remove the export or `unset` it.

Then, in two terminals:

```bash
python -m uvicorn api.main:app --reload       # API on :8000
python -m streamlit run ui/app.py             # UI on :8501
```

Reproduce the measured run (creates a new project; ~25 min on Groq's free tier):

```bash
python evals/run_medibook.py --report reports/medibook_latest.json
python evals/score_requirements.py <project_id> --misses
```

### Tests

```bash
pytest                                                    # unit tests, no DB needed
TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/stackforge_test pytest
```

The second form also runs the API tests against a throwaway database (create it
first). They fake the three Stage 1 LLM steps, so they're free and deterministic.

## Deployment

Not deployed, on purpose: it's a tool you run with your own keys and data, not a
hosted service. "Production" here means the install above works from a clean clone,
which was verified in November 2026.

## Limitations

- **Costs are priced, not paid.** Runs use free Groq/Gemini models; the token counts
  are priced at Anthropic rates. Different tokenisers make this an estimate.
- **Prompt caching and the Batch API are not implemented.** `CLAUDE.md` lists them as
  rules for the production design, but Groq supports neither, so the POC can't show
  those savings.
- **Map-reduce doesn't cut tokens on small inputs.** Stage 1 used more input tokens
  than a single call would have. Its value is quality and fitting cheap models.
- **Coverage scoring is keyword-based.** Approximate, but every match is recorded.
- **Stage 4 code is only syntax-checked.** It is never executed or tested, and runs
  for one sprint by default because of free-tier rate limits.
- **One measured run per configuration.** LLM output varies run to run.
- **Gemini is only partly working.** Summarisation runs, but requirement extraction
  fails: `gemini-3.5-flash` and `gemini-flash-latest` both returned a markdown list
  instead of JSON despite `response_format: json_object`, so Stage 1 can't finish on
  Gemini yet. Groq is the verified provider; OpenRouter and Ollama are untested in
  this rebuild.
- Still missed by extraction: walk-in registration, staff MFA, PHI audit logging.
- `run_demo.py` has Windows-style default paths and its own copy of the Stage 1 logic;
  use `evals/run_medibook.py` instead.
- `UsageTotals.total_tokens` double-counts reasoning tokens (display only; costs are right).
- No auth, permissive CORS. Local use only.
- Stages 5-6 (QA test generation, PR descriptions + knowledge base) are designed in
  `architecture.md` and `CLAUDE.md` but not built here.

---

## Learning log: mistakes and what I learned

I built StackForge in layers, from quick agent prototypes to this POC; each layer
records what went wrong and what I took from it.

### Layer 1 - First agent prototypes (Mar 2026, OpenRouter + gpt-4o-mini)

*What I built:* two small FastAPI apps: a requirements agent pipeline (analyst →
writer → reviewer → prioritiser) and "Possibility 3", a 5-phase SDLC chain of 8 agents streaming
to a one-page dashboard.

| # | Mistake | What actually happened | Lesson |
|---|---|---|---|
| 1.1 | **One model for every agent** | All 8 agents used `openai/gpt-4o-mini` (`BaseAgent.MODEL`), whether they were classifying or writing | Model choice belongs per task. Later this became cheap/capable routing (60% saving). |
| 1.2 | **Parsing JSON out of free text** | `_extract_json` tried direct parse, then stripping code fences, then regex, because responses weren't constrained | Ask for structured output up front (`response_format`, schema, `max_tokens`) instead of repairing it afterwards. |
| 1.3 | **Switching providers by commenting code** | `agents.py` kept two commented-out clients (Anthropic direct, Anthropic via OpenRouter) above the live OpenAI one | Provider should be config, not code. The POC switches with one env var. |
| 1.4 | **No token tracking** | No call recorded its usage, so there was no way to say what a run cost | You can't optimise what you don't measure; every POC call now logs tokens. |

### Layer 2 - LangGraph orchestration (Mar 20-23, 2026, LangGraph + Ollama qwen3:4b + Streamlit)

*What I built:* built on a colleague's initial LangGraph POC: PM, story-governance,
analyzer and delivery-planner agents, a router sending work to design / frontend /
backend / QA agents, plus Azure DevOps over MCP and Gmail notifications.

| # | Mistake | What actually happened | Lesson |
|---|---|---|---|
| 2.1 | **Secrets committed and pushed** | `.env` and `SDLC-Pipeline/.env`, holding an OpenRouter key, an Azure DevOps PAT and a Gmail app password, were tracked and pushed to the repo's `main` | `.gitignore` before the first commit, `.env.example` for names only, and revoke anything that was ever pushed. Git history keeps it. |

### Layer 3 - Knowledge base and ADO integration (Mar 24 - Apr 3, 2026)

*What I built:* webhook-driven processing of ADO work items, a Pinecone knowledge base
of scraped ADO and GitHub content with context expansion, and DOCX/PDF delivery
reports.

| # | Mistake | What actually happened | Lesson |
|---|---|---|---|
| 3.1 | **Debugging with loose scripts** | 13 one-off scripts at the repo root (`debug_ado.py`, `probe_ado.py`, `debug_wiql.py`, `search_wide.py`, ...) instead of a test suite | A probe worth writing is worth keeping as a test; loose scripts rot and nobody reruns them. |
| 3.2 | **"Production-grade" in the README** | The README called the system production-ready while it ran from a local Streamlit app with debug scripts beside it | Describe what's verified. Overclaiming costs credibility the first time someone runs it. |

### Layer 4 - The 6-stage StackForge POC (Jun 9-25, 2026, Groq + FastAPI + Postgres + Streamlit)

*What I built:* Stages 1-4 of the pipeline above, with per-stage token metrics and a
cost comparison against a naive single-call approach. OpenRouter and Ollama support
followed in August but was never committed.

| # | Mistake | What actually happened | Lesson |
|---|---|---|---|
| 4.1 | **The DOCX parser dropped every table** | `doc.paragraphs` skips tables; the BRD kept 820 of its words in tables, so the model saw 300 words and none of the 30 numbered requirements. Checklist coverage: 54.3% | Check what actually reaches the model before tuning prompts. Plumbing first. |
| 4.2 | **A savings headline built on a price table** | "87% savings" repriced Groq tokens at Opus $15/$75. At current prices the same run saves 60%, and `tokens_saved` was 0 | Separate what you measured (tokens) from what you modelled (prices), and date every price. |
| 4.3 | **Fixed token budget, growing reasoning** | Epic generation at medium effort with `max_tokens=4000` worked at 75 requirements; at 110 it spent the budget reasoning and returned empty JSON (7,810 of 8,000 tokens when raised) | Re-test settings at realistic input sizes; reasoning effort is a cost lever and a failure mode. |
| 4.4 | **Half the work never pushed** | The public repo stopped at Stage 2; Stages 3-4 (7 commits) sat on a local branch and the provider work (554 lines) was never committed | Push small and often; unpushed work is invisible and one disk away from gone. |
| 4.5 | **Zero tests** | An uncommitted one-character typo (`opusf_reprice`) made `/stage1-metrics` return 500, and nothing caught it | Even a few tests on the core path catch the cheap mistakes. |
| 4.6 | **The upload API lied** | The route returned a freshly generated `uuid4()` while the pipeline inserted its own row, so clients polled an id that didn't exist | Create the record you return; do only the slow work in the background. |
| 4.7 | **Retries made it worse** | Each re-upload inserted another row; with "ready only if all docs are done", one failure kept the project "failed" forever | Design for the retry path explicitly: reuse the row, clear partial output. |
| 4.8 | **Every provider sent the Groq key** | `get_llm_client()` hard-coded `GROQ_API_KEY`, so OpenRouter got the wrong key | A provider switch needs one test per provider. |
| 4.9 | **Setup docs that couldn't work** | README used `postgres:16`; `init.sql` needs pgvector, so `rag_chunks` was never created | Run your own README on a clean machine. |
| 4.10 | **Unpinned dependencies** | `>=` pins let `openai` resolve to 3.51 when the code was written for 1.56 | Pin what you tested. |
| 4.11 | **Config that misleads** | `.env.example` enabled Pinecone with a placeholder key and documented a "production swap" that `config.py` never read; `load_dotenv(override=True)` let `.env` beat explicit env vars | Example config is code: it must match what's actually read. |
| 4.12 | **Rules on paper** | `CLAUDE.md` makes prompt caching and the Batch API non-negotiable; neither is in the code, because Groq supports neither | Keep "designed" and "built" visibly separate. |

### Layer 5 - The team platform (Aug 2026)

*Context:* the POC's ideas fed into StackForgeAI, the platform built by the Stackular
team, where I was one of four engineers. It is out of scope for this repo; it is
covered in the successor product's learning log.

### Layer 6 - The rebuild (Nov 2026)

*What I did:* audited every folder from the earlier layers, brought the unpushed
Stages 3-4 into this repo with their history, fixed what was broken, and measured
the result against a checklist built from the source documents.

| # | Decision | Why |
|---|---|---|
| 6.1 | Merge the local Stage 3-4 branch as a fast-forward, then commit the provider work separately | Keeps the June history and dates honest (4.4) |
| 6.2 | Table-aware DOCX parsing, in document order, one block per row | 300 → 1,250 BRD words; coverage 54.3% → 93.5% (4.1) |
| 6.3 | Low reasoning effort for epic generation | 4/4 valid runs vs empty JSON at medium (4.3) |
| 6.4 | Register the document row before the background job; reuse it on retry | Real ids, no duplicates, projects recover (4.6, 4.7) |
| 6.5 | Gemini as a fourth provider; provider regression test | Each provider gets its own key (4.8). Gemini extraction doesn't return JSON yet, so it is documented as partial rather than claimed |
| 6.6 | `docker-compose.yml` + `migrate_all.py`; pinned deps; honest `.env.example`; env vars beat `.env` | A clean clone works (4.9, 4.10, 4.11) |
| 6.7 | 26 tests, the LLM faked in the API tests | Cheap, deterministic checks of the plumbing (4.5) |
| 6.8 | A 46-item checklist and a report builder; prices updated and dated | Quality and cost judged together, from recorded tokens (4.2) |
| 6.9 | Removed a 5 MB unrelated `prompts.csv` | Not part of the project |

*Known gaps I chose not to fix:* prompt caching and the Batch API (need the Anthropic
API), Stage 4 code execution, the `run_demo.py` duplicate, the `total_tokens`
display bug, auth. The successor product picks these up.

### Key takeaways

1. **Check the plumbing before the prompts.** The biggest quality gain (+39 points) came from a parser, not a model.
2. **Measure tokens, model prices, and date them.** Otherwise a pricing change rewrites your headline.
3. **Re-test at realistic sizes.** Fixing one stage raised the load on the next and exposed a limit that was always there.
4. **An unpushed commit doesn't exist.** Half of this POC was invisible for five months.
5. **A small eval set makes changes decidable.** 46 checklist items turned "seems better" into 54% → 94%.

## Future improvements

- An Anthropic provider using prompt caching and the Batch API, to measure the two
  rules this POC couldn't.
- Run and test Stage 4's generated code instead of only parsing it.
- Fold `run_demo.py` into `evals/run_medibook.py`.

## Successor

This POC's intake pipeline is being merged into the full StackForge platform as its
front door: client documents in, pull requests out. Link to follow.
