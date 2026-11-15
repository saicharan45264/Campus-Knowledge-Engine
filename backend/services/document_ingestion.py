"""backend/services/document_ingestion.py — Unified ingestion coordinator.

Coordinates parsing, relational mapping, vector embedding, and graph storage
across all 8 supported institutional document types:
- syllabus: Curriculum structure, course outcomes, topics, and prerequisite DAG
- pyq: Past exam question papers with diagram cropping and BTL mapping
- academic_calendar: Semester dates, working Saturdays, holidays, and milestones
- timetable: Class weekly schedule, slot mappings, and faculty venues
- regulations: B.Tech academic ordinances, attendance rules, and degree criteria
- exam_schedule: Mid-term and end-semester examination time tables
- seating_arrangement: Hall allocations indexed by student register number ranges
- lab_timetable: Lab batch schedules, vacant slots, and lab faculty allocations
"""
import asyncio
import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import (
    AcademicDocumentRecord,
    AcademicEvent,
    CalendarDay,
    CalendarDayCohort,
    ClauseReference,
    Document,
    DocumentChunk,
    ElectiveOption,
    RegulationClause,
    TimetableVersion,
    WeeklySession,
)
from backend.parsers.academic_documents_parser import parse_academic_document
from backend.parsers.calendar_parser import parse_academic_calendar_pdf
from backend.parsers.regulations_parser import parse_regulations_pdf
from backend.parsers.timetable_parser import parse_timetable_pdf
from backend.services.embedding_service import embed_texts, format_doc_embedding_text


async def ingest_regulations_document(
    doc_id: uuid.UUID,
    file_path: str,
    db: AsyncSession,
) -> dict[str, Any]:
    """
    Ingests an academic regulations PDF:
    - Extracts 90+ clauses with parent hierarchy
    - Stores into regulation_clauses and clause_references
    - Generates searchable, grounded chunks into document_chunks
    """
    # A retry or admin-triggered reprocess replaces this document's old index.
    await db.execute(delete(RegulationClause).where(RegulationClause.document_id == doc_id))
    await db.execute(delete(ClauseReference).where(ClauseReference.document_id == doc_id))
    await db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == doc_id))
    res = await asyncio.to_thread(parse_regulations_pdf, file_path)
    clauses = res["clauses"]
    references = res["references"]

    # 1. Store RegulationClause records
    clause_objs = []
    chunk_objs = []

    texts = []
    for c in clauses:
        clause_obj = RegulationClause(
            document_id=doc_id,
            clause_id=c["clause_id"],
            parent_clause_id=c.get("parent_clause_id"),
            title=c.get("title"),
            clause_text=c["clause_text"],
            page_number=c.get("page_number"),
        )
        clause_objs.append(clause_obj)

        # Build retrievable contextual chunk
        title_str = f" - {c['title']}" if c.get("title") else ""
        parent_str = f" (Parent: {c['parent_clause_id']})" if c.get("parent_clause_id") else ""
        chunk_content = (
            f"[Academic Regulations 2023] Clause {c['clause_id']}{title_str}{parent_str} (Page {c['page_number']}):\n"
            f"{c['clause_text']}"
        )

        texts.append(chunk_content)

    vectors = await embed_texts(
        [format_doc_embedding_text(content, title="Academic Regulations") for content in texts],
        batch_size=24,
    ) if texts else []
    for content, vector in zip(texts, vectors):
        chunk_objs.append(DocumentChunk(
            document_id=doc_id,
            content=content,
            course_code="REGULATIONS",
            content_type="clause",
            embedding=vector or None,
        ))

    db.add_all(clause_objs)
    db.add_all(chunk_objs)

    # 2. Store references
    ref_objs = [
        ClauseReference(
            document_id=doc_id,
            from_clause_id=r["from_clause_id"],
            to_clause_id=r["to_clause_id"],
        )
        for r in references
    ]
    db.add_all(ref_objs)
    await db.commit()

    return {
        "status": "success",
        "doc_type": "regulations",
        "clauses_stored": len(clause_objs),
        "chunks_indexed": len(chunk_objs),
        "references_stored": len(ref_objs),
    }


