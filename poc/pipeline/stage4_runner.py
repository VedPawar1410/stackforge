"""
Stage 4 orchestrator: AI-Assisted Development.

Groq-based code generation — replaces the previous Claude CLI subprocess approach.

Flow per task:
  1. Build isolated workspace (workspace_builder.py writes issue_context.md)
  2. Read issue_context.md as the user prompt for Groq
  3. Call Groq (MODEL_CAPABLE) → structured JSON with {files, summary}
  4. Write generated files to workspace
  5. Log to task_sessions + task_usage tables with real token counts
  6. Update tasks.status = 'done'

Throttle: Semaphore(STAGE4_MAX_PARALLEL) + STAGE4_THROTTLE_DELAY_S post-call hold.
Free-tier Groq: 8K TPM. Each call ≈ 2800 tokens. Semaphore(1) + 30s → ~5600 TPM.
"""

import asyncio
import json
import logging
import os
import random
import time
import uuid
from datetime import datetime, timezone

from openai import BadRequestError, RateLimitError

from config import (
    LLM_PROVIDER,
    MODEL_CAPABLE,
    OLLAMA_NUM_CTX,
    REASONING_LOW,
    STAGE4_MAX_PARALLEL,
    STAGE4_THROTTLE_DELAY_S,
    STAGE4_WORKSPACE_ROOT,
)
from db import DB
from pipeline.llm_utils import extract_usage, get_llm_client
from pipeline.workspace_builder import build_workspace

_logger = logging.getLogger(__name__)

_client = get_llm_client()

_MAX_RETRIES = 6

_CODE_GEN_SYSTEM_PROMPT = """\
You are a senior software engineer implementing development tasks.
Given a task specification, generate all necessary source code files.

Return ONLY valid JSON — no explanation, no prose outside the JSON.
Schema:
{
  "files": [
    {
      "path": "src/api/users.py",
      "content": "# complete file content here"
    }
  ],
  "summary": "1-2 sentences: what was built and which files were created"
}

Rules:
- Generate real, runnable code — not stubs or pseudocode
- Each file must be complete with proper imports and error handling
- Follow task_type file conventions:
  backend       → src/api/ or src/services/ — FastAPI or plain Python patterns
  frontend      → src/components/ (React/JSX) or src/ui/ (Streamlit)
  testing       → src/tests/ with test_ prefix, pytest AAA structure
  devops        → project root — Dockerfile, GitHub Actions .yml, deploy configs
  design        → src/styles/ — CSS/SCSS with design tokens
  documentation → docs/ — Markdown with clear headings
- Include type annotations on all function signatures
- Maximum 4 files per task (stay within output token budget)
- summary field: exactly 1-2 sentences
"""


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
    """Run a single task: build workspace → call Groq → write files → record results."""
    task_id = str(task["id"])
    session_id = str(uuid.uuid4())
    workspace_path = os.path.join(STAGE4_WORKSPACE_ROOT, task_id)

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

        summary, generated_files, in_tok, out_tok, think_tok, duration_ms = (
            await _generate_code_with_groq(task, story, workspace_path, sem)
        )

        now = datetime.now(timezone.utc)
        with DB() as db:
            db.execute(
                """
                UPDATE task_sessions
                SET status = 'completed',
                    session_summary = %s,
                    generated_files = %s,
                    completed_at = %s,
                    exit_code = 0
                WHERE id = %s
                """,
                (
                    summary,
                    json.dumps(generated_files),
                    now,
                    session_id,
                ),
            )
            db.execute(
                "UPDATE tasks SET status = 'done' WHERE id = %s",
                (task_id,),
            )

        _log_usage(task, project_id, duration_ms, in_tok, out_tok, think_tok)

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


