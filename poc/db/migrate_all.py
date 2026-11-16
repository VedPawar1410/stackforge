"""Create the full POC schema in one command. Safe to re-run.

Applies db/init.sql, then every stage migration in the order it was added.
Every statement uses IF NOT EXISTS, so running it on an existing database
changes nothing.

    python db/migrate_all.py
"""

import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import DB

HERE = os.path.dirname(os.path.abspath(__file__))

MIGRATIONS = [
    "migrate_stage2.py",
    "migrate_metrics.py",
    "migrate_key_specifics.py",
    "migrate_stage3.py",
    "migrate_stage3_ado.py",
    "migrate_stage4.py",
]


def _statements(sql: str) -> list[str]:
    # Strip "--" comments first: init.sql has semicolons inside comments, which
    # would break a plain split on ";". pg8000 runs one statement per execute.
    no_comments = re.sub(r"--[^\n]*", "", sql)
    return [s.strip() for s in no_comments.split(";") if s.strip()]


def main() -> None:
    with open(os.path.join(HERE, "init.sql")) as f:
        statements = _statements(f.read())
    with DB() as db:
        for stmt in statements:
            db.execute(stmt)
    print(f"init.sql applied ({len(statements)} statements)")

    for script in MIGRATIONS:
        subprocess.run([sys.executable, os.path.join(HERE, script)], check=True, stdout=subprocess.DEVNULL)
        print(f"{script} applied")

    print("Schema ready.")


if __name__ == "__main__":
    main()
