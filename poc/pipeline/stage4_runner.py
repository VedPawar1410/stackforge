"""
Stage 4 orchestrator: AI-Assisted Development.

Phase 1 (Scenario A) — Python task runner.

Flow per task:
  1. Build isolated workspace (workspace_builder.py)
  2. Inject Stitch designs (workspace_prep.py, called by workspace_builder)
  3. Spawn `claude -p <prompt> --dangerously-skip-permissions` in the workspace
  4. Capture stdout as session summary + scan generated files
  5. Log to task_sessions + task_usage tables
  6. Update tasks.status = 'done'

Token tracking: Phase 1 records 0 tokens (claude CLI doesn't expose usage in stdout).
Phase 2 (daemon pattern) will stream events and capture real token counts.
"""

import asyncio
import json
import logging
import os
import sys
import time
import uuid
from datetime import datetime, timezone

from config import STAGE4_CLAUDE_TIMEOUT_S, STAGE4_MAX_PARALLEL, STAGE4_WORKSPACE_ROOT
from db import DB
from pipeline.workspace_builder import build_workspace

_logger = logging.getLogger(__name__)

# Files/dirs created by build_workspace — not counted as "generated" by Claude
_SCAFFOLD_NAMES = frozenset({"issue_context.md", ".claude", "design_screens", ".mcp.json", "src"})

# Prompt sent to the claude CLI -p flag
_CLAUDE_PROMPT = (
    "Read issue_context.md completely, then implement the described task. "
    "Create all necessary source files in the appropriate subdirectory. "
    "When done, output a brief summary: what you built and which files were created."
)


async def run_stage4(
    project_id: str,
    sprint_id: str | None = None,
    max_parallel: int = STAGE4_MAX_PARALLEL,
) -> dict:
    """
    Run Stage 4 for all tasks in a project (or a specific sprint).
    Returns {"completed": N, "failed": M, "total": T}.
    Raises ValueError if no tasks found (Stage 3 must be complete).
    """
    with DB() as db:
        db.execute("DELETE FROM task_sessions WHERE project_id = %s", (project_id,))
        db.execute(
            "UPDATE projects SET stage4_status = 'generating' WHERE id = %s",
            (project_id,),
        )

    try:
        tasks_with_stories = _fetch_tasks_with_stories(project_id, sprint_id)

        if not tasks_with_stories:
            raise ValueError(
                "No tasks found — run Stage 3 (Generate Sprint Plan) first"
            )

        with DB() as db:
            proj = db.fetch_one("SELECT name FROM projects WHERE id = %s", (project_id,))
        project_name = proj["name"] if proj else project_id

        sem = asyncio.Semaphore(max_parallel)
        coros = [
            _run_task_session(task, story, project_id, project_name, sem)
            for task, story in tasks_with_stories
        ]
        results = await asyncio.gather(*coros, return_exceptions=True)

        completed = sum(
            1 for r in results if isinstance(r, dict) and r.get("status") == "completed"
        )
        failed = len(results) - completed

        with DB() as db:
            db.execute(
                "UPDATE projects SET stage4_status = 'ready' WHERE id = %s",
                (project_id,),
            )

        _logger.info(
            "Stage 4 complete for %s: %d/%d tasks succeeded",
            project_id, completed, len(results),
        )
        return {"completed": completed, "failed": failed, "total": len(results)}

    except Exception:
        with DB() as db:
            db.execute(
                "UPDATE projects SET stage4_status = 'failed' WHERE id = %s",
                (project_id,),
            )
        raise


async def _run_task_session(
    task: dict,
    story: dict,
    project_id: str,
    project_name: str,
    sem: asyncio.Semaphore,
) -> dict:
    """Run a single task: build workspace → spawn Claude CLI → record results."""
    async with sem:
        task_id = str(task["id"])
        session_id = str(uuid.uuid4())
        workspace_path = os.path.join(STAGE4_WORKSPACE_ROOT, task_id)

        # Insert session row
        with DB() as db:
            db.execute(
                """
                INSERT INTO task_sessions
                    (id, task_id, project_id, status, workspace_path)
                VALUES (%s, %s, %s, 'running', %s)
                """,
                (session_id, task_id, project_id, workspace_path),
            )
            db.execute(
                "UPDATE tasks SET status = 'in_progress' WHERE id = %s",
                (task_id,),
            )

        try:
            build_workspace(task, story, project_id, project_name, STAGE4_WORKSPACE_ROOT)

            summary, exit_code, duration_ms = await _spawn_claude(workspace_path)

            generated_files = _scan_workspace_files(workspace_path)

            now = datetime.now(timezone.utc)
            with DB() as db:
                db.execute(
                    """
                    UPDATE task_sessions
                    SET status = 'completed',
                        session_summary = %s,
                        generated_files = %s,
                        completed_at = %s,
                        exit_code = %s
                    WHERE id = %s
                    """,
                    (
                        summary,
                        json.dumps(generated_files),
                        now,
                        exit_code,
                        session_id,
                    ),
                )
                db.execute(
                    "UPDATE tasks SET status = 'done' WHERE id = %s",
                    (task_id,),
                )

            _log_usage(task, project_id, duration_ms)

            return {"status": "completed", "session_id": session_id}

        except Exception as exc:
            _logger.exception("Task session failed for task %s: %s", task_id, exc)
            with DB() as db:
                db.execute(
                    """
                    UPDATE task_sessions
                    SET status = 'failed', error_message = %s, completed_at = %s
                    WHERE id = %s
                    """,
                    (str(exc), datetime.now(timezone.utc), session_id),
                )
                db.execute(
                    "UPDATE tasks SET status = 'todo' WHERE id = %s",
                    (task_id,),
                )
            return {"status": "failed", "session_id": session_id, "error": str(exc)}


