from datetime import date
from types import SimpleNamespace

import pytest

from backend.services import academic_document_qa_service as qa
from backend.services.academic_document_qa_service import (
    _filter_course_segments,
    _query_dates,
    _requested_departments,
    _requested_semester,
    answer_academic_document_query,
    _is_seating_question,
    _roll_from_question,
    _seating_answer,
    _seating_entries,
)
from backend.academic_tool_calling import fallback_academic_tool, seating_query_intent


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


def _row(text, line, department="ALL"):
    record = SimpleNamespace(
        record_text=text,
        page_number=2,
        line_number=line,
        payload_json="{}",
        document_id="doc-cse-1",
    )
    return (record, "BTech-End-Sem-2023.pdf", "doc-cse-1", department)


def _seat_row(text, line, page=1):
    return {"text": text, "line": line, "page": page, "filename": "BTech-2023-SA.pdf", "document_id": "seat-doc", "payload": {}}


@pytest.fixture
def seating_rows():
    return [
        _seat_row("Seating Arrangement for 6th sem.B.Tech.(2023 Batch) Mid Term Examinations between 19th and 28th Jan.2026", 0),
        _seat_row("05.01.2026 Timings: FN: 09.30am to 11.30am AN: 02.00pm to 04.00pm", 1),
        _seat_row("A - 102 CSE-A CB.EN.U4CSE23001-018 17", 2, 2),
        _seat_row("A - 103 CSE-A CB.EN.U4CSE23020-036 17", 3, 2),
        _seat_row("A - 301 ECE-A CB.EN.U4ECE23001-031 30", 4, 1),
        _seat_row("W - 209 AEE CB.EN.U4AEE23062-072 11", 5, 1),
        _seat_row("E - 201 CIE CB.EN.U4CIE23001-014 13 ARE CB.EN.U4ARE23001-017 16", 6, 1),
        _seat_row("Auditorium near ASB CSE-E CB.EN.U4CSE23401-467 65", 7, 2),
        _seat_row("Auditorium near ASB CSE-F CB.EN.U4CSE23501-569 67", 8, 2),
        _seat_row("Auditorium near ASB CSE-G CB.EN.U4CSE23601-668 63", 9, 2),
        _seat_row("Auditorium near ASB CSE-H CB.EN.U4CSE23701-769 65", 10, 2),
        _seat_row("All Re-register candidates will be seated in W-209 Academic Block I", 11),
    ]


def test_seating_question_intent_precedes_generic_exam_intent():
    question = "What are the dates for the 6th-semester Mid Term Examinations?"
    assert seating_query_intent(question)
    assert fallback_academic_tool(question)["name"] == "lookup_exam_seating"
    assert seating_query_intent("Is there a separate hall for re-registration exams?")
    assert _is_seating_question("Which departments are located in Academic Block III?")


def test_explicit_roll_is_extracted_and_matched_to_the_hall(seating_rows):
    question = "Where is CB.EN.U4ECE23015 seated?"
    assert _roll_from_question(question) == "CB.EN.U4ECE23015"
    answer = _seating_answer(question, seating_rows, "CSE", "VII", "CB.EN.U4CSE23508")
    assert "A-301" in answer["markdown_answer"]
    assert "ECE-A" in answer["markdown_answer"]

    section_query = _seating_answer(
        "Where are CSE Sections E, F, G, and H seated?",
        seating_rows,
        "CSE",
        "VII",
        "CB.EN.U4CSE23508",
    )
    assert "CSE-E" in section_query["markdown_answer"]
    assert "CSE-H" in section_query["markdown_answer"]

    personal = _seating_answer("Where is my exam hall?", seating_rows, "CSE", "VII", "CB.EN.U4CSE23508")
    assert "CSE-F" in personal["markdown_answer"]


def test_seating_rows_supports_hall_sections_and_aggregate_counts(seating_rows):
    entries = _seating_entries(seating_rows)
    cse_f = next(entry for entry in entries if entry["department"] == "CSE" and entry["start"] == 501)
    assert cse_f["section"] == "F"
    assert cse_f["hall"] == "AUDITORIUM NEAR ASB"
    assert cse_f["count"] == 67
    auditorium = _seating_answer("How many students are seated in the Auditorium?", seating_rows, "CSE", "VI", None)
    assert "260" in auditorium["markdown_answer"]
    section = _seating_answer("How many students are in CSE Section F?", seating_rows, "CSE", "VI", None)
    assert "67" in section["markdown_answer"]
    auditorium_sections = _seating_answer("Where are CSE Sections E, F, G, and H seated?", seating_rows, "CSE", "VI", None)
    assert "CSE-E" in auditorium_sections["markdown_answer"]
    assert "CSE-H" in auditorium_sections["markdown_answer"]
    assert "AUDITORIUM NEAR ASB" in auditorium_sections["markdown_answer"]
    total = _seating_answer("How many total students are appearing across all branches?", seating_rows, "CSE", "VI", None)
    assert "students total" in total["markdown_answer"]


