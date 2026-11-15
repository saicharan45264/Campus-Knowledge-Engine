"""backend/api/ingestion.py — Admin Curriculum & Examination Ingestion Endpoints.

Handles bulk PDF uploading, automatic magic-byte security validation,
asynchronous background ingestion (Syllabus & PYQ), document listing,
status polling, and administrative database purging.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import shutil
import uuid
from pathlib import Path

logger = logging.getLogger("mach_ai.ingestion")

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
)
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth import require_admin
from backend.config import settings
from backend.core.semantic_cache import semantic_cache
from backend.database import (
    AsyncSessionLocal,
    Document,
    DocumentChunk,
    ProcessingReport,
    drop_neo4j_schema,
    ensure_neo4j_schema,
    get_db,
    get_neo4j,
)
from backend.parsers.pyq_parser import extract_pyq_structured
from backend.parsers.syllabus_parser import SyllabusParser
from backend.parsers.visual_cropper import (
    analyze_diagram_structured,
    describe_uploaded_image,
    process_pyq_visuals,
)
from backend.services.document_ingestion import (
    ingest_academic_document,
    ingest_calendar_document,
    ingest_regulations_document,
    ingest_timetable_document,
)
from backend.services.embedding_service import (
    embed_multimodal,
    embed_texts,
    format_doc_embedding_text,
)
from backend.services.graph_service import map_pyq_structured_to_kg
from backend.services.storage_service import (
    delete_all_images_from_cloudinary,
    delete_document_images_from_cloudinary,
    upload_question_image_to_cloudinary,
)

router = APIRouter(tags=["Document Ingestion"])

SUPPORTED_DOCUMENT_TYPES = {
    "syllabus",
    "pyq",
    "academic_calendar",
    "regulations",
    "timetable",
    "exam_schedule",
    "seating_arrangement",
    "lab_timetable",
}

_ROMAN_SEMESTERS = {1: "I", 2: "II", 3: "III", 4: "IV", 5: "V", 6: "VI", 7: "VII", 8: "VIII"}


def _normalize_semester(value: str | None) -> str | None:
    raw = (value or "").strip().upper()
    if not raw:
        return None
    raw = re.sub(r"^SEM(?:ESTER)?\s*", "", raw)
    if raw.isdigit() and 1 <= int(raw) <= 8:
        return _ROMAN_SEMESTERS[int(raw)]
    if raw in _ROMAN_SEMESTERS.values():
        return raw
    return None


def _validate_upload_scope(
    doc_type: str,
    department: str | None,
    year: str | None,
    course_code: str | None,
    course_name: str | None,
    semester: str | None,
    section: str | None,
) -> None:
    """Validate admin-provided document scope before saving any upload."""
    required: dict[str, tuple[str | None, ...]] = {
        "syllabus": (department, year),
        "pyq": (department, course_code),
        "timetable": (department, year, semester, section),
        "exam_schedule": (department, semester),
        "seating_arrangement": (department, semester),
        "lab_timetable": (department, semester),
        "academic_calendar": (),
        "regulations": (),
    }
    labels = {
        "syllabus": "department and academic year",
        "pyq": "department and course code",
        "timetable": "department, academic year, semester, and class/section",
        "exam_schedule": "department and semester",
        "seating_arrangement": "department and semester",
        "lab_timetable": "department and semester",
    }
    if doc_type not in SUPPORTED_DOCUMENT_TYPES:
        raise HTTPException(status_code=400, detail=f"Unsupported document type '{doc_type}'.")
    if any(not value or not value.strip() for value in required[doc_type]):
        raise HTTPException(status_code=422, detail=f"Select {labels[doc_type]} before uploading this document.")
    if doc_type in {"timetable", "exam_schedule", "seating_arrangement", "lab_timetable"} and not semester:
        raise HTTPException(status_code=422, detail="Select a semester from I to VIII.")


async def process_syllabus_background(
    file_path: str, department: str, year: str, document_id: uuid.UUID
) -> None:
    """Background ingestion task for university curriculum syllabus handbooks."""
    report = {"type": "syllabus", "status": "started", "file": file_path}
    neo4j_driver = get_neo4j()

    try:
        await asyncio.to_thread(ensure_neo4j_schema, neo4j_driver, "syllabus")
        parser = SyllabusParser(neo4j_driver)
        summary = await asyncio.to_thread(
            parser.parse_and_ingest, file_path, department, year
        )

        chunks_to_store = parser.last_chunks or []
        stored_count = 0
        chunk_error = None
        try:
            if chunks_to_store:
                embed_inputs = [
                    format_doc_embedding_text(ch["content"], title=ch.get("title") or ch["course_code"])
                    for ch in chunks_to_store
                ]
                embeddings = await embed_texts(embed_inputs, batch_size=24)

                async with AsyncSessionLocal() as db:
                    await db.execute(
                        text("DELETE FROM document_chunks WHERE document_id = :id"),
                        {"id": document_id},
                    )
                    await db.execute(text("DELETE FROM semantic_cache"))
                    semantic_cache.clear_memory()
                    stored_count = 0
                    for chunk_obj, emb in zip(chunks_to_store, embeddings):
                        if emb:
                            stored_count += 1
                            db.add(
                                DocumentChunk(
                                    document_id=document_id,
                                    content=chunk_obj["content"],
                                    course_code=chunk_obj["course_code"],
                                    content_type=chunk_obj.get("content_type", "syllabus"),
                                    embedding=emb,
                                )
                            )
                    await db.commit()
                    logger.info(
                        "Stored %d structured syllabus chunks in PostgreSQL for %s %s.",
                        stored_count,
                        department,
                        year,
                    )
        except Exception as _ce:
            stored_count = 0
            chunk_error = str(_ce)
            logger.warning("Chunk storage notice: %s", _ce)

        expected_count = len(chunks_to_store)
        report["status"] = "success" if expected_count > 0 and stored_count == expected_count else "partial"
        report["summary"] = summary
        report["chunks_stored"] = stored_count
        report["chunks_expected"] = expected_count
        if chunk_error:
            report["error"] = chunk_error
        async with AsyncSessionLocal() as db:
            db.add(ProcessingReport(document_id=document_id, course_code="SYLLABUS", report_json=json.dumps(report)))
            await db.commit()
    except Exception as e:
        logger.error("Error during syllabus ingestion: %s", e)
        report["status"] = "error"
        report["error"] = str(e)
        async with AsyncSessionLocal() as db:
            db.add(ProcessingReport(document_id=document_id, course_code="SYLLABUS", report_json=json.dumps(report)))
            await db.commit()


async def process_pyq_background(
    file_path: str, course_code: str, document_id: uuid.UUID, course_name: str = ""
) -> None:
    """Background ingestion task for past year question papers."""
    report = {
        "type": "pyq", "course_code": course_code, "course_name": course_name,
        "status": "started", "structured": 0, "vision": 0,
        "detected": 0, "mapped": 0, "suggested": 0, "unmapped": 0,
    }
    neo4j_driver = get_neo4j()

    structured_qs = await asyncio.to_thread(extract_pyq_structured, file_path, course_code, str(document_id))

    if structured_qs:
        # Analyze cropped diagrams via gemma4 vision for questions with graphics before topic mapping
        vis_sem = asyncio.Semaphore(2)

        async def _tag_diagram(q_item: dict) -> None:
            if q_item.get("has_figure") and q_item.get("image_b64"):
                async with vis_sem:
                    vis = await analyze_diagram_structured(q_item["image_b64"])
                    desc = vis.get("description") or ""
                    if desc and not desc.startswith("Error analyzing image:"):
                        q_item["diagram_summary"] = desc
                        q_item["diagram_type"] = vis.get("diagram_type", "other")

        await asyncio.gather(*(_tag_diagram(q) for q in structured_qs))

        await asyncio.to_thread(ensure_neo4j_schema, neo4j_driver, "pyq")
        mapping_report = await map_pyq_structured_to_kg(
            neo4j_driver,
            structured_qs,
            str(document_id),
            course_code=course_code,
            course_name=course_name,
        )

        # Embed PYQ chunks: multimodal (text + cropped diagram) when has_figure, batched otherwise
        text_only_items: list[tuple[dict, str]] = []
        mm_items: list[tuple[dict, str, str]] = []
        for q in structured_qs:
            labeled_content = f"[PYQ - {course_code} - Q{q['question_number']}]\n{q['question_text']}"
            if q.get("marks"):
                labeled_content += f"\nMarks: {q['marks']}"
            if q.get("btl_tag"):
                labeled_content += f" | BTL: {q['btl_tag']}"
            if q.get("co_tag"):
                labeled_content += f" | CO: {q['co_tag']}"
            if q.get("diagram_summary"):
                labeled_content += f"\nDiagram Analysis: {q['diagram_summary']}"
            img_url = q.get("image_url")
            if img_url:
                labeled_content += f"\n\n![Diagram for Q{q['question_number']}]({img_url})"

            if q.get("has_figure") and q.get("image_b64"):
                mm_items.append((q, labeled_content, q["image_b64"]))
            else:
                text_only_items.append((q, labeled_content))

        text_vecs = (
            await embed_texts(
                [format_doc_embedding_text(txt, title=course_code) for _, txt in text_only_items],
                batch_size=24,
            )
            if text_only_items
            else []
        )

        async with AsyncSessionLocal() as db:
            await db.execute(text("DELETE FROM semantic_cache"))
            semantic_cache.clear_memory()

            for (_q, labeled_content), emb in zip(text_only_items, text_vecs):
                if emb:
                    db.add(DocumentChunk(
                        document_id=document_id,
                        content=labeled_content,
                        course_code=course_code,
                        content_type="text",
                        embedding=emb,
                    ))

            for _q, labeled_content, img_b64 in mm_items:
                emb = await embed_multimodal(
                    format_doc_embedding_text(labeled_content, title=course_code),
                    [img_b64],
                )
                if emb:
                    db.add(DocumentChunk(
                        document_id=document_id,
                        content=labeled_content,
                        course_code=course_code,
                        content_type="visual",
                        embedding=emb,
                    ))
            await db.commit()
        report["status"] = "success_structured"
        report["structured"] = len(structured_qs)
        report["vision"] = len(mm_items)
        report.update(mapping_report)
        report["detected"] = len(structured_qs)
        async with AsyncSessionLocal() as db:
            db.add(ProcessingReport(document_id=document_id, course_code=course_code, report_json=json.dumps(report)))
            await db.commit()
        return

    # Fallback to visual page slices if question headers could not be parsed deterministically
    page_chunks = await asyncio.to_thread(process_pyq_visuals, file_path)
    if not page_chunks:
        report["status"] = "no_questions_extracted"
        async with AsyncSessionLocal() as db:
            db.add(ProcessingReport(document_id=document_id, course_code=course_code, report_json=json.dumps(report)))
            await db.commit()
        return

    async with AsyncSessionLocal() as db:
        for chunk_data in page_chunks:
            try:
                image_bytes = base64.b64decode(chunk_data["base64"])
                public_id = f"{document_id}_page_{chunk_data['page'] + 1}_chunk_{chunk_data['chunk_index']}"
                image_url = upload_question_image_to_cloudinary(image_bytes, public_id)
                desc = await describe_uploaded_image(chunk_data["base64"])
                chunk_text = f"[PYQ Visual - {course_code} - Page {chunk_data['page'] + 1}]\n{desc}\n\n![PYQ Slice]({image_url})"
                embedding = await embed_multimodal(
                    format_doc_embedding_text(chunk_text[:1200], title=course_code),
                    [chunk_data["base64"]],
                )
                if embedding:
                    db.add(
                        DocumentChunk(
                            document_id=document_id,
                            content=chunk_text,
                            course_code=course_code,
                            content_type="visual",
                            embedding=embedding,
                        )
                    )
            except Exception as e:
                logger.warning("Visual slice notice: %s", e)
        report["status"] = "success_visual_fallback"
        report["vision"] = len(page_chunks)
        db.add(ProcessingReport(document_id=document_id, course_code=course_code, report_json=json.dumps(report)))
        await db.commit()


async def process_document_background(
    file_path: str,
    document_id: uuid.UUID,
    doc_type: str,
    department: str | None,
    year: str | None,
    course_code: str | None,
    course_name: str | None = None,
) -> None:
    """Dispatch a document to its type-specific ingestion pipeline and persist final status."""
    try:
        if doc_type == "syllabus":
            await process_syllabus_background(file_path, department or "", year or "", document_id)
            return
        if doc_type == "pyq":
            await process_pyq_background(
                file_path, course_code or "", document_id, course_name or ""
            )
            return

        async with AsyncSessionLocal() as session:
            if doc_type == "academic_calendar":
                result = await ingest_calendar_document(document_id, file_path, session)
            elif doc_type == "regulations":
                result = await ingest_regulations_document(document_id, file_path, session)
            elif doc_type == "timetable":
                result = await ingest_timetable_document(document_id, file_path, session)
            else:
                result = await ingest_academic_document(document_id, file_path, doc_type, session)
            status = "success"
            summary = result.get("summary") or ", ".join(
                f"{key.replace('_', ' ')}: {value}"
                for key, value in result.items()
                if key not in {"status", "doc_type", "department", "semester", "academic_year"}
            )
            session.add(ProcessingReport(
                document_id=document_id,
                course_code=course_code or doc_type.upper(),
                report_json=json.dumps({"type": doc_type, "status": status, "summary": summary, **result}),
            ))
            await session.commit()
    except Exception as exc:
        logger.exception("Document ingestion failed: type=%s id=%s", doc_type, document_id)
        try:
            async with AsyncSessionLocal() as session:
                await session.rollback()
                session.add(ProcessingReport(
                    document_id=document_id,
                    course_code=course_code or doc_type.upper(),
                    report_json=json.dumps({
                        "type": doc_type,
                        "status": "error",
                        "error": str(exc)[:1500],
                    }),
                ))
                await session.commit()
        except Exception:
            logger.exception("Could not persist failed ingestion status for %s", document_id)


@router.post("/upload")
async def upload_document(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    doc_type: str = Form("syllabus"),
    department: str | None = Form(None),
    dept: str | None = Form(None),
    year: str | None = Form(None),
    course_code: str | None = Form(None),
    course_name: str | None = Form(None),
    semester: str | None = Form(None),
    section: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Upload PDFs using the admin-selected document type and authoritative metadata."""
    os.makedirs(settings.uploads_dir, exist_ok=True)
    doc_type = (doc_type or "").strip().lower()
    doc_type = {"curriculum": "syllabus", "academic calendar": "academic_calendar", "lab timetable": "lab_timetable", "exam schedule": "exam_schedule", "seating arrangement": "seating_arrangement"}.get(doc_type, doc_type)
    department = (department or dept or "").strip().upper() or None
    year = (year or "").strip() or None
    course_code = (course_code or "").strip().upper() or None
    course_name = (course_name or "").strip() or None
    semester = _normalize_semester(semester)
    section = (section or "").strip().upper() or None
    _validate_upload_scope(doc_type, department, year, course_code, course_name, semester, section)
    uploaded_items = []

    MAX_FILE_SIZE = 100 * 1024 * 1024  # 100 MB

    for file in files:
        raw_name = os.path.basename(file.filename or "uploaded.pdf")
        safe_name = re.sub(r"[^a-zA-Z0-9_\.-]", "_", raw_name)

        # Stream file in bounded chunks to avoid memory exhaustion
        content_buf = bytearray()
        while chunk := await file.read(1024 * 1024):
            content_buf.extend(chunk)
            if len(content_buf) > MAX_FILE_SIZE:
                raise HTTPException(
                    status_code=413,
                    detail=f"File '{safe_name}' exceeds the maximum allowed limit of 100 MB.",
                )
        content = bytes(content_buf)

        if not content.startswith(b"%PDF-"):
            raise HTTPException(
                status_code=400,
                detail=f"File '{safe_name}' is not a valid PDF document.",
            )

        file_path = os.path.join(settings.uploads_dir, f"{uuid.uuid4()}_{safe_name}")

        def _write_bytes(dest: str, data: bytes):
            with open(dest, "wb") as f:
                f.write(data)

        await asyncio.to_thread(_write_bytes, file_path, content)
        new_doc = Document(
            filename=safe_name,
            doc_type=doc_type,
            department=department,
            year=year,
            course_code=course_code,
            course_name=course_name,
            semester=semester,
            section=section,
        )
        db.add(new_doc)
        await db.commit()
        await db.refresh(new_doc)

        db.add(ProcessingReport(
            document_id=new_doc.id,
            course_code=course_code or doc_type.upper(),
            report_json=json.dumps({"type": doc_type, "status": "queued"}),
        ))
        await db.commit()
        background_tasks.add_task(
            process_document_background,
            file_path,
            new_doc.id,
            doc_type,
            department,
            year,
            course_code,
            course_name,
        )

        uploaded_items.append({
            "id": str(new_doc.id),
            "filename": safe_name,
            "doc_type": doc_type,
            "department": department,
            "year": year,
            "course_code": course_code,
            "course_name": course_name,
            "semester": semester,
            "section": section,
            "status": "queued",
        })

    return {
        "message": f"{len(files)} file(s) uploaded successfully. Ingestion has been queued.",
        "items": uploaded_items
    }


