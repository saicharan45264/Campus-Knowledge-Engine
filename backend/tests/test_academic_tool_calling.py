"""Offline tests for model tool routing and the route-to-service adapter."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import academic_tool_calling as router


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.request = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, url, **kwargs):
        self.request = (url, kwargs)
        return FakeResponse(self.payload)


def tool_payload(name, arguments):
    return {"message": {"tool_calls": [{"function": {"name": name, "arguments": arguments}}]}}


@pytest.mark.asyncio
async def test_select_tool_uses_ollama_chat_and_returns_registered_function(monkeypatch):
    client = FakeClient(tool_payload("lookup_academic_calendar", {
        "event_type": "holiday", "temporal_scope": "all", "festival": "Sankranthi",
    }))
    monkeypatch.setattr(router.httpx, "AsyncClient", lambda **_kwargs: client)

    selected = await router.select_academic_tool("List holidays for Sankranthi")

    assert selected == {
        "name": "lookup_academic_calendar",
        "arguments": {"event_type": "holiday", "temporal_scope": "all", "festival": "Sankranthi"},
    }
    url, request = client.request
    assert url.endswith("/api/chat")
    assert request["json"]["stream"] is False
    assert {t["function"]["name"] for t in request["json"]["tools"]} == router.TOOL_NAMES


@pytest.mark.asyncio
async def test_tool_router_parses_string_arguments(monkeypatch):
    client = FakeClient(tool_payload(
        "lookup_academic_regulations",
        '{"topic":"attendance condonation","clause_number":"R.12.2"}',
    ))
    monkeypatch.setattr(router.httpx, "AsyncClient", lambda **_kwargs: client)

    selected = await router.select_academic_tool("What is the attendance condonation rule?")

    assert selected["arguments"] == {"topic": "attendance condonation", "clause_number": "R.12.2"}


@pytest.mark.asyncio
async def test_unknown_tool_is_rejected_for_legacy_fallback(monkeypatch):
    client = FakeClient(tool_payload("run_sql", {}))
    monkeypatch.setattr(router.httpx, "AsyncClient", lambda **_kwargs: client)

    assert await router.select_academic_tool("List the holidays") is None


def test_calendar_arguments_are_normalized_for_existing_calendar_service():
    intent, query = router.route_tool_call("When are Sankranthi holidays scheduled?", {
        "name": "lookup_academic_calendar",
        "arguments": {"event_type": "holiday", "temporal_scope": "all", "festival": "Sankranthi"},
    })

    assert intent == "CALENDAR_LOOKUP"
    assert "all holidays" in query
    assert "Sankranthi" in query


def test_month_query_wins_over_model_generated_first_of_month_date():
    intent, query = router.route_tool_call("What events are scheduled in October 2026?", {
        "name": "lookup_academic_calendar",
        "arguments": {
            "event_type": "general",
            "target_date": "2026-10-01",
            "temporal_scope": "all",
        },
    })

    assert intent == "CALENDAR_LOOKUP"
    assert "events in October 2026" in query
    assert "2026-10-01" not in query


def test_semester_exam_commencement_is_routed_to_academic_calendar():
    intent, query = router.route_tool_call(
        "When do midterm examinations commence for Semester 5?",
        {
            "name": "lookup_exam_schedule",
            "arguments": {"exam_type": "mid_term", "semester": "5"},
        },
    )

    assert intent == "CALENDAR_LOOKUP"
    assert "midterm examination commencement" in query


def test_tool_routes_cover_all_document_families_and_pyq():
    expected = {
        "lookup_academic_calendar": "CALENDAR_LOOKUP",
        "lookup_class_timetable": "TIMETABLE_LOOKUP",
        "lookup_elective_directory": "TIMETABLE_LOOKUP",
        "lookup_lab_schedule": "ACADEMIC_DOCUMENT_LOOKUP",
        "lookup_exam_schedule": "ACADEMIC_DOCUMENT_LOOKUP",
        "lookup_exam_seating": "ACADEMIC_DOCUMENT_LOOKUP",
        "lookup_curriculum_concept": "SIMPLE_CURRICULUM",
        "lookup_academic_regulations": "REGULATIONS_QUERY",
        "lookup_past_exam_questions": "PROBLEM_LIST",
    }
    assert router.TOOL_INTENTS == expected
    assert "lookup_past_exam_questions" in router.TOOL_NAMES


@pytest.mark.parametrize(
    ("name", "arguments", "expected_intent", "expected_fragment"),
    [
        ("lookup_academic_calendar", {"event_type": "holiday", "temporal_scope": "all"}, "CALENDAR_LOOKUP", "all holidays"),
        ("lookup_class_timetable", {"query_type": "day_schedule", "weekday": "Friday"}, "TIMETABLE_LOOKUP", "Friday"),
        ("lookup_elective_directory", {"elective_bucket": "PE-VI"}, "TIMETABLE_LOOKUP", "PE-VI"),
        ("lookup_lab_schedule", {"lab_identifier": "IT Lab 1", "weekday": "Wednesday"}, "ACADEMIC_DOCUMENT_LOOKUP", "IT Lab 1"),
        ("lookup_exam_schedule", {"exam_type": "end_sem", "course_code": "23CSE312"}, "ACADEMIC_DOCUMENT_LOOKUP", "23CSE312"),
        ("lookup_exam_seating", {}, "ACADEMIC_DOCUMENT_LOOKUP", "exam seating"),
        ("lookup_curriculum_concept", {"course_code_or_name": "23CSE401", "aspect": "course_outcomes"}, "SIMPLE_CURRICULUM", "course=23CSE401 aspect=course_outcomes"),
        ("lookup_academic_regulations", {"topic": "attendance condonation"}, "REGULATIONS_QUERY", "attendance condonation"),
        ("lookup_past_exam_questions", {"topic": "binary search", "course_code_or_name": "23CSE401", "exam_type": "all"}, "PROBLEM_LIST", "topic=binary search"),
    ],
)
def test_each_tool_normalizes_into_its_backend_path(name, arguments, expected_intent, expected_fragment):
    query_by_tool = {
        "lookup_academic_calendar": "List the official holidays.",
        "lookup_class_timetable": "Show my Friday timetable.",
        "lookup_elective_directory": "What is offered in PE-VI?",
        "lookup_lab_schedule": "When is IT Lab 1 occupied Wednesday?",
        "lookup_exam_schedule": "When is the end-sem exam for 23CSE312?",
        "lookup_exam_seating": "Where is my exam seat?",
        "lookup_curriculum_concept": "What are the course outcomes for 23CSE401?",
        "lookup_academic_regulations": "What are attendance condonation rules?",
        "lookup_past_exam_questions": "Find questions about binary search for 23CSE401.",
    }

    intent, normalized_query = router.route_tool_call(
        query_by_tool[name],
        {"name": name, "arguments": arguments},
    )

    assert intent == expected_intent
    assert expected_fragment.lower() in normalized_query.lower()
