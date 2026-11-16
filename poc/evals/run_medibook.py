"""Run the MediBook sample docs through Stages 1-4, then build its report.

    python evals/run_medibook.py                 # Stages 1-4 (Stage 4 = first sprint)
    python evals/run_medibook.py --stages 1      # Stage 1 only
    python evals/run_medibook.py --report reports/medibook_groq_full.json

Uses whatever LLM_PROVIDER / DATABASE_URL your environment or poc/.env sets.
Creates a new client + project each run, so earlier runs are left untouched.
On Groq's free tier a full run takes ~25-30 minutes (rate-limit holds).
"""

import argparse
import asyncio
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
POC = os.path.dirname(HERE)
sys.path.insert(0, POC)
sys.path.insert(0, HERE)

DOCS = [
    "BRD_MediBook_Apex_Health.docx",
    "SOW_MediBook_Apex_Health.docx",
    "Technical_Spec_MediBook.pdf",
    "Discovery_Call_Transcript_Dec15.txt",
    "Client_Email_Jan12.txt",
]
DOC_DIR = os.path.join(POC, "..", "sample_client_docs")


async def run(stages: list[int]) -> str:
    from db import DB
    from pipeline.runner import ingest_document

    with DB() as db:
        client = db.fetch_one("INSERT INTO clients (name) VALUES ('Apex Health Group') RETURNING id")
        project = db.fetch_one(
            "INSERT INTO projects (client_id, name) VALUES (%s, 'MediBook') RETURNING id", (client["id"],))
    pid = str(project["id"])
    print(f"project {pid}", flush=True)

    def step(name: str, t0: float, result) -> None:
        print(f"{name} done in {time.perf_counter() - t0:.0f}s: {result}", flush=True)

    if 1 in stages:
        t0 = time.perf_counter()
        for name in DOCS:
            with open(os.path.join(DOC_DIR, name), "rb") as f:
                await ingest_document(pid, str(client["id"]), f.read(), name, name.rsplit(".", 1)[-1])
        step("stage 1", t0, f"{len(DOCS)} documents")
    if 2 in stages:
        from pipeline.stage2_runner import run_stage2
        t0 = time.perf_counter()
        step("stage 2", t0, await run_stage2(pid))
    if 3 in stages:
        from pipeline.stage3_runner import run_stage3
        t0 = time.perf_counter()
        step("stage 3", t0, await run_stage3(pid))
    if 4 in stages:
        from pipeline.stage4_runner import run_stage4
        with DB() as db:
            sprint = db.fetch_one(
                "SELECT id FROM sprints WHERE project_id = %s ORDER BY sprint_number LIMIT 1", (pid,))
        t0 = time.perf_counter()
        step("stage 4 (first sprint)", t0, await run_stage4(pid, sprint_id=str(sprint["id"])))
    return pid


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--stages", nargs="+", type=int, default=[1, 2, 3, 4])
    p.add_argument("--report", default=os.path.join(POC, "reports", "medibook_latest.json"))
    args = p.parse_args()
    project_id = asyncio.run(run(args.stages))

    import json
    from build_report import build

    report = build(project_id)
    os.makedirs(os.path.dirname(args.report), exist_ok=True)
    with open(args.report, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"report written to {args.report}")
    print(json.dumps({k: report[k] for k in ("counts", "checklist_coverage", "totals")}, indent=2, default=str))