def test_seating_supports_room_block_re_registration_and_metadata(seating_rows):
    room = _seating_answer("Who is writing exams in Hall E-201?", seating_rows, "CSE", "VI", None)
    assert "CIE" in room["markdown_answer"] and "ARE" in room["markdown_answer"]
    block = _seating_answer("Which halls are used in Academic Block III?", seating_rows, "CSE", "VI", None)
    assert "A-102" in block["markdown_answer"]
    re_reg = _seating_answer("Where should re-registered candidates sit?", seating_rows, "CSE", "VI", None)
    assert "W-209" in re_reg["markdown_answer"]
    re_registration = _seating_answer("Is there a separate hall for students writing re-registration exams?", seating_rows, "CSE", "VI", None)
    assert "W-209" in re_registration["markdown_answer"]
    window = _seating_answer("What are the dates for the 6th-semester Mid Term Examinations?", seating_rows, "CSE", "VI", None)
    assert "19 to 28 Jan 2026" in window["markdown_answer"]
    fn = _seating_answer("What is the timing for the morning FN session?", seating_rows, "CSE", "VI", None)
    assert "09.30am to 11.30am" in fn["markdown_answer"]
    an = _seating_answer("What is the timing for the afternoon AN session?", seating_rows, "CSE", "VI", None)
    assert "02.00pm to 04.00pm" in an["markdown_answer"]


@pytest.fixture
def exam_db():
    return _FakeDB([
        _row("16/03/2026", 1),
        _row("FN 23EEE311-PowerSystemAnalysis 23ELC311-Power Electronics andDrives 23CSE312-DistributedSystems", 2),
        _row("18/03/2026", 3),
        _row("FN Artificial Intelligence andEdge Computing 23CSE313-Foundations ofCyberSecurity", 4),
        _row("20/03/2026", 5),
        _row("FN 20CYS313-Network Security 23CSE334-CyberForensicsandMalware 23CSE475-GenerativeAI/23CSE473-NeuralNetworksandDeepLearning", 6),
        _row("23/03/2026", 7),
        _row("FN 23CSE311-SoftwareEngineering", 8),
        _row("AN 23HUM244-Understanding Science of Food and Nutrition", 9),
        _row("25/03/2026", 9),
        _row("FN 23CSE314-CompilerDesign", 10),
        _row("27/03/2026", 11),
        _row("FN/AN 23LSE311-Life Skills for Engineers IV", 12),
        _row("28/03/2026", 13),
        _row("FN/AN 19LAW300-Indian Constitution", 14),
        _row("30/03/2026", 13),
        _row("FN 23CSE363-CloudComputing", 14),
        _row("01/04/2026", 15),
        _row("FN 24CYS333-Internet of Things", 16),
        _row("04/04/2026", 17),
        _row("FN 23MEE315-Industrial Engineering", 18),
        _row("16/03/2026", 19),
        _row("FN 20CYS311-Cyber Forensics", 20),
        _row("16/03/2026", 21),
        _row("FN 20CYS314-Applied Cryptography", 22),
    ])


def test_query_date_range_infers_shared_year_for_both_endpoints():
    assert _query_dates("Show exams from March 23 through March 30, 2026") == [
        date(2026, 3, 23), date(2026, 3, 30)
    ]


def test_flattened_exam_row_only_returns_admin_department_courses():
    raw = "FN 23EEE311-PowerSystemAnalysis 23ELC311-Power Electronics andDrives 23CSE312-DistributedSystems"
    filtered = _filter_course_segments(raw, "CSE", "Which CSE courses?", None)
    assert "23CSE312" in filtered
    assert "23EEE311" not in filtered
    assert "23ELC311" not in filtered


@pytest.mark.asyncio
async def test_course_code_and_name_only_exam_lookups(exam_db, monkeypatch):
    async def no_embedding(*_args, **_kwargs):
        raise AssertionError("A direct course match should not call vector fallback")

    monkeypatch.setattr(qa, "embed_text", no_embedding)
    monkeypatch.setattr(qa, "hybrid_search_rrf", no_embedding)
    by_code = await answer_academic_document_query(
        "When is the exam for 23CSE312?", exam_db, "CSE", "VI"
    )
    assert "2026-03-16" in by_code["markdown_answer"]
    assert "23CSE312" in by_code["markdown_answer"]
    assert "23EEE311" not in by_code["markdown_answer"]

    by_name = await answer_academic_document_query(
        "When is the exam for Distributed Systems?", exam_db, "CSE", "VI"
    )
    assert "2026-03-16" in by_name["markdown_answer"]
    assert "23CSE312" in by_name["markdown_answer"]


