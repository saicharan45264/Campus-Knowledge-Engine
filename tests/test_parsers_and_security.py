"""tests/test_parsers_and_security.py — Automated tests for parsers, sanitization, and storage security."""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from backend.api.auth import _create_token, hash_password, verify_password
from backend.main import app
from backend.parsers.pyq_parser import extract_pyq_structured
from backend.services.storage_service import upload_question_image_to_cloudinary


def test_bcrypt_72_byte_safety():
    """Verify passwords exceeding 72 bytes are safely handled without raising ValueError."""
    long_pass = "a" * 150
    h = hash_password(long_pass)
    assert h.startswith("$2b$")
    assert verify_password(long_pass, h) is True
    assert verify_password("wrong_password", h) is False


def test_storage_service_path_traversal_sanitization(tmp_path, monkeypatch):
    """Verify that malicious public_ids cannot escape the target uploads directory."""
    from backend.config import settings

    test_dir = str(tmp_path / "images")
    monkeypatch.setattr(settings, "uploads_dir", str(tmp_path))

    # Attempt path traversal
    malicious_id = "../../../etc_passwd_exploit"
    url = upload_question_image_to_cloudinary(b"dummy_image_data", malicious_id)

    assert ".." not in url
    # Ensure written file is located inside test_dir, not outside
    written_files = list(Path(test_dir).glob("*.jpg"))
    assert len(written_files) == 1
    assert ".." not in written_files[0].name


def test_pyq_structured_parser_on_real_pdf():
    """Verify extract_pyq_structured extracts questions cleanly from a test syllabus PDF."""
    pdf_path = "tests/test_data/19CSE312_VI Sem Mid-Term_Even 2024.pdf"
    if not os.path.exists(pdf_path):
        pytest.skip(f"Test PDF {pdf_path} not found.")

    questions = extract_pyq_structured(pdf_path, "19CSE312", "test-doc-id-123")
    assert isinstance(questions, list)
    assert len(questions) > 0
    first_q = questions[0]
    assert "question_number" in first_q
    assert "question_text" in first_q
    assert first_q["course_code"] == "19CSE312"


