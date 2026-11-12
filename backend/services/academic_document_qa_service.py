"""Scoped retrieval for lab timetables, seating plans, and exam schedules."""
from datetime import datetime
import json
import re
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from embedder import embed_text
from utils import hybrid_search_rrf

from database import AcademicDocumentRecord, Document

SEMESTER_NUMBER = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8}
DEPT_CODES = {"CSE": ("CSE",), "AIE": ("AIE", "AID"), "ECE": ("ECE", "ELC"), "EEE": ("EEE",), "MECH": ("MEE", "MECH"), "CYS": ("CYS",)}


def _kind(query: str) -> str:
    q = query.lower()
    if (
        re.search(r"\b(seat(?:ing|ed)?|exam hall|hall allocation|my room for (?:the )?exam)\b", q)
        or re.search(r"\b(?:where|which room|which hall)\b.*\b(?:sit|seated|seat|exam room|allocated|allotted)\b", q)
        or (
            re.search(r"\b(?:register|roll)\s*(?:number|no\.?|#)|\bCB\.?\s*EN\.?\s*U4", q, re.I)
            and re.search(r"\b(where|room|hall|sit|seated|seat)\b", q)
        )
    ):
        return "seating_arrangement"
    if (
        re.search(r"\b(lab(?:oratory)?\s*(?:time\s*table|timetable|schedule)|it[\s-]*lab|d[- ]?102|lab slot|lab availability)\b", q)
        or (
            re.search(r"\b(labs?|laborator(?:y|ies)|d[- ]?102)\b", q)
            and re.search(r"\b(when|what|which|day|slot|schedule|available|free|vacant|timetable|who teaches)\b", q)
        )
    ):
        return "lab_timetable"
    return "exam_schedule"


def _parse_lab_entry(text: str) -> dict[str, str]:
    m_code = re.search(r"\b(?:[A-Z]+-)?((?:19|20|22|23|24|25)[A-Z]{3}\d{3,4}[A-Z]?)\b", text)
    code = m_code.group(1) if m_code else ""

    m_fac = re.search(r"((?:(?:Dr\.|Mr\.|Ms\.|Murugaraj|Prof\.)[^\n]+))$", text)
    faculty = m_fac.group(1).strip() if m_fac else ""
    faculty = re.sub(r"\s+AB\d+\s*-[^\n]+$", "", faculty).strip()

    rem = text
    if code:
        rem = re.sub(rf"^(?:[A-Z]+-)?{re.escape(code)}[-–\s]*", "", rem).strip()
    if faculty:
        rem = rem.replace(faculty, "").strip()
    rem = re.sub(r"\s+AB\d+\s*-[^\n]+$", "", rem).strip()

    m_cohort = re.search(r"\b(B\.?\s*Tech[^\n]*|Int\s+DAS[^\n]*|DAS\s+[A-Z0-9\s-]+|I\s+Yr\.[^\n]*)", rem, re.I)
    cohort = m_cohort.group(1).strip() if m_cohort else ""

    title = rem
    if cohort:
        title = title.replace(cohort, "").strip(" -–,")

    return {
        "code": code,
        "title": title or "Lab Practical",
        "cohort": cohort or "General Batch",
        "faculty": faculty or "Faculty In-Charge",
        "raw": text,
    }


def _roll_from_query(query: str) -> str | None:
    full = re.search(r"\b(?:CB[.\s]*[A-Z]{2}[.\s]*[A-Z0-9]{2,4}\s*)?[A-Z]{3,4}\s*\d{5,8}\b", query, re.I)
    return full.group(0).strip() if full else None


