"""API integration tests: the real routes, real database, fake LLM.

The three LLM steps (summarise, extract, clarify) are replaced with canned
results, so these tests check the plumbing around the models (document rows,
status transitions, retries, dedup, storage) for free and deterministically.
Needs TEST_DATABASE_URL (see conftest.py); skipped otherwise.
"""

import pytest
from fastapi.testclient import TestClient

from pipeline import runner
from pipeline.metrics_common import UsageTotals

SAMPLE = b"Patients must book appointments online.\n\nClinics need SMS reminders 24 hours before."


class FakeLLM:
    """Stands in for the summariser, extractor and clarifier. Can be told to fail."""

    def __init__(self):
        self.fail = False

    async def summarize_all(self, chunks, doc_name=""):
        if self.fail:
            raise RuntimeError("Error code: 401 - Invalid API Key")
        return [f"Summary of chunk {i}" for i in range(len(chunks))], UsageTotals().add(120, 30, 0)

    async def extract_requirements(self, summaries, doc_ids):
        reqs = [
            {"req_type": "functional", "sdlc_topic": "requirements", "title": "Online appointment booking",
             "description": "Patients book appointments online.", "confidence": 0.9, "key_specifics": []},
            {"req_type": "functional", "sdlc_topic": "integrations", "title": "SMS reminders",
             "description": "Send SMS reminders 24 hours before.", "confidence": 0.85,
             "key_specifics": ["24 hours"]},
        ]
        return reqs, UsageTotals().add(400, 200, 0)

    async def generate_clarifications(self, requirements):
        return [{"question": "Which SMS provider?", "context": "Reminders", "priority": "high"}], UsageTotals().add(300, 60, 0)


@pytest.fixture
def fake_llm(monkeypatch):
    fake = FakeLLM()
    monkeypatch.setattr(runner, "summarize_all", fake.summarize_all)
    monkeypatch.setattr(runner, "extract_requirements", fake.extract_requirements)
    monkeypatch.setattr(runner, "generate_clarifications", fake.generate_clarifications)
    monkeypatch.setattr(runner, "write_sdlc_docs", lambda *a, **k: None)  # no files under output/
    return fake


@pytest.fixture
def client(migrated_db):
    from api.main import app

    return TestClient(app)


def _upload(client, project_id, content=SAMPLE, name="sow.txt"):
    # TestClient runs FastAPI background tasks before returning, so the
    # pipeline has finished by the time this call returns.
    return client.post(f"/projects/{project_id}/documents", files={"file": (name, content)})


def test_upload_returns_the_stored_document_id(client, project_id, fake_llm):
    resp = _upload(client, project_id)
    assert resp.status_code == 202
    docs = client.get(f"/projects/{project_id}/documents").json()
    assert [d["id"] for d in docs] == [resp.json()["id"]]
    assert docs[0]["status"] == "done"


def test_successful_ingestion_stores_requirements_and_marks_project_ready(client, project_id, fake_llm):
    _upload(client, project_id)
    status = client.get(f"/projects/{project_id}/status").json()
    assert status["status"] == "ready"
    assert status["requirement_count"] == 2
    assert status["clarification_count"] == 1


def test_failed_upload_then_retry_reuses_row_and_recovers(client, project_id, fake_llm):
    fake_llm.fail = True
    first = _upload(client, project_id).json()
    assert client.get(f"/projects/{project_id}/status").json()["status"] == "failed"

    fake_llm.fail = False
    retry = _upload(client, project_id).json()
    assert retry["id"] == first["id"]

    docs = client.get(f"/projects/{project_id}/documents").json()
    assert len(docs) == 1 and docs[0]["status"] == "done"
    assert client.get(f"/projects/{project_id}/status").json()["status"] == "ready"


def test_reuploading_a_processed_file_skips_the_pipeline(client, project_id, fake_llm):
    first = _upload(client, project_id).json()
    fake_llm.fail = True  # would fail if the pipeline ran again
    again = _upload(client, project_id).json()
    assert again["id"] == first["id"] and again["status"] == "done"


def test_upload_validation(client, project_id, fake_llm):
    assert _upload(client, project_id, name="notes.exe").status_code == 400
    assert _upload(client, "00000000-0000-0000-0000-000000000000").status_code == 404
    assert client.get("/projects/not-a-uuid/docs").status_code == 400