async def _generate_code_with_groq(
    task: dict,
    story: dict,
    workspace_path: str,
    semaphore: asyncio.Semaphore,
) -> tuple[str, list[dict], int, int, int, int]:
    """
    Call Groq to generate code files for a task.

    Reads issue_context.md from the workspace as the user prompt (built by
    workspace_builder.py). Returns the Groq summary, list of written files,
    and token counts for usage tracking.

    Returns (summary, generated_files, input_tokens, output_tokens, thinking_tokens, duration_ms).
    """
    context_path = os.path.join(workspace_path, "issue_context.md")
    with open(context_path, encoding="utf-8") as f:
        issue_context = f.read()

    extra_body = dict(REASONING_LOW)
    if LLM_PROVIDER == "ollama":
        extra_body["options"] = {"num_ctx": OLLAMA_NUM_CTX}

    for attempt in range(_MAX_RETRIES):
        try:
            async with semaphore:
                t0 = time.monotonic()
                response = await _client.chat.completions.create(
                    model=MODEL_CAPABLE,
                    max_tokens=2500,
                    response_format={"type": "json_object"},
                    extra_body=extra_body,
                    messages=[
                        {"role": "system", "content": _CODE_GEN_SYSTEM_PROMPT},
                        {"role": "user", "content": issue_context},
                    ],
                )
                duration_ms = int((time.monotonic() - t0) * 1000)
                # Hold the slot after the call so concurrent waiters don't burst TPM.
                await asyncio.sleep(STAGE4_THROTTLE_DELAY_S)

            in_tok, out_tok, think_tok = extract_usage(response)
            raw = response.choices[0].message.content or "{}"

            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = {}

            files_spec = parsed.get("files", [])
            summary = parsed.get("summary", "Code generation completed.")

            generated_files = _write_files(workspace_path, files_spec)

            return summary, generated_files, in_tok, out_tok, think_tok, duration_ms

        except RateLimitError:
            if attempt == _MAX_RETRIES - 1:
                raise
            wait = min(60.0, (2 ** attempt) * 5) + random.uniform(0, 2)
            _logger.warning(
                "Groq rate limit hit for task %s (attempt %d/%d), waiting %.1fs",
                task.get("id", "?"), attempt + 1, _MAX_RETRIES, wait,
            )
            await asyncio.sleep(wait)
        except BadRequestError as exc:
            # gpt-oss reasoning model: Groq rejects truncated/non-JSON output
            # server-side before it's parseable. Retry — usually transient.
            if attempt == _MAX_RETRIES - 1:
                raise
            wait = (2 ** attempt) + random.uniform(0, 1)
            _logger.warning(
                "Groq rejected model output as invalid JSON for task %s "
                "(attempt %d/%d): %s", task.get("id", "?"), attempt + 1, _MAX_RETRIES, exc,
            )
            await asyncio.sleep(wait)

    return "Generation failed after retries.", [], 0, 0, 0, 0


def _write_files(workspace_path: str, files_spec: list[dict]) -> list[dict]:
    """
    Write the files returned by Groq to the workspace directory.
    Skips entries with empty path or content.
    Returns list of {path, size_bytes} for each file written.
    """
    written = []
    for entry in files_spec:
        if not isinstance(entry, dict):
            continue
        rel_path = entry.get("path", "").strip().lstrip("/")
        content = entry.get("content", "")
        if not rel_path or not content:
            continue

        full_path = os.path.join(workspace_path, rel_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)
        written.append({"path": rel_path, "size_bytes": len(content.encode("utf-8"))})

    return written


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
            "id":              str(row["id"]),
            "story_id":        str(row["story_id"]),
            "title":           row["title"],
            "description":     row["description"] or "",
            "task_type":       row["task_type"] or "backend",
            "estimated_hours": float(row["estimated_hours"] or 4.0),
            "status":          row["status"] or "todo",
        }
        story = {
            "id":                  str(row["story_id"]),
            "title":               row["story_title"],
            "description":         row["story_description"] or "",
            "acceptance_criteria": list(row.get("acceptance_criteria") or []),
        }
        pairs.append((task, story))
    return pairs


def _log_usage(
    task: dict,
    project_id: str,
    duration_ms: int,
    in_tok: int = 0,
    out_tok: int = 0,
    think_tok: int = 0,
) -> None:
    """Insert a task_usage row with real token counts from the Groq API response."""
    with DB() as db:
        db.execute(
            """
            INSERT INTO task_usage
                (id, task_id, project_id, step, model, stage, task_type,
                 input_tokens, output_tokens, thinking_tokens, duration_ms)
            VALUES (%s, %s, %s, %s, %s, '4', %s, %s, %s, %s, %s)
            """,
            (
                str(uuid.uuid4()),
                task["id"],
                project_id,
                f"code_generation_{task['task_type']}",
                MODEL_CAPABLE,
                task["task_type"],
                in_tok,
                out_tok,
                think_tok,
                duration_ms,
            ),
        )
