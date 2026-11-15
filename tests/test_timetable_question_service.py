from unittest.mock import AsyncMock

import pytest

from backend.services import academic_qa_service as service
from backend.services.timetable_directory import is_timetable_directory_query


def session(day, slot, activity, start, end):
    return {'weekday':day,'slot':slot,'activity':activity,'start_time':start,'end_time':end,
            'course_code':None,'faculty':None,'venue':None,'elective_bucket':None}


@pytest.mark.asyncio
async def test_weekday_is_weekly_schedule_and_slot_filter_applies(monkeypatch):
    values=[session('Monday','5','Lecture','11.35 am','12.25 pm'), session('Monday','6','Other','12.25 pm','01.15 pm')]
    lookup=AsyncMock(return_value={'sessions':values,'all_sessions':values})
    monkeypatch.setattr(service,'_query_weekday',lookup)
    r=await service.answer_timetable_query('What class is scheduled in Slot 5 on Monday?',None,'CSE','VII','F')
    assert lookup.await_args.args[-1] == 'Monday'
    assert [s['slot'] for s in r['sessions']] == ['5']


@pytest.mark.asyncio
async def test_time_interval_filters_overlapping_sessions(monkeypatch):
    values=[session('Tuesday','4','Lecture','10.45 am','11.35 am'), session('Tuesday','6','Other','12.25 pm','01.15 pm')]
    monkeypatch.setattr(service,'_query_weekday',AsyncMock(return_value={'sessions':values,'all_sessions':values}))
    r=await service.answer_timetable_query('Is there any class between 10:45 AM and 11:35 AM on Tuesday?',None,'CSE','VII','F')
    assert [s['slot'] for s in r['sessions']] == ['4']


@pytest.mark.asyncio
async def test_free_periods_use_explicit_blank_cells(monkeypatch):
    values=[session('Friday','1','No class listed','08.00 am','08.50 am'), session('Friday','2','Lecture','08.50 am','09.40 am')]
    monkeypatch.setattr(service,'_query_weekday',AsyncMock(return_value={'sessions':values,'all_sessions':values}))
    r=await service.answer_timetable_query('Are there any free slots on Friday?',None,'CSE','VII','F')
    assert r['timetable_mode'] == 'free_periods'
    assert [s['slot'] for s in r['sessions']] == ['1']


def test_faculty_venue_question_uses_timetable_directory():
    assert is_timetable_directory_query('What is the venue and faculty for 23HUM240 Psychology for Engineers?')
