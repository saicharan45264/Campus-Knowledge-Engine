from datetime import datetime
from types import SimpleNamespace

import pytest

from backend.academic_tool_calling import calendar_query_intent, fallback_academic_tool
from backend.services.calendar_qa_service import _event_matches_milestone, _extract_calendar_year


@pytest.mark.parametrize("question", [
    "list all holidays", "when is First Onam?", "is coming Friday a working day?",
    "what timetable is followed on Saturday June 20?", "which Saturday is working next?",
    "When do final exams begin for Semester 7?", "Total instructional class days for Semester 7",
])
def test_calendar_question_routes_to_calendar(question):
    assert calendar_query_intent(question)
    assert fallback_academic_tool(question)["name"] == "lookup_academic_calendar"


def test_academic_year_is_not_a_single_year_filter():
    assert _extract_calendar_year("show all holidays for AY 2026-2027") is None
    assert _extract_calendar_year("all holidays in 2026") == 2026


def test_commencement_does_not_match_exam_or_different_roman_cohort():
    e = SimpleNamespace(name="Commencement of Even Sem for All IV yr , III Yr", start_date=datetime(2026, 11, 16))
    assert not _event_matches_milestone(e, "reopen", 1)
    e.name = "End Sem Exam Commencement - I year B.Tech"
    assert not _event_matches_milestone(e, "reopen", 1)
    e.name = "Commencement for First Year ASC,ASE,ASAI"
    assert _event_matches_milestone(e, "reopen", 1)


def test_holidays_include_nonworking_weekends_and_exclude_overrides():
    from backend.services.calendar_qa_service import _named_holidays
    days = [
        SimpleNamespace(calendar_date=datetime(2026, 11, 14), day_of_week='Sat', status='H', particulars='', timetable_override=None),
        SimpleNamespace(calendar_date=datetime(2026, 11, 15), day_of_week='Sun', status='H', particulars='', timetable_override=None),
        SimpleNamespace(calendar_date=datetime(2026, 11, 21), day_of_week='Sat', status='W', particulars='', timetable_override='Monday'),
    ]
    holidays = _named_holidays(days, include_weekends=True)
    assert [h['date_iso'] for h in holidays] == ['2026-11-14', '2026-11-15']
