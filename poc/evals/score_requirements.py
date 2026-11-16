"""Score a project's extracted requirements against the MediBook checklist.

    python evals/score_requirements.py <project_id>            # reads the DB
    python evals/score_requirements.py <project_id> --misses   # also list misses

Coverage is keyword-based (see medibook_checklist.json): deterministic and
free, but approximate. Each covered item prints the requirement that matched,
so any match can be checked by hand.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CHECKLIST = os.path.join(HERE, "medibook_checklist.json")


def _text(req: dict) -> str:
    specifics = req.get("key_specifics") or []
    if isinstance(specifics, str):
        specifics = [specifics]
    return " ".join([req.get("title", ""), req.get("description", ""), *specifics]).lower()


def score(requirements: list[dict]) -> dict:
    """Return coverage over all requirements and over active (non-duplicate) ones."""
    items = json.load(open(CHECKLIST))["items"]

    def coverage(reqs: list[dict]) -> dict:
        texts = [(r.get("title", ""), _text(r)) for r in reqs]
        results = []
        for item in items:
            match = next(
                (title for title, text in texts
                 if all(any(term in text for term in group) for group in item["groups"])),
                None,
            )
            results.append({"id": item["id"], "what": item["what"], "covered": match is not None, "matched": match})
        covered = sum(r["covered"] for r in results)
        return {"covered": covered, "total": len(items), "pct": round(100 * covered / len(items), 1), "items": results}

    active = [r for r in requirements if r.get("status", "active") == "active"]
    return {"requirements": len(requirements), "active_requirements": len(active),
            "all": coverage(requirements), "active": coverage(active)}


def main() -> None:
    sys.path.insert(0, os.path.dirname(HERE))
    from db import DB

    project_id = sys.argv[1]
    with DB() as db:
        reqs = db.fetch_all(
            "SELECT title, description, key_specifics, status FROM requirements WHERE project_id = %s",
            (project_id,),
        )
    result = score(reqs)
    act = result["active"]
    print(f"{result['requirements']} requirements ({result['active_requirements']} active)")
    print(f"Checklist coverage (active): {act['covered']}/{act['total']} = {act['pct']}%")
    if "--misses" in sys.argv:
        for item in act["items"]:
            if not item["covered"]:
                print(f"  MISSED {item['id']}: {item['what']}")


if __name__ == "__main__":
    main()
