"""Per-stage token and cost totals for a project, from the recorded metrics.

One implementation shared by the API (UI cost tiles) and evals/build_report.py,
so the demo and the README can't disagree. Cost reprices the recorded tokens
at Anthropic rates (config.PRICING): "routed" uses each call's tier, "all_opus"
prices every call as Opus.
"""

from config import PRICING
from db import DB
from pipeline.metrics_common import tier_for_model

STAGE_TABLES = {"1": "stage1_metrics", "2": "stage2_metrics", "3": "stage3_metrics", "4": "task_usage"}


def price(rows: list[dict], pricing: dict, force_tier: str | None = None) -> float:
    total = 0.0
    for r in rows:
        p = pricing[force_tier or tier_for_model(r["model"])]
        # completion tokens already include reasoning tokens, so in + out is the bill
        total += r["input_tokens"] / 1e6 * p["input"] + r["output_tokens"] / 1e6 * p["output"]
    return round(total, 4)


def stage_rows(project_id: str) -> dict[str, list[dict]]:
    rows = {}
    with DB() as db:
        for stage, table in STAGE_TABLES.items():
            rows[stage] = db.fetch_all(
                f"SELECT model, input_tokens, output_tokens, thinking_tokens FROM {table} WHERE project_id = %s",
                (project_id,),
            )
    return rows


def project_cost_summary(project_id: str) -> dict:
    stages = {}
    for stage, rows in stage_rows(project_id).items():
        if not rows:
            continue
        stages[stage] = {
            "calls": len(rows),
            "input_tokens": sum(r["input_tokens"] for r in rows),
            "output_tokens": sum(r["output_tokens"] for r in rows),
            "cost_routed": price(rows, PRICING),
            "cost_all_opus": price(rows, PRICING, force_tier="opus"),
        }
    totals = {k: round(sum(s[k] for s in stages.values()), 4)
              for k in ("calls", "input_tokens", "output_tokens", "cost_routed", "cost_all_opus")}
    opus = totals["cost_all_opus"]
    totals["saving_pct"] = round(100 * (1 - totals["cost_routed"] / opus), 1) if opus else 0.0
    return {"stages": stages, "totals": totals}
