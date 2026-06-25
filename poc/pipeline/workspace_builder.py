"""
Per-task workspace builder for Stage 4 AI development sessions.

Creates an isolated workspace directory for each task, writes issue_context.md
(used as the Groq user prompt), then delegates Stitch design injection to
workspace_prep.py.
"""

import logging
import os
import shutil

from pipeline.workspace_prep import prep_workspace

_logger = logging.getLogger(__name__)


def build_workspace(
    task: dict,
    story: dict,
    project_id: str,
    project_name: str,
    workspace_root: str,
) -> str:
    """
    Build an isolated workspace directory for a single task.

    Creates:
        {workspace_root}/{task_id}/
            issue_context.md   — task + story context sent to Groq as user prompt
            src/               — empty scaffold; Groq writes files here

    Then calls prep_workspace() to optionally inject Stitch design assets.

    Returns the absolute workspace path.
    """
    task_id = str(task["id"])
    workspace_path = os.path.join(workspace_root, task_id)

    # Start fresh — remove any leftover workspace from a previous run
    if os.path.exists(workspace_path):
        shutil.rmtree(workspace_path)

    os.makedirs(os.path.join(workspace_path, "src"), exist_ok=True)

    _write_issue_context(task, story, project_name, workspace_path)

    # Inject Stitch designs if available — non-fatal if not present
    try:
        injected = prep_workspace(project_id, workspace_path)
        if injected:
            _logger.info("Stitch designs injected for task %s", task_id)
    except Exception as exc:
        _logger.warning("Stitch injection skipped for task %s: %s", task_id, exc)

    _logger.info("Workspace ready: %s", workspace_path)
    return workspace_path


def _write_issue_context(
    task: dict, story: dict, project_name: str, workspace_path: str
) -> None:
    """Write issue_context.md — this becomes the user prompt sent to Groq."""
    acs = story.get("acceptance_criteria", [])
    ac_lines = "\n".join(f"- {ac}" for ac in acs) if acs else "- (none specified)"

    content = f"""# Task: {task["title"]}

## Project
{project_name}

## Description
{task["description"]}

## Task Type
{task.get("task_type", "backend")}

## Estimated Hours
{task.get("estimated_hours", 0)}h

---

## Parent Story: {story["title"]}

{story.get("description", "")}

## Acceptance Criteria
{ac_lines}
"""
    with open(os.path.join(workspace_path, "issue_context.md"), "w", encoding="utf-8") as f:
        f.write(content)
