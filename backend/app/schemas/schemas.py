"""
CurriculumLens — Pydantic Schemas

Request/response schemas for the API endpoints.
"""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


# ──────────────────────────────────────────
# Documents
# ──────────────────────────────────────────

class DocumentUploadResponse(BaseModel):
    id: UUID
    filename: str
    document_type: str
    source_type: str
    processing_status: str
    message: str


class DocumentOut(BaseModel):
    id: UUID
    filename: str
    original_filename: str
    document_type: str
    source_type: str
    page_count: Optional[int]
    course_code: Optional[str]
    course_name: Optional[str]
    processing_status: str
    created_at: datetime

    class Config:
        from_attributes = True


class DocumentListResponse(BaseModel):
    documents: list[DocumentOut]
    total: int


# ──────────────────────────────────────────
# Chat
# ──────────────────────────────────────────

class ChatQueryRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=5000)
    session_id: Optional[UUID] = None
    course_code: Optional[str] = None
    include_pyq: bool = True
    include_kg: bool = True


class SourceCitation(BaseModel):
    document_name: str
    page_number: Optional[int]
    chunk_text: str
    relevance_score: float


class PYQReference(BaseModel):
    question_text: str
    exam_year: int
    question_type: Optional[str]
    marks: Optional[int]
    difficulty: Optional[str]
    course_code: str


class KGConceptNode(BaseModel):
    concept_name: str
    topic: str
    unit: str
    course: str
    related_concepts: list[str] = []
    prerequisites: list[str] = []


class ChatResponse(BaseModel):
    session_id: UUID
    message_id: UUID
    answer: str
    sources: list[SourceCitation] = []
    pyq_references: list[PYQReference] = []
    kg_concepts: list[KGConceptNode] = []
    identified_concept: Optional[str] = None  # If image was processed
    latency_ms: int


class ChatSessionOut(BaseModel):
    id: UUID
    title: str
    course_code: Optional[str]
    message_count: int = 0
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ──────────────────────────────────────────
# Knowledge Graph
# ──────────────────────────────────────────

class KGNodeOut(BaseModel):
    id: str
    label: str  # Course | Unit | Topic | Concept | Formula
    name: str
    properties: dict = {}


class KGEdgeOut(BaseModel):
    source: str
    target: str
    relationship: str  # belongsTo | prerequisiteOf | relatedTo | testedIn


class KGGraphResponse(BaseModel):
    nodes: list[KGNodeOut]
    edges: list[KGEdgeOut]


class SyllabusParseRequest(BaseModel):
    course_code: str
    course_name: str


# ──────────────────────────────────────────
# PYQ Intelligence
# ──────────────────────────────────────────

class ConceptFrequency(BaseModel):
    concept: str
    frequency: int
    years: list[int]
    avg_marks: float
    question_types: dict[str, int]


class DifficultyTrend(BaseModel):
    year: int
    easy_count: int
    medium_count: int
    hard_count: int


class PYQAnalyticsResponse(BaseModel):
    course_code: str
    total_questions: int
    concept_frequencies: list[ConceptFrequency]
    difficulty_trends: list[DifficultyTrend]
    question_type_distribution: dict[str, int]
    top_predicted_topics: list[str]


# ──────────────────────────────────────────
# VACI (Visual Concept Identification)
# ──────────────────────────────────────────

class VACIResult(BaseModel):
    concept: str
    confidence: float
    topic: Optional[str]
    unit: Optional[str]
    course: Optional[str]
    related_pyqs: list[PYQReference] = []


# ──────────────────────────────────────────
# Health
# ──────────────────────────────────────────

class HealthResponse(BaseModel):
    status: str
    version: str
    services: dict[str, str]