@pytest.mark.asyncio
async def test_exam_date_range_and_session_filter(exam_db, monkeypatch):
    async def no_embedding(*_args, **_kwargs):
        raise AssertionError("A direct date/session match should not call vector fallback")

    monkeypatch.setattr(qa, "embed_text", no_embedding)
    monkeypatch.setattr(qa, "hybrid_search_rrf", no_embedding)
    ranged = await answer_academic_document_query(
        "Show exams from March 23 through March 30, 2026", exam_db, "CSE", "VI"
    )
    assert "2026-03-23" in ranged["markdown_answer"]
    assert "2026-03-25" in ranged["markdown_answer"]
    assert "2026-03-30" in ranged["markdown_answer"]
    assert "2026-03-27" in ranged["markdown_answer"]
    assert "23LSE311" in ranged["markdown_answer"]
    assert "2026-03-20" not in ranged["markdown_answer"]

    session = await answer_academic_document_query(
        "Which courses are scheduled in the FN session on March 18, 2026?",
        exam_db,
        "CSE",
        "VI",
    )
    assert "23CSE313" in session["markdown_answer"]
    assert "2026-03-16" not in session["markdown_answer"]

    month_day_without_year = await answer_academic_document_query(
        "Which subjects are examined on Monday forenoon, March 23?",
        exam_db,
        "CSE",
        "VI",
    )
    assert "2026-03-23" in month_day_without_year["markdown_answer"]
    assert "23CSE311" in month_day_without_year["markdown_answer"]
    assert "March 23, 2026" in month_day_without_year["title"]

    strictly_afternoon = await answer_academic_document_query(
        "Which courses are conducted strictly in the Afternoon (AN) session?",
        exam_db,
        "CSE",
        "VI",
    )
    assert "23HUM244" in strictly_afternoon["markdown_answer"]
    assert "23LSE311" not in strictly_afternoon["markdown_answer"]


@pytest.mark.asyncio
async def test_course_name_match_does_not_combine_words_from_neighboring_courses(exam_db, monkeypatch):
    async def no_embedding(*_args, **_kwargs):
        return []

    monkeypatch.setattr(qa, "embed_text", no_embedding)
    monkeypatch.setattr(qa, "hybrid_search_rrf", no_embedding)
    result = await answer_academic_document_query(
        "What is the exam date and session for Foundations of Cyber Security?",
        exam_db,
        "CSE",
        "VI",
    )
    assert "2026-03-18" in result["markdown_answer"]
    assert "2026-03-20" not in result["markdown_answer"]
    assert "23CSE313" in result["markdown_answer"]


@pytest.mark.asyncio
async def test_fn_date_query_ignores_ordinary_word_listed(exam_db, monkeypatch):
    async def no_embedding(*_args, **_kwargs):
        return []

    monkeypatch.setattr(qa, "embed_text", no_embedding)
    monkeypatch.setattr(qa, "hybrid_search_rrf", no_embedding)
    result = await answer_academic_document_query(
        "What courses are listed for the FN session on March 20, 2026?",
        exam_db,
        "CSE",
        "VI",
    )
    assert "2026-03-20" in result["markdown_answer"]
    assert "23CSE334" in result["markdown_answer"]


def test_exam_scope_parses_ordinal_semesters_and_avoids_are_department_collision():
    assert _requested_semester("Complete schedule for 6th-semester CSE") == 6
    assert _requested_semester("What exams do ECE students have?") is None
    assert _requested_departments("What exams are scheduled for Foundations of Cyber Security?", "CSE") == ["CSE"]
    assert _requested_departments("Which branches have exams on March 18, 2026?", "CSE") == ["ALL"]


@pytest.mark.asyncio
async def test_multiple_course_code_lookup_returns_each_requested_course(exam_db, monkeypatch):
    async def no_embedding(*_args, **_kwargs):
        return []

    monkeypatch.setattr(qa, "embed_text", no_embedding)
    monkeypatch.setattr(qa, "hybrid_search_rrf", no_embedding)
    result = await answer_academic_document_query(
        "When is Cyber Forensics (20CYS311) / Applied Cryptography (20CYS314)?",
        exam_db,
        "CSE",
        "VI",
    )
    assert "20CYS311" in result["markdown_answer"]
    assert "20CYS314" in result["markdown_answer"]


@pytest.mark.asyncio
async def test_saturday_and_month_queries_filter_the_exam_rows(exam_db, monkeypatch):
    async def no_embedding(*_args, **_kwargs):
        return []

    monkeypatch.setattr(qa, "embed_text", no_embedding)
    monkeypatch.setattr(qa, "hybrid_search_rrf", no_embedding)
    saturday = await answer_academic_document_query(
        "Are there any examinations conducted on Saturdays?", exam_db, "CSE", "VI"
    )
    assert "2026-03-28" in saturday["markdown_answer"]
    assert "2026-03-16" not in saturday["markdown_answer"]

    april = await answer_academic_document_query(
        "Which branches have examinations in April 2026?", exam_db, "CSE", "VI"
    )
    assert "2026-04-01" in april["markdown_answer"]
    assert "2026-04-04" in april["markdown_answer"]
