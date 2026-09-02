#!/usr/bin/env python3
"""
legacy_migration.py — Repeat-safe migration to add canonical
(:Question)-[:BELONGS_TO]->(:Course) edges for legacy data that only has:

  (:Course)-[:HAS_QUESTION_MODEL]->(:QuestionModel)-[:HAS_QUESTION]->(:Question)

Usage:
    python backend/migrations/legacy_migration.py --dry-run   # read-only check
    python backend/migrations/legacy_migration.py --apply     # write edges

Notes:
  - Idempotent: questions that already have BELONGS_TO are skipped.
  - Legacy QuestionModel nodes and HAS_QUESTION relationships are NOT deleted.
  - A migrated_from_legacy=true property is set on the new BELONGS_TO edge
    so it can be identified separately if needed.
  - Rollback: MATCH ()-[r:BELONGS_TO]->() WHERE r.migrated_from_legacy = true DELETE r
"""

import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from database import get_neo4j

DRY_RUN = "--dry-run" in sys.argv or "--apply" not in sys.argv

FIND_LEGACY_QUESTIONS = """
MATCH (c:Course)-[:HAS_QUESTION_MODEL]->(qm:QuestionModel)-[:HAS_QUESTION]->(q:Question)
RETURN q.id AS q_id, c.code AS c_code,
       q.document_id AS doc_id,
       q.course_code AS q_code
"""

CHECK_ALREADY_CANONICAL = """
MATCH (q:Question {id: $q_id})-[:BELONGS_TO]->(:Course)
RETURN count(*) AS cnt
"""

ADD_BELONGS_TO = """
MATCH (q:Question {id: $q_id})
MATCH (c:Course {code: $c_code})
MERGE (q)-[r:BELONGS_TO]->(c)
SET r.migrated_from_legacy = true,
    r.migrated_at = $now
"""

SET_MISSING_COURSE_CODE = """
MATCH (q:Question {id: $q_id})
WHERE q.course_code IS NULL OR q.course_code = ''
SET q.course_code = $c_code
"""


def run_migration(driver):
    now_iso = datetime.utcnow().isoformat()

    print("\n=== Step 1: Finding legacy Question nodes ===")
    with driver.session() as session:
        legacy = session.run(FIND_LEGACY_QUESTIONS).data()

    print(f"  Found {len(legacy)} legacy Question nodes linked via QuestionModel.")

    migrated     = 0
    already_canonical = 0
    skipped      = 0
    failed       = 0

    for record in legacy:
        q_id   = record.get("q_id")
        c_code = record.get("c_code") or record.get("q_code")

        if not q_id:
            skipped += 1
            continue

        if not c_code:
            print(f"  SKIP Q id={q_id}: no course code found on Question or Course node.")
            skipped += 1
            continue

        # Check if already canonical
        with driver.session() as session:
            check = session.run(CHECK_ALREADY_CANONICAL, q_id=q_id).single()
            if check and check["cnt"] > 0:
                already_canonical += 1
                continue

        if DRY_RUN:
            print(f"  [DRY-RUN] Would add BELONGS_TO: Q={q_id} -> Course={c_code}")
            migrated += 1
            continue

        try:
            with driver.session() as session:
                session.run(ADD_BELONGS_TO, q_id=q_id, c_code=c_code, now=now_iso)
                # Backfill course_code property if missing
                session.run(SET_MISSING_COURSE_CODE, q_id=q_id, c_code=c_code)
            migrated += 1
        except Exception as e:
            print(f"  FAILED Q id={q_id}: {e}")
            failed += 1

    print("\n=== Migration Summary ===")
    print(f"  {'[DRY-RUN] Would migrate' if DRY_RUN else 'Migrated'}:  {migrated}")
    print(f"  Already canonical:                            {already_canonical}")
    print(f"  Skipped (missing id or code):                 {skipped}")
    print(f"  Failed:                                       {failed}")
    print(f"  Total legacy questions found:                 {len(legacy)}")

    if not DRY_RUN and migrated > 0:
        print(f"\n  NOTE: Legacy QuestionModel nodes and HAS_QUESTION edges are PRESERVED.")
        print(f"  To rollback the added BELONGS_TO edges run:")
        print(f"    MATCH ()-[r:BELONGS_TO]->() WHERE r.migrated_from_legacy = true DELETE r")

    print("\nDone." if not DRY_RUN else "\n[DRY-RUN] No changes made.")


if __name__ == "__main__":
    mode = "DRY-RUN" if DRY_RUN else "APPLY"
    print(f"Legacy Question Migration — {mode} mode")
    print("=" * 50)
    driver = get_neo4j()
    run_migration(driver)
