"""Shared pytest setup.

Unit tests never touch a database. Integration tests (tests/test_api.py) need
a throwaway Postgres given explicitly via TEST_DATABASE_URL and are skipped
otherwise, so `pytest` can never write to your dev database by accident.

    pytest                                   # unit tests only
    TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/stackforge_test pytest
"""

import os
import subprocess
import sys

import pytest

POC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, POC_DIR)

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

# config.py reads DATABASE_URL at import time. Point it at the test database,
# or at a placeholder that unit tests never connect to. Real env vars win over
# poc/.env, so this override always holds.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL or "postgresql://unused:unused@localhost:1/unused"
# Keep tests offline and deterministic regardless of the developer's .env.
os.environ["PINECONE_API_KEY"] = ""
os.environ["LLM_PROVIDER"] = "groq"
os.environ["GROQ_API_KEY"] = "test-key"


@pytest.fixture(scope="session")
def migrated_db():
    """Create the full schema in the test database (idempotent)."""
    if not TEST_DATABASE_URL:
        pytest.skip("set TEST_DATABASE_URL to run integration tests")
    subprocess.run(
        [sys.executable, os.path.join(POC_DIR, "db", "migrate_all.py")],
        check=True,
        capture_output=True,
    )
    return TEST_DATABASE_URL


@pytest.fixture
def project_id(migrated_db):
    """A fresh client + project, deleted (with everything under it) after the test."""
    from db import DB

    with DB() as db:
        client = db.fetch_one("INSERT INTO clients (name) VALUES ('Test Client') RETURNING id")
        project = db.fetch_one(
            "INSERT INTO projects (client_id, name) VALUES (%s, 'Test Project') RETURNING id",
            (client["id"],),
        )
    yield str(project["id"])
    with DB() as db:
        db.execute("DELETE FROM clients WHERE id = %s", (client["id"],))
