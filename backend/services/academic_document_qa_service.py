"""Scoped retrieval for exam schedules, seating plans, and lab timetables."""
from __future__ import annotations

import json
import math
import re
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import AcademicDocumentRecord, Document
from backend.services.date_resolution import extract_date_from_text
from backend.services.embedding_service import embed_text
from backend.services.rag_orchestrator import hybrid_search_rrf


SEMESTER_NUMBER = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8}
_DEPT_PREFIXES = {"MECH": ("MEE", "MECH")}
_DAY_NAMES = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_STOP = {
    "when", "what", "which", "where", "show", "give", "get", "me", "the", "for", "a", "an",
    "is", "are", "on", "in", "of", "to", "and", "does", "do", "my", "our", "this", "that",
    "schedule", "scheduled", "exam", "exams", "examination", "examinations", "course", "courses", "date", "day", "session",
    "semester", "sem", "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december", "2025", "2026", "2027", "fn", "an", "morning", "dates", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "saturdays", "sunday", "conducted",
    "afternoon", "btech", "tech", "year", "first", "second", "third", "fourth", "fifth", "sixth",
    "seventh", "eighth", "even", "odd", "all", "any", "between", "from", "through", "until",
    "list", "please", "are", "there", "happening", "happen", "held", "events", "forenoon", "afternoon",
    "listed", "tell", "occur", "taking", "place", "branch", "branches", "department", "departments", "departmental", "students", "student", "common", "core", "extending", "up", "have", "has", "which", "complete", "full", "entire", "all", "sixth", "six", "seventh", "eighth", "professional", "elective", "electives", "open", "offered", "offerings", "computer", "science", "engineering", "code", "share", "shared", "strictly", "subject", "subjects", "examined", "papers", "notification", "window", "start", "starts", "begin", "begins", "end", "ends", "conclude", "concludes", "finish", "finishes", "civil", "chemical", "aerospace", "automation", "robotics", "electrical", "cse", "cys", "eee", "ece", "elc", "mee", "mech", "aie", "aid", "cce", "cie", "che", "aee", "are",
}
_COURSE_CODE_RE = re.compile(r"\b((?:\d{2})?[A-Z]{3,4}\d{3,4}[A-Z]?)\b", re.I)
_DEPARTMENT_ALIASES = {"MECH": ("MECH", "MEE"), "MEE": ("MECH", "MEE")}
_KNOWN_DEPARTMENTS = ("CSE", "CYS", "ARE", "CHE", "AEE", "MEE", "ECE", "CCE", "EEE", "ELC", "AIE", "AID", "CIE", "MECH")
_DEPARTMENT_PHRASES = {
    "COMPUTER SCIENCE": ("CSE",), "COMPUTER SCIENCE AND ENGINEERING": ("CSE",),
    "ELECTRONICS AND COMMUNICATION": ("ECE",), "ELECTRONICS AND COMMUNICATION ENGINEERING": ("ECE",),
    "MECHANICAL": ("MEE",), "MECHANICAL ENGINEERING": ("MEE",),
    "CYBER SECURITY": ("CYS",), "CYBERSECURITY": ("CYS",),
    "CIVIL": ("CIE",), "CIVIL ENGINEERING": ("CIE",), "CHEMICAL": ("CHE",), "CHEMICAL ENGINEERING": ("CHE",),
    "AEROSPACE": ("AEE",), "AEROSPACE ENGINEERING": ("AEE",),
    "AUTOMATION AND ROBOTICS": ("ARE",), "AUTOMATION ROBOTICS": ("ARE",),
    "ELECTRICAL": ("EEE", "ELC"), "ELECTRICAL ENGINEERING": ("EEE", "ELC"),
}

_SEAT_HALL_RE = re.compile(r"\b([A-Z])\s*[-–]\s*(\d{3})\b", re.I)
_SEAT_RANGE_RE = re.compile(
    r"(?:CB\s*[.]?\s*EN\s*[.]?\s*U4\s*)?"
    r"(CSE|CYS|EEE|ECE|ELC|MEE|CCE|CIE|ARE|CHE|AEE|AIE)\s*(\d{2})(\d{3})"
    r"\s*[-–]\s*(\d{2,5})\b", re.I,
)
_ROLL_RE = re.compile(r"\bCB\s*[.]?\s*EN\s*[.]?\s*U4\s*(CSE|CYS|EEE|ECE|ELC|MEE|CCE|CIE|ARE|CHE|AEE|AIE)\s*(\d{5})\b", re.I)
_SEAT_BLOCKS = {
    "I": {"E-201", "E-202", "E-203", "E-209", "E-210", "E-211", "W-209", "W-309", "W-310", "W-401", "W-402"},
    "II": {"A-301", "A-302", "A-303", "B-201", "B-202", "B-203", "B-301", "B-302", "B-303", "C-205"},
    "III": {"A-102", "A-103", "A-104", "A-203", "A-204", "A-205", "C-102", "C-103", "C-104", "C-203", "C-204", "C-205", "F-201", "F-203", "F-205", "F-301"},
}


