# MediBook run reports (Nov 2026)

Measured runs of the five MediBook sample documents (`sample_client_docs/`) through
the pipeline on **Groq's free tier** (`openai/gpt-oss-20b` cheap, `openai/gpt-oss-120b`
capable). Each JSON file is produced by `evals/build_report.py` from the tokens the
pipeline itself recorded per call.

| File | What |
|---|---|
| `medibook_groq_baseline_stage1.json` | Stage 1 with the **old** DOCX parser (tables dropped) |
| `medibook_groq_full.json` | Stages 1-4 with the table-aware parser (Stage 4 = first sprint, 10 tasks) |

## Extraction quality: old vs fixed parser (Stage 1)

Scored with `evals/score_requirements.py` against a 46-item checklist built by
reading the source documents (`evals/medibook_checklist.json`).

| | Old parser | Fixed parser |
|---|---|---|
| BRD words reaching the model | 300 | 1,250 |
| Requirements (active after dedup) | 84 (75) | 122 (110) |
| **Checklist coverage** | **25/46 (54.3%)** | **43/46 (93.5%)** |
| Stage 1 cost at current Anthropic rates | $0.081 | $0.097 |

Still missed with the fixed parser: APT-004 (walk-in registration), SEC-MFA (staff
TOTP), SEC-AUDIT (PHI audit log).

## Full run (fixed parser)

| Stage | Output | Calls | Tokens in / out | Cost, routed | Cost, all Opus | Wall time |
|---|---|---|---|---|---|---|
| 1 Ingestion | 110 active requirements, 7 clarifications | 15 | 29,649 / 14,350 | $0.097 | $0.406 | ~7 min |
| 2 Epics & stories | 6 epics, 30 stories | 7 | 20,652 / 6,850 | $0.098 | $0.220 | 229 s |
| 3 Sprints & tasks | 9 sprints, 142 tasks | 31 | 26,888 / 9,310 | $0.147 | $0.294 | 485 s |
| 4 Code drafts (sprint 1) | 10/10 tasks, 25 files, 1,109 lines | 10 | 6,014 / 12,674 | $0.139 | $0.278 | 342 s |
| **Total** | | **63** | **83,203 / 43,184** | **$0.48** | **$1.20** | ~25 min |

Routing cheap work to Haiku-tier models and generation to Sonnet-tier models costs
**60% less** than running every call on Opus, at current prices.

## How to read these numbers

- **Costs are priced, not paid.** The run used free Groq models; the same token
  counts are priced at Anthropic rates (`config.PRICING`) to show what production
  would cost. Different models tokenise differently, so this is an estimate.
- **The old "87% savings" figure was mostly the price table.** At the June 2026
  rates (Opus 5x Sonnet) the same run shows 84% savings ($0.73 vs $4.49). At current
  rates (Opus 2x Sonnet) it is 60%.
- **Routing saves money; map-reduce does not save tokens here.** Stage 1 used more
  input tokens than the modelled single-call baseline (`tokens_saved: 0`). Chunking
  buys quality and makes the work fit cheap models, not a smaller token count on a
  document set this small.
- **Coverage is keyword-based.** Deterministic and free, but approximate. Each
  covered item records which requirement matched, so it can be checked by hand.
- **Stage 4 code is syntax-checked only.** All 13 Python files parse; nothing was
  executed or tested.
- **One run each.** LLM output varies between runs; treat small differences as noise.
- The full run was executed in two parts: Stage 1 completed, Stage 2 then failed on
  epic generation (reasoning effort, since fixed), and Stages 2-4 were resumed on the
  same project. `evals/run_medibook.py` runs all stages in one go.
