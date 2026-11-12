"""Regression checks for semester-safe exam answers and month calendar queries."""

from datetime import datetime
from types import SimpleNamespace
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from services.academic_document_qa_service import answer_academic_document_query
from services.academic_qa_service import answer_calendar_query


class _ScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _DocumentRowsResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeDB:
    def __init__(self, *, documents=(), events=()):
        self.documents = list(documents)
        self.events = list(events)
        self.calls = 0

    async def execute(self, _statement):
        self.calls += 1
        if self.documents:
            return _DocumentRowsResult(self.documents)
        return _ScalarResult(self.events)


@pytest.mark.asyncio
async def test_month_query_returns_month_events_instead_of_a_single_day():
    events = [
        SimpleNamespace(name="End Sem Exam Commencement - All Third Years", start_date=datetime(2026, 10, 26), event_category="exam"),
        SimpleNamespace(name="Wednesday's Timetable", start_date=datetime(2026, 10, 10), event_category="timetable_override"),
        SimpleNamespace(name="Pongal", start_date=datetime(2027, 1, 15), event_category="holiday"),
    ]
    db = _FakeDB(events=events)

    result = await answer_calendar_query("What events are scheduled in October 2026?", db)

    assert result["calendar_mode"] == "month_events"
    assert result["calendar_period"] == "October 2026"
    assert result["markdown_answer"] is None
    assert [event["name"] for event in result["events"]] == ["End Sem Exam Commencement - All Third Years"]


@pytest.mark.asyncio
async def test_semester_midterm_commencement_uses_the_matching_calendar_event():
    db = _FakeDB(events=[
        SimpleNamespace(
            name="Mid Term ALL Higher Semesters",
            start_date=datetime(2026, 8, 17),
            event_category="exam",
        ),
        SimpleNamespace(
            name="Mid Term Examinations for all higher semester commences",
            start_date=datetime(2027, 1, 27),
            event_category="exam",
        ),
    ])

    result = await answer_calendar_query(
        "When do midterm examinations commence for Semester 5?", db
    )

    assert result["calendar_mode"] == "milestone_lookup"
    assert "Semester 5" in result["markdown_answer"]
    assert "August 17, 2026 (Monday)" in result["markdown_answer"]
    assert len(result["events"]) == 1


@pytest.mark.asyncio
async def test_explicit_semester_mismatch_does_not_query_other_student_scope():
    db = _FakeDB()

    result = await answer_academic_document_query(
        "When do midterm examinations commence for Semester 5?", db, "CSE", "VII"
    )

    assert "profile is **CSE Semester VII**" in result["markdown_answer"]
    assert "Semester V" in result["markdown_answer"]
    assert db.calls == 0


@pytest.mark.asyncio
async def test_midterm_query_never_formats_an_end_semester_document_as_midterm():
    record_id, document_id = uuid.uuid4(), uuid.uuid4()
    rows = [(
        record_id, 1, 1, "text", "End-Semester Examination Schedule", None,
        "BTech-End-Sem-2023.pdf", document_id,
    )]
    db = _FakeDB(documents=rows)

    result = await answer_academic_document_query(
        "When do midterm examinations commence for Semester 6?", db, "CSE", "VI"
    )

    assert "does not contain a matching Mid-Term schedule" in result["markdown_answer"]
    assert "End-Semester" in result["markdown_answer"]


@pytest.mark.asyncio
async def test_no_schedule_from_another_scope_is_returned_when_exact_scope_is_empty():
    db = _FakeDB()

    result = await answer_academic_document_query(
        "Show the end-semester schedule", db, "CSE", "VII"
    )

    assert "No exam schedule is indexed for **CSE Semester VII**" in result["markdown_answer"]
    assert db.calls == 1


@pytest.mark.asyncio
async def test_seating_lookup_uses_the_authenticated_roll_number_without_tool_arguments():
    record_id, document_id = uuid.uuid4(), uuid.uuid4()
    rows = [(
        record_id, 1, 1, "seating", "CSE23001 - 010 A-105 CSE-F", None,
        "BTech-2023-SA.pdf", document_id,
    )]
    db = _FakeDB(documents=rows)

    result = await answer_academic_document_query(
        "Where is my exam seat? [Structured academic lookup: exam seating]",
        db,
        "CSE",
        "VI",
        student_roll_number="CSE23005",
    )

    assert "Allocated Exam Hall:** **A-105" in result["markdown_answer"]
    assert "CSE23005" in result["markdown_answer"]
