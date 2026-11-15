"""Natural-language tool selection for academic document lookups.

The model only selects a registered operation and extracts arguments. All data
access remains in parameterized application services; it cannot generate SQL.
"""
from __future__ import annotations

import json
import re
from typing import Any

import httpx

from backend.config import settings
from backend.core.http_client import get_shared_http_client

S = {"type": "string"}
I = {"type": "integer"}
B = {"type": "boolean"}


def _tool(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
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
    _tool("lookup_academic_calendar", "Look up official academic calendar dates, holidays, events, cohort milestones, and working Saturdays.", {
        "event_type": {"type": "string", "enum": ["commencement", "midterm", "endsem", "holiday", "working_saturday", "general"]},
        "term": {"type": "string", "enum": ["odd", "even", "all"]},
        "target_date": S, "target_month": S, "cohort": S,
        "temporal_scope": {"type": "string", "enum": ["next", "all", "specific", "general"]},
        "festival": S,
    }, ["event_type", "temporal_scope"]),
    _tool("lookup_class_timetable", "Look up a weekly class timetable, course slot, faculty, venue, or day schedule.", {
        "course_code": S, "weekday": S, "slot_number": I,
        "query_type": {"type": "string", "enum": ["venue", "faculty", "time_slot", "day_schedule", "lunch_break", "general"]},
        "department": S, "semester": S, "class_name": S,
    }, ["query_type"]),
    _tool("lookup_elective_directory", "Look up elective course offerings, faculty, or venues.", {
        "elective_bucket": {"type": "string", "enum": ["PE-IV", "PE-V", "PE-VI", "free_elective", "all"]},
    }, ["elective_bucket"]),
    _tool("lookup_lab_schedule", "Look up an uploaded lab timetable and lab availability.", {
        "lab_identifier": S, "weekday": S, "check_vacant_only": B,
        "department": S, "semester": S,
    }, ["lab_identifier"]),
    _tool("lookup_exam_schedule", "Look up individual course exam dates, sessions, and the uploaded midterm/end-semester schedule.", {
        "course_code": S, "department": S, "semester": S,
        "exam_type": {"type": "string", "enum": ["end_sem", "mid_term", "all"]},
    }, ["exam_type"]),
    _tool("lookup_exam_seating", "Search an uploaded seating circular by roll number, department, section, hall, academic block, re-registration rule, exam window, session timing, cohort, issuer, or candidate counts. Use an explicit roll number when the user provides one; otherwise use the signed-in student's roll number for personal lookups.", {
        "register_number": S, "department": S, "semester": S,
    }, []),
    _tool("lookup_academic_regulations", "Look up official academic regulations, clauses, attendance, grading, promotion, examinations, and requirements.", {
        "topic": S, "clause_number": S,
    }, ["topic"]),
]

TOOL_NAMES = {entry["function"]["name"] for entry in ACADEMIC_TOOLS}
TOOL_INTENTS = {
    "lookup_academic_calendar": "CALENDAR_LOOKUP",
    "lookup_class_timetable": "TIMETABLE_LOOKUP",
    "lookup_elective_directory": "TIMETABLE_LOOKUP",
    "lookup_lab_schedule": "ACADEMIC_DOCUMENT_LOOKUP",
    "lookup_exam_schedule": "ACADEMIC_DOCUMENT_LOOKUP",
    "lookup_exam_seating": "ACADEMIC_DOCUMENT_LOOKUP",
    "lookup_academic_regulations": "REGULATIONS_QUERY",
}

_ACADEMIC_QUERY = re.compile(
    r"\b(calendar|holiday|working saturday|timetable|schedule|course code|exams?|examinations?|"
    r"seating|seat|regulation|clause|academic rule|lab|laboratory|elective|"
    r"reopen(?:ing)?|commenc(?:e|ement)|resume|resumption|last instruction|"
    r"today|tomorrow|yesterday|next\s+\w+|this\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)|"
    r"january|february|march|april|may|june|july|august|september|october|november|december)\b",
    re.IGNORECASE,
)


def may_be_academic_document_query(question: str) -> bool:
    """Cheap gate only; the model chooses the operation and parameters."""
    return bool(_ACADEMIC_QUERY.search(question or ""))