async def _spawn_claude(workspace_path: str) -> tuple[str, int, int]:
    """
    Spawn `claude -p <prompt> --dangerously-skip-permissions` in workspace_path.
    Returns (summary_text, exit_code, duration_ms).
    Raises asyncio.TimeoutError if the process exceeds STAGE4_CLAUDE_TIMEOUT_S.
    """
    # On Windows, 'claude' resolves through PATH — same as running it in a terminal
    claude_bin = "claude"
    if sys.platform == "win32":
        claude_bin = "claude.cmd"  # npm global installs on Windows create a .cmd shim

    env = {**os.environ}

    start = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            claude_bin,
            "-p", _CLAUDE_PROMPT,
            "--dangerously-skip-permissions",
            cwd=workspace_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=STAGE4_CLAUDE_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        raise asyncio.TimeoutError(
            f"Claude CLI exceeded {STAGE4_CLAUDE_TIMEOUT_S}s timeout"
        )

    duration_ms = int((time.monotonic() - start) * 1000)
    summary = stdout.decode("utf-8", errors="replace").strip()

    if stderr:
        stderr_text = stderr.decode("utf-8", errors="replace").strip()
        if stderr_text:
            _logger.debug("Claude CLI stderr: %s", stderr_text[:500])

    return summary, proc.returncode, duration_ms


def _scan_workspace_files(workspace_path: str) -> list[dict]:
    """
    Return a list of files created by Claude in the workspace.
    Excludes the scaffold files we wrote before Claude ran.
    """
    result = []
    for root, dirs, files in os.walk(workspace_path):
        # Skip scaffold dirs
        dirs[:] = [d for d in dirs if d not in _SCAFFOLD_NAMES]
        rel_root = os.path.relpath(root, workspace_path)
        for fname in files:
            if rel_root == "." and fname in _SCAFFOLD_NAMES:
                continue
            full_path = os.path.join(root, fname)
            rel_path = os.path.relpath(full_path, workspace_path).replace("\\", "/")
            try:
                size = os.path.getsize(full_path)
            except OSError:
                size = 0
            result.append({"path": rel_path, "size_bytes": size})
    return result


def _fetch_tasks_with_stories(
    project_id: str, sprint_id: str | None
) -> list[tuple[dict, dict]]:
    """
    Fetch all tasks for a project (or a single sprint) along with their parent stories.
    Returns list of (task_dict, story_dict) tuples.
    """
    with DB() as db:
        if sprint_id:
            rows = db.fetch_all(
                """
                SELECT
                    t.id, t.story_id, t.title, t.description,
                    t.task_type, t.estimated_hours, t.status,
                    us.title          AS story_title,
                    us.description    AS story_description,
                    us.acceptance_criteria
                FROM tasks t
                JOIN user_stories us   ON us.id = t.story_id
                JOIN sprint_stories ss ON ss.story_id = t.story_id
                WHERE t.project_id = %s
                  AND ss.sprint_id  = %s
                ORDER BY t.created_at
                """,
                (project_id, sprint_id),
            )
        else:
            rows = db.fetch_all(
                """
                SELECT
                    t.id, t.story_id, t.title, t.description,
                    t.task_type, t.estimated_hours, t.status,
                    us.title          AS story_title,
                    us.description    AS story_description,
                    us.acceptance_criteria
                FROM tasks t
                JOIN user_stories us ON us.id = t.story_id
                WHERE t.project_id = %s
                ORDER BY t.created_at
                """,
                (project_id,),
            )

    pairs = []
    for row in rows:
        task = {
            "id":               str(row["id"]),
            "story_id":         str(row["story_id"]),
            "title":            row["title"],
            "description":      row["description"] or "",
            "task_type":        row["task_type"] or "backend",
            "estimated_hours":  float(row["estimated_hours"] or 4.0),
            "status":           row["status"] or "todo",
        }
        story = {
            "id":                 str(row["story_id"]),
            "title":              row["story_title"],
            "description":        row["story_description"] or "",
            "acceptance_criteria": list(row.get("acceptance_criteria") or []),
        }
        pairs.append((task, story))
    return pairs


def _log_usage(task: dict, project_id: str, duration_ms: int) -> None:
    """Insert a task_usage row. Tokens are 0 in Phase 1 — CLI doesn't expose them."""
    with DB() as db:
        db.execute(
            """
            INSERT INTO task_usage
                (id, task_id, project_id, step, model, stage, task_type,
                 input_tokens, output_tokens, thinking_tokens, duration_ms)
            VALUES (%s, %s, %s, %s, 'claude-code-cli', '4', %s, 0, 0, 0, %s)
            """,
            (
                str(uuid.uuid4()),
                task["id"],
                project_id,
                f"code_generation_{task['task_type']}",
                task["task_type"],
                duration_ms,
            ),
        )