@router.get("/documents")
async def list_documents(
    department: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Lists all uploaded documents with status reports. Requires admin role."""
    try:
        stmt = select(Document).order_by(Document.created_at.desc())
        if department and department.strip().upper() not in {"ALL", "ALL DEPARTMENTS"}:
            stmt = stmt.where(Document.department == department.strip().upper())
        result = await db.execute(stmt)
        docs = result.scalars().all()

        rep_result = await db.execute(select(ProcessingReport).order_by(ProcessingReport.created_at.desc()))
        reports_by_doc = {}
        for r in rep_result.scalars().all():
            if str(r.document_id) in reports_by_doc:
                continue
            try:
                reports_by_doc[str(r.document_id)] = json.loads(r.report_json)
            except Exception:
                reports_by_doc[str(r.document_id)] = {"status": "completed"}

        formatted = []
        for doc in docs:
            d_id = str(doc.id)
            rep = reports_by_doc.get(d_id)
            status_text = "processing"
            details = "Queued" if rep and rep.get("status") == "queued" else "Processing"
            if rep:
                st = str(rep.get("status", "")).lower()
                if st == "error" or "error" in st:
                    status_text = "error"
                    details = rep.get("error") or "Ingestion failed"
                elif st == "partial":
                    status_text = "partial"
                    details = f"Graph indexed; {rep.get('chunks_stored', 0)} of {rep.get('chunks_expected', 0)} chunks stored"
                elif st.startswith("success"):
                    status_text = "completed"
                    if doc.doc_type == "pyq" and rep.get("detected") is not None:
                        details = (
                            f"{rep.get('detected', 0)} detected; "
                            f"{rep.get('mapped', 0)} mapped; "
                            f"{rep.get('suggested', 0)} suggested; "
                            f"{rep.get('unmapped', 0)} unassigned"
                        )
                    else:
                        num_q = rep.get("structured", 0) or rep.get("vision", 0)
                        summary = rep.get("summary")
                        if isinstance(summary, dict):
                            details = f"Indexed successfully · {summary.get('courses', 0)} courses · {summary.get('chunks_generated', 0)} chunks"
                        else:
                            details = f"{num_q} questions extracted" if num_q else summary or "Indexed successfully"
                elif st in {"queued", "started", "processing"}:
                    status_text = "processing"
                    details = "Queued" if st == "queued" else "Processing"

            formatted.append({
                "id": d_id,
                "filename": doc.filename,
                "doc_type": doc.doc_type,
                "course_code": doc.course_code or doc.department or "—",
                "department": doc.department,
                "year": doc.year,
                "course_name": doc.course_name,
                "semester": doc.semester,
                "section": doc.section,
                "created_at": doc.created_at.isoformat() if doc.created_at else "",
                "status": status_text,
                "details": details or status_text
            })

        return formatted
    except Exception as e:
        logger.warning("Error fetching documents: %s", e)
        return []


@router.get("/documents/departments")
async def list_document_departments(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Return the departments currently represented in the document library."""
    try:
        result = await db.execute(
            select(Document.department)
            .where(Document.department.is_not(None), Document.department != "")
            .distinct()
            .order_by(Document.department)
        )
        return [value for value in result.scalars().all() if value]
    except Exception as exc:
        logger.warning("Could not list document departments: %s", exc)
        return []


@router.post("/documents/{doc_id}/reprocess")
async def reprocess_document(
    doc_id: str,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Re-run ingestion for a stored lab timetable after parser fixes."""
    try:
        document_uuid = uuid.UUID(doc_id)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="Invalid document UUID format.")

    result = await db.execute(select(Document).where(Document.id == document_uuid))
    document = result.scalar_one_or_none()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found.")
    if document.doc_type != "lab_timetable":
        raise HTTPException(status_code=400, detail="Reprocessing is currently available for lab timetables only.")
    if not document.department or not document.semester:
        raise HTTPException(status_code=422, detail="The document is missing its admin-selected department or semester.")

    # Upload storage names are UUID-prefixed; the database keeps the original
    # filename. Match the stored copy nearest the document's creation time so
    # repeated uploads with the same filename reprocess the correct revision.
    upload_dir = Path(settings.uploads_dir)
    candidates = list(upload_dir.glob(f"*_{document.filename}"))
    if not candidates:
        raise HTTPException(status_code=404, detail="The uploaded PDF is no longer present in storage. Please upload it again.")
    created_at = document.created_at.timestamp() if document.created_at else None
    selected_path = min(
        candidates,
        key=lambda path: abs(path.stat().st_mtime - created_at) if created_at is not None else -path.stat().st_mtime,
    )
    file_path = str(selected_path)

    db.add(ProcessingReport(
        document_id=document.id,
        course_code=document.course_code or document.department,
        report_json=json.dumps({"type": "lab_timetable", "status": "queued", "summary": "Reprocessing uploaded lab timetable."}),
    ))
    await db.commit()
    background_tasks.add_task(
        process_document_background,
        file_path,
        document.id,
        document.doc_type,
        document.department,
        document.year,
        document.course_code,
        document.course_name,
    )
    return {"message": "Lab timetable reprocessing queued.", "id": str(document.id), "filename": document.filename}


@router.get("/processing-status")
async def get_processing_status(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Live status polling endpoint for admin upload queue."""
    try:
        result = await db.execute(select(Document).order_by(Document.created_at.desc()).limit(20))
        docs = result.scalars().all()

        rep_result = await db.execute(select(ProcessingReport).order_by(ProcessingReport.created_at.desc()))
        reports_by_doc = {}
        for r in rep_result.scalars().all():
            if str(r.document_id) in reports_by_doc:
                continue
            try:
                reports_by_doc[str(r.document_id)] = json.loads(r.report_json)
            except Exception:
                reports_by_doc[str(r.document_id)] = {"status": "completed"}

        items = []
        for doc in docs:
            d_id = str(doc.id)
            rep = reports_by_doc.get(d_id)
            items.append({
                "id": d_id,
                "filename": doc.filename,
                "doc_type": doc.doc_type,
                "course_code": doc.course_code or doc.department or "—",
                "department": doc.department,
                "year": doc.year,
                "course_name": doc.course_name,
                "semester": doc.semester,
                "section": doc.section,
                "status": rep.get("status", "processing") if rep else "processing",
                "structured": rep.get("structured", 0) if rep else 0,
                "detected": rep.get("detected", 0) if rep else 0,
                "mapped": rep.get("mapped", 0) if rep else 0,
                "suggested": rep.get("suggested", 0) if rep else 0,
                "unmapped": rep.get("unmapped", 0) if rep else 0,
                "created_at": doc.created_at.isoformat() if doc.created_at else ""
            })
        return items
    except Exception as e:
        logger.warning("Error fetching processing status: %s", e)
        return []


@router.delete("/documents/{doc_id}")
async def delete_document(
    doc_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Deletes document and associated chunks from PostgreSQL, Neo4j, and disk."""
    try:
        doc_uuid = uuid.UUID(doc_id) if isinstance(doc_id, str) else doc_id
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="Invalid document UUID format.")

    result = await db.execute(select(Document).where(Document.id == doc_uuid))
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")

    try:
        c_code = doc.course_code.upper() if doc.course_code else None
        try:
            neo4j_driver = get_neo4j()
            with neo4j_driver.session() as session:
                if doc.doc_type == "pyq":
                    session.run("MATCH (q:Question) WHERE q.document_id = $doc_id DETACH DELETE q", doc_id=str(doc_id))
                    session.run("MATCH (ep:ExamPaper) WHERE NOT (:Question)-[:FROM_PAPER]->(ep) DETACH DELETE ep")
                elif doc.doc_type == "syllabus":
                    if doc.department and doc.year:
                        dept_u = doc.department.upper()
                        sv_id = f"{dept_u}_{doc.year}"
                        session.run(
                            "MATCH (n {syllabus_version_id: $sv_id}) DETACH DELETE n",
                            sv_id=sv_id,
                        )
                        session.run(
                            "MATCH (sv:SyllabusVersion {dept: $dept, year: $year}) DETACH DELETE sv",
                            dept=dept_u,
                            year=doc.year,
                        )
                        session.run(
                            """
                            MATCH (c:Course {dept: $dept, year: $year})
                            OPTIONAL MATCH (c)-[:HAS_UNIT]->(u:Unit)
                            OPTIONAL MATCH (u)-[:HAS_TOPIC]->(t:Topic)
                            OPTIONAL MATCH (c)-[:HAS_OBJECTIVE|HAS_OUTCOME|USES_TEXTBOOK|HAS_TEXTBOOK|HAS_REFERENCE|USES_REFERENCE]->(child)
                            DETACH DELETE child, u
                            WITH c, collect(DISTINCT t) AS topics
                            FOREACH (tp IN [x IN topics WHERE x IS NOT NULL AND NOT (:Question)-[:TESTS]->(x)] | DETACH DELETE tp)
                            WITH c
                            WHERE NOT (:Question)-[:BELONGS_TO]->(c)
                              AND NOT (c)-[:TESTED_BY]->(:Question)
                              AND NOT (:Semester)-[:OFFERS]->(c)
                              AND NOT (:ElectiveBasket)-[:CONTAINS]->(c)
                            DETACH DELETE c
                            """,
                            dept=dept_u,
                            year=doc.year,
                        )
                        session.run("MATCH (p:Program {name: $dept}) WHERE NOT (p)-[:HAS_SYLLABUS_VERSION]->() DETACH DELETE p", dept=dept_u)
                        session.run("MATCH (ep:EvaluationPattern) WHERE NOT ()-[:HAS_EVALUATION_PATTERN]->(ep) DETACH DELETE ep")
                        session.run("MATCH (ac:AssessmentComponent) WHERE NOT ()-[:ASSESSED_BY]->(ac) DETACH DELETE ac")
                    elif c_code:
                        session.run(
                            "MATCH (c:Course {code: $c_code}) OPTIONAL MATCH (c)-[:HAS_UNIT]->(u:Unit) OPTIONAL MATCH (u)-[:HAS_TOPIC]->(t:Topic) DETACH DELETE t, u",
                            c_code=c_code,
                        )
                remaining = session.run("MATCH (n) RETURN count(n) AS cnt").single()
                if remaining and remaining["cnt"] == 0:
                    drop_neo4j_schema(neo4j_driver)
        except Exception as graph_err:
            logger.warning("Neo4j deletion notice: %s", graph_err)

        try:
            delete_document_images_from_cloudinary(str(doc_id))
            images_dir = settings.images_dir
            if os.path.exists(images_dir):
                for fname in os.listdir(images_dir):
                    if str(doc_id) in fname:
                        os.remove(os.path.join(images_dir, fname))
        except Exception as fs_err:
            logger.warning("Disk cleanup notice: %s", fs_err)

        await db.execute(text("DELETE FROM document_chunks WHERE document_id = :id"), {"id": doc_uuid})
        await db.execute(text("DELETE FROM processing_reports WHERE document_id = :id"), {"id": doc_uuid})
        await db.execute(text("DELETE FROM academic_document_records WHERE document_id = :id"), {"id": doc_uuid})
        await db.execute(text("DELETE FROM calendar_day_cohorts WHERE calendar_day_id IN (SELECT id FROM calendar_days WHERE document_id = :id)"), {"id": doc_uuid})
        await db.execute(text("DELETE FROM calendar_days WHERE document_id = :id"), {"id": doc_uuid})
        await db.execute(text("DELETE FROM academic_events WHERE document_id = :id"), {"id": doc_uuid})
        await db.execute(text("DELETE FROM regulation_clauses WHERE document_id = :id"), {"id": doc_uuid})
        await db.execute(text("DELETE FROM clause_references WHERE document_id = :id"), {"id": doc_uuid})
        await db.execute(text("DELETE FROM weekly_sessions WHERE timetable_id IN (SELECT id FROM timetable_versions WHERE document_id = :id)"), {"id": doc_uuid})
        await db.execute(text("DELETE FROM elective_options WHERE timetable_id IN (SELECT id FROM timetable_versions WHERE document_id = :id)"), {"id": doc_uuid})
        await db.execute(text("DELETE FROM timetable_versions WHERE document_id = :id"), {"id": doc_uuid})
        await db.execute(text("DELETE FROM semantic_cache"))
        await db.execute(text("DELETE FROM documents WHERE id = :id"), {"id": doc_uuid})
        await db.commit()
        semantic_cache.clear_memory()
        return {"message": "Document and its associated graph nodes deleted successfully."}
    except Exception:
        await db.rollback()
        logger.exception("Error deleting document %s", doc_id)
        raise HTTPException(status_code=500, detail="Failed to delete document.")


@router.post("/reset")
async def reset_system(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Wipes all documents, chunks, graph nodes, and uploaded diagram crops."""
    errors = []
    try:
        await db.execute(text("DELETE FROM document_chunks"))
        await db.execute(text("DELETE FROM processing_reports"))
        await db.execute(text("DELETE FROM semantic_cache"))
        await db.execute(text("DELETE FROM academic_document_records"))
        await db.execute(text("DELETE FROM calendar_day_cohorts"))
        await db.execute(text("DELETE FROM calendar_days"))
        await db.execute(text("DELETE FROM academic_events"))
        await db.execute(text("DELETE FROM weekly_sessions"))
        await db.execute(text("DELETE FROM elective_options"))
        await db.execute(text("DELETE FROM timetable_versions"))
        await db.execute(text("DELETE FROM clause_references"))
        await db.execute(text("DELETE FROM regulation_clauses"))
        await db.execute(text("DELETE FROM documents"))
        await db.commit()
        semantic_cache.clear_memory()
    except Exception as e:
        await db.rollback()
        errors.append(f"PostgreSQL error: {e}")

    try:
        neo4j_driver = get_neo4j()
        with neo4j_driver.session() as session:
            session.run("MATCH (n) DETACH DELETE n")
        drop_neo4j_schema(neo4j_driver)
    except Exception as e:
        errors.append(f"Neo4j error: {e}")

    try:
        delete_all_images_from_cloudinary()
        uploads_dir = settings.uploads_dir
        if os.path.exists(uploads_dir):
            # Remove contents rather than uploads_dir itself (it may be a Docker volume mount point)
            for entry in os.scandir(uploads_dir):
                if entry.is_dir(follow_symlinks=False):
                    shutil.rmtree(entry.path)
                else:
                    os.remove(entry.path)
            os.makedirs(settings.images_dir, exist_ok=True)
    except Exception as e:
        errors.append(f"File system error: {e}")

    if errors:
        logger.error("System reset encountered errors: %s", "; ".join(errors))
        raise HTTPException(status_code=500, detail="System reset partially failed. Check server logs.")

    return {"message": "System reset complete. All data has been wiped."}
