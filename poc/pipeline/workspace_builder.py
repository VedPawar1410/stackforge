"""
Per-task workspace builder for Stage 4 AI development sessions.

Creates an isolated workspace directory for each task, writes CLAUDE.md and
issue_context.md, then delegates Stitch design injection to workspace_prep.py.
"""

import logging
import os
import shutil

from pipeline.workspace_prep import prep_workspace

_logger = logging.getLogger(__name__)

# Maps task_type → file convention guidance written into CLAUDE.md
_FILE_CONVENTIONS: dict[str, str] = {
    "backend":       "Python files in src/api/ or src/services/. Use FastAPI patterns.",
    "frontend":      "React/JSX components in src/components/, or Streamlit widgets in src/ui/.",
    "testing":       "pytest files in src/tests/ with test_ prefix. Use AAA structure.",
    "devops":        "Dockerfile, GitHub Actions .yml, and deployment configs in project root.",
    "design":        "CSS/SCSS design tokens and component styles in src/styles/.",
    "documentation": "Markdown files in docs/. Clear headings, concise prose.",
}

_DEFAULT_CONVENTION = "Create files in the src/ directory using project-appropriate conventions."


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
            .claude/CLAUDE.md      — task instructions for Claude Code
            issue_context.md       — full context (populated before Stitch injection)
            src/                   — empty scaffold for generated code

    Then calls prep_workspace() to optionally inject Stitch design assets.

    Returns the absolute workspace path.
    """
    task_id = str(task["id"])
    workspace_path = os.path.join(workspace_root, task_id)

    # Start fresh — remove any leftover workspace from a previous run
    if os.path.exists(workspace_path):
        shutil.rmtree(workspace_path)

    os.makedirs(os.path.join(workspace_path, "src"), exist_ok=True)
    os.makedirs(os.path.join(workspace_path, ".claude"), exist_ok=True)

    _write_claude_md(task, story, project_name, workspace_path)
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


def _write_claude_md(task: dict, story: dict, project_name: str, workspace_path: str) -> None:
    task_type = task.get("task_type", "backend")
    convention = _FILE_CONVENTIONS.get(task_type, _DEFAULT_CONVENTION)

    content = f"""# {project_name} — Task Workspace

## Your Task
**{task["title"]}**

Read `issue_context.md` for the full requirements before writing any code.

## File Conventions
{convention}

## Rules
- Implement only what is described in `issue_context.md`. Do not add extra features.
- Write clean, well-named code. No debug print statements.
- Handle errors explicitly — never swallow exceptions silently.
- After implementing, write a brief summary of what you built and which files you created.

## Project
{project_name}
"""
    with open(os.path.join(workspace_path, ".claude", "CLAUDE.md"), "w", encoding="utf-8") as f:
        f.write(content)


def _write_issue_context(
    task: dict, story: dict, project_name: str, workspace_path: str
) -> None:
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
    # Write to workspace root — workspace_prep.py will append the design section if available
    with open(os.path.join(workspace_path, "issue_context.md"), "w", encoding="utf-8") as f:
        f.write(content)