def _infer_seating_section(department: str, start: int) -> str | None:
    """Infer section from the register-number band when OCR moves labels."""
    if department == "CSE":
        return chr(ord("A") + min(7, max(0, (start - 1) // 100)))
    if department == "MEE":
        return "A" if start < 100 else "B"
    if department == "ECE":
        return "A" if start < 100 else "B" if start < 200 else "C"
    if department == "EEE":
        return "A" if start < 100 else "B"
    return None


def _seat_location_label(entry: dict[str, Any]) -> str:
    block = entry.get("block")
    if block == "Auditorium near ASB":
        return block
    return f"Academic Block {block}" if block else "Location not extracted"


def _is_seating_question(question: str) -> bool:
    """Recognize seating-plan questions, including hall/count metadata queries."""
    q = (question or "").casefold()
    return bool(
        _ROLL_RE.search(question or "")
        or re.search(r"\b(seating|seated|sit|sitting|exam\s+hall|halls?|hall allocation|register number|roll number|arrear|re[ -]?regist\w*|auditorium)\b", q)
        or (re.search(r"\b(block|room|capacity|students|candidates|batch|issued|issued by|duration|timings?|session|appearing|writing)\b", q)
            and re.search(r"\b(seating|hall|circular|candidate|student|mid[ -]?term|academic block)\b", q))
        or (re.search(r"\b(mid[ -]?term|mid[ -]?sem)\b", q)
            and re.search(r"\b(date|dates|window|duration|timings?)\b", q)
            and re.search(r"\b(semester|sem)\b", q)
            and not re.search(r"\b(course|code)\b", q))
        or (re.search(r"\b(timing|time|session)\b", q)
            and re.search(r"\b(FN|AN|forenoon|afternoon|morning session)\b", question, re.I)
            and not re.search(r"\b(slot|timetable|class|course|courses)\b", q))
        or (re.search(r"\b(academic block|across all branches|total candidates)\b", q)
            and re.search(r"\b(department|departments|branch|branches|students|halls?)\b", q))
    )


def _roll_from_question(question: str) -> str | None:
    match = _ROLL_RE.search(question or "")
    if not match:
        return None
    return f"CB.EN.U4{match.group(1).upper()}{match.group(2)}"


def _seating_entries(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Turn OCR lines from a seating table into queryable hall/range entries."""
    entries: list[dict[str, Any]] = []
    current_section: dict[str, str] = {}
    for row in rows:
        text = str(row.get("text") or "")
        upper = text.upper()
        for dept, section in re.findall(r"\b(CSE|CYS|EEE|ECE|ELC|MEE|CCE|CIE|ARE|CHE|AEE|AIE)\s*[-–]\s*([A-H])\b", upper):
            current_section[dept] = section
        halls = [f"{m.group(1).upper()}-{m.group(2)}" for m in _SEAT_HALL_RE.finditer(upper)]
        if "AUDITORIUM" in upper:
            halls = ["AUDITORIUM NEAR ASB"]
        ranges = list(_SEAT_RANGE_RE.finditer(upper))
        if not ranges:
            continue
        parsed = []
        for match in ranges:
            dept, batch, first, end = match.groups()
            first_num = int(first)
            end_num = int(end[-3:])
            parsed.append({"department": dept.upper(), "batch": batch, "start": first_num, "end": end_num, "range": f"{dept.upper()}{batch}{first_num:03d}–{end_num:03d}"})
        # Some scanned rows list halls and register spans in opposite order.
        # Pair ordered ranges with ordered rooms so ranges stay stable across OCR.
        parsed.sort(key=lambda item: (item["department"], item["batch"], item["start"]))
        halls.sort(key=lambda hall: (hall[0], int(hall.split("-")[1]) if "-" in hall else 0))
        counts = [int(value) for value in re.findall(r"\b(\d{1,3})\b", upper[ranges[-1].end():])]
        # Keep only trailing count values (not section letters or unrelated labels).
        for idx, item in enumerate(parsed):
            hall = halls[min(idx, len(halls) - 1)] if halls else ""
            if item["department"] == "CSE" and item["start"] >= 400:
                hall = "AUDITORIUM NEAR ASB"
            if not hall:
                continue
            count = counts[min(idx, len(counts) - 1)] if counts else item["end"] - item["start"] + 1
            # OCR often moves merged-cell section labels one or more rows away
            # from the corresponding hall/range. Prefer the register band.
            section = _infer_seating_section(item["department"], item["start"]) or current_section.get(item["department"])
            entry = {
                **row, "hall": hall, "department": item["department"], "batch": item["batch"],
                "start": item["start"], "end": item["end"], "roll_range": item["range"],
                "count": count, "section": section,
            }
            if hall == "AUDITORIUM NEAR ASB":
                entry["block"] = "Auditorium near ASB"
            elif item["department"] in {"CIE", "ARE", "MEE", "AEE"}:
                entry["block"] = "I"
            elif item["department"] in {"ECE", "CCE", "CHE"}:
                entry["block"] = "II"
            elif item["department"] in {"CSE", "EEE", "ELC", "CYS"}:
                entry["block"] = "III"
            else:
                entry["block"] = next((name for name, block_halls in _SEAT_BLOCKS.items() if hall in block_halls), None)
            entries.append(entry)
    return entries


def _requested_departments(question: str, default: str) -> list[str]:
    """Resolve explicit branch names/codes; otherwise use profile scope or ALL."""
    q = re.sub(r"[^A-Z0-9&]+", " ", (question or "").upper())
    departments: list[str] = []
    for phrase, codes in sorted(_DEPARTMENT_PHRASES.items(), key=lambda item: -len(item[0])):
        if phrase == "CYBER SECURITY" and re.search(r"\bFOUNDATIONS? OF CYBER SECURITY\b", q):
            continue  # this is a course title, not a department scope
        if re.search(rf"\b{re.escape(phrase)}\b", q):
            departments.extend(codes)
    for code in _KNOWN_DEPARTMENTS:
        # ARE is also the very common auxiliary verb "are". Treat it as a
        # department code only when a user types the all-caps identifier.
        if code == "ARE" and not re.search(r"(?<![A-Za-z0-9])ARE(?![A-Za-z0-9])", question or ""):
            continue
        if re.search(rf"\b{code}\b", q):
            departments.append("MEE" if code == "MECH" else code)
    course_codes = re.findall(r"\b\d{2}[A-Z]{3,4}\d{3,4}[A-Z]?\b", question.upper())
    for course_code in course_codes:
        prefix_match = re.match(r"^\d{2}([A-Z]{3,4})", course_code)
        prefix = prefix_match.group(1) if prefix_match else ""
        if prefix in _KNOWN_DEPARTMENTS:
            departments.append("MEE" if prefix == "MECH" else prefix)
    course_code = course_codes[0] if course_codes else None
    # A date-only schedule question asks what is happening across the
    # institution; an unqualified course/timetable request remains profile scoped.
    global_query = bool(re.search(
        r"\b(which branches|which departments|what branches|what departments|all branches|all departments|across all|common to all|every branch|every department|which branches share|which departments share|which branches have|are there any examinations? (?:conducted )?on saturdays?|examinations? (?:conducted )?on saturdays?|who has exams on saturday|which departments? have exams on saturday|which branches? have exams on saturday|which branches have exams extending|which departments have exams extending|which courses are conducted .*afternoon|which courses .* strictly .*afternoon|exam window|start and end|start to finish)\b",
        question or "", re.I,
    ))
    date_only = bool(_query_dates(question)) and not departments and not course_code
    if global_query or date_only:
        return ["ALL"]
    return list(dict.fromkeys(departments)) or ["MEE" if default.upper() == "MECH" else default.upper()]


def _kind(query: str) -> str:
    q = query.casefold()
    if re.search(r"\b(lab(?:oratory)?|it\s*lab)\b", q) and not re.search(r"\b(seating|exam\s+hall|hall\s+allocation)\b", q):
        return "lab_timetable"
    if _is_seating_question(query):
        return "seating_arrangement"
    return "exam_schedule"


def _semester_number(value: str | None) -> int | None:
    value = (value or "").strip().upper()
    return int(value) if value.isdigit() else SEMESTER_NUMBER.get(value)


def _semester_values(value: str) -> set[str]:
    normalized = (value or "").strip().upper()
    number = _semester_number(normalized)
    values = {normalized} if normalized else set()
    if number:
        values.add(str(number))
        values.update(roman for roman, n in SEMESTER_NUMBER.items() if n == number)
    return values


def _requested_semester(query: str) -> int | None:
    ordinal_words = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8}
    m = re.search(r"\b(?:sem(?:ester)?\s*)([1-8])(?:st|nd|rd|th)?\b", query, re.I)
    if m:
        return int(m.group(1))
    m = re.search(r"\b(first|second|third|fourth|fifth|sixth|seventh|eighth)\s*(?:semester|sem)\b", query, re.I)
    if m:
        return ordinal_words[m.group(1).casefold()]
    m = re.search(r"\b([1-8])(?:st|nd|rd|th)[- ]*(?:semester|sem)\b", query, re.I)
    if m:
        return int(m.group(1))
    m = re.search(r"\b(?:sem(?:ester)?\s*)(i{1,3}|iv|v|vi|vii|viii)\b", query, re.I)
    return SEMESTER_NUMBER[m.group(1).upper()] if m else None


def _course_code(query: str) -> str | None:
    m = re.search(r"\b\d{2}[A-Z]{3,4}\d{3,4}[A-Z]?\b", query.upper())
    return m.group(0) if m else None


def _date_in_text(text: str) -> date | None:
    for pattern, order in (
        (r"\b(\d{1,2})[/-](\d{1,2})[/-](20\d{2})\b", "dmy"),
        (r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b", "ymd"),
        (r"\b(\d{1,2})\.(\d{1,2})\.(20\d{2})\b", "dmy"),
    ):
        m = re.search(pattern, text)
        if not m:
            continue
        try:
            parts = [int(x) for x in m.groups()]
            y, month, day = (parts[0], parts[1], parts[2]) if order == "ymd" else (parts[2], parts[1], parts[0])
            return date(y, month, day)
        except ValueError:
            continue
    for pattern, month_first in (
        (r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{1,2})(?:st|nd|rd|th)?[,]?\s+(20\d{2})\b", True),
        (r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(january|february|march|april|may|june|july|august|september|october|november|december)[,]?\s+(20\d{2})\b", False),
    ):
        match = re.search(pattern, text, re.I)
        if not match:
            continue
        month_names = {name: number for number, name in enumerate(("january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"), 1)}
        first, second, year = match.groups()
        try:
            month, day = (month_names[first.casefold()], int(second)) if month_first else (month_names[second.casefold()], int(first))
            return date(int(year), month, day)
        except (KeyError, ValueError):
            continue
    return None


def _query_dates(query: str) -> list[date]:
    found: list[date] = []
    months = "january|february|march|april|may|june|july|august|september|october|november|december"
    explicit_years = re.findall(r"\b20\d{2}\b", query)
    shared_year = int(explicit_years[-1]) if explicit_years else None
    month_numbers = {name: number for number, name in enumerate(months.split("|"), 1)}
    named_patterns = (
        (rf"\b({months})\s+(\d{{1,2}})(?:st|nd|rd|th)?[,]?(?:\s+(20\d{{2}}))?\b", True),
        (rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({months})[,]?(?:\s+(20\d{{2}}))?\b", False),
    )
    for pattern, month_first in named_patterns:
        for match in re.finditer(pattern, query, re.I):
            first, second, explicit = match.groups()
            try:
                month, day = (
                    (month_numbers[first.casefold()], int(second))
                    if month_first
                    else (month_numbers[second.casefold()], int(first))
                )
                year = int(explicit) if explicit else shared_year
                if year is None:
                    resolved = extract_date_from_text(match.group(0))
                    if resolved:
                        found.append(date.fromisoformat(resolved))
                else:
                    found.append(date(year, month, day))
            except (KeyError, ValueError):
                continue
    numeric = re.findall(r"\b\d{1,2}[/-]\d{1,2}[/-]20\d{2}\b", query)
    for value in numeric:
        parsed = _date_in_text(value)
        if parsed:
            found.append(parsed)
    unique = sorted(set(found))
    if not unique:
        parsed = extract_date_from_text(query)
        if parsed:
            unique.append(date.fromisoformat(parsed))
    return unique


def _query_month_year(query: str) -> tuple[int, int] | None:
    match = re.search(
        r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\s+(20\d{2})\b",
        query,
        re.I,
    )
    if not match:
        return None
    months = "january february march april may june july august september october november december".split()
    return months.index(match.group(1).casefold()) + 1, int(match.group(2))


def _tokens(query: str) -> list[str]:
    return [
        token for token in re.findall(r"[a-z0-9]+", query.casefold())
        if len(token) > 2 and token not in _STOP and not token.isdigit()
        and not re.fullmatch(r"[1-8](?:st|nd|rd|th)", token)
    ]


def _minimum_course_token_hits(tokens: list[str]) -> int:
    """Require a close title match without combining words across courses."""
    if not tokens:
        return 0
    if len(tokens) == 1:
        return 1
    return max(2, math.ceil(len(tokens) * 0.8))


def _normalized_course_text(value: str) -> str:
    """Normalize camel-cased PDF/OCR course names into searchable words."""
    value = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", value or "")
    value = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", value)
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _record_dict(record: AcademicDocumentRecord, filename: str, admin_department: str | None = None, document_created_at: datetime | None = None) -> dict[str, Any]:
    try:
        payload = json.loads(record.payload_json or "{}")
    except (TypeError, ValueError):
        payload = {}
    return {
        "text": record.record_text or "",
        "page": record.page_number,
        "line": record.line_number,
        "payload": payload if isinstance(payload, dict) else {},
        "filename": filename,
        "document_id": str(record.document_id),
        "document_created_at": document_created_at,
        "admin_department": (admin_department or "").strip().upper(),
        "table_department": str(payload.get("table_department") or "").strip().upper(),
        "has_scoped_table_department": bool(payload.get("table_department")),
    }


def _collapse_exam_table_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse OCR lines from one ruled exam-table row into a single record."""
    collapsed: list[dict[str, Any]] = []
    positions: dict[tuple[str, int, int, str], int] = {}
    for row in rows:
        payload = row.get("payload") or {}
        table_row = payload.get("table_row")
        table_text = payload.get("table_row_text")
        if table_row is None or not table_text:
            collapsed.append(row)
            continue
        key = (row["document_id"], int(row["page"]), int(table_row), row.get("table_department") or "")
        if key in positions:
            continue
        item = dict(row)
        item["text"] = str(table_text)
        positions[key] = len(collapsed)
        collapsed.append(item)
    return collapsed


def _filter_exam_pages_by_department(rows: list[dict[str, Any]], department: str) -> list[dict[str, Any]]:
    """Prefer pages whose timetable column header matches the admin scope.

    Multi-department PDFs sometimes repeat courses such as CSE electives in a
    different department's column. Course-code prefix filtering alone would
    leak those rows into the student's schedule. If the document has explicit
    department headers, use the requested department's page(s) as the source
    of truth; retain legacy line-only documents when no headers are detectable.
    """
    if department.strip().upper() in {"ALL", "ALL DEPARTMENTS"}:
        return rows
    page_rows: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for row in rows:
        page_rows.setdefault((row["document_id"], int(row["page"])), []).append(row)

    recognized_pages: set[tuple[str, int]] = set()
    matching_pages: set[tuple[str, int]] = set()
    wanted_aliases = _DEPARTMENT_ALIASES.get(department.upper(), (department.upper(),))
    for key, page in page_rows.items():
        for row in page:
            text = str((row.get("payload") or {}).get("table_header_text") or row["text"])
            if not (re.search(r"\bdate\b", text, re.I) and re.search(r"\bsession\b", text, re.I)):
                continue
            headers = {
                name for name in _KNOWN_DEPARTMENTS
                if re.search(rf"\b{re.escape(name)}\b", text, re.I)
            }
            if headers:
                recognized_pages.add(key)
                if any(alias in headers for alias in wanted_aliases):
                    matching_pages.add(key)
                break

    if not recognized_pages or not matching_pages:
        return rows
    return [row for row in rows if (row["document_id"], int(row["page"])) in matching_pages]


def _matches_course(row: dict[str, Any], query: str, code: str | None) -> bool:
    normalized = _normalized_course_text(row["text"])
    if code:
        return re.sub(r"[^a-z0-9]+", "", code.casefold()) in normalized.replace(" ", "")
    tokens = _tokens(query)
    if not tokens:
        return True
    hits = sum(1 for token in tokens if token in normalized)
    return hits >= _minimum_course_token_hits(tokens)


def _match_roll_range(text: str, roll: str) -> bool:
    normalized = re.sub(r"[^A-Z0-9]", "", text.upper())
    target = re.sub(r"[^A-Z0-9]", "", roll.upper())
    if target in normalized:
        return True
    # Seating schedules commonly abbreviate the right-hand end, e.g. CSE23001-018.
    match = re.search(r"\b(CSE|CYS|EEE|ECE|ELC|MEE|CCE|CIE|ARE|CHE|AEE|AIE)(\d{2})(\d{3})\s*[-–]\s*(\d{2,4})\b", text.upper())
    if not match:
        return False
    prefix, batch, first, last = match.groups()
    roll_match = re.search(rf"{prefix}{batch}(\d{{3}})$", target)
    if not roll_match:
        return False
    last_num = int(last[-3:])
    return int(first) <= int(roll_match.group(1)) <= last_num


def _seat_entry_matches_roll(entry: dict[str, Any], roll: str) -> bool:
    match = _ROLL_RE.search(roll or "")
    if not match:
        return False
    dept, serial = match.group(1).upper(), int(match.group(2)[-3:])
    return entry["department"] == dept and entry["start"] <= serial <= entry["end"]


def _seating_answer(question: str, rows: list[dict[str, Any]], department: str, semester: str, student_roll_number: str | None) -> dict[str, Any]:
    entries = _seating_entries(rows)
    if not entries:
        return {"document_mode": "seating_arrangement", "markdown_answer": "The seating PDF is indexed, but its hall and register-number rows could not be parsed. Reprocess it with OCR enabled to answer these seating lookups."}
    q = question.casefold()
    joined = "\n".join(row["text"] for row in rows)
    if re.search(r"\b(re[ -]?regist\w*|arrear)\b", q) and re.search(r"\bW\s*[-–]\s*209\b", joined, re.I):
        return {"document_mode": "seating_arrangement", "title": "Re-registration candidates", "markdown_answer": "Re-registered / arrear candidates are assigned to **Hall W-209, Academic Block I** according to the seating circular.", "records": [row for row in rows if re.search(r"re[ -]?register|arrear|W\s*[-–]\s*209", row["text"], re.I)][:8]}
    explicit_roll = _roll_from_question(question)
    personal_lookup = bool(re.search(r"\b(my|me|where am i|where do i sit|my roll)\b", q))
    roll = explicit_roll or (student_roll_number if personal_lookup else None)
    if roll:
        hit = next((entry for entry in entries if _seat_entry_matches_roll(entry, roll) or _match_roll_range(entry["text"], roll)), None)
        if hit:
            block = hit["block"] if hit.get("block") == "Auditorium near ASB" else f"Academic Block – {hit['block']}" if hit.get("block") else "Block not extracted"
            return {"document_mode": "seating_arrangement", "title": "Exam seating allocation", "markdown_answer": f"### Exam Seating Allocation\n\n- **Register number:** `{roll}`\n- **Hall:** **{hit['hall']}**\n- **Department / section:** {hit['department']}{'-' + hit['section'] if hit.get('section') else ''}\n- **Location:** {block}\n- **Register range:** `{hit['roll_range']}`\n- **Source:** `{hit['filename']}`, page {hit['page']}", "records": [hit], "source": {"filename": hit["filename"], "page": hit["page"]}}
        return {"document_mode": "seating_arrangement", "markdown_answer": f"Register number **{roll}** was not found in the uploaded seating arrangement for **{department} Semester {semester}**. Check that the correct seating circular and semester are indexed."}

    # Header and issuer questions are answered from extracted source lines.
    if re.search(r"\b(when was|issued|notification|circular date|who issued|issued by|controller)\b", q):
        date_match = re.search(r"\b(\d{1,2}[.]\d{1,2}[.]20\d{2}|\d{1,2}[/-]\d{1,2}[/-]20\d{2})\b", joined)
        issuer = re.search(r"((?:Dy\.?\s*)?Controller of Examinations)", joined, re.I)
        facts = []
        if date_match:
            facts.append(f"**Issue date:** {date_match.group(1)}")
        if issuer:
            facts.append(f"**Issued by:** {issuer.group(1)}")
        if facts:
            return {"document_mode": "seating_arrangement", "title": "Seating circular details", "markdown_answer": "### Seating Circular\n\n" + "\n\n".join(facts), "records": rows[:8]}

    if re.search(r"\b(when|what are|date|window|between|from|start|end)\b", q) and re.search(r"\b(exam|examination|mid[ -]?term|mid[ -]?sem|window)\b", q):
        window = re.search(r"\b(?:between\s+)?(\d{1,2})(?:st|nd|rd|th)?\s+(?:to|through|and|[-–])\s+(\d{1,2})(?:st|nd|rd|th)?\s+(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s*(20\d{2})\b", joined, re.I)
        if window:
            return {"document_mode": "seating_arrangement", "title": "Examination window", "markdown_answer": f"The seating circular covers **Semester {semester}** examinations from **{window.group(1)} to {window.group(2)} {window.group(3)} {window.group(4)}**.", "records": rows[:8]}

    if re.search(r"\b(timing|time|duration|long|FN|forenoon|morning|AN|afternoon|session)\b", q):
        session = "FN" if re.search(r"\b(FN|forenoon|morning)\b", q, re.I) else "AN" if re.search(r"\b(AN|afternoon)\b", q, re.I) else None
        time_matches = list(re.finditer(r"(FN|AN)\s*:?\s*(\d{1,2}[.:]\d{2}\s*(?:am|pm))\s*(?:to|[-–])\s*(\d{1,2}[.:]\d{2}\s*(?:am|pm))", joined, re.I))
        time_re = next((m for m in time_matches if m.group(1).upper() == session), None) if session else (time_matches[0] if time_matches else None)
        if time_re:
            duration = "2 hours" if "2" in q or "long" in q or "duration" in q else ""
            label = time_re.group(1).upper()
            return {"document_mode": "seating_arrangement", "title": "Examination session timing", "markdown_answer": f"**{label} session:** {time_re.group(2)} to {time_re.group(3)}" + (f" (**{duration}**)." if duration else "."), "records": rows[:8]}
    if re.search(r"\b(batch|semester|cohort)\b", q) and not re.search(r"\b(roll|register|hall|section|department|students|candidates)\b", q):
        batch = re.search(r"(20\d{2})\s*batch", joined, re.I)
        sem = re.search(r"(\d)(?:st|nd|rd|th)\s*sem(?:ester)?[. ]*", joined, re.I)
        if batch or sem:
            return {"document_mode": "seating_arrangement", "title": "Covered cohort", "markdown_answer": f"This seating circular covers **{sem.group(1) + 'th semester ' if sem else ''}B.Tech{', ' + batch.group(1) + ' batch' if batch else ''}**.", "records": rows[:8]}

    room_matches = {f"{m.group(1).upper()}-{m.group(2)}" for m in _SEAT_HALL_RE.finditer(question)}
    asks_cse_main_blocks = bool(
        re.search(r"\bCSE\b", question, re.I)
        and re.search(r"\b(main\s+)?academic\s+blocks?\b", q)
        and not re.search(r"\bauditorium\b", q)
    )
    block_match = re.search(r"\b(?:academic\s+)?block\s*(?:-|–)?\s*(i{1,3}|1|2|3|one|two|three)\b", q)
    block_wanted = None
    if block_match:
        block_wanted = {"1": "I", "one": "I", "2": "II", "two": "II", "3": "III", "three": "III"}.get(block_match.group(1), block_match.group(1).upper())
    if "auditorium" in q:
        selected = [entry for entry in entries if "auditorium" in entry["text"].casefold() or entry.get("section") in {"E", "F", "G", "H"} and entry["department"] == "CSE"]
    elif asks_cse_main_blocks:
        selected = [entry for entry in entries if entry["department"] == "CSE" and entry.get("section") in {"A", "B", "C", "D"}]
        if selected:
            sections = ", ".join(sorted({entry["section"] for entry in selected}))
            halls = ", ".join(sorted({entry["hall"] for entry in selected}))
            return {"document_mode": "seating_arrangement", "title": "CSE sections in academic blocks", "markdown_answer": f"Yes. **CSE Sections {sections}** are assigned to the main Academic Blocks (Block III): {halls}.", "records": selected[:80]}
    elif room_matches:
        selected = [entry for entry in entries if entry["hall"] in room_matches]
    elif block_wanted:
        selected = [entry for entry in entries if entry.get("block") == block_wanted]
    else:
        requested_depts = _requested_departments(question, department)
        selected = entries if requested_depts == ["ALL"] else [entry for entry in entries if entry["department"] in requested_depts]
        sections = {section.upper() for section in re.findall(r"\b(?:section|sec\.?|CSE\s*[- ]?|ECE\s*[- ]?|EEE\s*[- ]?|ELC\s*[- ]?|MEE\s*[- ]?|CYS\s*[- ]?|CCE\s*[- ]?|CHE\s*[- ]?|CIE\s*[- ]?|ARE\s*[- ]?|AEE\s*[- ]?)([A-H])\b", question, re.I)}
        for section_match in re.finditer(r"\bsections?\b(.{0,50})", question, re.I):
            sections.update(letter.upper() for letter in re.findall(r"\b([A-H])\b", section_match.group(1), re.I))
        if sections:
            selected = [entry for entry in selected if entry.get("section") in sections]
        if not selected and not requested_depts:
            selected = entries

    if not selected:
        return {"document_mode": "seating_arrangement", "markdown_answer": "No matching hall or register range was found in the indexed seating circular. Check its upload scope and OCR extraction status."}

    asks_count = bool(re.search(r"\b(how many|count|total|strength|capacity|number of students|candidates)\b", q))
    asks_block = bool(re.search(r"\b(block|building|academic block)\b", q))
    if asks_count:
        count = sum(int(entry["count"]) for entry in selected)
        scope = ", ".join(sorted({entry["department"] + ("-" + entry["section"] if entry.get("section") else "") for entry in selected}))
        if "across all branches" in q or "total candidates" in q:
            page_counts: dict[int, int] = {}
            for entry in selected:
                page_counts[int(entry["page"])] = page_counts.get(int(entry["page"]), 0) + int(entry["count"])
            subtotals = "; ".join(f"page {page}: {value:,}" for page, value in sorted(page_counts.items()))
            answer = f"The indexed seating entries list **{count:,} students total**" + (f" ({subtotals})." if subtotals else ".")
        else:
            answer = f"The indexed seating entries list **{count:,} students** for **{scope}**."
        title = "Seating count"
    else:
        if asks_block:
            blocks = sorted({entry["block"] for entry in selected if entry.get("block")})
            if re.search(r"\b(hall|halls|room|rooms)\b", q):
                hall_labels = sorted({entry["hall"] for entry in selected})
                answer = "**Hall(s):** " + ", ".join(hall_labels) + (f"\n\n**Location:** Academic Block {', '.join(blocks)}" if blocks else "")
            elif re.search(r"\b(department|departments|branch|branches)\b", q):
                depts = sorted({entry["department"] for entry in selected})
                answer = "**Departments represented:** " + ", ".join(depts) + (f"\n\n**Location:** Academic Block {', '.join(blocks)}" if blocks else "")
            else:
                answer = "**Academic block(s):** " + (", ".join(f"Block {block}" for block in blocks) or "not identified in the indexed rows")
        else:
            title = "Seating locations"
            answer = "\n".join(f"- **{entry['department']}{'-' + entry['section'] if entry.get('section') else ''}** — Hall **{entry['hall']}**, {_seat_location_label(entry)}; register range `{entry['roll_range']}` ({entry['count']} students)." for entry in selected)
        title = "Seating locations"
    return {"document_mode": "seating_arrangement", "title": title, "markdown_answer": f"### {title}\n\n{answer}", "records": selected[:80], "source": {"filename": selected[0]["filename"], "page": selected[0]["page"]}}


def _exam_type(question: str) -> str | None:
    q = question.casefold().replace("-", " ")
    if re.search(r"\b(mid\s*term|mid\s*semester|midsem)\b", q):
        return "mid_term"
    if re.search(r"\b(end\s*sem(?:ester)?|endsem|final\s+exam)\b", q):
        return "end_sem"
    return None


def _document_exam_type(filename: str, rows: list[dict[str, Any]]) -> str | None:
    source = filename.casefold().replace("-", " ").replace("_", " ") + " " + " ".join(r["text"] for r in rows[:30]).casefold()
    if re.search(r"\b(mid\s*term|mid\s*semester|midsem)\b", source):
        return "mid_term"
    if re.search(r"\b(end\s*sem(?:ester)?|endsem|final\s+exam)\b", source):
        return "end_sem"
    return None


def _context_rows(rows: list[dict[str, Any]], selected: list[int], radius: int = 1) -> list[dict[str, Any]]:
    indexes: set[int] = set()
    for idx in selected:
        seed = rows[idx]
        for j in range(max(0, idx - radius), min(len(rows), idx + radius + 1)):
            candidate = rows[j]
            if candidate.get("document_id") == seed.get("document_id") and candidate.get("page") == seed.get("page"):
                indexes.add(j)
    return [rows[i] for i in sorted(indexes)]


def _date_matches(row: dict[str, Any], target_dates: list[date]) -> bool:
    row_date = _date_in_text(row["text"])
    return row_date in target_dates if row_date and target_dates else False


def _is_date_range_query(question: str, target_dates: list[date]) -> bool:
    return len(target_dates) >= 2 and bool(re.search(r"\b(between|from)\b", question, re.I)) and bool(re.search(r"\b(to|through|until|and)\b", question, re.I))


def _row_has_requested_date(
    rows: list[dict[str, Any]],
    index: int,
    target_dates: list[date],
    date_range: bool,
    *,
    year_is_explicit: bool = True,
) -> bool:
    row = rows[index]
    effective = row.get("effective_date")
    if effective is None:
        return False
    if date_range:
        if not year_is_explicit:
            start, end = min(target_dates), max(target_dates)
            return (start.month, start.day) <= (effective.month, effective.day) <= (end.month, end.day)
        return min(target_dates) <= effective <= max(target_dates)
    if not year_is_explicit:
        return any((effective.month, effective.day) == (target.month, target.day) for target in target_dates)
    return effective in target_dates


def _add_schedule_context(rows: list[dict[str, Any]]) -> None:
    """Carry date/session labels down OCR-wrapped table rows, within each page."""
    current_key = None
    current_date: date | None = None
    current_sessions: set[str] = set()
    current_weekday: str | None = None
    for row in rows:
        payload = row.get("payload") or {}
        if "table_row" in payload and payload.get("table_row") is not None:
            # Scanned schedules have table-cell reading order rather than
            # sentence reading order. Use the row label extracted from the
            # whole printed grid band, so a centered date applies to course
            # text that OCR emitted earlier in that same band.
            row["effective_date"] = _date_in_text(str(payload.get("table_row_date") or ""))
            row["effective_sessions"] = {
                str(value).upper() for value in payload.get("table_row_sessions") or []
            }
            row["effective_weekday"] = (
                payload.get("table_row_weekday")
                or (row["effective_date"].strftime("%A") if row["effective_date"] else None)
            )
            continue
        key = (row.get("document_id"), row.get("page"))
        if key != current_key:
            current_key = key
            current_date = None
            current_sessions = set()
            current_weekday = None
        parsed_date = _date_in_text(row["text"])
        if parsed_date:
            current_date = parsed_date
            current_sessions = set()
            current_weekday = parsed_date.strftime("%A")
        weekday_match = next((day for day in _DAY_NAMES if re.search(rf"\b{day}\b", row["text"], re.I)), None)
        if weekday_match:
            current_weekday = weekday_match.title()
        session_markers = set(re.findall(r"\b(FN|AN)\b", row["text"], re.I))
        if session_markers:
            current_sessions = {marker.upper() for marker in session_markers}
        row["effective_date"] = current_date
        row["effective_sessions"] = set(current_sessions)
        row["effective_weekday"] = current_weekday


def _filter_course_segments(
    text: str,
    department: str,
    query: str,
    requested_code: str | list[str] | None,
    *,
    include_shared: bool = False,
) -> str:
    """Keep only course fragments in the admin-scoped department."""
    if (department or "").strip().upper() in {"ALL", "ALL DEPARTMENTS"}:
        return text
    matches = list(_COURSE_CODE_RE.finditer(text))
    requested_codes = {re.sub(r"[^a-z0-9]+", "", code.casefold()) for code in (requested_code if isinstance(requested_code, list) else [requested_code]) if code}
    if not matches:
        if _date_in_text(text) or re.search(r"\b(FN|AN|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b", text, re.I):
            return text
        wanted = _tokens(query)
        normalized_text = _normalized_course_text(text)
        hits = sum(1 for token in wanted if re.search(rf"\b{re.escape(token)}\b", normalized_text))
        return text if wanted and hits >= _minimum_course_token_hits(wanted) else ""
    aliases = _DEPARTMENT_ALIASES.get((department or "").upper(), ((department or "").upper(),))
    local = []
    local_title_matches = []
    explicit_matches = []
    shared = []
    shared_prefixes = {"LSE"}
    wanted = _tokens(query)
    for index, match in enumerate(matches):
        code = match.group(1).upper()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        segment = text[match.start():end].strip(" /|,;:-")
        segment = re.sub(
            r"\b(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b.*$",
            "",
            segment,
            flags=re.I,
        ).strip(" /|,;:-")
        prefix = re.sub(r"^\d{2}", "", code)
        if requested_codes and re.sub(r"[^a-z0-9]+", "", code.casefold()) in requested_codes:
            explicit_matches.append(segment)
            continue
        segment_normalized = _normalized_course_text(segment)
        hits = sum(1 for token in wanted if re.search(rf"\b{re.escape(token)}\b", segment_normalized))
        title_matches = bool(wanted and hits >= _minimum_course_token_hits(wanted))
        if any(prefix.startswith(alias) for alias in aliases):
            local.append(segment)
            if title_matches:
                local_title_matches.append(segment)
            continue
        if include_shared and any(prefix.startswith(alias) for alias in shared_prefixes):
            shared.append(segment)
            if title_matches:
                explicit_matches.append(segment)
            continue
        # A shared course can still be returned when the student explicitly
        # asked for its title rather than its department-prefix code.
        if title_matches:
            explicit_matches.append(segment)
    if requested_codes:
        selected = explicit_matches
    elif wanted and local_title_matches:
        # If a course title was supplied, return the title-matching course
        # fragment(s) in the student's department when OCR flattened columns.
        selected = local_title_matches
    elif wanted and explicit_matches:
        # Shared or explicitly named courses can be matched by title when the
        # uploaded schedule has no same-title course in this department.
        selected = explicit_matches
    elif local:
        selected = [*local, *shared] if include_shared else local
    elif shared:
        selected = shared
    else:
        selected = explicit_matches
    if not selected:
        # Preserve only structural date/session labels when no course fragment
        # belongs to this department. This prevents other departments' subjects
        # from leaking through a flattened multi-column OCR row.
        prefix = text[:matches[0].start()].strip(" /|,;:-")
        return prefix if _date_in_text(prefix) or re.search(r"\b(FN|AN|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b", prefix, re.I) else ""
    prefix = text[:matches[0].start()].strip(" /|,;:-")
    return " — ".join(part for part in (prefix, " / ".join(selected)) if part)


def _is_course_name_query(question: str, code: str | None) -> bool:
    return code is not None or bool(_tokens(question))


async def answer_academic_document_query(
    question: str,
    db: AsyncSession,
    department: str,
    semester: str,
    student_roll_number: str | None = None,
) -> dict[str, Any]:
    """Retrieve only records in the authenticated student's department/semester."""
    kind = _kind(question)
    department = (department or "").strip().upper()
    semester = (semester or "").strip().upper()
    if not department or not semester:
        return {"markdown_answer": "Your student profile needs a department and semester before I can search these academic documents."}

    requested_semester = _requested_semester(question)
    profile_semester = _semester_number(semester)
    if kind not in {"exam_schedule", "seating_arrangement", "lab_timetable"} and requested_semester and profile_semester and requested_semester != profile_semester:
        return {"markdown_answer": f"Your registered profile is **{department} Semester {semester}**, but this question asks about Semester {requested_semester}. I only search records matching your registered profile."}

    explicit_roll = _roll_from_question(question) if kind == "seating_arrangement" else None
    roll_department = None
    if explicit_roll:
        roll_match = _ROLL_RE.search(explicit_roll)
        roll_department = roll_match.group(1).upper() if roll_match else None
    personal_seating_lookup = kind == "seating_arrangement" and bool(
        explicit_roll or re.search(r"\b(my|me|where am i|where do i sit|my roll)\b", question, re.I)
    )
    query_semester = str(requested_semester) if requested_semester and kind in {"exam_schedule", "seating_arrangement"} else (semester if personal_seating_lookup else None) if kind == "seating_arrangement" else semester
    query_semester_values = _semester_values(query_semester or "")
    if kind == "seating_arrangement":
        departments = [roll_department] if roll_department else _requested_departments(question, department)
        if re.search(r"\b(all branches|all departments|across all|every branch|every department|total candidates|across all branches|which branches|which departments|all halls|which exam halls|hall(s)? used|auditorium)\b", question, re.I) or re.search(r"\bacademic\s+block\b", question, re.I) or _SEAT_HALL_RE.search(question):
            departments = ["ALL"]
    else:
        departments = _requested_departments(question, department) if kind == "exam_schedule" else [department]
    is_global_department_query = departments == ["ALL"]
    document_departments: set[str] = {"ALL", "ALL DEPARTMENTS"}
    if is_global_department_query:
        document_departments.update(_KNOWN_DEPARTMENTS)
    else:
        for requested_department in departments:
            document_departments.add(requested_department)
            document_departments.update(_DEPARTMENT_ALIASES.get(requested_department, ()))

    conditions = [Document.doc_type == kind]
    # A lab room timetable can list many programs on the same facility grid.
    # The admin-selected department/semester remains document metadata, but it
    # must not discard the other rows in a room-wide schedule.
    if kind != "lab_timetable":
        conditions.append(Document.department.in_(document_departments))
    if query_semester_values and kind != "lab_timetable":
        conditions.append(Document.semester.in_(query_semester_values))
    stmt = (
        select(AcademicDocumentRecord, Document.filename, Document.id, Document.department, Document.created_at)
        .join(Document, Document.id == AcademicDocumentRecord.document_id)
        .where(*conditions)
        .order_by(Document.created_at.desc(), AcademicDocumentRecord.page_number, AcademicDocumentRecord.line_number)
    )
    result = await db.execute(stmt)
    rows = []
    for item in result.all():
        record, filename = item[0], item[1]
        admin_department = item[3] if len(item) > 3 else department
        created_at = item[4] if len(item) > 4 else None
        rows.append(_record_dict(record, filename, admin_department, created_at))
    if kind == "lab_timetable" and rows:
        # Lab room schedules are often re-uploaded for a new term. Use one
        # matching document revision per query instead of merging stale rows
        # from every historical upload into a single availability answer.
        room_match = re.search(r"\b([A-Z]{1,3})\s*[-–]?\s*(\d{2,4})\b", question, re.I)
        facility_match = re.search(r"\b(?:[A-Z0-9]+\s+)?LAB\s*[-–]?\s*(?:[IVX]+|\d+)\b", question, re.I)
        if room_match or facility_match:
            wanted_room = re.sub(r"[^A-Z0-9]", "", room_match.group(0).upper()) if room_match else None
            wanted_facility = _normalized_course_text(facility_match.group(0)) if facility_match else None
            grouped: dict[str, list[dict[str, Any]]] = {}
            for row in rows:
                grouped.setdefault(row["document_id"], []).append(row)
            matching_groups = []
            for doc_rows in grouped.values():
                payload = next((r["payload"] for r in doc_rows if r.get("payload")), {})
                room = re.sub(r"[^A-Z0-9]", "", str(payload.get("room") or "").upper())
                facility = _normalized_course_text(str(payload.get("facility") or ""))
                if (wanted_room and room == wanted_room) or (wanted_facility and wanted_facility in facility):
                    matching_groups.append(doc_rows)
            if matching_groups:
                rows = max(
                    matching_groups,
                    key=lambda group: group[0].get("document_created_at") or datetime.min,
                )
        else:
            # Without a room identifier, select the latest upload. This avoids
            # treating superseded timetable revisions as simultaneously active.
            newest_document_id = rows[0]["document_id"]
            rows = [row for row in rows if row["document_id"] == newest_document_id]
    if kind == "exam_schedule" and not is_global_department_query and len(departments) == 1:
        rows = _filter_exam_pages_by_department(rows, departments[0])
    if kind == "exam_schedule":
        rows = _collapse_exam_table_rows(rows)
    _add_schedule_context(rows)
    if not rows:
        label = {"lab_timetable": "lab timetable", "seating_arrangement": "seating arrangement", "exam_schedule": "exam schedule"}[kind]
        requested_label = "all departments" if is_global_department_query else ", ".join(departments)
        semester_label = f" Semester {query_semester}" if query_semester else ""
        return {"document_mode": kind, "markdown_answer": f"No {label} is indexed for **{requested_label}{semester_label}**. An administrator must upload it with the matching department scope and semester selected."}

    code = _course_code(question)
    course_codes = re.findall(r"\b\d{2}[A-Z]{3,4}\d{3,4}[A-Z]?\b", question.upper())
    target_dates = _query_dates(question)
    weekday = next((day.title() for day in _DAY_NAMES if re.search(rf"\b{day}s?\b", question, re.I)), None)
    session_filter = "FN" if re.search(r"\b(FN|forenoon|morning)\b", question, re.I) else "AN" if re.search(r"\b(AN|afternoon)\b", question, re.I) else None
    strict_session = bool(re.search(r"\b(strictly|only|exclusively)\b", question, re.I))
    is_free_query = bool(re.search(r"\b(free|vacant|available|availability|empty|open|unoccupied|booking|booked|reserve|reserved|extra\s+session)\b", question, re.I))

    if kind == "seating_arrangement":
        return _seating_answer(question, rows, department, query_semester or semester, student_roll_number)

    if kind == "lab_timetable":
        entries = []
        for row in rows:
            payload = row["payload"]
            day = str(payload.get("day") or "")
            slots = payload.get("slots") or []
            entries.append({**row, "text": payload.get("text") or row["text"], "day": day, "slots": [int(s) for s in slots if str(s).isdigit()], "time": payload.get("time") or "", "course_code": payload.get("code") or "", "faculty": payload.get("faculty") or ""})
        metadata = next((r["payload"] for r in rows if r.get("payload")), {})
        q = question.casefold()
        facility_question = bool(re.search(r"\b(where|located|location|room number|how many|desktop|computer count|capacity|number of systems|which academic semester|what semester|academic year|schedule belong)\b", q))
        if facility_question and not re.search(r"\b(course|subject|faculty|teacher|taught|scheduled|held|conducted|slot|occupied|schedule for (?:monday|tuesday|wednesday|thursday|friday))\b", q):
            if re.search(r"\b(where|located|location|room number)\b", q):
                detail = ", ".join(str(v) for v in (metadata.get("location"), metadata.get("room")) if v)
                answer = f"**{metadata.get('facility') or 'Lab'}:** {detail or 'Location not extracted from the uploaded document.'}."
                title = "Lab location"
            elif re.search(r"\b(how many|systems|computers|desktop|capacity)\b", q):
                count = metadata.get("capacity")
                answer = f"The uploaded schedule lists **{count} desktop PCs** in {metadata.get('facility') or 'the lab'} ({metadata.get('room') or 'room not identified'})." if count else "The uploaded schedule does not state the lab's computer capacity."
                title = "Lab capacity"
            else:
                term = " ".join(str(v) for v in (metadata.get("academic_term"), metadata.get("academic_year")) if v)
                answer = f"This lab schedule is for **{term}**." if term else "The academic term is not identified in the uploaded schedule."
                title = "Lab schedule period"
            return {"document_mode": kind, "title": title, "markdown_answer": f"### {title}\n\n{answer}", "records": []}

        if weekday:
            entries = [r for r in entries if r["day"].casefold() == weekday.casefold()]
        code_match = re.search(r"\b\d{2}[A-Z]{3,4}\d{3,4}[A-Z]?\b", question, re.I)
        if code_match:
            wanted_code = re.sub(r"[^A-Z0-9]", "", code_match.group(0).upper())
            entries = [r for r in entries if wanted_code in re.sub(r"[^A-Z0-9]", "", r["text"].upper())]
        else:
            stop = {"when", "what", "which", "where", "who", "does", "do", "is", "are", "the", "a", "an", "in", "on", "for", "of", "to", "my", "me", "have", "has", "with", "during", "held", "conducted", "scheduled", "schedule", "timetable", "lab", "laboratory", "it", "itlab", "lab1", "labi", "room", "slot", "slots", "section", "sem", "semester", "year", "btech", "bt", "and", "vs", "all", "week", "weekday", "weekdays", "morning", "afternoon", "day", "free", "vacant", "available", "availability", "occupied", "extra", "session", "sessions", "booked", "book", "test", "any", "there", "between", "after", "before", "computer", "systems", "desktop", "number", "many", "capacity", "academic", "period", "does", "handle", "handles", "teach", "teaches", "faculty", "faculties", "member", "members", "class", "classes", "dr", "mr", "ms", "prof", "col"}
            tokens = [t for t in re.findall(r"[a-z0-9]+", q) if len(t) > 1 and t not in stop] if not is_free_query else []
            # Remove slot/day/date fragments; these are applied as structured filters.
            tokens = [t for t in tokens if not t.isdigit() and t not in {d.casefold() for d in _DAY_NAMES}]
            tokens = ["mech" if token == "mechanical" else token for token in tokens]
            if tokens:
                scored = []
                for row in entries:
                    haystack = _normalized_course_text(row["text"])
                    haystack_tokens = set(haystack.split())
                    hits = sum(token in haystack_tokens for token in tokens)
                    if hits:
                        scored.append((hits, row))
                if scored:
                    best = max(score for score, _ in scored)
                    threshold = 1 if re.search(r"\b(who|faculty|faculties|teacher|teaches|handles|handled)\b", q) else max(1, math.ceil(len(tokens) * 0.45))
                    entries = [row for score, row in scored if score >= max(threshold, best if len(tokens) <= 2 else threshold)]
                else:
                    entries = []

        requested_sections = re.findall(r"\b(?:section|sec\.?|class)\s*[-:]?\s*([A-H])\b", question, re.I)
        if len(set(letter.upper() for letter in requested_sections)) == 1:
            letter = requested_sections[0].upper()
            section_patterns = (
                rf"\b{letter}\s+section\b", rf"\bsection\s*[-:]?\s*{letter}\b",
                rf"\b(?:sem|semester|yr|year|eee|elc|cce|das)\s*[-:]?\s*{letter}\b",
                rf"\b{letter}\s*\(\s*\d+\s*\)",
            )
            entries = [r for r in entries if any(re.search(pattern, r["text"], re.I) for pattern in section_patterns)]

        slot_set: set[int] = set()
        for match in re.finditer(r"\bslots?\s*(\d{1,2})(?:\s*(?:-|–|to)\s*(\d{1,2}))?", question, re.I):
            start_slot = int(match.group(1))
            end_slot = int(match.group(2) or start_slot)
            if 1 <= start_slot <= 12 and 1 <= end_slot <= 12:
                slot_set.update(range(min(start_slot, end_slot), max(start_slot, end_slot) + 1))
        time_points = re.findall(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", question, re.I)
        if not slot_set and len(time_points) >= 2 and re.search(r"\b(between|from)\b", q):
            def _minutes(point: tuple[str, str, str]) -> int:
                hour, minute, meridiem = int(point[0]), int(point[1] or 0), point[2].lower()
                return (hour % 12 + (12 if meridiem == "pm" else 0)) * 60 + minute
            query_start, query_end = map(_minutes, time_points[:2])
            slot_intervals = {
                1: (480, 530), 2: (530, 580), 3: (580, 630), 4: (645, 695),
                5: (695, 745), 6: (745, 795), 7: (795, 845), 8: (845, 895),
                9: (895, 945), 10: (945, 995), 11: (995, 1045), 12: (1045, 1095),
            }
            slot_set = {slot for slot, (start_minute, end_minute) in slot_intervals.items()
                        if max(query_start, start_minute) < min(query_end, end_minute)}
        if slot_set:
            entries = [r for r in entries if slot_set.intersection(r["slots"])]

        if is_free_query or re.search(r"\b(occupied|scheduled in slot|sessions scheduled)\b", q):
            days = [weekday] if weekday else ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
            default_availability_slots = (
                {11, 12} if re.search(r"\b(after\s+4[:.]35|after\s+4\s*35|after\s+4\s*pm)\b", q)
                else set(range(1, 7)) if re.search(r"\bmorning\b", q)
                else set(range(8, 13)) if re.search(r"\bafternoon\b", q)
                else set(range(1, 13))
            )
            checked_slots = sorted(slot_set or default_availability_slots)
            lunch_requested = 7 in checked_slots
            occupied_by_day = {day: {slot for row in entries if row["day"].casefold() == day.casefold() for slot in row["slots"]} for day in days}
            free_by_day = {day: [slot for slot in checked_slots if slot not in occupied_by_day[day]] for day in days}
            if re.search(r"\b(occupied|scheduled in slot|sessions scheduled)\b", q) and slot_set:
                occupied = [(day, slot) for day in days for slot in checked_slots if slot in occupied_by_day[day]]
                answer = ("Yes. Scheduled lab entries were found for " + ", ".join(f"{day} Slot {slot}" for day, slot in occupied) + ".") if occupied else f"No. No lab sessions are listed in Slot {', '.join(map(str, checked_slots))} on the queried weekdays."
            else:
                free_labels = [f"**{day}:** Slots {', '.join(map(str, slots))}" for day, slots in free_by_day.items() if slots]
                if not free_labels:
                    answer = "No unoccupied slots were found in the requested period."
                elif len(days) == 1:
                    day = days[0]
                    free_slots = free_by_day[day]
                    slot_label = ", ".join(map(str, free_slots))
                    answer = f"Yes. **{day} Slots {slot_label}** have no lab sessions listed in the uploaded timetable. Confirm room booking availability with the lab administrator."
                    if lunch_requested or 7 in free_slots:
                        answer += " Slot 7 (1:15–2:05 PM) is the daily lunch break; it has no lab session listed, but should not be treated as bookable lab time."
                else:
                    answer = "No lab classes are listed in these slots in the uploaded schedule:\n\n" + "\n\n".join(free_labels) + "\n\nThis indicates an unlisted period in this timetable; confirm room booking availability with the lab administrator."
                    if lunch_requested or any(7 in slots for slots in free_by_day.values()):
                        answer += " Slot 7 (1:15–2:05 PM) is the daily lunch break; it has no lab session listed, but should not be treated as bookable lab time."
            title = "Lab availability"
            return {"document_mode": kind, "title": title, "markdown_answer": f"### {title}\n\n{answer}", "records": entries[:80]}
        if not entries:
            return {"document_mode": kind, "title": "Lab timetable", "markdown_answer": "No matching lab timetable entries were found in the uploaded schedule.", "records": []}
        # Merge adjacent slot cells for the same class, so a 2–3 or 4–6 class
        # is presented once with its complete period.
        merged = []
        for row in entries:
            key = (row["day"], tuple(row["slots"]), row["text"])
            if merged and (merged[-1]["day"], tuple(merged[-1]["slots"]), merged[-1]["text"]) == key:
                continue
            merged.append(row)
        title = f"{weekday} Lab Schedule" if weekday else "Lab Timetable"
        answer = f"### {title}\n\n**{metadata.get('facility') or 'Lab'} · {metadata.get('room') or 'Room not identified'}**"
        if metadata.get("academic_term") or metadata.get("academic_year"):
            answer += f" · {metadata.get('academic_term') or ''} {metadata.get('academic_year') or ''}".strip()
        answer += "\n\n" + "\n".join(
            f"- **{r['day']} · Slot{'s' if len(r['slots']) > 1 else ''} {', '.join(map(str, r['slots']))}** ({r['time']}): {r['text']}"
            for r in merged[:80]
        )
        return {"document_mode": kind, "title": title, "markdown_answer": answer, "records": merged[:80]}

    # Exam timetable retrieval: first use explicit course/date/session constraints,
    # then include nearby lines to preserve table row/header context.
    requested_type = _exam_type(question)
    if requested_type:
        per_document: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            per_document.setdefault(row["document_id"], []).append(row)
        matching_docs = {
            document_id
            for document_id, doc_rows in per_document.items()
            if _document_exam_type(doc_rows[0]["filename"], doc_rows) == requested_type
        }
        if not matching_docs:
            existing_types = sorted({
                label
                for doc_rows in per_document.values()
                if (label := _document_exam_type(doc_rows[0]["filename"], doc_rows))
            })
            available = ", ".join(label.replace("_", "-") for label in existing_types) or "another exam period"
            expected = "Mid-Term" if requested_type == "mid_term" else "End-Semester"
            return {
                "document_mode": kind,
                "markdown_answer": f"The indexed {department} Semester {semester} document is {available}; it does not contain a matching {expected} schedule.",
            }
        rows = [row for row in rows if row["document_id"] in matching_docs]
    gap_question = bool(re.search(r"\b(gap|days between|preparation days)\b", question, re.I))
    if gap_question and len(target_dates) >= 2:
        start_date, end_date = min(target_dates), max(target_dates)
        gap_days = max(0, (end_date - start_date).days - 1)
        days = [
            (start_date.fromordinal(start_date.toordinal() + offset)).strftime("%A, %B %-d, %Y")
            for offset in range(1, (end_date - start_date).days)
        ]
        return {
            "document_mode": kind,
            "title": "Exam preparation gap",
            "markdown_answer": f"There are **{gap_days} calendar day(s)** between {start_date:%B %-d} and {end_date:%B %-d}, excluding the exam dates: {', '.join(days) if days else 'none'}.",
            "records": [],
        }
    if gap_question:
        between = re.search(r"\bbetween\s+(.+?)\s+and\s+(.+?)(?:\?|$)", question, re.I)
        if between:
            mentions = [re.sub(r"\([^)]*\)", " ", part).strip(" .,?!") for part in between.groups()]
            course_dates: list[date] = []
            course_rows: list[dict[str, Any]] = []
            all_codes = re.findall(r"\b\d{2}[A-Z]{3,4}\d{3,4}[A-Z]?\b", question, re.I)
            for mention_index, mention in enumerate(mentions):
                mentioned_code = all_codes[mention_index] if len(all_codes) >= 2 else _course_code(mention)
                found = next((
                    row for row in rows
                    if _matches_course(row, mention, mentioned_code)
                    and row.get("effective_date") is not None
                ), None)
                if found:
                    course_rows.append(found)
                    course_dates.append(found["effective_date"])
            if len(course_dates) >= 2:
                start_date, end_date = min(course_dates), max(course_dates)
                gap_days = max(0, (end_date - start_date).days - 1)
                return {
                    "document_mode": kind,
                    "title": "Exam preparation gap",
                    "markdown_answer": f"There are **{gap_days} calendar day(s)** between the two exams ({start_date:%B %-d} and {end_date:%B %-d}), excluding the exam dates.",
                    "records": course_rows,
                }
    indexes: list[int] = []
    course_terms = _tokens(question)
    if course_codes or course_terms:
        for i, row in enumerate(rows):
            if any(_matches_course(row, question, value) for value in course_codes) if course_codes else _matches_course(row, question, None):
                indexes.append(i)
    else:
        # The student is already scoped to the administrator-selected
        # department/semester. A broad "show the schedule" request means all
        # rows in that schedule, with course fragments filtered below.
        indexes = list(range(len(rows)))
    date_range = _is_date_range_query(question, target_dates)
    year_is_explicit = bool(re.search(r"\b20\d{2}\b", question))
    month_year = _query_month_year(question)
    if target_dates:
        date_indexes = [
            i for i, row in enumerate(rows)
            if _row_has_requested_date(
                rows, i, target_dates, date_range, year_is_explicit=year_is_explicit
            )
        ]
        indexes = sorted(set(indexes) & set(date_indexes))
    elif month_year:
        month, year = month_year
        indexes = [
            i for i in indexes
            if rows[i].get("effective_date")
            and rows[i]["effective_date"].month == month
            and rows[i]["effective_date"].year == year
        ]
    if weekday:
        indexes = [
            i for i in indexes
            if str(rows[i].get("effective_weekday") or "").casefold() == weekday.casefold()
        ]
    if session_filter:
        indexes = [i for i in indexes if session_filter in rows[i].get("effective_sessions", set())]
        if strict_session:
            indexes = [i for i in indexes if rows[i].get("effective_sessions", set()) == {session_filter}]
    if not indexes:
        # The parser can flatten multi-column PDF tables into lines. Use hybrid
        # retrieval as a fallback, with strict admin-selected document scope.
        vector = await embed_text(question)
        hits = await hybrid_search_rrf(
            db, query_text=question, query_embedding=vector, k=8,
            department=departments[0] if len(departments) == 1 and departments[0] != "ALL" else None,
            semester=query_semester, doc_type=kind,
        )
        if hits:
            records = []
            for hit in hits:
                raw_text = hit.get("content", "")
                filtered = _filter_course_segments(raw_text, departments[0] if departments[0] != "ALL" else "ALL", question, code)
                if filtered:
                    records.append({"text": filtered, "page": None, "filename": "Indexed exam schedule"})
            if records:
                return {"document_mode": kind, "markdown_answer": "I couldn't extract a precise schedule row; these matching passages came from the scoped uploaded exam schedule:\n\n" + "\n\n".join(f"- {r['text']}" for r in records[:5]), "records": records}
        return {"document_mode": kind, "markdown_answer": f"No matching exam schedule entry was found for **{department} Semester {semester}**. Check the course name/code or verify the uploaded PDF extraction."}

    selected = [rows[i] for i in indexes]
    include_shared = not code and not course_terms
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in selected:
        stored_department = row.get("table_department") or row.get("admin_department") or department
        if stored_department not in _KNOWN_DEPARTMENTS and stored_department not in {"ALL", "ALL DEPARTMENTS"}:
            stored_department = row.get("admin_department") or department
        if is_global_department_query:
            row_departments = [stored_department]
        elif stored_department in {"ALL", "ALL DEPARTMENTS"}:
            row_departments = departments
        elif stored_department in departments or any(stored_department in _DEPARTMENT_ALIASES.get(d, ()) for d in departments):
            row_departments = [stored_department]
        else:
            continue
        for row_department in row_departments:
            # Parser-created department cells have already been cropped to the
            # admin-selected column. Keep cross-listed electives in that cell,
            # even when their course-code prefix belongs to another department.
            if row.get("has_scoped_table_department"):
                filtered_text = row["text"].strip()
            else:
                filtered_text = _filter_course_segments(
                    row["text"], row_department, question, course_codes or code, include_shared=include_shared
                ).strip()
            if not filtered_text:
                continue
            course_text = filtered_text.split(" — ", 1)[-1].strip()
            is_lab_exam_label = bool(re.search(r"\blab exam\b", question, re.I) and course_text.strip().casefold() == "lab")
            if not _COURSE_CODE_RE.search(course_text) and not is_lab_exam_label and not (course_terms and _matches_course(row, question, code)):
                continue
            effective_date = row.get("effective_date")
            date_label = effective_date.isoformat() if effective_date else "Date not extracted"
            sessions = row.get("effective_sessions") or set()
            session_label = "/".join(sorted(sessions)) if sessions else "Session not extracted"
            key = (f"{row['document_id']}:{row_department}", date_label, session_label)
            group = grouped.setdefault(key, {
                "date": date_label,
                "session": session_label,
                "department": row_department,
                "courses": [],
                "page": row["page"],
                "filename": row["filename"],
                "document_id": row["document_id"],
            })
            for course in re.split(r"\s*/\s*", course_text):
                normalized = re.sub(r"\s+", "", course.casefold())
                if course and normalized not in {re.sub(r"\s+", "", old.casefold()) for old in group["courses"]}:
                    group["courses"].append(course)
    deduped = list(grouped.values())
    for row in deduped:
        row["text"] = " / ".join(row.pop("courses"))
    title = "Exam timetable results"
    if re.search(r"\b(exam window|start and end|start to finish|concludes?)\b", question, re.I) and deduped:
        schedule_dates = sorted({date.fromisoformat(item["date"]) for item in deduped if item.get("date") != "Date not extracted"})
        if schedule_dates:
            title = f"Examination window: {schedule_dates[0]:%B %-d}–{schedule_dates[-1]:%B %-d, %Y}"
    if code:
        title = f"Exam timetable for {code}"
    elif target_dates:
        if not year_is_explicit and not date_range:
            resolved_dates = sorted({date.fromisoformat(item["date"]) for item in deduped if item.get("date") != "Date not extracted"})
            if resolved_dates:
                title = f"Exam timetable for {', '.join(value.strftime('%B %d, %Y') for value in resolved_dates)}"
            else:
                title = f"Exam timetable for {', '.join(d.strftime('%B %d') for d in target_dates)}"
        elif date_range:
            title = f"Exam timetable from {min(target_dates).strftime('%B %d, %Y')} through {max(target_dates).strftime('%B %d, %Y')}"
        else:
            title = f"Exam timetable for {', '.join(d.strftime('%B %d, %Y') for d in target_dates)}"
    elif month_year:
        month, year = month_year
        title = f"Exam timetable for {date(year, month, 1):%B %Y}"
    answer = f"### {title}\n\n| Date | Session | Department | Scheduled courses | Source |\n|---|---|---|---|---|\n" + "\n".join(
        f"| {row['date']} | {row['session']} | {row['department']} | {row['text'].replace('|', '\\|')} | {row['filename']}, p. {row['page']} |"
        for row in deduped[:60]
    )
    if re.search(r"\blab\s+exam\b", question, re.I) and deduped:
        lab_departments = ", ".join(dict.fromkeys(row["department"] for row in deduped))
        answer = (
            f"A lab examination entry is listed for **{lab_departments}** on the requested date. "
            "The scanned schedule did not yield a readable course code or full lab title for that entry.\n\n"
            + answer
        )
    return {"document_mode": kind, "title": title, "markdown_answer": answer, "records": deduped[:60]}