@pytest.mark.asyncio
async def test_image_query_rejects_non_image():
    """Verify /image-query rejects non-image payloads with 400 Bad Request."""
    token = _create_token("student", "student")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        files = {"file": ("malicious.sh", b"#!/bin/bash\necho bad", "text/plain")}
        res = await ac.post(
            "/image-query",
            files=files,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 400
        assert "not a valid image" in res.json().get("detail", "")


def test_ensure_neo4j_schema_is_scoped_by_doc_type():
    """Verify Neo4j schema setup creates only syllabus or PYQ labels on demand."""
    from unittest.mock import MagicMock

    from backend.database import ensure_neo4j_schema

    mock_session = MagicMock()
    mock_driver = MagicMock()
    mock_driver.session.return_value.__enter__.return_value = mock_session

    ensure_neo4j_schema(mock_driver, "syllabus")
    syllabus_queries = [c.args[0] for c in mock_session.run.call_args_list]
    assert any("Course" in q for q in syllabus_queries)
    assert any("topic_name_ft" in q for q in syllabus_queries)
    assert not any("Question" in q for q in syllabus_queries)

    mock_session.reset_mock()
    ensure_neo4j_schema(mock_driver, "pyq")
    pyq_queries = [c.args[0] for c in mock_session.run.call_args_list]
    assert any("Question" in q for q in pyq_queries)
    assert not any("topic_name_ft" in q for q in pyq_queries)

    with pytest.raises(ValueError):
        ensure_neo4j_schema(mock_driver, "unknown")


@pytest.mark.asyncio
async def test_fetch_problems_guards_missing_fulltext_index():
    """Verify fulltext queryNodes is skipped cleanly when topic_name_ft does not exist yet."""
    from unittest.mock import MagicMock

    from backend.services.graph_service import fetch_problems_by_topic_graph

    empty_match_res = MagicMock()
    empty_match_res.data.return_value = []
    empty_tested_res = MagicMock()
    empty_tested_res.data.return_value = []
    idx_absent_res = MagicMock()
    idx_absent_res.single.return_value = {"cnt": 0}

    mock_session = MagicMock()
    mock_session.run.side_effect = [empty_match_res, empty_tested_res, idx_absent_res]
    mock_driver = MagicMock()
    mock_driver.session.return_value.__enter__.return_value = mock_session

    rows = await fetch_problems_by_topic_graph(mock_driver, "Binary Search Trees")
    assert rows == []
    executed_queries = [c.args[0] for c in mock_session.run.call_args_list]
    assert not any("db.index.fulltext.queryNodes" in q for q in executed_queries)


def test_pyq_23cse104_part_marks_btl_and_footer_trim():
    """Verify 23CSE104 PYQ extracts Part-header marks, [ BTL 2], course_title, and trims footer."""
    pdf_path = Path("tests/test_data/23CSE104_I Sem Mid-Term_Odd 2023.pdf")
    if not pdf_path.exists():
        pytest.skip("Sample PYQ PDF not present in workspace")

    questions = extract_pyq_structured(str(pdf_path), "23CSE104")
    assert len(questions) == 7
    assert "electrical" in (questions[0].get("course_title") or "").lower()
    # Part A = 6 marks (Q1-Q3), Part B = 8 marks (Q4-Q7)
    assert [q["marks"] for q in questions[:3]] == [6, 6, 6]
    assert [q["marks"] for q in questions[3:]] == [8, 8, 8, 8]
    # Q3 had '[ BTL 2]' with leading space
    assert questions[2]["btl_tag"] == "BTL2"
    # Q7 must not swallow the trailing Mark Distribution footer table
    assert "mark distribution" not in questions[6]["question_text"].lower()


def test_extract_topic_and_course_code_specific():
    """Verify specific-topic extraction strips modifier noise like marks, 'only', and course codes."""
    from backend.services.rag_orchestrator import (
        extract_course_code_from_query,
        extract_topic_from_query,
    )

    q = "give me 6 mark questions only on mesh analysis in 23CSE104"
    assert extract_topic_from_query(q) == "Mesh Analysis"
    assert extract_course_code_from_query(q) == "23CSE104"


def test_syllabus_parser_avp_eval_and_chunks():
    """Verify B.Tech CSE 2023 PDF extracts AVP courses, clean eval ratios, 10 slots, and <=1650 chunks."""
    import pymupdf as fitz

    from backend.parsers.syllabus_parser import (
        _parse_courses,
        _parse_curriculum_rules,
        _parse_elective_baskets,
        _parse_general_info,
        _parse_semester_tables,
        build_syllabus_chunks,
        phase1_fast_pass,
        phase2_table_pass,
        set_live_doc,
    )

    pdf_path = Path("tests/test_data/B.Tech CSE - 2023 Curriculum.pdf")
    if not pdf_path.exists():
        pytest.skip("Curriculum PDF not present in workspace")

    doc = fitz.open(str(pdf_path))
    try:
        pt, pti = phase1_fast_pass(doc)
        ptbl = phase2_table_pass(doc, pti)
        set_live_doc(doc)
        gi = _parse_general_info(pt, pti, ptbl)
        sems = _parse_semester_tables(pt, pti, ptbl)
        baskets = _parse_elective_baskets(pt, pti, ptbl)
        courses = _parse_courses(pt, pti, sems, baskets)
        rules = _parse_curriculum_rules(pt, pti)
    finally:
        set_live_doc(None)
        doc.close()

    by_code = {c["code"]: c for c in courses}
    # AVP courses recovered (pp. 203-205)
    assert "22AVP201" in by_code and "22AVP221" in by_code
    # False 13:97 fixed to 70:30 on 23CSE357
    assert by_code["23CSE357"]["evaluation_pattern"]["ratio"] == "70:30"
    # Combined TEXTBOOKS / REFERENCES extracted
    assert len(by_code["23HUM242"]["textbooks"]) >= 1
    assert len(by_code["23TAM231"]["textbooks"]) >= 1
    # All 10 electiveCourse slots preserved (including PE IV, V, VI in Sem 7)
    all_slots = [sl["name"] for s in sems for sl in s["elective_slots"]]
    assert len(all_slots) == 10
    assert len(baskets) == 12
    assert len(gi["framework"]) >= 8

    chunks = build_syllabus_chunks("CSE", "2023", gi, sems, baskets, courses, rules)
    assert len(chunks) > 400
    assert max(len(c["content"]) for c in chunks) <= 1650

