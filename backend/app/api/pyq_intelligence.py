"""
CurriculumLens — PYQ Intelligence API Routes

Endpoints for PYQ analytics: concept frequency, difficulty trends,
question type distribution, and topic importance prediction.
"""

from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.core.database import get_db
from app.models.models import PYQQuestion
from app.schemas.schemas import PYQAnalyticsResponse, ConceptFrequency, DifficultyTrend

router = APIRouter(prefix="/pyq", tags=["PYQ Intelligence"])


@router.get("/analytics/{course_code}", response_model=PYQAnalyticsResponse)
async def get_pyq_analytics(
    course_code: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Get comprehensive PYQ analytics for a course:

    1. Concept frequency heatmap data
    2. Difficulty trend across years
    3. Question type distribution
    4. Predicted important topics for upcoming exams
    """
    # Count total questions
    total_result = await db.execute(
        select(func.count(PYQQuestion.id))
        .where(PYQQuestion.course_code == course_code)
    )
    total = total_result.scalar() or 0

    # TODO: Compute analytics from PYQ data
    # This will be implemented once PYQ processing pipeline is complete

    return PYQAnalyticsResponse(
        course_code=course_code,
        total_questions=total,
        concept_frequencies=[],
        difficulty_trends=[],
        question_type_distribution={},
        top_predicted_topics=[],
    )


@router.get("/questions/{course_code}")
async def get_pyq_questions(
    course_code: str,
    year: Optional[int] = None,
    topic: Optional[str] = None,
    difficulty: Optional[str] = None,
    question_type: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    """
    List PYQ questions with filters.
    Supports filtering by year, topic, difficulty, and question type.
    """
    query = (
        select(PYQQuestion)
        .where(PYQQuestion.course_code == course_code)
        .order_by(PYQQuestion.exam_year.desc())
    )

    if year:
        query = query.where(PYQQuestion.exam_year == year)
    if difficulty:
        query = query.where(PYQQuestion.difficulty_level == difficulty)
    if question_type:
        query = query.where(PYQQuestion.question_type == question_type)

    query = query.offset(skip).limit(limit)
    result = await db.execute(query)
    questions = result.scalars().all()

    return {
        "questions": [
            {
                "id": str(q.id),
                "question_text": q.question_text,
                "exam_year": q.exam_year,
                "question_type": q.question_type.value if q.question_type else None,
                "difficulty": q.difficulty_level.value if q.difficulty_level else None,
                "marks": q.marks,
                "topics": q.topic_labels,
                "concepts": q.concept_labels,
                "has_diagram": q.has_diagram,
                "has_formula": q.has_formula,
            }
            for q in questions
        ],
        "total": len(questions),
    }


@router.get("/concept-frequency/{course_code}")
async def get_concept_frequency_heatmap(
    course_code: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Get concept frequency data for heatmap visualization.

    Returns: {concept_name: {year: count}} for all concepts across all exam years.
    """
    # TODO: Aggregate concept frequency from PYQ questions
    # This requires the concept_labels array to be populated by the PYQ processing pipeline
    return {
        "course_code": course_code,
        "heatmap_data": {},
        "message": "Will be populated once PYQ processing pipeline is complete",
    }


@router.get("/difficulty-curve/{course_code}")
async def get_difficulty_curve(
    course_code: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Get difficulty trend data for chart visualization.

    Returns difficulty distribution per year to show how exam difficulty has evolved.
    """
    # TODO: Aggregate difficulty levels by year
    return {
        "course_code": course_code,
        "curves": [],
        "message": "Will be populated once PYQ processing pipeline is complete",
    }


@router.get("/predict-topics/{course_code}")
async def predict_important_topics(
    course_code: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Predict important topics for the upcoming exam based on:
    - Concept frequency trends
    - Recency weighting (recent years matter more)
    - Difficulty progression patterns
    - Concept coverage gaps (topics not asked recently are due)
    """
    # TODO: Implement prediction algorithm
    return {
        "course_code": course_code,
        "predicted_topics": [],
        "confidence_scores": {},
        "message": "Topic prediction algorithm will be implemented in Phase 6",
    }
