"""Apply Stage 4 schema migration (task_sessions, task_usage tables + stage4_status on projects)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from db import DB

SQL_PATH = os.path.join(os.path.dirname(__file__), "stage4.sql")

with open(SQL_PATH) as f:
    sql = f.read()


def _has_sql(stmt: str) -> bool:
    """Return True if stmt contains at least one non-comment, non-empty line."""
    for line in stmt.splitlines():
        line = line.strip()
        if line and not line.startswith("--"):
            return True
    return False


# Execute each statement separately — pg8000 does not support multi-statement strings.
statements = [s.strip() for s in sql.split(";") if _has_sql(s)]

with DB() as db:
    for stmt in statements:
        db.execute(stmt)

print("Stage 4 migration applied successfully.")
print("  Tables created: task_sessions, task_usage")
print("  Column added:   projects.stage4_status")
