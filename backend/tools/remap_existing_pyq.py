"""Re-run retrieval-only topic mapping for existing PYQ nodes.

Run from the repository root after the configured embedding endpoint is
reachable:
    venv/bin/python backend/tools/remap_existing_pyq.py --course-code 23CSE213

Questions are processed per source document so their document IDs and dashboard
reports remain intact. The script aborts before writing if endpoint preflight
fails; it does not re-extract or duplicate question nodes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from collections import defaultdict
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from database import AsyncSessionLocal, ProcessingReport, engine, get_neo4j  # noqa: E402
from utils import get_embedding, map_pyq_structured_to_kg  # noqa: E402
from sqlalchemy import select  # noqa: E402


async def preflight() -> None:
    vector = await get_embedding("PYQ topic mapping connectivity check")
    if not vector:
        raise RuntimeError("Embedding endpoint did not return a vector; no graph data was changed.")


def load_questions(course_code: str) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    driver = get_neo4j()
    with driver.session() as session:
        records = session.run(
            """
            MATCH (q:Question {course_code: $course_code})
            RETURN q.id AS id, q.document_id AS document_id,
                   q.text AS question_text, q.question_number AS question_number,
                   q.btl AS btl_tag, q.marks AS marks, q.has_figure AS has_figure,
                   q.image_url AS image_url,
                   q.subpart_label AS subpart_label, q.subpart_text AS subpart_text,
                   q.original_text AS original_text, q.page_number AS page_number,
                   q.source_y AS source_y, q.parent_question_id AS parent_question_id
            ORDER BY q.document_id, q.page_number, q.question_number, q.subpart_label
            """,
            course_code=course_code,
        ).data()
    for record in records:
        doc_id = str(record.get("document_id") or "")
        if not doc_id:
            continue
        grouped[doc_id].append({
            **{
                key: value for key, value in record.items()
                if key not in {"document_id"} and value is not None
            },
            "question_text": record.get("question_text") or "",
            "btl_tag": record.get("btl_tag") or "",
            "marks": record.get("marks") or "",
            "has_figure": bool(record.get("has_figure")),
            "co_tag": None,
        })
    return grouped


def list_pyq_courses() -> list[str]:
    driver = get_neo4j()
    with driver.session() as session:
        records = session.run(
            "MATCH (q:Question) WHERE q.course_code IS NOT NULL "
            "RETURN DISTINCT q.course_code AS course_code ORDER BY course_code"
        ).data()
    return [str(row["course_code"]).strip().upper() for row in records if row.get("course_code")]
async def update_report(document_id: str, course_code: str, stats: dict) -> None:
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(ProcessingReport)
            .where(ProcessingReport.document_id == uuid.UUID(document_id))
            .order_by(ProcessingReport.created_at.desc())
        )).scalars().all()
        if rows:
            report = rows[0]
            try:
                payload = json.loads(report.report_json or "{}")
            except json.JSONDecodeError:
                payload = {}
        else:
            report = ProcessingReport(document_id=uuid.UUID(document_id), course_code=course_code)
            payload = {"type": "pyq", "course_code": course_code}
            db.add(report)

        mapped = int(stats.get("mapped", 0))
        total = int(stats.get("total", 0))
        unmapped = int(stats.get("unmapped", max(0, total - mapped)))
        payload.update({
            "detected": total,
            "structured": total,
            "mapped": mapped,
            "suggested": int(stats.get("suggested", 0)),
            "unmapped": unmapped,
            "mapping_failed": int(stats.get("mapping_failed", 0)),
            "mapping_method": "hybrid_retrieval_provisional_v8",
            "suggestion_threshold": 0.50,
            "status": "success_structured" if total and not unmapped else "partial_success",
        })
        payload.pop("mapping_error", None)
        payload.pop("review_required", None)
        report.report_json = json.dumps(payload)
        report.course_code = course_code
        await db.commit()


async def remap(course_codes: list[str]) -> None:
    await preflight()
    driver = get_neo4j()
    try:
        for course_code in course_codes:
            grouped = load_questions(course_code)
            if not grouped:
                print(f"No existing question nodes found for {course_code}.")
                continue

            for document_id, questions in grouped.items():
                stats = await map_pyq_structured_to_kg(
                    driver,
                    questions,
                    course_code=course_code,
                    document_id=document_id,
                )
                await update_report(document_id, course_code, stats)
                print(
                    f"{course_code} / {document_id}: {stats.get('mapped', 0)} confirmed; "
                    f"{stats.get('suggested', 0)} with provisional suggestions; "
                    f"{stats.get('unmapped', 0)} unassigned."
                )
    finally:
        driver.close()
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--course-code", action="append", help="Course code; repeat for multiple courses")
    selection.add_argument("--all-courses", action="store_true", help="Remap every course with existing PYQ questions")
    args = parser.parse_args()
    codes = list_pyq_courses() if args.all_courses else [code.strip().upper() for code in args.course_code]
    asyncio.run(remap(codes))


if __name__ == "__main__":
    main()
