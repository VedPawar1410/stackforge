"""Build a metrics report for one project from what the pipeline recorded.

    python evals/build_report.py <project_id> reports/<name>.json

Every number comes from the metrics tables the pipeline writes during a run
(tokens per call, per model, per stage), plus the checklist score and a syntax
check of Stage 4's generated files. Cost is the same token counts priced at
Anthropic rates (config.PRICING); the run itself used free-tier models.
"""

import ast
import glob
import json
import os
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from config import PRICING  # noqa: E402
from db import DB  # noqa: E402
from pipeline.metrics_common import tier_for_model  # noqa: E402
from score_requirements import score  # noqa: E402

# Rates the June 2026 reports used, kept to show how much the old savings
# figures depended on them.
JUNE_2026_PRICING = {
    "haiku": {"input": 0.25, "output": 1.25},
    "sonnet": {"input": 3.00, "output": 15.00},
    "opus": {"input": 15.00, "output": 75.00},
}

STAGE_TABLES = {"1": "stage1_metrics", "2": "stage2_metrics", "3": "stage3_metrics", "4": "task_usage"}


def _cost(rows: list[dict], pricing: dict, force_tier: str | None = None) -> float:
    total = 0.0
    for r in rows:
        p = pricing[force_tier or tier_for_model(r["model"])]
        # completion tokens already include reasoning tokens, so in + out is the bill
        total += r["input_tokens"] / 1e6 * p["input"] + r["output_tokens"] / 1e6 * p["output"]
    return round(total, 4)


def _stage4_code(project_id: str) -> dict:
    with DB() as db:
        sessions = db.fetch_all(
            "SELECT status, workspace_path FROM task_sessions WHERE project_id = %s", (project_id,))
    files = py_files = py_parse_ok = lines = 0
    skip = {"issue_context.md", "CLAUDE.md", "AGENTS.md"}
    for s in sessions:
        for path in glob.glob(os.path.join(s["workspace_path"] or "", "**", "*"), recursive=True):
            if os.path.isdir(path) or "/.claude/" in path or os.path.basename(path) in skip:
                continue
            files += 1
            src = open(path, errors="replace").read()
            lines += src.count("\n")
            if path.endswith(".py"):
                py_files += 1
                try:
                    ast.parse(src)
                    py_parse_ok += 1
                except SyntaxError:
                    pass
    return {"tasks_run": len(sessions), "tasks_completed": sum(s["status"] == "completed" for s in sessions),
            "files_generated": files, "lines_generated": lines,
            "python_files": py_files, "python_files_that_parse": py_parse_ok}


def build(project_id: str) -> dict:
    stages = {}
    with DB() as db:
        for stage, table in STAGE_TABLES.items():
            rows = db.fetch_all(
                f"SELECT model, input_tokens, output_tokens, thinking_tokens FROM {table} WHERE project_id = %s",
                (project_id,),
            )
            if not rows:
                continue
            stages[stage] = {
                "calls_recorded": len(rows),
                "models": sorted({r["model"] for r in rows}),
                "input_tokens": sum(r["input_tokens"] for r in rows),
                "output_tokens": sum(r["output_tokens"] for r in rows),
                "of_which_reasoning_tokens": sum(r["thinking_tokens"] or 0 for r in rows),
                "cost_usd_routed": _cost(rows, PRICING),
                "cost_usd_all_opus": _cost(rows, PRICING, force_tier="opus"),
                "cost_usd_routed_june_2026_rates": _cost(rows, JUNE_2026_PRICING),
                "cost_usd_all_opus_june_2026_rates": _cost(rows, JUNE_2026_PRICING, force_tier="opus"),
            }
        reqs = db.fetch_all(
            "SELECT title, description, key_specifics, status FROM requirements WHERE project_id = %s",
            (project_id,))
        counts = db.fetch_one(
            """SELECT
                 (SELECT count(*) FROM documents WHERE project_id = %s AND status = 'done') AS documents_done,
                 (SELECT count(*) FROM clarifications WHERE project_id = %s) AS clarifications,
                 (SELECT count(*) FROM epics WHERE project_id = %s) AS epics,
                 (SELECT count(*) FROM user_stories WHERE project_id = %s) AS user_stories,
                 (SELECT count(*) FROM sprints WHERE project_id = %s) AS sprints,
                 (SELECT count(*) FROM tasks WHERE project_id = %s) AS tasks""",
            (project_id,) * 6)  # pg8000 supports positional %s only

    checklist = score(reqs)
    totals = {k: round(sum(s[k] for s in stages.values()), 4) for k in
              ("input_tokens", "output_tokens", "cost_usd_routed", "cost_usd_all_opus",
               "cost_usd_routed_june_2026_rates", "cost_usd_all_opus_june_2026_rates")}
    return {
        "generated": date.today().isoformat(),
        "project_id": project_id,
        "pricing_usd_per_mtok": PRICING,
        "counts": {**counts, "requirements": checklist["requirements"],
                   "active_requirements": checklist["active_requirements"]},
        "checklist_coverage": {k: checklist["active"][k] for k in ("covered", "total", "pct")},
        "checklist_missed": [i["id"] for i in checklist["active"]["items"] if not i["covered"]],
        "stages": stages,
        "totals": totals,
        "stage4_code": _stage4_code(project_id) if "4" in stages else None,
    }


if __name__ == "__main__":
    report = build(sys.argv[1])
    with open(sys.argv[2], "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(json.dumps({k: report[k] for k in ("counts", "checklist_coverage", "totals")}, indent=2, default=str))
