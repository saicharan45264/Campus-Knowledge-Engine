"""tests/test_intent_router.py — Unit tests for deterministic and semantic intent routing."""
from __future__ import annotations

import pytest

from backend.core.intent_router import classify_query_intent, normalize_query_typos
from backend.services.graph_service import (
    _extract_requested_aspects,
    _filter_course_detail_aspects,
    _resolve_course_fuzzy,
)


@pytest.mark.asyncio
async def test_classify_problem_list():
    # Should map to PROBLEM_LIST
    intent = await classify_query_intent("give me a list of practice problems on superposition theorem")
    assert intent == "PROBLEM_LIST"


@pytest.mark.asyncio
async def test_classify_prereq():
    # Should map to MULTI_HOP_PREREQ
    intent = await classify_query_intent("what should I know before taking embedded systems")
    assert intent == "MULTI_HOP_PREREQ"


@pytest.mark.asyncio
async def test_classify_syllabus():
    # Should map to SIMPLE_CURRICULUM
    intent = await classify_query_intent("what are the topics in unit 2 of physics")
    assert intent == "SIMPLE_CURRICULUM"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query,expected",
    [
        ("what all courses do i have in 3rd semester", "SIMPLE_CURRICULUM"),
        ("what all syllabus will be covered in machine learning course", "SIMPLE_CURRICULUM"),
        ("what all electives do i have under ai stream", "SIMPLE_CURRICULUM"),
        ("what prerequite courses i should have done before choosing Design and analysis of Algorithms", "MULTI_HOP_PREREQ"),
        ("what prerequite courses for Design and analysis of Algorithms", "MULTI_HOP_PREREQ"),
        ("what are the references or texbooks of the course distributed systems", "SIMPLE_CURRICULUM"),
        ("what is the evaluation pattern of cryptography course", "SIMPLE_CURRICULUM"),
        ("what is the course code of calclulus", "SIMPLE_CURRICULUM"),
        ("Which semester is Operating Systems taught in?", "SIMPLE_CURRICULUM"),
        ("How many credits is Machine Learning?", "SIMPLE_CURRICULUM"),
        ("What are the internal and end-sem marks for Computer Networks?", "SIMPLE_CURRICULUM"),
        ("What are the lab experiments in Data Structures?", "SIMPLE_CURRICULUM"),
        ("What is in Unit 3 of Compiler Design?", "SIMPLE_CURRICULUM"),
        ("What are the COs of Cryptography?", "SIMPLE_CURRICULUM"),
        ("Show BTL 4 questions in DBMS", "GRAPH_PYQ_MAPPING"),
        ("Give me 10 mark questions on Normalization", "PROBLEM_LIST"),
        ("Repeated questions on Deadlock", "PROBLEM_LIST"),
        ("How many total credits are needed for B.Tech CSE?", "SIMPLE_CURRICULUM"),
        ("What is the NPTEL credit exemption rule?", "SIMPLE_CURRICULUM"),
    ],
)
async def test_realistic_student_queries_zero_llm(query: str, expected: str):
    assert await classify_query_intent(query) == expected


def test_normalize_query_typos():
    assert normalize_query_typos("references or texbooks") == "references or textbook"
    assert normalize_query_typos("what prerequite courses") == "what prerequisite courses"


def test_aspect_extraction_and_filtering():
    aspects_eval = _extract_requested_aspects("what is the evaluation pattern of cryptography course")
    assert aspects_eval == {"evaluation"}

    aspects_refs = _extract_requested_aspects("what are the references or textbook of distributed systems")
    assert aspects_refs == {"textbooks", "references"}

    aspects_code = _extract_requested_aspects("what is the course code of calclulus")
    assert aspects_code == {"course_code"}

    sample = [{
        "course_code": "23CSE301",
        "course_name": "Cryptography",
        "semester": 5,
        "credits": 3,
        "L": 3, "T": 0, "P": 0,
        "evaluation_pattern": "50:50",
        "eval_internal": 50,
        "eval_endsem": 50,
        "assessment_components": ["Midterm: 30"],
        "lab_experiments": [],
        "textbooks": ["William Stallings"],
        "references": ["Bruce Schneier"],
        "outcomes": ["CO1: Understand ciphers"],
        "objectives": ["Learn crypto"],
        "prerequisites": ["Discrete Math"],
    }]
    filtered = _filter_course_detail_aspects(sample, aspects_eval)
    assert filtered[0]["evaluation_pattern"] == "50:50"
    assert filtered[0]["textbooks"] == []
    assert filtered[0]["references"] == []
    assert filtered[0]["outcomes"] == []


def test_resolve_course_fuzzy_typo():
    class _FakeResult:
        def data(self):
            return [
                {"code": "23MAT101", "name": "Calculus and Linear Algebra"},
                {"code": "23CSE301", "name": "Machine Learning"},
            ]

    class _FakeSession:
        def run(self, *args, **kwargs):
            return _FakeResult()

    hits = _resolve_course_fuzzy(_FakeSession(), "calclulus", ["calclulus"])
    assert hits and hits[0]["code"] == "23MAT101"


def test_followup_does_not_override_explicit_course():
    from backend.services.rag_orchestrator import append_session_message, resolve_followup_search_query

    sid = "test_followup_override"
    append_session_message(sid, "user", "Show syllabus of 23CSE201")
    # Explicit new course name must NOT carry over 23CSE201
    q_new = "What are the references or texbooks of the course Distributed Systems?"
    assert resolve_followup_search_query(sid, q_new) == q_new
    # Bare/pronoun follow-up SHOULD carry over 23CSE201
    assert "(23CSE201)" in resolve_followup_search_query(sid, "What are its textbooks?")
    assert "(23CSE201)" in resolve_followup_search_query(sid, "What are the references?")
