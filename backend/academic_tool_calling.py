"""Tool selection for natural-language academic query routing.

The model selects a known tool and extracts arguments. SQL and all data access
remain in the existing application services.
"""

from __future__ import annotations

import json
import re

import httpx

from utils import OLLAMA_BASE_URL, OLLAMA_MODEL


_S = {"type": "string"}
_I = {"type": "integer"}
_B = {"type": "boolean"}


def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


ACADEMIC_TOOLS = [
    _tool("lookup_academic_calendar", "Find cohort or semester reopening and the date midterm/end-semester exams commence, plus holidays and working Saturdays. Use this for semester-level exam commencement dates.", {
        "event_type": {"type": "string", "enum": ["commencement", "midterm", "endsem", "holiday", "working_saturday", "general"]},
        "term": {"type": "string", "enum": ["odd", "even", "all"]},
        "target_date": _S,
        "target_month": _S,
        "cohort": _S,
        "temporal_scope": {"type": "string", "enum": ["next", "all", "specific", "general"]},
        "festival": _S,
    }, ["event_type", "temporal_scope"]),
    _tool("lookup_class_timetable", "Find a student's weekly class schedule, course slot, venue, faculty, or lunch break.", {
        "course_code": _S, "weekday": _S, "slot_number": _I,
        "query_type": {"type": "string", "enum": ["venue", "faculty", "time_slot", "day_schedule", "lunch_break", "general"]},
        "department": _S, "semester": _S, "class_name": _S,
    }, ["query_type"]),
    _tool("lookup_elective_directory", "Find elective bucket offerings, courses, faculty, or venues.", {
        "elective_bucket": {"type": "string", "enum": ["PE-IV", "PE-V", "PE-VI", "free_elective", "all"]},
    }, ["elective_bucket"]),
    _tool("lookup_lab_schedule", "Find lab sessions, vacant time, or lab venue from an uploaded lab timetable.", {
        "lab_identifier": _S, "weekday": _S, "check_vacant_only": _B,
        "department": _S, "semester": _S,
    }, ["lab_identifier"]),
    _tool("lookup_exam_schedule", "Find course-level mid-term or end-semester timetable entries, including each course's date and session. Use this only for course schedule lookups; use the academic calendar for when a semester or cohort's exams commence.", {
        "course_code": _S, "department": _S, "semester": _S,
        "exam_type": {"type": "string", "enum": ["end_sem", "mid_term"]},
    }, ["exam_type"]),
    _tool("lookup_exam_seating", "Find the signed-in student's exam room and seat from the registered roll number.", {
        "register_number": _S, "department": _S, "semester": _S,
    }, []),
    _tool("lookup_curriculum_concept", "Find syllabus units, outcomes, prerequisites, Bloom taxonomy, credits, or L-T-P-C.", {
        "course_code_or_name": _S,
        "aspect": {"type": "string", "enum": ["prerequisites", "unit_syllabus", "course_outcomes", "blooms_taxonomy", "credits_ltpc", "general"]},
        "unit_number": _I,
    }, ["course_code_or_name", "aspect"]),
    _tool("lookup_academic_regulations", "Find academic rules, regulation clauses, attendance, grading, re-registration, exams, promotion, or honors.", {
        "topic": _S, "clause_number": _S,
    }, ["topic"]),
    _tool("lookup_past_exam_questions", "Find past-year questions by course, topic, exam type, or marks.", {
        "course_code_or_name": _S, "topic": _S,
        "exam_type": {"type": "string", "enum": ["mid_term", "end_sem", "all"]},
        "marks": _I,
    }, []),
]

TOOL_NAMES = {tool["function"]["name"] for tool in ACADEMIC_TOOLS}

