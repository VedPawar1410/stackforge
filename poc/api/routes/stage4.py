"""Routes: Stage 4 — AI-Assisted Development."""

import asyncio
import json
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query

from api.models import (
    Stage4MetricsResponse,
    Stage4StatusResponse,
    TaskSessionDetail,
    TaskSessionSummary,
)
from api.routes import validate_project_id
from db import DB
from pipeline.stage4_metrics_calculator import get_metrics_report
from pipeline.stage4_runner import run_stage4

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects/{project_id}", tags=["stage4"])


@router.post("/run-stage4", status_code=202)
def trigger_run_stage4(
    project_id: str,
    background_tasks: BackgroundTasks,
    sprint_id: str = Query(
        None,
        description="Limit execution to tasks in this sprint UUID. Omit to run all tasks.",
    ),
    max_parallel: int = Query(
        2,
        ge=1,
        le=4,
        description="Maximum concurrent Claude Code processes (1–4, default 2).",
    ),
):
    """
    Trigger Stage 4 AI development for a project or a specific sprint.
    Runs in the background — poll /stage4-status for progress.
    Prerequisite: Stage 3 must be complete (tasks must exist).
    """
    validate_project_id(project_id)

    with DB() as db:
        if not db.fetch_one("SELECT id FROM projects WHERE id = %s", (project_id,)):
            raise HTTPException(status_code=404, detail="Project not found")
        task_row = db.fetch_one(
            "SELECT COUNT(*) AS cnt FROM tasks WHERE project_id = %s", (project_id,)
        )
        if not task_row or int(task_row["cnt"]) == 0:
            raise HTTPException(
                status_code=400,
                detail="No tasks found — run Stage 3 (Generate Sprint Plan) first",
            )
        if sprint_id:
            sprint_row = db.fetch_one(
                "SELECT id FROM sprints WHERE id = %s AND project_id = %s",
                (sprint_id, project_id),
            )
            if not sprint_row:
                raise HTTPException(status_code=404, detail="Sprint not found")

    background_tasks.add_task(
        _run_stage4_sync, project_id, sprint_id, max_parallel
    )
    scope = f"sprint {sprint_id}" if sprint_id else "all tasks"
    return {
        "status": "generating",
        "message": f"Stage 4 started ({scope}, max_parallel={max_parallel})",
    }


def _run_stage4_sync(
    project_id: str, sprint_id: str | None, max_parallel: int
) -> None:
    """Adapter: runs the async Stage 4 pipeline in a background thread."""
    asyncio.run(run_stage4(project_id, sprint_id=sprint_id, max_parallel=max_parallel))


@router.get("/stage4-status", response_model=Stage4StatusResponse)
def get_stage4_status(project_id: str):
    """Poll Stage 4 execution progress."""
    validate_project_id(project_id)

    with DB() as db:
        project = db.fetch_one(
            "SELECT stage4_status FROM projects WHERE id = %s", (project_id,)
        )
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        total_row = db.fetch_one(
            "SELECT COUNT(*) AS cnt FROM tasks WHERE project_id = %s", (project_id,)
        )
        completed_row = db.fetch_one(
            "SELECT COUNT(*) AS cnt FROM task_sessions WHERE project_id = %s AND status = 'completed'",
            (project_id,),
        )
        failed_row = db.fetch_one(
            "SELECT COUNT(*) AS cnt FROM task_sessions WHERE project_id = %s AND status = 'failed'",
            (project_id,),
        )
        running_row = db.fetch_one(
            "SELECT COUNT(*) AS cnt FROM task_sessions WHERE project_id = %s AND status = 'running'",
            (project_id,),
        )

    return {
        "status":      project.get("stage4_status", "idle"),
        "total_tasks": int(total_row["cnt"]) if total_row else 0,
        "completed":   int(completed_row["cnt"]) if completed_row else 0,
        "failed":      int(failed_row["cnt"]) if failed_row else 0,
        "in_progress": int(running_row["cnt"]) if running_row else 0,
    }


