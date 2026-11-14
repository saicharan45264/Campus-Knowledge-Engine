import json
from types import SimpleNamespace

import pytest

from backend.services.academic_document_qa_service import answer_academic_document_query


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeDB:
    def __init__(self, rows):
        self.rows = rows

    async def execute(self, _statement):
        return _Result(self.rows)


@pytest.fixture
def lab_db():
    metadata = {
        "facility": "IT Lab-I",
        "location": "Academic Block-2, Ground Floor",
        "room": "D-102",
        "capacity": 71,
        "academic_term": "Even Semester",
        "academic_year": "2025–2026",
    }
    entries = [
        ("Monday", [2, 3], "22CSC282 Design and Analysis of Algorithms Lab Int DAS - Sem IV Mr Rahul Pawar Ms D Bharathi"),
        ("Monday", [4, 5, 6], "23CCE284 Database Management Systems Lab B.Tech CCE (A Section) Mr Sriram Dr D Venkataraman"),
        ("Monday", [8, 9], "23ELC213 Data Structures and Algorithms B.Tech ELC-IV Sem-A Dr D Venkataraman Ms D Bharathi"),
        ("Tuesday", [4, 5], "23CSE212 Principles of Functional Languages B.Tech CSE IV Sem H Ms Radhika G Ms Subathra P Dr S Vandhana"),
        ("Tuesday", [8, 9, 10], "23CSE115 Algorithmic Thinking and Computer Programming I Yr EEE A (60) Murugaraj Govindaraju"),
        ("Wednesday", [4, 5, 6], "23CCE284 Database Management Systems Lab B.Tech CCE IV Sem-B Dr Bagavathi C Dr N Radhika"),
        ("Wednesday", [8, 9, 10], "23CSE115 Algorithmic Thinking and Computer Programming I Yr EEE B (60) Dr Kanakadhurga D"),
        ("Thursday", [4, 5, 6], "25ARE112 Programming in C++ B.Tech Mech II Sem Ms Rohini S Dr R Gowtham"),
        ("Friday", [2, 3], "23ELC213 Data Structures and Algorithms B.Tech ELC-IV Sem-B Dr D Venkataraman Ms Nayana"),
        ("Friday", [8, 9], "23ELC114 Computer Programming B.Tech ELC-II Sem-B Ms Arunima M Dr N Lalithamani"),
    ]
    rows = []
    for index, (day, slots, text) in enumerate(entries):
        payload = {
            **metadata,
            "day": day,
            "slots": slots,
            "time": "10:45 AM–01:15 PM" if 4 in slots else "08:50 AM–10:30 AM",
            "text": text,
        }
        record = SimpleNamespace(
            record_text=f"{day} · Slots {slots} · {text}",
            page_number=1,
            line_number=index,
            payload_json=json.dumps(payload),
            document_id="lab-doc",
        )
        rows.append((record, "lab.pdf", "lab-doc", "CSE"))
    return _FakeDB(rows)


@pytest.mark.asyncio
async def test_lab_facility_facts_use_uploaded_header(lab_db):
    location = await answer_academic_document_query("Where is IT Lab-I located?", lab_db, "CSE", "IV")
    assert "Academic Block-2, Ground Floor" in location["markdown_answer"]
    assert "D-102" in location["markdown_answer"]

    capacity = await answer_academic_document_query("How many desktop computers are available in IT Lab-I?", lab_db, "CSE", "IV")
    assert "71 desktop PCs" in capacity["markdown_answer"]

    term = await answer_academic_document_query("Which academic semester does this lab schedule belong to?", lab_db, "CSE", "IV")
    assert "Even Semester 2025–2026" in term["markdown_answer"]


@pytest.mark.asyncio
async def test_lab_vacancy_supports_clock_range_and_weekly_free_slots(lab_db):
    friday = await answer_academic_document_query(
        "Is IT Lab-I free on Friday between 10:45 AM and 1:15 PM?", lab_db, "CSE", "IV"
    )
    assert "Friday Slots 4, 5, 6" in friday["markdown_answer"]

    weekly = await answer_academic_document_query("When is IT Lab-I vacant during the week?", lab_db, "CSE", "IV")
    assert "Monday" in weekly["markdown_answer"] and "Friday" in weekly["markdown_answer"]
    monday = await answer_academic_document_query("When is IT Lab-I free on Monday?", lab_db, "CSE", "IV")
    assert "Monday Slots 1, 7, 10, 11, 12" in monday["markdown_answer"]
    assert "daily lunch break" in monday["markdown_answer"]


@pytest.mark.asyncio
async def test_lab_queries_filter_course_section_and_faculty(lab_db):
    dbms = await answer_academic_document_query(
        "Who handles Database Management Systems Lab (23CCE284) for Section A vs Section B?", lab_db, "CSE", "IV"
    )
    assert "Monday" in dbms["markdown_answer"] and "Wednesday" in dbms["markdown_answer"]
    assert "Dr D Venkataraman" in dbms["markdown_answer"] and "Dr N Radhika" in dbms["markdown_answer"]

    faculty = await answer_academic_document_query(
        "Which labs are handled by Dr. D. Venkataraman in IT Lab-1?", lab_db, "CSE", "IV"
    )
    assert "23CCE284" in faculty["markdown_answer"] and "23ELC213" in faculty["markdown_answer"]
    assert "23CSE115" not in faculty["markdown_answer"]
