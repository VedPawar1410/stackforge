"""
Stage 4 metrics report.

Phase 1: timing-only (no token data — claude CLI doesn't expose usage).
Returns a structure compatible with the UI's metrics panel, with
tokens_available=False so the UI can show a "Phase 1" notice instead
of zero-value token columns.
"""

import logging

from db import DB

_logger = logging.getLogger(__name__)


def get_metrics_report(project_id: str) -> dict:
    """
    Build a Stage 4 metrics report for a project.

    Returns:
        {
          total_tasks:       int,
          completed:         int,
          failed:            int,
          in_progress:       int,
          total_duration_ms: int,   -- sum across all completed sessions
          avg_duration_ms:   int,   -- mean per completed session
          tokens_available:  bool,  -- always False in Phase 1
          task_breakdown: [
            {task_title, task_type, status, duration_ms, file_count}
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
                ts.duration_ms,
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

        # Also count tasks that have no session yet (not started)
        total_row = db.fetch_one(
            "SELECT COUNT(*) AS cnt FROM tasks WHERE project_id = %s",
            (project_id,),
        )

    total_tasks = int(total_row["cnt"]) if total_row else 0

    completed = sum(1 for s in sessions if s["status"] == "completed")
    failed = sum(1 for s in sessions if s["status"] == "failed")
    running = sum(1 for s in sessions if s["status"] == "running")

    completed_durations = [
        int(s["duration_ms"] or 0)
        for s in sessions
        if s["status"] == "completed" and s["duration_ms"] is not None
    ]
    total_duration_ms = sum(completed_durations)
    avg_duration_ms = (
        total_duration_ms // len(completed_durations) if completed_durations else 0
    )

    breakdown = []
    for s in sessions:
        files = s.get("generated_files") or []
        if isinstance(files, str):
            import json
            try:
                files = json.loads(files)
            except Exception:
                files = []
        breakdown.append(
            {
                "task_title":   s["task_title"],
                "task_type":    s["task_type"] or "backend",
                "status":       s["status"],
                "duration_ms":  int(s["duration_ms"] or 0),
                "file_count":   len(files),
                "error":        s.get("error_message") or "",
            }
        )

    return {
        "total_tasks":       total_tasks,
        "completed":         completed,
        "failed":            failed,
        "in_progress":       running,
        "total_duration_ms": total_duration_ms,
        "avg_duration_ms":   avg_duration_ms,
        "tokens_available":  False,
        "task_breakdown":    breakdown,
    }