def calendar_query_intent(question: str) -> bool:
    """Recognize calendar questions before weekly timetable or exam routing."""
    from backend.services.calendar_qa_service import _festival_topic_tokens
    q = (question or "").casefold()
    if re.search(r"\b(holiday|holidays|festival|festivals|calendar|working day|working days|instructional day|instruction day|class day|class days|last working|last instruction)\b", q):
        return True
    if _festival_topic_tokens(q):
        return True
    if re.search(r"\bsaturdays?\b", q) and re.search(r"\b(working|timetable|substitution|substitutions)\b", q):
        return True
    if re.search(r"\b(timetable|schedule)\b", q) and re.search(r"\b(followed|applies|substitution|substitutions|override)\b", q):
        return True
    if re.search(r"\b(semester|sem|year|years)\b", q) and re.search(r"\b(start|starts|begin|begins|commence|commencement|reopen|reopening|midterm|mid term|mid sem|end sem|final exams?)\b", q):
        return True
    return bool(
        re.search(r"\b(events?|scheduled|happening)\b", q)
        and re.search(r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\b", q)
        and not re.search(r"\b(exam|exams|examination)\b", q)
    )


def exam_schedule_query_intent(question: str) -> bool:
    """Route course/date/session exam lookups to extracted exam schedules.

    Semester level commencement milestones remain calendar queries. An explicit
    exam window, course, date, department, session, or schedule asks about the
    uploaded exam table and should take precedence over the broad calendar gate.
    """
    q = (question or "").casefold()
    has_exam = bool(re.search(r"\b(exam|exams|examination|examinations|end\s*sem|mid\s*term|midterm)\b", q))
    course_code_lookup = bool(re.search(r"\bcourse\s+code\b", q))
    course_code = bool(re.search(r"\b\d{2}[a-z]{3,4}\d{3,4}[a-z]?\b", q))
    branch_code_schedule = course_code and bool(re.search(r"\b(branch(?:es)?|department(?:s)?|on\s+\d{1,2}|january|february|march|april|may|june|july|august|september|october|november|december)\b", q))
    if not has_exam and not course_code_lookup and not branch_code_schedule:
        return False
    if re.search(r"\b(holiday|calendar|commencement|reopen(?:ing)?|class(?:es)? commence)\b", q):
        return False
    if re.search(r"\b(start|begin|commence)\b", q) and not re.search(r"\b(start\s+and\s+end|start\s+to\s+finish|exam\s+window|schedule|course|code|date)\b", q):
        return False  # semester-level exam commencement belongs to the calendar
    return bool(re.search(
        r"\b(schedule|date|when|which|what|session|forenoon|afternoon|fn|an|course|code|department|branch|between|from|window|start and end|start to finish|saturdays?)\b|"
        r"\b\d{2}[a-z]{3,4}\d{3,4}[a-z]?\b|\b20\d{2}[-/]\d{1,2}[-/]\d{1,2}\b",
        q,
    ))


def seating_query_intent(question: str) -> bool:
    """Route seating-circular facts before generic exam-schedule intent."""
    q = (question or "").casefold()
    return bool(
        re.search(r"\bCB\s*[.]?\s*EN\s*[.]?\s*U4\s*[A-Z]{3,4}\s*\d{5}\b", question or "", re.IGNORECASE)
        or re.search(r"\b(seating|seated|sit|sitting|exam\s+halls?|hall allocation|register number|roll number|arrear|re[ -]?regist\w*|auditorium)\b", q)
        or (re.search(r"\b(academic\s+block|across all branches|total candidates)\b", q)
            and re.search(r"\b(department|departments|branch|branches|students|halls?)\b", q))
        or (re.search(r"\b(mid[ -]?term|mid[ -]?sem)\b", q)
            and re.search(r"\b(date|dates|window|duration|timings?)\b", q)
            and re.search(r"\b(semester|sem)\b", q)
            and not re.search(r"\b(course|code)\b", q))
        or (re.search(r"\b(timing|time|session)\b", q)
            and re.search(r"\b(FN|AN|forenoon|afternoon|morning session)\b", question or "", re.IGNORECASE)
            and not re.search(r"\b(slot|timetable|class|course|courses|date|schedule|scheduled)\b", q))
        or (re.search(r"\b(appearing|writing|capacity|student strength|how many students)\b", q)
            and re.search(r"\b(CSE|CYS|EEE|ECE|ELC|MEE|CCE|CIE|ARE|CHE|AEE|AIE|department|branch|section)\b", question or "", re.IGNORECASE))
    )


def fallback_academic_tool(question: str) -> dict[str, Any] | None:
    """Low-cost dispatch fallback when the configured model/tunnel is unavailable.

    This only chooses the retrieval service; the service still retrieves facts
    from scoped database records and does not construct answers from keywords.
    """
    q = re.sub(r"[-_]+", " ", (question or "").casefold())
    if seating_query_intent(question):
        return {"name": "lookup_exam_seating", "arguments": {}}
    if exam_schedule_query_intent(question):
        exam_type = "mid_term" if re.search(r"\b(mid\s*term|midterm|mid\s*sem)\b", q) else "end_sem" if re.search(r"\b(end\s*sem(?:ester)?|endsem|final exam)\b", q) else "all"
        return {"name": "lookup_exam_schedule", "arguments": {"exam_type": exam_type}}
    if calendar_query_intent(question):
        event_type = "midterm" if re.search(r"\b(mid\s*term|midterm|mid\s*sem)\b", q) else "endsem" if re.search(r"\b(end\s*sem|endsem|final exams?)\b", q) else "holiday" if re.search(r"\bholidays?\b", q) else "general"
        return {"name": "lookup_academic_calendar", "arguments": {"event_type": event_type}}
    if not may_be_academic_document_query(q):
        return None
    if seating_query_intent(question):
        return {"name": "lookup_exam_seating", "arguments": {}}
    if re.search(r"\b(lab|laboratory|it\s*lab)\b", q):
        return {
            "name": "lookup_lab_schedule",
            "arguments": {"lab_identifier": "lab", "check_vacant_only": bool(re.search(r"\b(free|vacant|available|availability)\b", q))},
        }
    if re.search(r"\b(regulation|regulations|clause|attendance rule|grading rule|academic rule|requirement)\b", q):
        return {"name": "lookup_academic_regulations", "arguments": {"topic": question}}
    if re.search(r"\b(class|classes|lecture|timetable|time table|slot|faculty|venue|room)\b", q) and not re.search(r"\bexam|examination\b", q):
        return {"name": "lookup_class_timetable", "arguments": {"query_type": "day_schedule"}}
    if re.search(r"\b(exams?|examinations?|end\s*sem|endsem|mid\s*term|midterm)\b", q) and not re.search(r"\b(commenc(?:e|ement)|start|begin|reopen|reopening)\b", q):
        exam_type = "mid_term" if re.search(r"\b(mid\s*term|midterm|mid\s*sem)\b", q) else "end_sem" if re.search(r"\b(end\s*sem(?:ester)?|endsem|final exam)\b", q) else "all"
        return {"name": "lookup_exam_schedule", "arguments": {"exam_type": exam_type}}
    if re.search(r"\b(holiday|festival|calendar|working day|working saturday|reopen|reopening|commenc(?:e|ement)|resume|resumption|last instruction|month|events?|scheduled|happening|today|tomorrow|yesterday)\b", q) or re.search(r"\b20\d{2}[-/]\d{1,2}[-/]\d{1,2}\b", q):
        scope = "all" if re.search(r"\b(all|every|complete|entire|full|list)\b", q) else "next" if re.search(r"\b(next|upcoming|nearest)\b", q) else "specific" if re.search(r"\b20\d{2}[-/]\d{1,2}[-/]\d{1,2}\b|\b\d{1,2}(?:st|nd|rd|th)?\s+[a-z]+\b", q) else "general"
        event_type = "holiday" if re.search(r"\b(holiday|festival)\b", q) else "working_saturday" if "working saturday" in q else "midterm" if re.search(r"\bmid\s*term|midterm|mid\s*sem\b", q) else "endsem" if re.search(r"\bend\s*sem|endsem\b", q) else "commencement" if re.search(r"\b(commenc|reopen|resume|resumption|start|begin)\w*\b", q) else "general"
        return {"name": "lookup_academic_calendar", "arguments": {"event_type": event_type, "temporal_scope": scope}}
    return None


async def select_academic_tool(question: str) -> dict[str, Any] | None:
    """Ask the configured Ollama-compatible endpoint to select a known tool.

    Returns None on timeout, invalid output, or provider failure so the normal
    intent router can continue. Timeouts are deliberately bounded for tunnels.
    """
    if not question.strip() or not may_be_academic_document_query(question):
        return None
    messages = [
        {
            "role": "system",
            "content": (
                "Route the student's question to one registered academic lookup tool. "
                "Call a tool instead of answering. Extract only facts explicitly stated "
                "or unambiguously implied; leave unknown fields empty. For next/upcoming "
                "use temporal_scope=next; for all/every/full/list use all. Use target_month "
                "as YYYY-MM for a month query, target_date only for a specific date. "
                "Choose calendar for semester-level commencement/exam start dates, and "
                "exam_schedule for an exam date/schedule question. Set exam_type=all "
                "when the student did not say midterm or end-semester. Personal seating uses "
                "the authenticated profile; never invent a roll number. Never invent scope."
            ),
        },
        {"role": "user", "content": question},
    ]
    try:
        response = await get_shared_http_client().post(
            f"{settings.ollama_base_url.rstrip('/')}/api/chat",
            json={
                "model": settings.ollama_model,
                "messages": messages,
                "tools": ACADEMIC_TOOLS,
                "stream": False,
                "think": False,
                "options": {"temperature": 0.0, "num_ctx": 4096},
            },
            timeout=httpx.Timeout(connect=4.0, read=12.0, write=5.0, pool=3.0),
        )
        response.raise_for_status()
        raw_calls = response.json().get("message", {}).get("tool_calls") or []
        if not raw_calls:
            return None
        function = raw_calls[0].get("function") or {}
        name, arguments = function.get("name"), function.get("arguments") or {}
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
        if name not in TOOL_NAMES or not isinstance(arguments, dict):
            return None
        # Discard fields the selected tool does not declare.
        schema = next(t["function"]["parameters"] for t in ACADEMIC_TOOLS if t["function"]["name"] == name)
        arguments = {k: v for k, v in arguments.items() if k in schema["properties"]}
        return {"name": name, "arguments": arguments}
    except (httpx.HTTPError, ValueError, TypeError, AttributeError, KeyError) as exc:
        print(f"[TOOL ROUTER] Academic tool selection unavailable; use fallback routing: {exc}")
        return None


def route_tool_call(question: str, tool_call: dict[str, Any]) -> tuple[str, str] | None:
    """Normalize a tool call into an existing domain service intent and query."""
    name = tool_call.get("name")
    args = tool_call.get("arguments") or {}
    intent = TOOL_INTENTS.get(name)
    if not intent:
        return None

    additions: list[str] = []
    if name == "lookup_academic_calendar":
        event_type = args.get("event_type")
        scope = args.get("temporal_scope")
        aliases = {"commencement": "commencement", "midterm": "midterm examination", "endsem": "end semester examination", "holiday": "holiday", "working_saturday": "working Saturday"}
        if event_type in aliases:
            additions.append(aliases[event_type])
        if scope in {"next", "all", "specific"}:
            additions.append({"next": "next", "all": "all", "specific": "specific date"}[scope])
        for field in ("festival", "cohort", "term", "target_date", "target_month"):
            if args.get(field):
                additions.append(str(args[field]))
    elif name == "lookup_academic_regulations":
        additions.extend(str(args.get(k) or "") for k in ("topic", "clause_number"))
    elif name == "lookup_class_timetable":
        additions.append(str(args.get("query_type") or "timetable"))
        additions.extend(str(args[k]) for k in ("weekday", "course_code", "slot_number") if args.get(k) is not None)
    elif name == "lookup_elective_directory":
        additions.extend(("elective offerings", str(args.get("elective_bucket") or "")))
    elif name == "lookup_lab_schedule":
        additions.extend(("lab timetable", str(args.get("lab_identifier") or "")))
        additions.extend(str(args[k]) for k in ("weekday",) if args.get(k))
        if args.get("check_vacant_only"):
            additions.append("vacant availability")
    elif name == "lookup_exam_schedule":
        additions.append("exam schedule")
        if args.get("exam_type") in {"end_sem", "mid_term"}:
            additions.append(str(args["exam_type"]))
        additions.extend(str(args[k]) for k in ("course_code", "department", "semester") if args.get(k))
    elif name == "lookup_exam_seating":
        additions.append("exam seating")
    details = " ".join(item for item in additions if item).strip()
    return intent, f"{question}\n[Structured lookup: {details}]" if details else question
