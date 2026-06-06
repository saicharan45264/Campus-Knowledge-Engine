"""
CurriculumLens — SQLAlchemy Models

Database models for documents, chunks, PYQ questions, users, and chat sessions.
"""

import uuid
from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import (
    Column, String, Integer, Float, Text, Boolean, DateTime, 
    ForeignKey, JSON, Enum, Index
)
from sqlalchemy.dialects.postgresql import UUID, ARRAY, TSVECTOR
from pgvector.sqlalchemy import Vector
from sqlalchemy.orm import relationship

from app.core.database import Base


class DocumentType(str, PyEnum):
    PDF = "pdf"
    PPT = "ppt"
    IMAGE = "image"
    HANDWRITTEN = "handwritten"


class SourceType(str, PyEnum):
    LECTURE_NOTE = "lecture_note"
    PYQ = "pyq"
    TEXTBOOK = "textbook"
    PPT = "ppt"
    REFERENCE = "reference"


class ProcessingStatus(str, PyEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class QuestionType(str, PyEnum):
    MCQ = "mcq"
    SHORT_ANSWER = "short_answer"
    LONG_ANSWER = "long_answer"
    NUMERICAL = "numerical"
    DIAGRAM = "diagram"
    PROOF = "proof"


class DifficultyLevel(str, PyEnum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


# ──────────────────────────────────────────
# Document Management
# ──────────────────────────────────────────

class Document(Base):
    __tablename__ = "documents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    filename = Column(String(500), nullable=False)
    original_filename = Column(String(500), nullable=False)
    document_type = Column(Enum(DocumentType), nullable=False)
    source_type = Column(Enum(SourceType), nullable=False)
    file_path = Column(String(1000), nullable=False)
    file_size_bytes = Column(Integer)
    page_count = Column(Integer)
    course_code = Column(String(50), index=True)
    course_name = Column(String(200))
    processing_status = Column(
        Enum(ProcessingStatus), default=ProcessingStatus.PENDING
    )
    processing_error = Column(Text, nullable=True)
    metadata_json = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    chunks = relationship("DocumentChunk", back_populates="document", cascade="all, delete-orphan")
    pages = relationship("DocumentPage", back_populates="document", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_doc_course_source", "course_code", "source_type"),
    )


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id"), nullable=False)
    chunk_index = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    page_number = Column(Integer)
    token_count = Column(Integer)
    embedding = Column(Vector(1024))  # BGE-M3 1024-dim dense vector
    content_tsvector = Column(TSVECTOR)  # Full-text search
    metadata_json = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    document = relationship("Document", back_populates="chunks")

    __table_args__ = (
        Index("idx_chunk_doc_index", "document_id", "chunk_index"),
        Index("idx_chunk_embedding", "embedding", postgresql_using="hnsw", postgresql_with={"m": 16, "ef_construction": 64}, postgresql_ops={"embedding": "vector_cosine_ops"}),
        Index("idx_chunk_content_tsvector", "content_tsvector", postgresql_using="gin"),
    )


class DocumentPage(Base):
    """Stores page-level data for visual retrieval (ColPali embeddings)."""
    __tablename__ = "document_pages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id"), nullable=False)
    page_number = Column(Integer, nullable=False)
    image_path = Column(String(1000))  # Path to rendered page image
    visual_embedding_id = Column(String(200))  # Qdrant point ID for ColPali embedding
    extracted_text = Column(Text)  # Fallback text extraction
    identified_concepts = Column(JSON, default=list)  # VACI output
    metadata_json = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    document = relationship("Document", back_populates="pages")

    __table_args__ = (
        Index("idx_page_doc_num", "document_id", "page_number"),
    )


# ──────────────────────────────────────────
# PYQ Intelligence
# ──────────────────────────────────────────

class PYQQuestion(Base):
    """Individual questions extracted from Previous Year Question papers."""
    __tablename__ = "pyq_questions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id"), nullable=False)
    question_text = Column(Text, nullable=False)
    question_number = Column(String(20))
    question_type = Column(Enum(QuestionType))
    difficulty_level = Column(Enum(DifficultyLevel))
    marks = Column(Integer)
    exam_year = Column(Integer, index=True)
    exam_type = Column(String(50))  # mid-term, end-semester, supplementary
    course_code = Column(String(50), index=True)
    topic_labels = Column(ARRAY(String))  # Topics from CKG
    concept_labels = Column(ARRAY(String))  # Concepts from CKG
    has_diagram = Column(Boolean, default=False)
    has_formula = Column(Boolean, default=False)
    answer_text = Column(Text, nullable=True)  # If answer is available
    confidence_score = Column(Float, default=0.0)  # Classification confidence
    metadata_json = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_pyq_course_year", "course_code", "exam_year"),
        Index("idx_pyq_difficulty", "course_code", "difficulty_level"),
    )


# ──────────────────────────────────────────
# Chat & Sessions
# ──────────────────────────────────────────

class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title = Column(String(200), default="New Chat")
    course_code = Column(String(50), nullable=True)  # Optional course filter
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    messages = relationship("ChatMessage", back_populates="session", cascade="all, delete-orphan")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(UUID(as_uuid=True), ForeignKey("chat_sessions.id"), nullable=False)
    role = Column(String(20), nullable=False)  # user | assistant | system
    content = Column(Text, nullable=False)
    input_modality = Column(String(20), default="text")  # text | voice | image
    image_path = Column(String(1000), nullable=True)  # If image was uploaded
    identified_concept = Column(String(200), nullable=True)  # VACI result
    retrieved_sources = Column(JSON, default=list)  # Source citations
    pyq_references = Column(JSON, default=list)  # Linked PYQ questions
    kg_concepts = Column(JSON, default=list)  # KG nodes traversed
    latency_ms = Column(Integer)  # Response generation time
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    session = relationship("ChatSession", back_populates="messages")

    __table_args__ = (
        Index("idx_msg_session_created", "session_id", "created_at"),
    )