TOOL_INTENTS = {
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


async def select_academic_tool(question: str) -> dict | None:
    """Return a validated registered tool call, or None to use legacy routing."""
    if not question.strip():
        return None
    messages = [
        {
            "role": "system",
            "content": (
                "Route the student's question to one academic lookup tool. "
                "Call a tool instead of answering. Extract only facts stated or "
                "unambiguously implied in the question; never invent a roll number, "
                "department, semester, date, or class. For 'next/upcoming' use "
                "temporal_scope=next; for complete/every/list use all. For a month "
                "and year such as October 2026, set target_month='2026-10'; use "
                "target_date only when a specific calendar day is stated. Select the "
                "most specific tool and leave unknown optional fields blank. For "
                "personal exam hall/seat questions choose lookup_exam_seating; do "
                "not require or invent a register number because the signed-in "
                "student profile supplies it. Use lookup_past_exam_questions for "
                "past-paper question retrieval."
            ),
        },
        {"role": "user", "content": question},
    ]
    try:
        async with httpx.AsyncClient(headers={"ngrok-skip-browser-warning": "true"}) as client:
            response = await client.post(
                f"{OLLAMA_BASE_URL}/api/chat",
                json={
                    "model": OLLAMA_MODEL,
                    "messages": messages,
                    "tools": ACADEMIC_TOOLS,
                    "stream": False,
                    "think": False,
                    "options": {"temperature": 0.0, "num_ctx": 4096},
                },
                timeout=35.0,
            )
            response.raise_for_status()
        calls = response.json().get("message", {}).get("tool_calls") or []
        if not calls:
            return None
        function = calls[0].get("function") or {}
        name, arguments = function.get("name"), function.get("arguments") or {}
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
        if name not in TOOL_NAMES or not isinstance(arguments, dict):
            return None
        return {"name": name, "arguments": arguments}
    except (httpx.HTTPError, ValueError, TypeError, AttributeError) as exc:
        print(f"[TOOL ROUTER] Tool selection unavailable; using intent router: {exc}")
        return None


def route_tool_call(question: str, tool_call: dict) -> tuple[str, str] | None:
    """Map a tool call to an existing intent and normalize arguments for services."""
    name = tool_call.get("name")
    args = tool_call.get("arguments") or {}
    # A semester-wide commencement question is a calendar milestone, even if
    # the model selected the course-by-course exam timetable tool.
    if (
        name == "lookup_exam_schedule"
        and not args.get("course_code")
        and re.search(r"\b(commenc\w*|start|begin|when)\b", question, re.I)
        and re.search(r"\b(mid\s*[- ]?term|mid\s*[- ]?sem|end\s*[- ]?sem|end\s*[- ]?semester)\b", question, re.I)
        and re.search(r"\b(?:sem(?:ester)?\s*(?:[1-8]|i{1,3}|iv|v|vi|vii|viii))\b", question, re.I)
    ):
        name = "lookup_academic_calendar"
        args = {
            "event_type": "midterm" if args.get("exam_type") == "mid_term" or re.search(r"\bmid", question, re.I) else "endsem",
            "temporal_scope": "specific",
            "cohort": args.get("semester") or "",
        }
    intent = TOOL_INTENTS.get(name)
    if not intent:
        return None

    additions: list[str] = []
    if name == "lookup_academic_calendar":
        event_type = args.get("event_type")
        scope = args.get("temporal_scope")
        festival = args.get("festival")
        term = args.get("term")
        cohort = args.get("cohort")
        target_date = args.get("target_date")
        target_month = args.get("target_month")
        # Some model versions turn a month query into the month's first day.
        # Preserve the user's month-level intent instead of routing it to a day card.
        month_match = re.search(
            r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\s+(20\d{2})\b",
            question,
            re.I,
        )
        if month_match:
            month_number = {
                "january": 1, "february": 2, "march": 3, "april": 4,
                "may": 5, "june": 6, "july": 7, "august": 8,
                "september": 9, "october": 10, "november": 11, "december": 12,
            }[month_match.group(1).lower()]
            target_month = f"{month_match.group(2)}-{month_number:02d}"
            target_date = None
        if event_type == "holiday":
            additions.append("holidays")
            if scope == "all": additions.append("all holidays")
            elif scope == "next": additions.append("next holiday")
            if festival: additions.append(str(festival))
        elif event_type == "working_saturday":
            additions.append("working Saturday")
            additions.append("all working Saturdays" if scope == "all" else "next working Saturday" if scope == "next" else "working Saturdays")
        elif event_type in {"commencement", "midterm", "endsem"}:
            label = {"commencement": "reopen commencement", "midterm": "midterm examination commencement", "endsem": "end sem examination commencement"}[event_type]
            additions.append(label)
        if term and term != "all": additions.append(f"{term} semester")
        if cohort: additions.append(str(cohort))
        if target_date: additions.append(str(target_date))
        if target_month:
            try:
                from datetime import datetime as _datetime
                year, month = str(target_month).split("-", 1)
                additions.append(f"events in {_datetime(int(year), int(month), 1).strftime('%B %Y')}")
            except (ValueError, TypeError):
                additions.append(str(target_month))
    elif name == "lookup_academic_regulations":
        additions.extend((str(args.get("topic") or ""), str(args.get("clause_number") or "")))
    elif name == "lookup_class_timetable":
        qtype = args.get("query_type")
        additions.append({"day_schedule": "class schedule", "venue": "class venue", "faculty": "class faculty", "time_slot": "class time slot", "lunch_break": "lunch break"}.get(qtype, "class timetable"))
        for field in ("weekday", "course_code", "slot_number"):
            if args.get(field) is not None: additions.append(str(args[field]))
    elif name == "lookup_elective_directory":
        additions.extend(("elective", str(args.get("elective_bucket") or "")))
    elif name == "lookup_lab_schedule":
        additions.extend(("lab schedule", str(args.get("lab_identifier") or "")))
        for field in ("weekday", "department", "semester"):
            if args.get(field): additions.append(str(args[field]))
        if args.get("check_vacant_only"): additions.append("vacant lab availability")
    elif name == "lookup_exam_schedule":
        additions.extend(("exam schedule", str(args.get("exam_type") or "")))
        for field in ("course_code", "department", "semester"):
            if args.get(field): additions.append(str(args[field]))
    elif name == "lookup_exam_seating":
        additions.append("exam seating")
        if args.get("register_number"): additions.append(str(args["register_number"]))
    elif name == "lookup_curriculum_concept":
        additions.extend((
            f"course={str(args.get('course_code_or_name') or '')}",
            f"aspect={str(args.get('aspect') or '')}",
        ))
        if args.get("unit_number"): additions.append(f"unit={args['unit_number']}")
    elif name == "lookup_past_exam_questions":
        if args.get("topic"): additions.append(f"topic={str(args['topic'])}")
        if args.get("course_code_or_name"): additions.append(f"course={str(args['course_code_or_name'])}")
        if args.get("exam_type"): additions.append(f"exam_type={str(args['exam_type'])}")
        if args.get("marks"): additions.append(f"marks={args['marks']}")

    normalized_query = question
    details = " ".join(part for part in additions if part).strip()
    if details:
        normalized_query = f"{question}\n[Structured academic lookup: {details}]"
    return intent, normalized_query
