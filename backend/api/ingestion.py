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

logger = logging.getLogger("mach_ai.ingestion")

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
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
from backend.parsers.pdf_classifier import classify_pdf_type
from backend.parsers.pyq_parser import extract_pyq_structured
from backend.parsers.syllabus_parser import SyllabusParser
from backend.parsers.visual_cropper import (
    analyze_diagram_structured,
    describe_uploaded_image,
    process_pyq_visuals,
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

        try:
            chunks_to_store = parser.last_chunks or []
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
            logger.warning("Chunk storage notice: %s", _ce)

        report["status"] = "success"
        report["summary"] = summary
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
    file_path: str, course_code: str, document_id: uuid.UUID
) -> None:
    """Background ingestion task for past year question papers."""
    report = {"type": "pyq", "course_code": course_code, "status": "started", "structured": 0, "vision": 0, "mapped": 0}
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
        await map_pyq_structured_to_kg(neo4j_driver, structured_qs, str(document_id))

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


@router.post("/upload")
async def upload_document(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    doc_type: str | None = Form(None),
    department: str | None = Form(None),
    dept: str | None = Form(None),
    year: str | None = Form(None),
    course_code: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Admin PDF upload: verifies %PDF- magic bytes and 100 MB limits, and triggers async parsing."""
    os.makedirs(settings.uploads_dir, exist_ok=True)
    department = department or dept
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
        meta = await asyncio.to_thread(classify_pdf_type, file_path)

        effective_doc_type = doc_type if doc_type in ("syllabus", "pyq") else meta.get("doc_type", "pyq")

        if effective_doc_type == "syllabus":
            eff_dept = department or meta.get("department", "CSE")
            eff_year = year or meta.get("year", "2023")
            eff_code = None
        else:
            eff_code = course_code or meta.get("course_code", "UNKNOWN")
            eff_dept = None
            eff_year = None

        new_doc = Document(
            filename=safe_name,
            doc_type=effective_doc_type,
            department=eff_dept,
            year=eff_year,
            course_code=eff_code,
        )
        db.add(new_doc)
        await db.commit()
        await db.refresh(new_doc)

        if effective_doc_type == "syllabus":
            background_tasks.add_task(
                process_syllabus_background, file_path, eff_dept, eff_year, new_doc.id
            )
        elif effective_doc_type == "pyq":
            background_tasks.add_task(
                process_pyq_background, file_path, eff_code, new_doc.id
            )

        uploaded_items.append({
            "id": str(new_doc.id),
            "filename": safe_name,
            "doc_type": effective_doc_type,
            "course_code": eff_code or f"{eff_dept} ({eff_year})",
            "status": "processing"
        })

    return {
        "message": f"{len(files)} file(s) uploaded successfully! Ingestion underway.",
        "items": uploaded_items
    }


@router.get("/documents")
async def list_documents(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Lists all uploaded documents with status reports. Requires admin role."""
    try:
        result = await db.execute(select(Document).order_by(Document.created_at.desc()))
        docs = result.scalars().all()

        rep_result = await db.execute(select(ProcessingReport))
        reports_by_doc = {}
        for r in rep_result.scalars().all():
            try:
                reports_by_doc[str(r.document_id)] = json.loads(r.report_json)
            except Exception:
                reports_by_doc[str(r.document_id)] = {"status": "completed"}

        formatted = []
        for doc in docs:
            d_id = str(doc.id)
            rep = reports_by_doc.get(d_id)
            status_text = "completed" if rep else "processing"
            details = None
            if rep:
                st = rep.get("status", "")
                if "error" in st:
                    status_text = "error"
                elif "success" in st:
                    status_text = "completed"
                num_q = rep.get("structured", 0) or rep.get("vision", 0)
                if num_q:
                    details = f"{num_q} questions mapped"
                elif rep.get("summary"):
                    details = "Graph indexed"

            formatted.append({
                "id": d_id,
                "filename": doc.filename,
                "doc_type": doc.doc_type,
                "course_code": doc.course_code or doc.department or "—",
                "created_at": doc.created_at.isoformat() if doc.created_at else "",
                "status": status_text,
                "details": details or status_text
            })

        return formatted
    except Exception as e:
        logger.warning("Error fetching documents: %s", e)
        return []


@router.get("/processing-status")
async def get_processing_status(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Live status polling endpoint for admin upload queue."""
    try:
        result = await db.execute(select(Document).order_by(Document.created_at.desc()).limit(20))
        docs = result.scalars().all()

        rep_result = await db.execute(select(ProcessingReport))
        reports_by_doc = {}
        for r in rep_result.scalars().all():
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
                "status": rep.get("status", "processing") if rep else "processing",
                "structured": rep.get("structured", 0) if rep else 0,
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