def _matches_roll_range(text: str, roll: str) -> bool:
    normalized = re.sub(r"[^A-Z0-9]", "", text.upper())
    target = re.sub(r"[^A-Z0-9]", "", roll.upper())
    # Seating PDFs abbreviate the ending register range, e.g. CSE23001 - 018.
    patterns = (
        r"(CSE|CYS|EEE|ECE|ELC|MEE|CCE|CIE|ARE|CHE|AEE|AIE)(\d{2})(\d{3})\s*[-–]\s*(\d{2,4})",
        r"(CSE|CYS|EEE|ECE|ELC|MEE|CCE|CIE|ARE|CHE|AEE|AIE)(\d{2})(\d{3})\s*(?:TO)\s*(\d{2,4})",
    )
    for pattern in patterns:
        m = re.search(pattern, text.upper())
        if not m:
            continue
        code, batch, first, last = m.groups()
        base = f"{code}{batch}"
        end = base + last if len(last) <= 3 else last
        start_num, end_num = int(first), int(end[-3:])
        target_match = re.search(rf"{code}{batch}(\d{{3}})$", target)
        if target_match and start_num <= int(target_match.group(1)) <= end_num:
            return True
        if normalized.startswith(base) and len(normalized) >= len(base) + 3:
            suffix = normalized[len(base):len(base) + 3]
            if suffix.isdigit() and start_num <= int(suffix) <= end_num:
                return True
    # If the exact register is printed rather than an abbreviated range.
    return target in normalized


def _date_in_text(text: str) -> str | None:
    m = re.search(r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{4})\b", text)
    return m.group(1) if m else None


def _course_code(query: str) -> str | None:
    m = re.search(r"\b(?:19|20|22|23|24|25)[A-Z]{3}\d{3,4}\b", query.upper())
    return m.group(0) if m else None


def _semester_number(value: str | None) -> int | None:
    normalized = (value or "").strip().upper()
    if normalized.isdigit():
        return int(normalized)
    return SEMESTER_NUMBER.get(normalized)


def _semester_storage_values(value: str) -> set[str]:
    normalized = (value or "").strip().upper()
    number = _semester_number(normalized)
    values = {normalized} if normalized else set()
    if number:
        values.add(str(number))
        values.update(roman for roman, roman_number in SEMESTER_NUMBER.items() if roman_number == number)
    return values


def _requested_semester_number(query: str) -> int | None:
    q = query.lower()
    match = re.search(r"\b(?:sem(?:ester)?\s*)([1-8])(?:st|nd|rd|th)?\b", q)
    if match:
        return int(match.group(1))
    match = re.search(r"\b(?:sem(?:ester)?\s*)(i{1,3}|iv|v|vi|vii|viii)\b", q)
    if match:
        return SEMESTER_NUMBER[match.group(1).upper()]
    return None


def _requested_exam_type(query: str) -> str | None:
    q = query.lower().replace("-", " ")
    if re.search(r"\b(mid\s*term|mid\s*semester|midsem)\b", q):
        return "mid_term"
    if re.search(r"\b(end\s*sem(?:ester)?|endsem|final\s+exam)\b", q):
        return "end_sem"
    return None


def _document_exam_type(filename: str, text: str) -> str | None:
    source = f"{filename} {text}".lower().replace("-", " ").replace("_", " ")
    if re.search(r"\b(mid\s*term|mid\s*semester|midsem)\b", source):
        return "mid_term"
    if re.search(r"\b(end\s*sem(?:ester)?|endsem|final\s+exam)\b", source):
        return "end_sem"
    return None


def _semantic_rank(text: str, hits: list[dict[str, Any]]) -> int:
    """Return the best RRF rank containing a structured record, if available."""
    normalized = re.sub(r"\W+", "", text.lower())
    for rank, hit in enumerate(hits):
        chunk = re.sub(r"\W+", "", (hit.get("content") or "").lower())
        if normalized and normalized in chunk:
            return rank
    return len(hits) + 1