async def ingest_calendar_document(
    doc_id: uuid.UUID,
    file_path: str,
    db: AsyncSession,
) -> dict[str, Any]:
    """
    Ingests an academic calendar PDF:
    - Extracts 395 days and 22 timetable overrides
    - Stores into calendar_days, calendar_day_cohorts, academic_events
    - Embeds monthly summary chunks into document_chunks
    """
    await db.execute(delete(CalendarDay).where(CalendarDay.document_id == doc_id))
    await db.execute(delete(AcademicEvent).where(AcademicEvent.document_id == doc_id))
    await db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == doc_id))
    res = await asyncio.to_thread(parse_academic_calendar_pdf, file_path)
    days = res["days"]
    events = res["events"]
    ay = res["academic_year"]
    # Persist the parsed AY on the upload record so queries and admin views can
    # distinguish multiple calendar versions. The selected doc_type remains the
    # admin's source of truth.
    doc_result = await db.execute(select(Document).where(Document.id == doc_id))
    source_doc = doc_result.scalar_one_or_none()
    if source_doc:
        source_doc.year = ay

    day_objs = []
    cohort_objs = []
    chunk_objs = []

    for d in days:
        dt = datetime.fromisoformat(d["calendar_date"])
        day_id = uuid.uuid4()
        day_obj = CalendarDay(
            id=day_id,
            document_id=doc_id,
            calendar_date=dt,
            day_of_week=d["day_of_week"],
            status=d["status"],
            particulars=d.get("particulars"),
            timetable_override=d.get("timetable_override"),
            academic_year=ay,
            month_name=d.get("month_name"),
        )
        day_objs.append(day_obj)

        for cohort_name, cd_num in d.get("cohort_class_days", {}).items():
            cohort_objs.append(
                CalendarDayCohort(
                    id=uuid.uuid4(),
                    calendar_day_id=day_id,
                    cohort=cohort_name,
                    class_day_number=cd_num,
                    is_instruction_day=True,
                )
            )

    db.add_all(day_objs)
    db.add_all(cohort_objs)

    # Store events
    event_objs = [
        AcademicEvent(
            document_id=doc_id,
            name=e["name"],
            start_date=datetime.fromisoformat(e["start_date"]),
            end_date=datetime.fromisoformat(e["end_date"]) if e.get("end_date") else None,
            event_category=e["event_category"],
            cohort_applicability=e.get("cohort_applicability", "ALL"),
        )
        for e in events
    ]
    db.add_all(event_objs)

    # Create searchable chunks for events and overrides
    events_summary = "\n".join([f"- {e['start_date']}: {e['name']}" for e in events[:40]])
    overrides = [d for d in days if d.get("timetable_override")]
    overrides_summary = "\n".join([
        f"- {d['calendar_date']} ({d['day_of_week']}): {d['timetable_override']}'s Timetable applies (Notes: {d.get('particulars')})"
        for d in overrides
    ])

    cal_chunk_text = (
        f"[Academic Calendar AY {ay}] Key Milestones, Exam Periods and Holidays:\n{events_summary}\n\n"
        f"Timetable Day Substitutions / Overrides:\n{overrides_summary}"
    )
    vectors = await embed_texts(
        [format_doc_embedding_text(cal_chunk_text, title=f"Academic Calendar AY {ay}")],
        batch_size=1,
    )
    vec = vectors[0] if vectors else None
    chunk_objs.append(
        DocumentChunk(
            document_id=doc_id,
            content=cal_chunk_text,
            course_code="CALENDAR",
            content_type="calendar",
            embedding=vec if vec else None,
        )
    )
    db.add_all(chunk_objs)
    await db.commit()

    return {
        "status": "success",
        "doc_type": "academic_calendar",
        "academic_year": ay,
        "days_stored": len(day_objs),
        "cohort_mappings": len(cohort_objs),
        "events_stored": len(event_objs),
    }


