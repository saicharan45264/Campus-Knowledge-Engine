"""Structured retrieval for academic calendars and class timetables.

Answers are built from the uploaded relational records. No sample dates, course
details, venues, or timetable entries are embedded in this module.
"""
from __future__ import annotations

import re
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import (
    AcademicEvent,
    CalendarDay,
    Document,
    ElectiveOption,
    TimetableVersion,
    WeeklySession,
)
from backend.services.date_resolution import extract_date_from_text

_SEMESTERS = {
    "I": 1, "II": 2, "III": 3, "IV": 4,
    "V": 5, "VI": 6, "VII": 7, "VIII": 8,
}
_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_FESTIVAL_ALIASES = {
    "sankranthi": ("pongal", "sankranthi", "sankranti"),
    "sankranti": ("pongal", "sankranthi", "sankranti"),
    "deepavali": ("diwali", "deepavali"),
    "diwali": ("diwali", "deepavali"),
    "onam": ("onam", "thiruvonam"),
    "christmas": ("christmas",),
    "pongal": ("pongal", "sankranthi", "sankranti"),
    "independence": ("independence day",),
    "republic": ("republic day",),
    "muharam": ("muharam", "muharram"),
    "muharram": ("muharam", "muharram"),
    "ramzan": ("ramzan", "eid"),
    "bakrid": ("bakrid", "eid ul adha", "eid al adha"),
    "ugadi": ("ugadi",),
    "vishu": ("vishu",),
}


def _semester_number(value: str | None) -> int | None:
    value = (value or "").strip().upper()
    return int(value) if value.isdigit() else _SEMESTERS.get(value)


def _requested_semester(question: str) -> int | None:
    q = question.lower()
    match = re.search(r"\b(?:sem(?:ester)?\s*)([1-8])(?:st|nd|rd|th)?\b", q)
    if match:
        return int(match.group(1))
    match = re.search(r"\b(?:sem(?:ester)?\s*)(i{1,3}|iv|v|vi|vii|viii)\b", q)
    if match:
        return _SEMESTERS[match.group(1).upper()]
    return None


def _cohort_aliases(semester: int | None, query: str) -> tuple[str, ...]:
    q = query.lower()
    if semester is not None:
        year = (semester + 1) // 2
        return {
            1: ("first year", "i year", "i yr", "1st year"),
            2: ("second year", "ii year", "ii yr", "2nd year"),
            3: ("third year", "iii year", "iii yr", "3rd year"),
            4: ("final year", "fourth year", "iv year", "iv yr", "4th year"),
        }[year]
    if any(term in q for term in ("final year", "fourth year", "4th year", "iv year", "iv yr")):
        return ("final year", "fourth year", "iv year", "iv yr", "4th year")
    if any(term in q for term in ("third year", "3rd year", "iii year", "iii yr")):
        return ("third year", "iii year", "iii yr", "3rd year")
    if any(term in q for term in ("second year", "2nd year", "ii year", "ii yr")):
        return ("second year", "ii year", "ii yr", "2nd year")
    if any(term in q for term in ("first year", "1st year", "i year", "i yr")):
        return ("first year", "i year", "i yr", "1st year")
    return ()


