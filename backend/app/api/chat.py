"""
CurriculumLens — Chat API Routes

Handles the main Q&A interaction pipeline:
Query → Modality Detection → Concept Identification → Hybrid Retrieval → Generation → Response
"""

import time
import uuid
from typing import Optional

from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db
from app.models.models import ChatSession, ChatMessage
from app.schemas.schemas import (
    ChatQueryRequest,
    ChatResponse,
    ChatSessionOut,
    SourceCitation,
    PYQReference,
    KGConceptNode,
)

router = APIRouter(prefix="/chat", tags=["Chat"])


@router.post("/query", response_model=ChatResponse)
async def process_query(
    message: str = Form(...),
    session_id: Optional[str] = Form(None),
    course_code: Optional[str] = Form(None),
    include_pyq: bool = Form(True),
    include_kg: bool = Form(True),
    image: Optional[UploadFile] = File(None),
    db: AsyncSession = Depends(get_db),
):
    """
    Process a multimodal academic query through the full pipeline:

    1. Modality detection (text / image / voice)
    2. If image: VACI concept identification
    3. KG concept mapping
    4. Hybrid retrieval (dense + sparse + KG + visual)
    5. Cross-encoder re-ranking
    6. LLM response generation with context
    7. PYQ reference linking

    Returns: Answer with source citations, PYQ references, and KG concept links.
    """
    start_time = time.time()

    # ── Session Management ──
    if session_id:
        sid = uuid.UUID(session_id)
        result = await db.execute(select(ChatSession).where(ChatSession.id == sid))
        session = result.scalar_one_or_none()
        if not session:
            raise HTTPException(status_code=404, detail="Chat session not found")
    else:
        session = ChatSession(
            title=message[:100] if len(message) > 0 else "New Chat",
            course_code=course_code,
        )
        db.add(session)
        await db.flush()

    # ── Save User Message ──
    input_modality = "image" if image else "text"
    image_path = None

    if image:
        # Save uploaded image for VACI processing
        img_id = str(uuid.uuid4())
        img_dir = f"/data/uploads/chat_images/{img_id}"
        import os
        os.makedirs(img_dir, exist_ok=True)
        image_path = f"{img_dir}/{image.filename}"
        content = await image.read()
        with open(image_path, "wb") as f:
            f.write(content)

    user_msg = ChatMessage(
        session_id=session.id,
        role="user",
        content=message,
        input_modality=input_modality,
        image_path=image_path,
    )
    db.add(user_msg)
    await db.flush()

    # ── Pipeline Execution ──
    # Step 1: Concept identification (VACI for images, NLP for text)
    identified_concept = None
    if image:
        # TODO: Run VACI inference
        # identified_concept = await vaci_service.identify_concept(image_path)
        identified_concept = None  # Placeholder

    # Step 2: KG concept mapping
    kg_concepts = []
    if include_kg:
        # TODO: Query Neo4j for concept and its neighborhood
        # kg_concepts = await kg_service.get_concept_context(identified_concept or message)
        pass

    # Step 3: Hybrid retrieval
    sources = []
    # TODO: Run hybrid retrieval pipeline
    # results = await retrieval_service.hybrid_retrieve(
    #     query=message,
    #     concept=identified_concept,
    #     course_code=course_code,
    #     image_path=image_path,
    # )

    # Step 4: PYQ reference linking
    pyq_references = []
    if include_pyq:
        # TODO: Find relevant PYQs via KG traversal
        # pyq_references = await pyq_service.find_related_questions(
        #     concept=identified_concept or message,
        #     course_code=course_code,
        # )
        pass

    # Step 5: LLM response generation
    # TODO: Generate response using LLM with retrieved context
    # answer = await generation_service.generate_response(
    #     query=message,
    #     context=results,
    #     concept=identified_concept,
    #     pyqs=pyq_references,
    # )
    answer = (
        f"[CurriculumLens Pipeline — Placeholder Response]\n\n"
        f"Query: {message}\n"
        f"Modality: {input_modality}\n"
        f"Concept identified: {identified_concept or 'N/A'}\n"
        f"Course filter: {course_code or 'All courses'}\n\n"
        f"The full pipeline (VACI → KG → Hybrid Retrieval → Re-ranking → LLM Generation) "
        f"will be connected in subsequent phases."
    )

    latency_ms = int((time.time() - start_time) * 1000)

    # ── Save Assistant Message ──
    assistant_msg = ChatMessage(
        session_id=session.id,
        role="assistant",
        content=answer,
        identified_concept=identified_concept,
        retrieved_sources=[s.model_dump() for s in sources],
        pyq_references=[p.model_dump() for p in pyq_references],
        kg_concepts=[k.model_dump() for k in kg_concepts],
        latency_ms=latency_ms,
    )
    db.add(assistant_msg)
    await db.flush()

    return ChatResponse(
        session_id=session.id,
        message_id=assistant_msg.id,
        answer=answer,
        sources=sources,
        pyq_references=pyq_references,
        kg_concepts=kg_concepts,
        identified_concept=identified_concept,
        latency_ms=latency_ms,
    )


@router.get("/sessions", response_model=list[ChatSessionOut])
async def list_sessions(
    skip: int = 0,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
):
    """List all chat sessions, most recent first."""
    result = await db.execute(
        select(ChatSession)
        .order_by(ChatSession.updated_at.desc())
        .offset(skip)
        .limit(limit)
    )
    sessions = result.scalars().all()
    return [ChatSessionOut.model_validate(s) for s in sessions]


@router.get("/sessions/{session_id}/messages")
async def get_session_messages(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get all messages in a chat session."""
    result = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.asc())
    )
    messages = result.scalars().all()
    return [
        {
            "id": str(m.id),
            "role": m.role,
            "content": m.content,
            "input_modality": m.input_modality,
            "identified_concept": m.identified_concept,
            "sources": m.retrieved_sources,
            "pyq_references": m.pyq_references,
            "kg_concepts": m.kg_concepts,
            "latency_ms": m.latency_ms,
            "created_at": m.created_at.isoformat(),
        }
        for m in messages
    ]


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Delete a chat session and all its messages."""
    result = await db.execute(
        select(ChatSession).where(ChatSession.id == session_id)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    await db.delete(session)
    return {"message": "Session deleted", "id": str(session_id)}