async def ingest_academic_document(
    doc_id: uuid.UUID,
    file_path: str,
    doc_type: str,
    db: AsyncSession,
) -> dict[str, Any]:
    """Parse and index a scoped lab, seating, or exam schedule PDF."""
    source_result = await db.execute(select(Document).where(Document.id == doc_id))
    source_doc = source_result.scalar_one_or_none()
    if not source_doc or not source_doc.department or not source_doc.semester:
        raise ValueError("Academic schedule uploads must include department and semester scope.")
    parsed = await asyncio.to_thread(parse_academic_document, file_path, doc_type, source_doc.department, source_doc.semester)
    # Keep reprocessing idempotent and remove stale vectors/records before writing.
    await db.execute(delete(AcademicDocumentRecord).where(AcademicDocumentRecord.document_id == doc_id))
    await db.execute(
        delete(DocumentChunk).where(
            DocumentChunk.document_id == doc_id,
            DocumentChunk.content_type == f"academic_{doc_type}",
        )
    )
    db.add_all([
        AcademicDocumentRecord(
            document_id=doc_id,
            record_type=doc_type,
            page_number=item["page"],
            line_number=item["line"],
            record_text=item["text"],
            payload_json=json.dumps(item.get("payload") or {}, ensure_ascii=False),
        )
        for item in parsed["records"]
    ])
    # Structure-aware chunks preserve the row/line data while adding overlapping
    # context for semantic retrieval (e.g. date headers adjacent to course rows).
    chunks = []
    chunk_texts = []
    page_groups: dict[int, list[dict[str, Any]]] = {}
    for item in parsed["records"]:
        page_groups.setdefault(int(item["page"]), []).append(item)
    for page_number, page_records in page_groups.items():
        lines = [
            f"Page {page_number}, line {item['line']}: {item['text']}"
            for item in page_records
        ]
        start = 0
        while start < len(lines):
            end = start
            parts = []
            size = 0
            while end < len(lines) and (size < 1000 or not parts):
                parts.append(lines[end])
                size += len(lines[end]) + 1
                end += 1
            content = (
                f"[Academic {doc_type.replace('_', ' ')} | {source_doc.department} "
                f"Semester {source_doc.semester} | {source_doc.filename}]\n"
                + "\n".join(parts)
            )
            chunk_texts.append(content)
            if end >= len(lines):
                break
            # Two-line overlap protects table row relationships across boundaries.
            start = max(start + 1, end - 2)
    vectors = await embed_texts(
        [format_doc_embedding_text(content, title=f"{source_doc.department} {doc_type}") for content in chunk_texts],
        batch_size=24,
    ) if chunk_texts else []
    chunks = [
        DocumentChunk(
            document_id=doc_id,
            content=content,
            course_code=source_doc.department,
            content_type=f"academic_{doc_type}",
            embedding=vector or None,
        )
        for content, vector in zip(chunk_texts, vectors)
    ]
    db.add_all(chunks)
    await db.commit()
    return {
        "status": "success", "doc_type": doc_type,
        "department": source_doc.department, "semester": source_doc.semester,
        "records_indexed": len(parsed["records"]), "summary": parsed["summary"],
        "chunks_indexed": len(chunks),
        "embedded_chunks": sum(1 for chunk in chunks if chunk.embedding),
    }


