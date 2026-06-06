"""
CurriculumLens — Document API Routes

Handles document upload, listing, and management.
Documents are processed asynchronously via the document processing pipeline.
"""

import os
import uuid
import shutil
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.core.config import get_settings
from app.core.database import get_db
from app.models.models import Document, DocumentType, SourceType, ProcessingStatus
from app.schemas.schemas import DocumentUploadResponse, DocumentOut, DocumentListResponse

router = APIRouter(prefix="/documents", tags=["Documents"])
settings = get_settings()


def detect_document_type(filename: str) -> DocumentType:
    """Detect document type from file extension."""
    ext = Path(filename).suffix.lower()
    mapping = {
        ".pdf": DocumentType.PDF,
        ".pptx": DocumentType.PPT,
        ".ppt": DocumentType.PPT,
        ".png": DocumentType.IMAGE,
        ".jpg": DocumentType.IMAGE,
        ".jpeg": DocumentType.IMAGE,
        ".webp": DocumentType.IMAGE,
    }
    if ext not in mapping:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {ext}. Supported: {list(mapping.keys())}",
        )
    return mapping[ext]


@router.post("/upload", response_model=DocumentUploadResponse)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    source_type: str = Form("lecture_note"),
    course_code: Optional[str] = Form(None),
    course_name: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
):
    """
    Upload an academic document for processing.

    The document will be stored and queued for async processing:
    1. Text extraction + semantic chunking
    2. Embedding generation + vector indexing
    3. Page-as-image rendering + visual embedding
    4. KG entity extraction (if applicable)
    """
    # Validate file size
    file_content = await file.read()
    file_size = len(file_content)
    if file_size > settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds {settings.MAX_UPLOAD_SIZE_MB}MB limit",
        )

    # Detect type and validate
    doc_type = detect_document_type(file.filename)

    try:
        source = SourceType(source_type)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid source_type. Must be one of: {[s.value for s in SourceType]}",
        )

    # Save file to disk
    file_id = str(uuid.uuid4())
    upload_dir = Path(settings.UPLOAD_DIR) / file_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    file_path = upload_dir / file.filename

    with open(file_path, "wb") as f:
        f.write(file_content)

    # Create database record
    document = Document(
        id=uuid.UUID(file_id),
        filename=file.filename,
        original_filename=file.filename,
        document_type=doc_type,
        source_type=source,
        file_path=str(file_path),
        file_size_bytes=file_size,
        course_code=course_code,
        course_name=course_name,
        processing_status=ProcessingStatus.PENDING,
    )
    db.add(document)
    await db.flush()

    # Dispatch async processing task via FastAPI BackgroundTasks
    from app.worker import process_document_async
    background_tasks.add_task(process_document_async, str(document.id))

    return DocumentUploadResponse(
        id=document.id,
        filename=file.filename,
        document_type=doc_type.value,
        source_type=source.value,
        processing_status=ProcessingStatus.PENDING.value,
        message="Document uploaded successfully. Processing will begin shortly.",
    )


@router.get("/", response_model=DocumentListResponse)
async def list_documents(
    course_code: Optional[str] = None,
    source_type: Optional[str] = None,
    status: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    """List all uploaded documents with optional filters."""
    query = select(Document).order_by(Document.created_at.desc())

    if course_code:
        query = query.where(Document.course_code == course_code)
    if source_type:
        query = query.where(Document.source_type == SourceType(source_type))
    if status:
        query = query.where(Document.processing_status == ProcessingStatus(status))

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar()

    # Paginate
    query = query.offset(skip).limit(limit)
    result = await db.execute(query)
    documents = result.scalars().all()

    return DocumentListResponse(
        documents=[DocumentOut.model_validate(d) for d in documents],
        total=total,
    )


@router.get("/{document_id}", response_model=DocumentOut)
async def get_document(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get details of a specific document."""
    result = await db.execute(
        select(Document).where(Document.id == document_id)
    )
    document = result.scalar_one_or_none()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    return DocumentOut.model_validate(document)


@router.delete("/{document_id}")
async def delete_document(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Delete a document and all associated data."""
    result = await db.execute(
        select(Document).where(Document.id == document_id)
    )
    document = result.scalar_one_or_none()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    # Delete file from disk
    file_dir = Path(document.file_path).parent
    if file_dir.exists():
        shutil.rmtree(file_dir)

    # TODO: Delete from Qdrant, Elasticsearch, and Neo4j

    await db.delete(document)

    return {"message": "Document deleted successfully", "id": str(document_id)}
