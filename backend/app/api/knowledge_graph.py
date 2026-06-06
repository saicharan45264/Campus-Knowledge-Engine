"""
CurriculumLens — Knowledge Graph API Routes

Endpoints for KG visualization, syllabus parsing, and concept exploration.
"""

from typing import Optional

from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas.schemas import (
    KGGraphResponse, KGNodeOut, KGEdgeOut,
    SyllabusParseRequest,
)

router = APIRouter(prefix="/knowledge-graph", tags=["Knowledge Graph"])


@router.get("/graph", response_model=KGGraphResponse)
async def get_curriculum_graph(
    course_code: Optional[str] = None,
    depth: int = 3,
):
    """
    Get the Curriculum Knowledge Graph for visualization.

    Returns nodes and edges suitable for D3.js force-directed rendering.
    - depth=1: Course → Units only
    - depth=2: Course → Units → Topics
    - depth=3: Course → Units → Topics → Concepts (default)
    - depth=4: Full graph including Formulas and PYQ links
    """
    # TODO: Query Neo4j and return graph structure
    # cypher = '''
    # MATCH path = (c:Course)-[*1..{depth}]->(n)
    # WHERE c.code = $course_code OR $course_code IS NULL
    # RETURN path
    # '''

    # Placeholder — returns empty graph
    return KGGraphResponse(nodes=[], edges=[])


@router.get("/concepts/{concept_name}")
async def get_concept_details(concept_name: str):
    """
    Get full details for a specific concept node:
    - Parent topic and unit
    - Prerequisites
    - Related concepts
    - Linked PYQ questions
    - Associated resources
    """
    # TODO: Neo4j query for concept neighborhood
    # cypher = '''
    # MATCH (c:Concept {name: $name})
    # OPTIONAL MATCH (c)-[:belongsTo]->(t:Topic)-[:belongsTo]->(u:Unit)-[:belongsTo]->(co:Course)
    # OPTIONAL MATCH (c)-[:prerequisiteOf]->(prereq:Concept)
    # OPTIONAL MATCH (c)-[:relatedTo]-(related:Concept)
    # OPTIONAL MATCH (c)<-[:testedIn]-(q:PYQQuestion)
    # RETURN c, t, u, co, collect(DISTINCT prereq) as prereqs,
    #        collect(DISTINCT related) as related, collect(DISTINCT q) as questions
    # '''
    return {
        "concept": concept_name,
        "message": "KG concept lookup will be connected to Neo4j service",
    }


@router.post("/parse-syllabus")
async def parse_syllabus(
    file: UploadFile = File(...),
    course_code: str = Form(...),
    course_name: str = Form(...),
):
    """
    Parse a syllabus document and auto-construct the CKG structure.

    Pipeline:
    1. Extract text from syllabus PDF
    2. Use LLM to identify Course → Unit → Topic → Concept hierarchy
    3. Create nodes and edges in Neo4j
    4. Return the constructed subgraph for verification
    """
    # TODO: Implement syllabus parsing pipeline
    # content = await extract_text(file)
    # hierarchy = await llm_parse_syllabus(content, course_code, course_name)
    # await neo4j_service.create_course_graph(hierarchy)

    return {
        "message": f"Syllabus parsing for {course_code} will be implemented in Phase 3",
        "course_code": course_code,
        "course_name": course_name,
    }


@router.get("/courses")
async def list_courses():
    """List all courses in the knowledge graph."""
    # TODO: Query Neo4j for all Course nodes
    return {"courses": [], "message": "Will query Neo4j for Course nodes"}


@router.get("/search")
async def search_concepts(
    query: str,
    limit: int = 10,
):
    """Search for concepts in the knowledge graph by name or description."""
    # TODO: Neo4j full-text search on concept names
    return {"results": [], "query": query}