async def ingest_timetable_document(
    doc_id: uuid.UUID,
    file_path: str,
    db: AsyncSession,
) -> dict[str, Any]:
    """
    Ingests a class timetable PDF:
    - Extracts weekly sessions across Monday-Friday
    - Stores into timetable_versions, weekly_sessions, elective_options
    - Generates searchable summary chunks in document_chunks
    """
    res = await asyncio.to_thread(parse_timetable_pdf, file_path)
    meta = res["metadata"]
    # Apply the scope selected by the admin during upload, even when PDF text
    # extraction misses or misreads its heading.
    doc_result = await db.execute(select(Document).where(Document.id == doc_id))
    source_doc = doc_result.scalar_one_or_none()
    if not source_doc:
        raise ValueError("The uploaded timetable document metadata could not be loaded.")
    if (
        not source_doc.department
        or not source_doc.year
        or not source_doc.semester
        or not source_doc.section
    ):
        raise ValueError("The timetable is missing admin-selected department, academic year, semester, or class/section metadata.")

    # Admin-selected scope is canonical. The PDF parser may still contribute
    # descriptive fields such as building and advisors, but never its scope.
    meta["department"] = source_doc.department
    meta["academic_year"] = source_doc.year
    meta["semester"] = source_doc.semester
    meta["section"] = source_doc.section
    sessions = res["weekly_sessions"]
    offerings = res["elective_options"]

    # Reprocessing should replace the parsed version, not duplicate sessions.
    await db.execute(delete(TimetableVersion).where(TimetableVersion.document_id == doc_id))
    await db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == doc_id))

    tt_version = TimetableVersion(
        document_id=doc_id,
        department=meta.get("department", "CSE"),
        semester=meta.get("semester", "VII"),
        section=meta.get("section", "F"),
        program=meta.get("program", "B.Tech CSE"),
        building=meta.get("building", "AB III"),
        academic_year=meta.get("academic_year", "2026-2027"),
        class_advisors=meta.get("class_advisors"),
    )
    db.add(tt_version)
    await db.flush()

    session_objs = [
        WeeklySession(
            timetable_id=tt_version.id,
            day_of_week=s["day_of_week"],
            slot_numbers=s["slot_numbers"],
            start_time=s["start_time"],
            end_time=s["end_time"],
            activity_label=s["activity_label"],
            course_code=s.get("course_code"),
            elective_bucket=s.get("elective_bucket"),
            group_label=s.get("group_label"),
            faculty=s.get("faculty"),
            venue=s.get("venue"),
        )
        for s in sessions
    ]
    db.add_all(session_objs)

    offering_objs = [
        ElectiveOption(
            timetable_id=tt_version.id,
            elective_bucket=o["elective_bucket"],
            course_code=o["course_code"],
            course_name=o["course_name"],
            faculty=o.get("faculty"),
            venue=o.get("venue"),
            group_label=o.get("group_label"),
        )
        for o in offerings
    ]
    db.add_all(offering_objs)

    # Searchable summary chunk
    sessions_text = "\n".join([
        f"- {s['day_of_week']} ({s['start_time']} - {s['end_time']}): {s['activity_label']}"
        + (f" [{s['course_code']}]" if s.get('course_code') else "")
        + (f" in {s['venue']}" if s.get('venue') else "")
        for s in sessions
    ])
    chunk_content = (
        f"[Timetable {meta.get('department')} Semester {meta.get('semester')} Section {meta.get('section')}] "
        f"Weekly Schedule ({meta.get('building')}):\n{sessions_text}"
    )
    vectors = await embed_texts(
        [format_doc_embedding_text(chunk_content, title=f"{meta.get('department')} timetable")],
        batch_size=1,
    )
    vec = vectors[0] if vectors else None
    db.add(
        DocumentChunk(
            document_id=doc_id,
            content=chunk_content,
            course_code=f"{meta.get('department')}_{meta.get('semester')}_{meta.get('section')}",
            content_type="timetable",
            embedding=vec if vec else None,
        )
    )

    await db.commit()

    return {
        "status": "success",
        "doc_type": "timetable",
        "timetable_id": str(tt_version.id),
        "sessions_stored": len(session_objs),
        "offerings_stored": len(offering_objs),
    }
