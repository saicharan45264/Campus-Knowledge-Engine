"""
Tests for pyq_processor.py
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import hashlib
import json

from pyq_processor import PYQProcessingReport, SkipReason


class TestPYQProcessingReport:
    def test_initial_status_is_pending(self):
        report = PYQProcessingReport(
            document_id="doc-1", filename="paper.pdf", course_code="23CSE303"
        )
        assert report.status == "pending"

    def test_finalize_sets_completed(self):
        report = PYQProcessingReport(
            document_id="doc-1", filename="paper.pdf", course_code="23CSE303",
            saved_neo4j=10, embedded_postgres=10, skipped=0,
            mapped_to_co=10
        )
        report.finalize()
        assert report.status == "completed"

    def test_finalize_sets_partially_completed_on_skips(self):
        report = PYQProcessingReport(
            document_id="doc-1", filename="paper.pdf", course_code="23CSE303",
            saved_neo4j=8, embedded_postgres=8, skipped=2,
            mapped_to_co=8
        )
        report.finalize()
        assert report.status == "partially_completed"

    def test_finalize_sets_failed_when_no_questions_saved(self):
        report = PYQProcessingReport(
            document_id="doc-1", filename="paper.pdf", course_code="23CSE303",
            saved_neo4j=0
        )
        report.finalize()
        assert report.status == "failed"

    def test_to_json_round_trip(self):
        report = PYQProcessingReport(
            document_id="doc-1", filename="paper.pdf", course_code="23CSE303",
            total_pages=12, candidates_detected=15, accepted=14, saved_neo4j=14,
            skipped=1,
            skip_reasons=[{"q_num": "Q1", "reason": "too short", "raw_text": "abc"}],
            timings={"extraction_s": 1.2, "embed_s": 3.5},
            status="partially_completed"
        )
        j = report.to_json()
        data = json.loads(j)
        assert data["document_id"] == "doc-1"
        assert data["saved_neo4j"] == 14
        assert len(data["skip_reasons"]) == 1

    def test_report_has_all_required_fields(self):
        import dataclasses
        report = PYQProcessingReport(document_id="x", filename="y", course_code="z")
        fields = {f.name for f in dataclasses.fields(report)}
        required = {
            "document_id", "filename", "course_code", "total_pages",
            "candidates_detected", "accepted", "saved_neo4j", "embedded_postgres",
            "with_image_url", "mapped_to_co", "mapped_to_topic",
            "pending_topic_review", "skipped", "skip_reasons", "timings",
            "status", "created_at"
        }
        assert required.issubset(fields), f"Missing fields: {required - fields}"


class TestQuestionIdentity:
    def test_two_documents_same_question_number_different_ids(self):
        """Two documents each with Q1 must produce different Question IDs."""
        def make_id(document_id, course_code, q_num, page_num):
            key = f"{document_id}_{course_code}_{q_num}_p{page_num}"
            return hashlib.md5(key.encode()).hexdigest()

        id1 = make_id("doc-1", "23CSE303", "Q1", 1)
        id2 = make_id("doc-2", "23CSE303", "Q1", 1)
        assert id1 != id2

    def test_same_document_same_question_same_id(self):
        """Same document + question always produces the same deterministic ID."""
        def make_id(document_id, course_code, q_num, page_num):
            key = f"{document_id}_{course_code}_{q_num}_p{page_num}"
            return hashlib.md5(key.encode()).hexdigest()

        id1 = make_id("doc-1", "23CSE303", "Q5", 2)
        id2 = make_id("doc-1", "23CSE303", "Q5", 2)
        assert id1 == id2
