"""
Stage 4 metrics report.

Groq-based: real token counts are available from task_usage.
tokens_available=True so the UI shows actual input/output token totals.
"""

import json
import logging

from config import MODEL_TIER
from db import DB

_logger = logging.getLogger(__name__)


def get_metrics_report(project_id: str) -> dict:
    """
    Build a Stage 4 metrics report for a project.

    Returns:
        {
          total_tasks:          int,
          completed:            int,
          failed:               int,
          in_progress:          int,
          total_duration_ms:    int,
          avg_duration_ms:      int,
          tokens_available:     bool,   -- True (Groq API exposes usage)
          actual_input_tokens:  int,
          actual_output_tokens: int,
          task_breakdown: [
            {task_title, task_type, status, duration_ms, file_count, error}
          ]
        }
    """
    with DB() as db:
        sessions = db.fetch_all(
            """
            SELECT
                ts.id           AS session_id,
                ts.task_id,
                ts.status,
                EXTRACT(EPOCH FROM (ts.completed_at - ts.started_at)) * 1000 AS duration_ms,
                ts.generated_files,
                ts.error_message,
                t.title         AS task_title,
                t.task_type
            FROM task_sessions ts
            JOIN tasks t ON t.id = ts.task_id
            WHERE ts.project_id = %s
            ORDER BY ts.started_at
            """,
            (project_id,),
        )

        total_row = db.fetch_one(
            "SELECT COUNT(*) AS cnt FROM tasks WHERE project_id = %s",
            (project_id,),
        )

        usage_row = db.fetch_one(
            """
            SELECT COALESCE(SUM(input_tokens), 0)  AS total_in,
                   COALESCE(SUM(output_tokens), 0) AS total_out
            FROM task_usage
            WHERE project_id = %s AND stage = '4'
            """,
            (project_id,),
        )

        model_rows = db.fetch_all(
            "SELECT DISTINCT model FROM task_usage WHERE project_id = %s AND stage = '4'",
            (project_id,),
        )

    total_tasks = int(total_row["cnt"]) if total_row else 0
    actual_input_tokens  = int(usage_row["total_in"])  if usage_row else 0
    actual_output_tokens = int(usage_row["total_out"]) if usage_row else 0

    models_used = [r["model"] for r in model_rows]
    # A run is only cost-representative if every model it used maps to a
    # known Anthropic pricing tier (see MODEL_TIER in config.py). Local
    # Ollama models are deliberately left out of that map — flag them here
    # instead of letting the report imply real savings numbers.
    representative = bool(models_used) and all(m in MODEL_TIER for m in models_used)

    completed = sum(1 for s in sessions if s["status"] == "completed")
    failed    = sum(1 for s in sessions if s["status"] == "failed")
    running   = sum(1 for s in sessions if s["status"] == "running")

    completed_durations = [
        int(s["duration_ms"] or 0)
        for s in sessions
        if s["status"] == "completed" and s["duration_ms"] is not None
    ]
    total_duration_ms = sum(completed_durations)
    avg_duration_ms   = (
        total_duration_ms // len(completed_durations) if completed_durations else 0
    )

    breakdown = []
    for s in sessions:
        files = s.get("generated_files") or []
        if isinstance(files, str):
            try:
                files = json.loads(files)
            except Exception:
                files = []
        breakdown.append(
            {
                "task_title":  s["task_title"],
                "task_type":   s["task_type"] or "backend",
                "status":      s["status"],
                "duration_ms": int(s["duration_ms"] or 0),
                "file_count":  len(files),
                "error":       s.get("error_message") or "",
            }
        )

    return {
        "total_tasks":          total_tasks,
        "completed":            completed,
        "failed":               failed,
        "in_progress":          running,
        "total_duration_ms":    total_duration_ms,
        "avg_duration_ms":      avg_duration_ms,
        "tokens_available":     True,
        "actual_input_tokens":  actual_input_tokens,
        "actual_output_tokens": actual_output_tokens,
        "representative":       representative,
        "models_used":          models_used,
        "task_breakdown":       breakdown,
    }