@router.get("/task-sessions", response_model=list[TaskSessionSummary])
def list_task_sessions(project_id: str):
    """List all task sessions for a project with per-session summary."""
    validate_project_id(project_id)

    with DB() as db:
        if not db.fetch_one("SELECT id FROM projects WHERE id = %s", (project_id,)):
            raise HTTPException(status_code=404, detail="Project not found")

        rows = db.fetch_all(
            """
            SELECT
                ts.id,
                ts.task_id,
                ts.status,
                ts.generated_files,
                ts.started_at,
                ts.completed_at,
                EXTRACT(EPOCH FROM (ts.completed_at - ts.started_at)) * 1000
                    AS duration_ms,
                t.title     AS task_title,
                t.task_type
            FROM task_sessions ts
            JOIN tasks t ON t.id = ts.task_id
            WHERE ts.project_id = %s
            ORDER BY ts.started_at
            """,
            (project_id,),
        )

    result = []
    for r in rows:
        files = r.get("generated_files") or []
        if isinstance(files, str):
            try:
                files = json.loads(files)
            except Exception:
                files = []
        dur = r.get("duration_ms")
        result.append({
            "id":           str(r["id"]),
            "task_id":      str(r["task_id"]),
            "task_title":   r["task_title"],
            "task_type":    r["task_type"] or "backend",
            "status":       r["status"],
            "duration_ms":  int(dur) if dur is not None else None,
            "file_count":   len(files),
            "started_at":   r["started_at"],
            "completed_at": r.get("completed_at"),
        })
    return result


@router.get("/task-sessions/{task_id}", response_model=TaskSessionDetail)
def get_task_session(project_id: str, task_id: str):
    """
    Get the full session detail for a task: Claude's summary, generated file list,
    and error info if it failed.
    """
    validate_project_id(project_id)

    with DB() as db:
        row = db.fetch_one(
            """
            SELECT
                ts.id,
                ts.task_id,
                ts.status,
                ts.workspace_path,
                ts.session_summary,
                ts.generated_files,
                ts.error_message,
                ts.started_at,
                ts.completed_at,
                EXTRACT(EPOCH FROM (ts.completed_at - ts.started_at)) * 1000
                    AS duration_ms,
                t.title     AS task_title,
                t.task_type
            FROM task_sessions ts
            JOIN tasks t ON t.id = ts.task_id
            WHERE ts.task_id = %s
              AND ts.project_id = %s
            ORDER BY ts.started_at DESC
            LIMIT 1
            """,
            (task_id, project_id),
        )

    if not row:
        raise HTTPException(status_code=404, detail="No session found for this task")

    files = row.get("generated_files") or []
    if isinstance(files, str):
        try:
            files = json.loads(files)
        except Exception:
            files = []

    dur = row.get("duration_ms")
    return {
        "id":              str(row["id"]),
        "task_id":         str(row["task_id"]),
        "task_title":      row["task_title"],
        "task_type":       row["task_type"] or "backend",
        "status":          row["status"],
        "duration_ms":     int(dur) if dur is not None else None,
        "file_count":      len(files),
        "started_at":      row["started_at"],
        "completed_at":    row.get("completed_at"),
        "session_summary": row.get("session_summary"),
        "generated_files": files,
        "error_message":   row.get("error_message"),
        "workspace_path":  row.get("workspace_path"),
    }


@router.get("/stage4-metrics", response_model=Stage4MetricsResponse)
def get_stage4_metrics(project_id: str):
    """Return the Stage 4 execution metrics report (timing-only in Phase 1)."""
    validate_project_id(project_id)

    with DB() as db:
        if not db.fetch_one("SELECT id FROM projects WHERE id = %s", (project_id,)):
            raise HTTPException(status_code=404, detail="Project not found")

    return get_metrics_report(project_id)