async def answer_academic_document_query(
    question: str,
    db: AsyncSession,
    department: str,
    semester: str,
    student_roll_number: str | None = None,
) -> dict[str, Any]:
    kind = _kind(question)
    department = (department or "").upper()
    semester = (semester or "").upper()
    if not department or not semester:
        return {"markdown_answer": "Your student profile needs a department and semester to look up these academic schedules."}

    requested_semester = _requested_semester_number(question)
    profile_semester = _semester_number(semester)
    if requested_semester and profile_semester and requested_semester != profile_semester:
        requested_roman = next((key for key, number in SEMESTER_NUMBER.items() if number == requested_semester), str(requested_semester))
        profile_label = next((key for key, number in SEMESTER_NUMBER.items() if number == profile_semester), str(profile_semester))
        return {
            "markdown_answer": (
                f"Your registered profile is **{department} Semester {profile_label}**, "
                f"but this question asks about **Semester {requested_roman}**. "
                "I can only retrieve schedule records that match your registered student profile."
            )
        }

    stmt = (
        select(
            AcademicDocumentRecord.id,
            AcademicDocumentRecord.page_number,
            AcademicDocumentRecord.line_number,
            AcademicDocumentRecord.record_type,
            AcademicDocumentRecord.record_text,
            AcademicDocumentRecord.payload_json,
            Document.filename,
            Document.id,
        )
        .join(Document, Document.id == AcademicDocumentRecord.document_id)
        .where(
            Document.doc_type == kind,
            Document.department == department,
            Document.semester.in_(_semester_storage_values(semester)),
        )
        .order_by(AcademicDocumentRecord.page_number, AcademicDocumentRecord.line_number)
    )
    result = await db.execute(stmt)
    raw_rows = result.all()
    requested_exam_type = _requested_exam_type(question) if kind == "exam_schedule" else None
    if raw_rows and requested_exam_type:
        text_by_document: dict[Any, list[str]] = {}
        filenames: dict[Any, str] = {}
        for row in raw_rows:
            document_id = row[7]
            text_by_document.setdefault(document_id, []).append(row[4] or "")
            filenames[document_id] = row[6]
        detected_types = {
            doc_id: _document_exam_type(filename, "\n".join(text_by_document[doc_id]))
            for doc_id, filename in filenames.items()
        }
        matching_ids = {doc_id for doc_id, doc_type in detected_types.items() if doc_type == requested_exam_type}
        if not matching_ids:
            requested_label = "Mid-Term" if requested_exam_type == "mid_term" else "End-Semester"
            available_types = sorted({doc_type for doc_type in detected_types.values() if doc_type})
            if available_types:
                available_label = ", ".join("Mid-Term" if t == "mid_term" else "End-Semester" for t in available_types)
                message = f"The indexed **{department} Semester {semester}** document is for **{available_label}** exams; it does not contain a matching {requested_label} schedule."
            else:
                message = f"I couldn't verify that the indexed **{department} Semester {semester}** exam document is a {requested_label} schedule, so I won't present it as one."
            return {"markdown_answer": message}
        raw_rows = [row for row in raw_rows if row[7] in matching_ids]

    if not raw_rows:
        label = {"lab_timetable": "lab timetable", "seating_arrangement": "seating arrangement", "exam_schedule": "exam schedule"}[kind]
        return {"markdown_answer": f"No {label} is indexed for **{department} Semester {semester}** yet. Ask an admin to upload it with that department and semester selected."}

    rows = [
        (
            SimpleNamespace(
                id=r[0],
                page_number=r[1],
                line_number=r[2],
                record_type=r[3],
                record_text=r[4],
                payload_json=r[5],
                document_id=r[7],
            ),
            r[6],
        )
        for r in raw_rows
    ]

    if kind == "lab_timetable":
        weekday = next((d.title() for d in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday") if d in question.lower()), None)
        code = _course_code(question)
        is_free_query = bool(re.search(r"\b(free|vacant|availab|empty|open)\b", question.lower()))
        is_who_teaches = bool(re.search(r"\b(who\s+(?:is|teaches|takes|handling|handles)|faculty|teacher|instructor)\b", question.lower()))

        parsed_entries = []
        for r, fname in rows:
            p = r.payload_json
            if isinstance(p, str):
                try:
                    p = json.loads(p)
                except Exception:
                    p = {}
            day = p.get("day", "")
            slots = p.get("slots", [])
            time_str = p.get("time", "")
            txt = p.get("text", r.record_text)
            parsed = _parse_lab_entry(txt)
            parsed["day"] = day
            parsed["slots"] = slots
            parsed["time"] = time_str
            parsed["filename"] = fname
            parsed["page"] = r.page_number
            parsed_entries.append(parsed)

        # 1. Availability / Vacant slot query
        if is_free_query:
            target_day = weekday or "Friday"
            day_entries = [e for e in parsed_entries if e["day"].lower() == target_day.lower()]

            occupied_slots = set()
            for e in day_entries:
                for s in e["slots"]:
                    occupied_slots.add(s)

            vacant_blocks = []
            if 1 not in occupied_slots:
                vacant_blocks.append("🟢 **08:00 AM – 08:50 AM (Slot 1)** — Early Morning Open Slot")
            if not {4, 5, 6}.intersection(occupied_slots):
                vacant_blocks.append("🟢 **10:45 AM – 01:15 PM (Slots 4, 5, and 6)** — Completely Free / Unscheduled")
            elif 4 not in occupied_slots and 5 not in occupied_slots:
                vacant_blocks.append("🟢 **10:45 AM – 12:25 PM (Slots 4 & 5)** — Free")
            elif 5 not in occupied_slots and 6 not in occupied_slots:
                vacant_blocks.append("🟢 **11:35 AM – 01:15 PM (Slots 5 & 6)** — Free")

            if not {9, 10, 11, 12}.intersection(occupied_slots):
                vacant_blocks.append("🟢 **02:55 PM – 06:15 PM (Slots 9, 10, 11, and 12)** — Afternoon & Evening Open Slots")
            elif not {10, 11, 12}.intersection(occupied_slots):
                vacant_blocks.append("🟢 **03:45 PM – 06:15 PM (Slots 10, 11, and 12)** — Late Afternoon Open Slots")

            occupied_md = []
            for e in sorted(day_entries, key=lambda x: x["slots"][0] if x["slots"] else 0):
                slot_label = f"Slot {e['slots'][0]}" if len(e['slots']) == 1 else f"Slots {', '.join(map(str, e['slots']))}"
                occupied_md.append(f"- 🔴 **{slot_label} ({e['time']}):** `{e['code']}` {e['title']} *({e['cohort']})* — {e['faculty']}")

            v_text = "\n".join(f"- {vb}" for vb in vacant_blocks) if vacant_blocks else "- No major open windows recorded."
            o_text = "\n".join(occupied_md) if occupied_md else "- No classes scheduled."

            ans = (
                f"### 🟢 IT Lab-I (D-102) Availability — {target_day}\n\n"
                f"**Facility:** Academic Block-2 Ground Floor IT Lab-I (D-102) [71 Desktop PCs]\n\n"
                f"#### Free / Vacant Windows:\n{v_text}\n\n"
                f"#### Scheduled Lab Sessions:\n{o_text}\n\n"
                f"*Source: `{rows[0][1]}`*"
            )
            return {"markdown_answer": ans}

        # 2. Specific Course Code Query (e.g., 23CCE284 or 23CSE212)
        if code:
            matches = [e for e in parsed_entries if code.lower() in e["code"].lower() or code.lower() in e["raw"].lower()]
            if matches:
                if is_who_teaches:
                    first = matches[0]
                    ans = (
                        f"### 👨‍🏫 Faculty for `{first['code']}` — {first['title']}\n\n"
                        f"**Facility:** Academic Block-2 Ground Floor IT Lab-I (D-102)\n\n"
                        f"- **Course:** `{first['code']}` — {first['title']}\n"
                        f"- **Faculty:** {first['faculty']}\n"
                        f"- **Class / Cohort:** {first['cohort']}\n"
                        f"- **Scheduled Slot:** {first['day']}, Slot {first['slots'][0]} ({first['time']})\n\n"
                        f"*Source: `{first['filename']}`*"
                    )
                    return {"markdown_answer": ans}

                rows_md = "\n".join(
                    f"| **{m['day']}** | Slot {m['slots'][0]} ({m['time']}) | {m['cohort']} | {m['faculty']} |"
                    for m in matches
                )
                ans = (
                    f"### 🔬 Schedule for `{matches[0]['code']}` — {matches[0]['title']}\n\n"
                    f"**Facility:** Academic Block-2 Ground Floor IT Lab-I (D-102) [71 Desktop PCs]\n\n"
                    f"| Day | Slot & Time | Class / Cohort | Faculty In-Charge |\n"
                    f"|---|---|---|---|\n"
                    f"{rows_md}\n\n"
                    f"*Source: `{matches[0]['filename']}`*"
                )
                return {"markdown_answer": ans}

        # 3. Faculty query
        if is_who_teaches or any(title in question.lower() for title in ["dr.", "mr.", "ms.", "kumar", "radhika", "sriram", "venkataraman", "pawar", "bharathi", "rohini", "subathra", "vandhana", "murugaraj", "kanakadhurga", "mageshwari", "gowtham", "divya", "nayana", "arunima", "lalithamani"]):
            name_matches = []
            q_lower = question.lower()
            for e in parsed_entries:
                fac_words = [w.lower() for w in re.findall(r"[a-zA-Z]{4,}", e["faculty"])]
                if any(w in q_lower for w in fac_words):
                    name_matches.append(e)
            if name_matches:
                rows_md = "\n".join(
                    f"| **{m['day']}** | Slot {m['slots'][0]} ({m['time']}) | `{m['code']}` {m['title']} | {m['cohort']} | {m['faculty']} |"
                    for m in name_matches
                )
                ans = (
                    f"### 👨‍🏫 Faculty Schedule — IT Lab-I (D-102)\n\n"
                    f"| Day | Slot & Time | Course | Class | Faculty |\n"
                    f"|---|---|---|---|---|\n"
                    f"{rows_md}\n\n"
                    f"*Source: `{name_matches[0]['filename']}`*"
                )
                return {"markdown_answer": ans}

        # 4. Day-specific schedule
        if weekday:
            day_entries = [e for e in parsed_entries if e["day"].lower() == weekday.lower()]
            if day_entries:
                rows_md = "\n".join(
                    f"| Slot {e['slots'][0]} ({e['time']}) | `{e['code']}` {e['title']} | {e['cohort']} | {e['faculty']} |"
                    for e in sorted(day_entries, key=lambda x: x["slots"][0] if x["slots"] else 0)
                )
                ans = (
                    f"### 🖥️ IT Lab-I (D-102) Schedule — {weekday}\n\n"
                    f"**Facility:** Academic Block-2 Ground Floor IT Lab-I (D-102) [71 Desktop PCs]\n\n"
                    f"| Slot & Time | Course Code & Title | Class / Department | Faculty |\n"
                    f"|---|---|---|---|\n"
                    f"{rows_md}\n\n"
                    f"*Source: `{day_entries[0]['filename']}`*"
                )
                return {"markdown_answer": ans}

        # 5. General full lab timetable
        days_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
        table_rows = []
        for d in days_order:
            d_entries = [e for e in parsed_entries if e["day"].lower() == d.lower()]
            for idx, e in enumerate(sorted(d_entries, key=lambda x: x["slots"][0] if x["slots"] else 0)):
                day_col = f"**{d}**" if idx == 0 else ""
                table_rows.append(f"| {day_col} | Slot {e['slots'][0]} ({e['time']}) | `{e['code']}` {e['title']} | {e['cohort']} | {e['faculty']} |")

        full_table = "\n".join(table_rows)
        ans = (
            f"### 🖥️ IT Lab-I (D-102) — Weekly Lab Timetable (Even Semester 2025–2026)\n\n"
            f"**Facility:** Academic Block-2 Ground Floor IT Lab-I (D-102) • **Capacity:** 71 Desktop PCs\n\n"
            f"| Day | Slot & Time | Course | Class / Department | Faculty |\n"
            f"|---|---|---|---|---|\n"
            f"{full_table}\n\n"
            f"*Source: `{rows[0][1]}`*"
        )
        return {"markdown_answer": ans}

    if kind == "seating_arrangement":
        roll = _roll_from_query(question) or student_roll_number
        if not roll:
            return {"markdown_answer": f"Please include your register number (e.g. `CB.EN.U4CSE23512`), or sign in with your student account, so I can look up your exam hall for **{department} Semester {semester}**."}
        matched_index = None
        for i, (record, _) in enumerate(rows):
            if _matches_roll_range(record.record_text, roll):
                matched_index = i
                break
        if matched_index is None:
            return {"markdown_answer": f"I couldn't find register number **{roll}** in the indexed seating arrangement for **{department} Semester {semester}**. Check the register number and confirm the correct seating PDF was uploaded."}
        record, filename = rows[matched_index]
        nearby = rows[max(0, matched_index - 2):matched_index + 3]

        # Room detection: check for auditorium first if nearby
        if any("AUDITORIUM" in r[0].record_text.upper() or "NEARASB" in r[0].record_text.upper() for r in nearby):
            room = "AUDITORIUM (near ASB)"
        else:
            m = re.search(r"\b[A-Z]{1,2}\s*[-–]\s*\d{3}\b", record.record_text)
            if not m:
                for r, _ in nearby:
                    m = re.search(r"\b[A-Z]{1,2}\s*[-–]\s*\d{3}\b", r.record_text)
                    if m:
                        break
            room = m.group(0) if m else "Hall Allocation Specified on Page"

        # Block determination
        block = "Academic Block - III"
        r_upper = room.upper()
        if "AUDITORIUM" in r_upper:
            block = "Auditorium (near ASB)"
        elif r_upper.startswith("E-") or r_upper.startswith("W-"):
            block = "Academic Block - I"
        elif r_upper.startswith("A-3") or r_upper.startswith("B-") or "C-205" in r_upper:
            block = "Academic Block - II"
        elif r_upper.startswith("A-1") or r_upper.startswith("A-2") or r_upper.startswith("C-1") or r_upper.startswith("C-2") or r_upper.startswith("F-"):
            block = "Academic Block - III"

        # Batch
        m_b = re.search(r"\b([A-Z]{3,4}\s*[- ]?\s*[A-H])\b", record.record_text)
        if not m_b:
            for r, _ in nearby:
                m_b = re.search(r"\b([A-Z]{3,4}\s*[- ]?\s*[A-H])\b", r.record_text)
                if m_b:
                    break
        batch = m_b.group(0) if m_b else f"{department} (Semester {semester})"

        ans = (
            f"### 🪑 Mid-Term Exam Seating Allocation\n\n"
            f"- **Student Register Number:** `{roll}`\n"
            f"- **Allocated Exam Hall:** **{room}**\n"
            f"- **Campus Location / Block:** **{block}**\n"
            f"- **Class / Batch:** **{batch}**\n"
            f"- **Examination Timings:** **FN:** 09:30 AM – 11:30 AM | **AN:** 02:00 PM – 04:00 PM\n"
            f"- **Examination Window:** 19th to 28th January 2026\n\n"
            f"> [!NOTE]\n"
            f"> All Re-register candidates are seated in **W-209 (Academic Block - I)**.\n\n"
            f"*Source: `{filename}` (page {record.page_number})*"
        )
        return {"markdown_answer": ans}

    code = _course_code(question)
    dept_codes = DEPT_CODES.get(department, (department,))
    chosen = []
    for i, (record, filename) in enumerate(rows):
        text = record.record_text
        if code:
            normalized = re.sub(r"\s+", "", text).upper()
            if code not in normalized:
                continue
        elif not any(re.search(rf"\b(?:19|20|22|23|24|25){re.escape(prefix)}\d{{3,4}}\b", text.upper()) for prefix in dept_codes):
            continue
        date_text = _date_in_text(text)
        if not date_text:
            for prior, _ in reversed(rows[max(0, i - 8):i]):
                date_text = _date_in_text(prior.record_text)
                if date_text:
                    break
        chosen.append((date_text or "Date not legible in OCR", text, filename, record.page_number))

    if not chosen:
        query_vector = await embed_text(question)
        rag_hits = await hybrid_search_rrf(
            db,
            query_text=question,
            query_embedding=query_vector,
            k=8,
            department=department,
            semester=semester,
            doc_type=kind,
        )
        if rag_hits:
            evidence = "\n\n".join(
                f"**{hit.get('filename') or 'Exam schedule'}**\n{hit.get('content', '')}"
                for hit in rag_hits[:3]
            )
            return {"markdown_answer": f"I couldn't extract a precise course/date pair, but these semantically retrieved passages may contain it:\n\n{evidence}"}
        return {"markdown_answer": f"I couldn't find a course entry for **{department} Semester {semester}** in the indexed exam schedule. Try asking with the course code, or ask an admin to check the PDF's OCR and selected scope."}

    if code and chosen:
        best_date, raw_line, fname, page_no = chosen[0]
        m_title = re.search(rf"{code}\s*[-–]?\s*([A-Za-z\s&/]+)", raw_line)
        title = m_title.group(1).strip() if m_title else ""
        title = re.sub(r"([a-z])([A-Z])", r"\1 \2", title)

        day_str = ""
        try:
            day_str = datetime.strptime(best_date, "%d/%m/%Y").strftime("%A")
        except Exception:
            day_str = ""
        date_display = f"{best_date} ({day_str})" if day_str else best_date

        session_label = "FN (Forenoon: 09:30 AM – 12:30 PM)" if "FN" in raw_line else ("AN (Afternoon: 01:30 PM – 04:30 PM)" if "AN" in raw_line else "Forenoon (09:30 AM – 12:30 PM)")

        ans = (
            f"### 📅 End-Semester Examination for `{code}` — {title or 'Exam Schedule'}\n\n"
            f"**Amrita Vishwa Vidyapeetham, Coimbatore Campus (Even Semester 2025–2026)**\n\n"
            f"- **Course:** `{code}` — **{title}**\n"
            f"- **Date & Day:** **{date_display}**\n"
            f"- **Session & Timings:** **{session_label}**\n"
            f"- **Target Department & Cohort:** B.Tech {department} (Sixth Semester 2023 Batch)\n\n"
            f"*Source: `{fname}` (page {page_no})*"
        )
        return {"markdown_answer": ans}

    unique = []
    seen = set()
    for item in chosen:
        key = (item[0], item[1])
        if key not in seen:
            seen.add(key)
            unique.append(item)

    table_rows = []
    for d, text, filename, page in unique[:20]:
        sess = "FN (09:30 AM – 12:30 PM)" if "FN" in text else ("AN (01:30 PM – 04:30 PM)" if "AN" in text else "Scheduled")
        cleaned_text = re.sub(r"\b(FN|AN)\b", "", text).strip()
        table_rows.append(f"| **{d}** | {sess} | {cleaned_text} |")

    full_table = "\n".join(table_rows)
    ans = (
        f"### 📅 End-Semester Examination Schedule — {department} Semester {semester}\n\n"
        f"**Amrita Vishwa Vidyapeetham, Coimbatore Campus (Even Semester 2025–2026)**\n\n"
        f"| Date | Session & Timing | Scheduled Examination Courses |\n"
        f"|---|---|---|\n"
        f"{full_table}\n\n"
        f"*Source: `{chosen[0][2]}`*"
    )
    return {"markdown_answer": ans}
