"""tests/test_integration_neo4j.py — Integration tests for Neo4j driver and GraphRAG retrieval."""
from __future__ import annotations

import pytest

from backend.core.intent_router import classify_query_intent
from backend.database import get_neo4j
from backend.services.graph_service import fetch_problems_by_topic_graph


def _get_neo4j_or_skip():
    driver = get_neo4j()
    try:
        driver.verify_connectivity()
    except Exception as e:
        pytest.skip(f"Neo4j database is unavailable: {e}")
    return driver


@pytest.mark.asyncio
async def test_neo4j_driver_connection():
    driver = _get_neo4j_or_skip()
    # verify connection by executing a simple query
    with driver.session() as session:
        result = session.run("RETURN 1 as num")
        record = result.single()
        assert record["num"] == 1


@pytest.mark.asyncio
async def test_full_pipeline_to_graph_rag():
    driver = _get_neo4j_or_skip()
    intent = await classify_query_intent("give me a list of practice problems on superposition theorem")
    assert intent == "PROBLEM_LIST"

    # We won't assert output length since DB might be empty in CI,
    # but we ensure the function runs without throwing exceptions.
    problems = await fetch_problems_by_topic_graph(driver, "superposition theorem")
    assert isinstance(problems, list)


def test_syllabus_write_merges_existing_course_node():
    """Verify _write_neo4j merges onto existing Course nodes (e.g. from PREREQUISITE_MAP) without constraint error."""
    from backend.parsers.syllabus_parser import _write_neo4j

    driver = _get_neo4j_or_skip()
    test_code = "99TEST101"
    sv_id = "TEST_9999"

    try:
        with driver.session() as session:
            # Simulate prerequisite seeder creating Course node without dept/year
            session.run("MERGE (c:Course {code: $code})", code=test_code)

        _write_neo4j(
            driver=driver,
            dept="TEST",
            year="9999",
            sv_id=sv_id,
            general_info={
                "abbreviations": {},
                "program_outcomes": [],
                "program_educational_objectives": [],
                "program_specific_outcomes": [],
            },
            semesters=[
                {
                    "number": 1,
                    "label": "Semester 1",
                    "total_credits": 4,
                    "courses": [{"code": test_code, "title": "Test Course", "credits": "4"}],
                    "elective_slots": [],
                }
            ],
            baskets=[],
            courses=[
                {
                    "code": test_code,
                    "name": "Test Course",
                    "credits": "4",
                    "prerequisites_courses": [],
                    "prerequisites_knowledge": [],
                    "objectives": [],
                    "outcomes": [],
                    "co_po_mapping": [],
                    "units": [],
                    "textbooks": [],
                    "references": [],
                }
            ],
            rules=[],
            page_texts=[],
            page_type_idx={},
        )

        with driver.session() as session:
            rec = session.run("MATCH (c:Course {code: $code}) RETURN c.dept AS dept, c.year AS year", code=test_code).single()
            assert rec is not None
            assert rec["dept"] == "TEST"
            assert rec["year"] == "9999"
    finally:
        with driver.session() as session:
            session.run("MATCH (n {syllabus_version_id: $sv_id}) DETACH DELETE n", sv_id=sv_id)
            session.run("MATCH (sv:SyllabusVersion {dept: 'TEST', year: '9999'}) DETACH DELETE sv")
            session.run("MATCH (c:Course {code: $code}) DETACH DELETE c", code=test_code)
            session.run("MATCH (p:Program {name: 'TEST'}) DETACH DELETE p")