async def _latest_calendar_id(db: AsyncSession):
    result = await db.execute(
        select(Document.id)
        .where(Document.doc_type == "academic_calendar")
        .order_by(Document.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _calendar_rows(db: AsyncSession) -> tuple[list[CalendarDay], list[AcademicEvent]]:
    doc_id = await _latest_calendar_id(db)
    if doc_id is None:
        return [], []
    day_result = await db.execute(
        select(CalendarDay).where(CalendarDay.document_id == doc_id).order_by(CalendarDay.calendar_date)
    )
    event_result = await db.execute(
        select(AcademicEvent).where(AcademicEvent.document_id == doc_id).order_by(AcademicEvent.start_date)
    )
    return day_result.scalars().all(), event_result.scalars().all()


def _event_dict(event: AcademicEvent) -> dict[str, str]:
    d = event.start_date.date()
    return {
        "date_iso": d.isoformat(),
        "date": d.strftime("%B %d, %Y"),
        "day": d.strftime("%A"),
        "name": event.name,
        "category": event.event_category or "milestone",
    }


def _holiday_rows(days: list[CalendarDay], events: list[AcademicEvent]) -> list[dict[str, str]]:
    entries: dict[str, dict[str, str]] = {}
    for event in events:
        if (event.event_category or "").casefold() == "holiday":
            row = _event_dict(event)
            entries[row["date_iso"]] = row
    for day in days:
        status = (day.status or "").strip().casefold()
        particulars = (day.particulars or "").strip()
        if "holiday" not in status and not re.search(r"\bholiday\b", particulars, re.IGNORECASE):
            continue
        iso = day.calendar_date.date().isoformat()
        name = re.sub(r"\bholiday\b", "", particulars or day.status or "Holiday", flags=re.IGNORECASE)
        name = re.sub(r"\s+", " ", name).strip(" :,-") or "Holiday"
        entries.setdefault(iso, {
            "date_iso": iso,
            "date": day.calendar_date.strftime("%B %d, %Y"),
            "day": day.day_of_week or day.calendar_date.strftime("%A"),
            "name": name,
            "category": "holiday",
        })
    return [entries[k] for k in sorted(entries)]


def _date_row(day: CalendarDay | None, events: list[AcademicEvent], target: date) -> dict[str, Any]:
    day_events = [_event_dict(e) for e in events if e.start_date.date() == target]
    status = (day.status or "") if day else ""
    particulars = (day.particulars or "") if day else ""
    holiday = any(e["category"].casefold() == "holiday" for e in day_events)
    holiday = holiday or bool(re.search(r"\bholiday\b", f"{status} {particulars}", re.IGNORECASE))
    if day and day.day_of_week == "Sun" and status != "W" and not day.timetable_override:
        holiday = True
    if day and day.day_of_week == "Sat" and status != "W" and not day.timetable_override:
        holiday = True
    return {
        "date_iso": target.isoformat(),
        "date": target.strftime("%B %d, %Y"),
        "day": day.day_of_week if day else target.strftime("%A"),
        "status": "Holiday" if holiday else (status or "Not found in uploaded calendar"),
        "particulars": particulars,
        "timetable_override": day.timetable_override if day else None,
        "is_holiday": holiday,
        "events": day_events,
    }


def _calendar_query_mode(question: str, tool_hints: str = "") -> str:
    q = f"{question} {tool_hints}".lower()
    if re.search(r"\bworking\s+saturdays?\b", q):
        return "next_working_saturday" if re.search(r"\b(next|upcoming|nearest|coming)\b", q) else "working_saturdays"
    if re.search(r"\bholidays?\b|\bfestivals?\b", q):
        if re.search(r"\b(next|upcoming|nearest|coming)\b", q):
            return "next_holiday"
        if re.search(r"\b(all|every|each|complete|entire|full|list)\b", q) or "holidays" in q:
            return "holiday_list"
        return "festival_holidays" if any(k in q for k in _FESTIVAL_ALIASES) else "next_holiday"
    if re.search(r"\b(events?|calendar|scheduled|happening)\b", q) and re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|january|february|march|april|may|june|july|august|september|october|november|december)\b", q):
        return "month_events"
    if re.search(r"\b(timetable|timetable applies|substitution|override)\b", q) and ("saturday" in q or extract_date_from_text(question)):
        return "date_lookup"
    if re.search(r"\b(holiday|working day|non working|non-working)\b", q) and extract_date_from_text(question):
        return "date_lookup"
    if re.search(r"\b(reopen|reopening|commence|commencement|resume|mid\s*term|midterm|end\s*sem|endsem|last instruction)\b", q):
        return "milestone_lookup"
    if extract_date_from_text(question):
        return "date_lookup"
    return "calendar_summary"


async def answer_calendar_query(
    question: str,
    db: AsyncSession,
    semester: str | None = None,
) -> dict[str, Any]:
    """Answer date, holiday, event, working-Saturday, and milestone queries."""
    days, events = await _calendar_rows(db)
    if not days and not events:
        return {"calendar_mode": "empty", "markdown_answer": "No academic calendar has been indexed yet. Ask an administrator to upload the official calendar."}

    now = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    mode = _calendar_query_mode(question)
    year_match = re.search(r"\b(20\d{2})\b", question)
    requested_year = int(year_match.group(1)) if year_match else None

    if mode == "date_lookup":
        parsed = extract_date_from_text(question, reference_date=now)
        if not parsed:
            return {"calendar_mode": mode, "markdown_answer": "Please include a date so I can check the uploaded calendar."}
        target = date.fromisoformat(parsed)
        day = next((d for d in days if d.calendar_date.date() == target), None)
        detail = _date_row(day, events, target)
        if day is None and not detail["events"]:
            detail["status"] = "No entry for this date in the uploaded calendar"
        if detail.get("timetable_override"):
            detail["applied_timetable"] = detail["timetable_override"]
        status = "a holiday" if detail["is_holiday"] else ("a working day" if detail["status"] == "W" or detail["timetable_override"] else detail["status"])
        answer = f"**{detail['date']} ({detail['day']})** is {status}."
        if detail.get("particulars"):
            answer += f" {detail['particulars']}"
        if detail.get("timetable_override"):
            answer += f" The **{detail['timetable_override']} timetable** applies."
        if detail["events"]:
            answer += " Events: " + "; ".join(e["name"] for e in detail["events"])
        return {"calendar_mode": mode, "markdown_answer": answer, "day_details": detail, "events": detail["events"]}

    if mode in {"holiday_list", "festival_holidays", "next_holiday"}:
        holidays = _holiday_rows(days, events)
        q_norm = re.sub(r"[^a-z0-9]+", " ", question.lower())
        aliases: tuple[str, ...] = ()
        for key, values in _FESTIVAL_ALIASES.items():
            if re.search(rf"\b{re.escape(key)}\b", q_norm):
                aliases = values
                break
        if aliases:
            holidays = [h for h in holidays if any(alias in h["name"].casefold() for alias in aliases)]
            mode = "festival_holidays"
        if requested_year:
            holidays = [h for h in holidays if date.fromisoformat(h["date_iso"]).year == requested_year]
        if mode == "next_holiday":
            upcoming = [h for h in holidays if date.fromisoformat(h["date_iso"]) >= now]
            holidays = upcoming[:1]
        if not holidays:
            label = "upcoming holiday" if mode == "next_holiday" else "matching holiday"
            return {"calendar_mode": mode, "markdown_answer": f"No {label} was found in the indexed academic calendar.", "holidays": []}
        if mode == "next_holiday":
            h = holidays[0]
            answer = f"The next listed holiday is **{h['name']}** on **{h['date']} ({h['day']})**."
        else:
            title = "Official Holidays" if not aliases else f"{aliases[0].title()} Holidays"
            rows = "\n".join(f"| {h['date']} | {h['day']} | {h['name']} |" for h in holidays)
            answer = f"### {title}\n\n| Date | Day | Occasion |\n|---|---|---|\n{rows}"
        return {"calendar_mode": mode, "markdown_answer": answer, "holidays": holidays}

    if mode in {"working_saturdays", "next_working_saturday"}:
        substitutions = []
        for day in days:
            if day.calendar_date.date().weekday() != 5:
                continue
            if (day.status or "").casefold() == "holiday" and not day.timetable_override:
                continue
            if (day.status or "").casefold() != "w" and not day.timetable_override:
                continue
            row = {
                "date_iso": day.calendar_date.date().isoformat(),
                "date": day.calendar_date.strftime("%B %d, %Y"),
                "natural_weekday": day.day_of_week or "Saturday",
                "applied_timetable": day.timetable_override or "Saturday",
                "notes": day.particulars or "",
            }
            if requested_year and day.calendar_date.year != requested_year:
                continue
            substitutions.append(row)
        if mode == "next_working_saturday":
            substitutions = [s for s in substitutions if date.fromisoformat(s["date_iso"]) > now][:1]
        if not substitutions:
            answer = "No matching working Saturday is listed in the indexed calendar."
        elif mode == "next_working_saturday":
            s = substitutions[0]
            answer = f"The next working Saturday is **{s['date']}**; it follows the **{s['applied_timetable']} timetable**."
        else:
            rows = "\n".join(f"| {s['date']} | {s['natural_weekday']} | {s['applied_timetable']} |" for s in substitutions)
            answer = f"### Working Saturdays\n\n| Date | Day | Timetable followed |\n|---|---|---|\n{rows}"
        return {"calendar_mode": mode, "markdown_answer": answer, "overrides": substitutions}

    if mode == "month_events":
        month_match = re.search(r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\s+(20\d{2})\b", question, re.IGNORECASE)
        if not month_match:
            return {"calendar_mode": mode, "markdown_answer": "Please include a month and year so I can look up calendar events."}
        month_num = datetime.strptime(month_match.group(1), "%B").replace(tzinfo=UTC).month
        year = int(month_match.group(2))
        include_overrides = bool(re.search(r"\b(timetable|override|substitution|working saturday)\b", question, re.IGNORECASE))
        rows = [
            _event_dict(e) for e in events
            if e.start_date.year == year and e.start_date.month == month_num
            and (include_overrides or (e.event_category or "").casefold() != "timetable_override")
        ]
        # If the calendar parser represented an event only as a day-level particular,
        # include it when no corresponding event row exists.
        known_dates = {item["date_iso"] for item in rows}
        for d in days:
            if d.calendar_date.year == year and d.calendar_date.month == month_num and d.particulars and d.calendar_date.date().isoformat() not in known_dates:
                if include_overrides or not d.timetable_override:
                    rows.append({"date_iso": d.calendar_date.date().isoformat(), "date": d.calendar_date.strftime("%B %d, %Y"), "day": d.day_of_week, "name": d.particulars, "category": "event"})
        rows.sort(key=lambda item: item["date_iso"])
        answer = "No events are listed for this month in the uploaded calendar." if not rows else "### Academic Calendar Events\n\n" + "\n".join(f"- **{e['date']} ({e['day']})** — {e['name']}" for e in rows)
        return {"calendar_mode": mode, "calendar_period": f"{month_match.group(1).title()} {year}", "markdown_answer": answer, "events": rows}

    if mode == "milestone_lookup":
        q = question.casefold()
        patterns = []
        if re.search(r"\bmid\s*[- ]?term|midterm|mid\s*sem", q):
            patterns = ("mid-term", "mid term", "midterm", "mid-sem", "mid sem", "mid semester")
        elif re.search(r"\bend\s*[- ]?sem|endsem|final exam", q):
            patterns = ("end-sem", "end sem", "endsem", "final examination", "final exam")
        elif re.search(r"\blast instruction|last working|last class", q):
            patterns = ("last instruction", "last working", "last class")
        elif re.search(r"\breopen|re-opening|reopening|resume|resumption", q):
            patterns = ("reopen", "re-opening", "reopening", "resume", "resumption", "commencement")
        else:
            patterns = ("commencement", "commence", "classes begin", "classes start")
        sem_num = _requested_semester(question) or _requested_semester(semester or "")
        cohort_aliases = _cohort_aliases(sem_num, question)
        matches = []
        for event in events:
            name = event.name.casefold()
            if not any(p in name for p in patterns):
                continue
            if cohort_aliases and (event.cohort_applicability or "ALL").upper() not in {"ALL", "*"}:
                applicability = (event.cohort_applicability or "").casefold()
                if not any(alias in applicability for alias in cohort_aliases):
                    continue
            if cohort_aliases and not any(alias in name for alias in cohort_aliases):
                # Cohort-specific question must not receive another cohort's date.
                continue
            matches.append(_event_dict(event))
        if requested_year:
            matches = [e for e in matches if date.fromisoformat(e["date_iso"]).year == requested_year]
        if not matches:
            return {"calendar_mode": mode, "markdown_answer": "I couldn't find a matching cohort milestone in the indexed academic calendar. The uploaded calendar may not contain this event or semester."}
        rendered = "; ".join(f"**{e['name']}** — {e['date']} ({e['day']})" for e in matches)
        return {"calendar_mode": mode, "markdown_answer": rendered, "events": matches}

    all_holidays = _holiday_rows(days, events)
    return {
        "calendar_mode": mode,
        "markdown_answer": f"The uploaded academic calendar contains {len(days)} dated day records and {len(events)} event records, including {len(all_holidays)} named holiday dates. Ask for a date, holiday, month, milestone, or working Saturday for details.",
    }


def _session_dict(session: WeeklySession) -> dict[str, Any]:
    return {
        "weekday": session.day_of_week,
        "slot": session.slot_numbers,
        "start_time": session.start_time,
        "end_time": session.end_time,
        "course_code": session.course_code,
        "activity": session.activity_label,
        "faculty": session.faculty,
        "venue": session.venue,
        "elective_bucket": session.elective_bucket,
    }


async def answer_timetable_query(
    question: str,
    db: AsyncSession,
    department: str,
    semester: str,
    section: str,
) -> dict[str, Any]:
    """Query the newest admin-scoped timetable and apply official date overrides."""
    if not all((department, semester, section)):
        return {"markdown_answer": "Your student profile needs a department, semester, and class/section to look up the timetable."}

    # Resolve faculty, venue and elective directory requests against the admin-scoped timetable.
    from backend.services.timetable_directory import answer_directory, is_timetable_directory_query
    if is_timetable_directory_query(question):
        directory_schedule = await _query_weekday(db, department, semester, section, None)
        if directory_schedule.get("version"):
            directory_result = await answer_directory(question, db, directory_schedule["version"], directory_schedule["all_sessions"])
            if directory_result:
                return directory_result

    # Date-specific questions first consult the official calendar. If that date
    # changes the timetable weekday, the corresponding weekly sessions are shown.
    parsed = extract_date_from_text(question)
    explicit_date = bool(re.search(r"\b(today|tomorrow|yesterday|20\d{2}[-/]\d{1,2}|\d{1,2}(?:st|nd|rd|th)?\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2})\b", question, re.IGNORECASE))
    if parsed and explicit_date and re.search(r"\b(schedule|classes|class|timetable|today|tomorrow|weekday)\b", question, re.IGNORECASE):
        calendar = await answer_calendar_query(question, db, semester=semester)
        details = calendar.get("day_details") or {}
        if details.get("is_holiday") and not details.get("timetable_override"):
            return {"timetable_mode": "date", "markdown_answer": calendar["markdown_answer"], "date_details": details, "sessions": []}
        weekday = details.get("timetable_override") or details.get("day") or date.fromisoformat(parsed).strftime("%A")
        weekday = next((day for day in _WEEKDAYS if day.casefold().startswith(str(weekday).casefold()[:3])), weekday)
        schedule = await _query_weekday(db, department, semester, section, weekday)
        return {
            "timetable_mode": "date",
            "markdown_answer": f"### {weekday} timetable for {department} Semester {semester}-{section}\n\n{calendar.get('markdown_answer', '')}",
            "date_details": details,
            "sessions": [item for item in schedule["sessions"] if (item.get("activity") or "").casefold() != "no class listed"],
            "department": department,
            "semester": semester,
            "section": section,
        }

    q = question.casefold()
    requested_weekdays = [d for d in _WEEKDAYS if re.search(rf"\b{d.casefold()}\b", q)]
    if not requested_weekdays:
        requested_weekdays = [d for d in _WEEKDAYS if re.search(rf"\b{d[:3].casefold()}\b", q)]
    weekday = requested_weekdays[0] if len(requested_weekdays) == 1 else None
    if weekday is None and re.search(r"\b(today|tomorrow)\b", q):
        parsed = extract_date_from_text(question)
        weekday = date.fromisoformat(parsed).strftime("%A") if parsed else None
    slot_match = re.search(r"\bslot\s*(\d{1,2})\b", q)
    slot_numbers = re.findall(r"\bslot\s*(\d{1,2})\b", q)
    code_match = re.search(r"\b(?:\d{2}[a-z]{3}\d{3,4})\b", q, re.IGNORECASE)
    query_type = "day_schedule" if weekday else "general"
    if re.search(r"\b(lunch|break)\b", q):
        query_type = "lunch_break"
    elif re.search(r"\b(faculty|who teaches|instructor|teacher)\b", q):
        query_type = "faculty"
    elif re.search(r"\b(where|venue|room|location|held)\b", q):
        query_type = "venue"
    elif slot_match:
        query_type = "time_slot"

    schedule = await _query_weekday(db, department, semester, section, weekday)
    sessions = schedule["sessions"]
    if len(requested_weekdays) > 1:
        sessions = [item for item in sessions if item.get("weekday") in requested_weekdays]
        query_type = "multi_day"
    if re.search(r"\b(free slots?|vacant periods?|free periods?)\b", q) or ("free" in q and slot_match):
        blanks = [s for s in sessions if (s.get("activity") or "").casefold() == "no class listed"]
        if slot_numbers:
            blanks = [s for s in blanks if any(n in re.findall(r"\d+", s.get("slot") or "") for n in slot_numbers)]
        if not blanks:
            return {"markdown_answer": "No matching blank slots were recorded in the indexed timetable. Please check the source PDF before treating a period as free."}
        rows = "\n".join(f"- **{s['weekday']} · Slot {s['slot']}** ({s['start_time']}–{s['end_time']})" for s in blanks)
        return {"timetable_mode": "free_periods", "markdown_answer": "### Slots with no class listed\n\n" + rows + "\n\nThe source timetable leaves these cells blank.", "sessions": blanks}
    blank_sessions = [s for s in sessions if (s.get("activity") or "").casefold() == "no class listed"]
    sessions = [s for s in sessions if (s.get("activity") or "").casefold() != "no class listed"]
    if slot_numbers and re.search(r"\b(any classes|any class|have.*classes)\b", q):
        matching = [s for s in sessions if any(n in re.findall(r"\d+", s.get("slot") or "") for n in slot_numbers)]
        blanks = [s for s in blank_sessions if any(n in re.findall(r"\d+", s.get("slot") or "") for n in slot_numbers)]
        if not matching and blanks:
            return {"timetable_mode": "free_periods", "markdown_answer": "No class is listed in these timetable cells.", "sessions": blanks}
    if slot_numbers:
        sessions = [s for s in sessions if any(wanted in re.findall(r"\d+", s["slot"] or "") for wanted in slot_numbers)]
    if slot_numbers and re.search(r"\b(timings?|time of|time for)\b", q) and not weekday:
        lines = []
        for number in dict.fromkeys(slot_numbers):
            matches = [s for s in sessions if re.findall(r"\d+", s["slot"] or "") == [number]]
            times_found = list(dict.fromkeys((s["start_time"], s["end_time"]) for s in matches))
            lines.append(f"- **Slot {number}:** " + (", ".join(f"{a} – {b}" for a, b in times_found) if times_found else "No individual slot timing was extracted."))
        return {"timetable_mode": "timings", "markdown_answer": "### Slot timings\n\n" + "\n".join(lines)}
    activities = ((r"\b(project(?: phase)?(?:\s*[-–]?\s*(?:ii|2))?)\b", "project"), (r"\bmentoring\b", "mentoring"), (r"\bcir\b", "cir"), (r"\bfree\s+elective\s*[- ]?\s*2\b", "free elective"))
    for pattern, token in activities:
        if re.search(pattern, q):
            sessions = [s for s in sessions if token in (s.get("activity") or "").casefold()]
            break
    times = re.findall(r"\b(\d{1,2})[:.](\d{2})\s*(am|pm)\b", q)
    if len(times) >= 2:
        def minutes(parts):
            hour, minute, period = parts
            return (int(hour) % 12 + (12 if period == "pm" else 0)) * 60 + int(minute)
        start, end = minutes(times[0]), minutes(times[1])
        def overlaps(item):
            a = re.search(r"(\d{1,2})[.:](\d{2})\s*(am|pm)", (item.get("start_time") or "").lower())
            b = re.search(r"(\d{1,2})[.:](\d{2})\s*(am|pm)", (item.get("end_time") or "").lower())
            return bool(a and b and minutes(a.groups()) < end and minutes(b.groups()) > start)
        sessions = [s for s in sessions if overlaps(s)]
    if code_match:
        code = code_match.group(0).upper()
        sessions = [s for s in sessions if code in (s.get("course_code") or "").upper() or code in (s.get("activity") or "").upper()]
    if re.search(r"\b(first|earliest)\s+class\b", q) and sessions:
        first_by_day = {}
        for item in sessions:
            first_by_day.setdefault(item.get("weekday"), item)
        sessions = list(first_by_day.values())
    if schedule.get("version") and any(s.get("course_code") for s in sessions):
        options = (await db.execute(select(ElectiveOption).where(ElectiveOption.timetable_id == schedule["version"].id))).scalars().all()
        for item in sessions:
            code = (item.get("course_code") or "").upper()
            lab = "lab" in (item.get("activity") or "").lower()
            candidates = [o for o in options if (o.course_code or "").upper() == code and ("lab" in (o.elective_bucket or "").lower()) == lab]
            if len(candidates) == 1:
                option = candidates[0]
                if (item.get("activity") or "").strip().upper() == code:
                    item["activity"] = option.course_name
                if not item.get("faculty") and option.faculty:
                    item["faculty"] = option.faculty
                day = (item.get("weekday") or "").casefold()
                day_patterns = {"monday": r"mon(?:day)?", "tuesday": r"tue(?:s(?:day)?)?", "wednesday": r"wed(?:nesday)?", "thursday": r"thu(?:r(?:s(?:day)?)?)?", "friday": r"fri(?:day)?", "saturday": r"sat(?:urday)?"}
                day_pattern = day_patterns.get(day)
                venues = [part.strip() for part in (option.venue or "").split(" | ") if day_pattern and re.search(r"\b" + day_pattern + r"\b", part, re.IGNORECASE)]
                if len(venues) == 1:
                    item["venue"] = venues[0]
    if query_type == "lunch_break":
        return {"timetable_mode": query_type, "markdown_answer": "The uploaded timetable does not define a lunch-break row." if not any("lunch" in (s["activity"] or "").casefold() for s in sessions) else "Lunch break is shown in the timetable.", "sessions": sessions}
    if query_type in {"venue", "faculty"} and sessions:
        sessions = [s for s in sessions if s.get("venue" if query_type == "venue" else "faculty")]
    if not sessions:
        return {"timetable_mode": query_type, "markdown_answer": f"No matching class timetable entry was found for **{department} Semester {semester}-{section}**. Confirm that the correct timetable is uploaded.", "sessions": [], "department": department, "semester": semester, "section": section}

    if len(requested_weekdays) > 1:
        title = f"{', '.join(requested_weekdays)} timetable for {department} Semester {semester}-{section}"
    elif weekday:
        title = f"{weekday} timetable for {department} Semester {semester}-{section}"
    elif code_match:
        title = f"Timetable entries for {code_match.group(0).upper()}"
    else:
        title = f"Class timetable for {department} Semester {semester}-{section}"
    rows = "\n".join(
        f"- **Slot {s['slot']}** ({s['start_time']}–{s['end_time']}): {s['activity']}"
        + (f" · {s['faculty']}" if s.get("faculty") else "")
        + (f" · {s['venue']}" if s.get("venue") else "")
        for s in sessions
    )
    return {"timetable_mode": query_type, "markdown_answer": f"### {title}\n\n{rows}", "sessions": sessions, "department": department, "semester": semester, "section": section}


async def _query_weekday(db: AsyncSession, department: str, semester: str, section: str, weekday: str | None) -> dict[str, Any]:
    # Roman and numeric semester values can coexist in older uploads. The admin
    # metadata is still authoritative; these are equivalent representations.
    semester_num = _semester_number(semester)
    semester_values = {str(semester).upper()}
    if semester_num:
        semester_values.add(str(semester_num))
        semester_values.update(k for k, value in _SEMESTERS.items() if value == semester_num)
    stmt = (
        select(TimetableVersion)
        .where(
            TimetableVersion.department == department.upper(),
            TimetableVersion.semester.in_(semester_values),
            TimetableVersion.section == section.upper(),
        )
        .order_by(TimetableVersion.created_at.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    version = result.scalars().first()
    if not version:
        return {"sessions": [], "all_sessions": []}
    query = select(WeeklySession).where(WeeklySession.timetable_id == version.id).order_by(WeeklySession.day_of_week, WeeklySession.slot_numbers)
    session_result = await db.execute(query)
    all_sessions = [_session_dict(s) for s in session_result.scalars().all()]
    weekday_order = {day.casefold(): i for i, day in enumerate(_WEEKDAYS)}
    def session_order(item):
        numbers = re.findall(r"\d+", item.get("slot") or "")
        return (weekday_order.get((item.get("weekday") or "").casefold(), len(weekday_order)), int(numbers[0]) if numbers else 999)
    all_sessions.sort(key=session_order)
    filtered = [s for s in all_sessions if not weekday or (s.get("weekday") or "").casefold() == weekday.casefold()]
    return {"sessions": filtered, "all_sessions": all_sessions, "version": version}
