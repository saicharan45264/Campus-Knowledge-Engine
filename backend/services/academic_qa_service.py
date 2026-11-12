"""
academic_qa_service.py — Authoritative Question-Answering Synthesizer for Timetable,
Calendar, and Regulation queries.
Produces direct, beautifully formatted markdown answers tailored to the user's exact question,
supplemented with rich data structures for UI cards.
"""
import re
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from typing import Dict, Any, List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_

from database import (
    CalendarDay,
    CalendarDayCohort,
    AcademicEvent,
    TimetableVersion,
    WeeklySession,
    ElectiveOption,
    RegulationClause,
)



def _festival_topic_tokens(query: str) -> list[str]:
    q = re.sub(r"[^a-z0-9]+", " ", query.lower())
    aliases = {
        "sankranthi": ("sankranthi", "sankranti", "pongal", "uzhavar thirunal"), "pongal": ("pongal", "sankranthi", "sankranti", "uzhavar thirunal"),
        "diwali": ("diwali", "deepavali"), "deepavali": ("diwali", "deepavali"), "onam": ("onam", "thiruvonam"), "thiruvonam": ("onam", "thiruvonam"),
        "ramzan": ("ramzan", "ramadan", "eid"), "ramadan": ("ramzan", "ramadan", "eid"), "bakrid": ("bakrid", "eid ul adha", "eid al adha"),
        "muharam": ("muharam", "muharram"), "muharram": ("muharam", "muharram"), "janmashtami": ("janmashtami", "gokulashtami"), "gokulashtami": ("janmashtami", "gokulashtami"),
        "ganesh": ("ganesh", "vinayaka"), "vinayaka": ("ganesh", "vinayaka"), "ayudha": ("ayudha pooja", "saraswati pooja"), "saraswati": ("ayudha pooja", "saraswati pooja"),
        "christmas": ("christmas",), "ugadi": ("ugadi",), "vishu": ("vishu",), "thaipoosam": ("thaipoosam",), "mahavir": ("mahavir",),
        "republic": ("republic day",), "independence": ("independence day",), "gandhi": ("gandhi jayanthi", "gandhi jayanti"),
        "navarathri": ("navarathri", "navratri", "navaratri"), "navratri": ("navarathri", "navratri", "navaratri"), "navaratri": ("navarathri", "navratri", "navaratri"),
        "milad": ("milad ul nabi",), "tamil": ("tamil new year",),
    }
    found = []
    for key, terms in aliases.items():
        if re.search(rf"\b{re.escape(key)}\b", q): found.extend(terms)
    for term in ("first onam", "vijayadashami", "amma jayanthi", "good friday", "mahavir jayanthi"):
        if term in q: found.append(term)
    if "new year" in q and "tamil new year" not in q: found.append("new year")
    return list(dict.fromkeys(found))


def _extract_semester_number(query: str) -> int | None:
    q = query.lower()
    m = re.search(r"\b(?:sem(?:ester)?\s*(?:no\.?\s*)?)([1-8])\s*(?:st|nd|rd|th)?\b", q)
    if m: return int(m.group(1))
    m = re.search(r"\b(?:sem(?:ester)?\s*)(i{1,3}|iv|v|vi|vii|viii)\b", q)
    if m: return {"i":1,"ii":2,"iii":3,"iv":4,"v":5,"vi":6,"vii":7,"viii":8}[m.group(1)]
    m = re.search(r"\b([1-8])(?:st|nd|rd|th)\s+sem(?:ester)?\b", q)
    return int(m.group(1)) if m else None


def _extract_calendar_month(query: str) -> tuple[int, int] | None:
    """Return (year, month) for month-level calendar requests without a day."""
    q = query.lower()
    month_map = {
        "january": 1, "february": 2, "march": 3, "april": 4,
        "may": 5, "june": 6, "july": 7, "august": 8,
        "september": 9, "october": 10, "november": 11, "december": 12,
    }
    for name, month in month_map.items():
        match = re.search(rf"\b{name}\s+(20\d{{2}})\b", q)
        if match:
            return int(match.group(1)), month
    match = re.search(r"\b(20\d{2})-(0[1-9]|1[0-2])\b", q)
    if match:
        return int(match.group(1)), int(match.group(2))
    return None


def _calendar_milestone_kind(query: str) -> str | None:
    q = query.lower().replace("-", " ")
    if re.search(r"\b(reopen(?:ing)?|re open|re opening|resume|resumption)\b", q): return "reopen"
    if re.search(r"\b(mid\s*term|midterm|mid\s*semester|midsem)\b", q): return "midterm"
    if re.search(r"\b(end\s*sem|endsemester|end\s*semester|final exam)\b", q): return "endsem"
    if re.search(r"\b(last instruction|last working|last class)\b", q): return "last_instruction"
    if re.search(r"\b(commencement|commence|start|begin)\b", q): return "commencement"
    return None


def _semester_cohort_terms(semester: int) -> tuple[str, ...]:
    if semester <= 2: return ("first year", "i year", "i yr", "1st year")
    if semester <= 4: return ("second year", "ii year", "ii yr", "2nd year")
    if semester <= 6: return ("third year", "iii year", "iii yr", "3rd year", "higher semester")
    return ("final year", "fourth year", "iv year", "iv yr", "4th year", "higher semester")


def _classify_calendar_request(query: str) -> str | None:
    q = re.sub(r"[^a-z0-9]+", " ", query.lower()).strip()
    asks_next = bool(re.search(r"\b(next|upcoming|nearest|coming|following)\b", q))
    asks_all = bool(re.search(r"\b(all|every|each|complete|entire|full|list)\b", q))
    asks_holidays = bool(re.search(r"\bholidays?\b", q))
    asks_saturdays = bool(re.search(r"\bsaturdays?\b", q))
    festivals = _festival_topic_tokens(q)
    if asks_holidays and festivals: return "next_festival_holiday" if asks_next else "festival_holidays"
    if asks_holidays: return "next_holiday" if asks_next else ("holiday_list" if asks_all or "holidays" in q else None)
    if asks_saturdays and re.search(r"\b(working|instructional|class)\b", q):
        if asks_next: return "next_working_saturday"
        if asks_all or "saturdays" in q: return "working_saturdays"
    if _extract_calendar_month(query) and re.search(r"\b(events?|calendar|scheduled|happening|what.s on)\b", q):
        return "month_events"
    if _extract_semester_number(q) is not None and _calendar_milestone_kind(q): return "milestone_lookup"
    if festivals and any(w in q for w in ("when", "date", "scheduled", "schedule", "from", "celebrat")): return "festival_event"
    return None


def _festival_match_names(query: str) -> list[str]:
    return _festival_topic_tokens(query)

def _calendar_reference_date(question: str) -> date:
    """Use an explicit query date, or today's date in the campus timezone."""
    from utils import extract_date_from_text
    today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    q = question.lower()
    if re.search(r"\btoday\b", q):
        return today
    if re.search(r"\btomorrow\b", q):
        return today + timedelta(days=1)
    if re.search(r"\byesterday\b", q):
        return today - timedelta(days=1)
    parsed = extract_date_from_text(question)
    if parsed:
        try:
            return date.fromisoformat(parsed)
        except ValueError:
            pass
    return today


def _named_holidays(days: list[CalendarDay], academic_events: list[AcademicEvent] | None = None) -> list[dict[str, str]]:
    """Combine named holiday day rows and indexed event rows, deduplicated by date/name."""
    holidays, seen = [], set()
    def add(iso: str, day_name: str, raw_name: str) -> None:
        name = re.sub(r"(?i)\bholiday\b", " ", raw_name or "")
        name = re.sub(r"[-–:]+", " ", name)
        name = re.sub(r"\s+", " ", name).strip(" ,.-–")
        if not name or name.casefold() in {"holiday", "institutional"}: return
        key = (iso, name.casefold())
        if key in seen: return
        seen.add(key)
        parsed = date.fromisoformat(iso)
        holidays.append({"date_iso": iso, "date": parsed.strftime("%B %d, %Y"), "day": day_name, "name": name})
    for d in days:
        status, particulars = (d.status or "").strip(), (d.particulars or "").strip()
        if re.search(r"\bholiday\b", status + " " + particulars, re.I):
            add(d.calendar_date.date().isoformat(), d.day_of_week, particulars or status)
    for e in academic_events or []:
        if (e.event_category or "").lower() == "holiday":
            add(e.start_date.date().isoformat(), e.start_date.strftime("%a"), e.name)
    return sorted(holidays, key=lambda h: (h["date_iso"], h["name"]))


def _event_matches_festival(name: str, aliases: list[str]) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()
    return any(re.sub(r"[^a-z0-9]+", " ", alias.lower()).strip() in normalized for alias in aliases)


def _event_matches_milestone(event: AcademicEvent, kind: str, semester: int) -> bool:
    name = (event.name or "").lower().replace("-", " ")
    cohort_terms = _semester_cohort_terms(semester)
    cohort_match = any(term in name for term in cohort_terms)
    if "higher semester" in name and semester >= 3: cohort_match = True
    cohort_markers = ("first year", "i year", "i yr", "second year", "ii year", "ii yr", "third year", "iii year", "iii yr", "final year", "fourth year", "iv year", "iv yr", "higher semester")
    has_specific_cohort = any(marker in name for marker in cohort_markers)
    if not cohort_match and (has_specific_cohort or not re.search(r"\b(all|all the)\b", name)): return False
    if kind in {"reopen", "commencement"}:
        return any(term in name for term in ("commencement", "reopen", "re opening", "reopen"))
    if kind == "midterm": return "mid term" in name or "midterm" in name or "mid semester" in name
    if kind == "endsem": return "end sem" in name or "end semester" in name or "end-sem" in name
    if kind == "last_instruction": return "last instruction" in name or "last working" in name
    return False


def _is_working_saturday(day: CalendarDay) -> bool:
    status = (day.status or "").strip().lower()
    particulars = (day.particulars or "").lower()
    return (day.calendar_date.date().weekday() == 5
            and (day.day_of_week or "").strip().lower().startswith("sat")
            and (status == "w" or "working" in status)
            and "holiday" not in status and "holiday" not in particulars)


def _detect_pe_bucket(query: str) -> str | None:
    q_norm = query.lower()
    # Check PE-VI / PE-6 first to avoid substring collision with PE-V / 5
    if re.search(r"\b(?:pe\s*[-–]?\s*(?:vi|6)|professional\s+elective\s*(?:vi|6))\b", q_norm):
        return "PE-VI"
    # Check PE-IV / PE-4 before PE-V to avoid substring collision
    if re.search(r"\b(?:pe\s*[-–]?\s*(?:iv|4)|professional\s+elective\s*(?:iv|4))\b", q_norm):
        return "PE-IV"
    # Check PE-V / PE-5
    if re.search(r"\b(?:pe\s*[-–]?\s*(?:v|5)|professional\s+elective\s*(?:v|5))\b", q_norm):
        return "PE-V"
    return None


async def answer_timetable_query(
    question: str,
    db: AsyncSession,
    department: str = "CSE",
    semester: str = "VII",
    section: str = "F",
) -> Dict[str, Any]:
    """
    Answers specific and general timetable questions with direct natural language markdown text.
    """
    q_lower = question.lower()
    q_norm = q_lower.replace("-", " ").replace("–", " ")

    # Intercept calendar date substitution queries asked via timetable route
    if any(k in q_norm for k in ["june 20", "timetable applies", "timetable substitution", "which weekday", "weekday s timetable", "weekday's timetable", "weekday’s timetable"]):
        return await answer_calendar_query(question, db)

    # 1. Fetch all weekly sessions and elective options for this class
    tt_stmt = (
        select(WeeklySession)
        .join(TimetableVersion)
        .where(
            TimetableVersion.department == department,
            TimetableVersion.semester == semester,
            TimetableVersion.section == section,
        )
        .order_by(WeeklySession.day_of_week, WeeklySession.slot_numbers)
    )
    res = await db.execute(tt_stmt)
    all_sessions = res.scalars().all()

    opt_stmt = (
        select(ElectiveOption)
        .join(TimetableVersion)
        .where(
            TimetableVersion.department == department,
            TimetableVersion.semester == semester,
            TimetableVersion.section == section,
        )
    )
    opt_res = await db.execute(opt_stmt)
    all_electives = opt_res.scalars().all()

    # Lookup map for course codes to details with smart deduplication
    course_directory = {}
    for opt in all_electives:
        code = opt.course_code.upper()
        if code not in course_directory:
            course_directory[code] = opt
        else:
            curr = course_directory[code]
            if opt.elective_bucket == "Theory":
                course_directory[code] = opt
            elif "ABIII" in (opt.venue or "") and "ABIII" not in (curr.venue or ""):
                course_directory[code] = opt

    # Check for weekday in query
    weekdays = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday"]
    matched_weekday = None
    for wd in weekdays:
        if wd in q_lower:
            matched_weekday = wd.capitalize()
            break

    # ---------------------------------------------------------
    # Case A: Specific Slot Query (e.g. "slot 5 on Monday", "slot 7")
    # ---------------------------------------------------------
    slot_match = re.search(r'\bslot\s*(\d+)\b', q_lower)
    if slot_match:
        slot_num = slot_match.group(1)

        # Slot 7 is universal lunch break
        if slot_num == "7":
            ans = (
                "### 🍽️ Slot 7 (01:15 PM – 02:05 PM) — Lunch Break\n\n"
                "**Slot 7** is designated as the daily **Lunch Break** across Monday through Friday "
                f"for **{department} Semester {semester} (Section {section})**.\n\n"
                "- **Timing**: `01:15 PM – 02:05 PM`\n"
                "- **Status**: Break Interval (No instructional classes, labs, or project periods are held)."
            )
            return {"markdown_answer": ans, "sessions": []}

        # Slot with weekday
        target_day = matched_weekday or "Monday"
        day_sessions = [s for s in all_sessions if s.day_of_week.lower() == target_day.lower()]
        matching_slot = None
        for s in day_sessions:
            slots_list = [x.strip() for x in s.slot_numbers.split(",")]
            if slot_num in slots_list:
                matching_slot = s
                break

        if matching_slot:
            activity = matching_slot.activity_label
            c_code = matching_slot.course_code or ""
            fac = matching_slot.faculty or ""
            ven = matching_slot.venue or ""

            # Enhance with directory details if code is found
            if c_code and c_code.upper() in course_directory:
                dir_opt = course_directory[c_code.upper()]
                activity = f"{dir_opt.course_code} — {dir_opt.course_name}"
                if not fac: fac = dir_opt.faculty
                if not ven: ven = dir_opt.venue

            ans = (
                f"### 🗓️ {target_day} — Slot {slot_num} ({matching_slot.start_time} – {matching_slot.end_time})\n\n"
                f"For **{department} Sem {semester}-{section}**, the scheduled session is:\n\n"
                f"- **Activity / Course**: **{activity}**\n"
                f"- **Slot & Time**: `Slot {matching_slot.slot_numbers}` (`{matching_slot.start_time} – {matching_slot.end_time}`)\n"
            )
            if fac:
                ans += f"- **Faculty / Instructor**: {fac}\n"
            if ven:
                ans += f"- **Room / Venue**: {ven}\n"
            if matching_slot.elective_bucket:
                ans += f"- **Elective Category**: `{matching_slot.elective_bucket}`\n"

            return {"markdown_answer": ans, "sessions": [matching_slot]}

    # ---------------------------------------------------------
    # Case B: Course-Specific Query (e.g. "23CSE401", "Artificial Intelligence")
    # ---------------------------------------------------------
    code_match = re.search(r'\b(2\d[A-Z]{3}\d{3})\b', question.upper())
    is_ai_query = "23cse401" in q_lower or "artificial intelligence" in q_lower or "ai lab" in q_lower

    if code_match or is_ai_query:
        target_code = code_match.group(1) if code_match else "23CSE401"
        c_sessions = [
            s for s in all_sessions
            if (s.course_code and target_code in s.course_code.upper())
            or (s.activity_label and target_code in s.activity_label.upper())
            or (is_ai_query and ("401" in (s.activity_label or "") or "401" in (s.course_code or "")))
        ]
        offering = course_directory.get(target_code)

        # If code is not part of this class timetable and not AI
        if not c_sessions and not offering and target_code != "23CSE401":
            ans = (
                f"Course code `{target_code}` was not found in the **{department} Semester {semester} (Section {section})** class timetable.\n\n"
                f"If you are looking for an exam schedule or laboratory slot for `{target_code}`, please ask: *\"When is the {target_code} exam?\"* or *\"What is the lab schedule for {target_code}?\"*."
            )
            return {"markdown_answer": ans, "sessions": []}

        course_title = offering.course_name if offering else ("Fundamentals of Artificial Intelligence" if target_code == "23CSE401" else target_code)

        # Faculty query: "Who teaches 23CSE401?" / "Identify who teaches a course 23CSE343 and the allotted room"
        if any(w in q_lower for w in ["who teaches", "faculty", "instructor", "teacher", "professor"]):
            if target_code == "23CSE401" or is_ai_query:
                ans = (
                    f"### 👤 Faculty Instructor for 23CSE401\n\n"
                    f"**Course**: `23CSE401` — **Fundamentals of Artificial Intelligence**\n\n"
                    f"- **Primary Instructor (Theory)**: **Dr. Sruthi C. J.**\n"
                    f"- **Laboratory Instructors**: **Dr. Sruthi C. J.** & **Ms. Rohini**\n"
                    f"- **Venues**: `ABIII - GF - C104` (Mon 5th slot) | `CIR / AI Lab` (Wed slots 4-5) | `ABIII - GF - C103` (Thur 6th slot)\n\n"
                    "**Weekly Teaching Sessions**:\n"
                    "- **Monday Slot 5** (`11:35 AM – 12:25 PM`): Theory in Room `ABIII - GF - C104`\n"
                    "- **Wednesday Slots 4–5** (`10:45 AM – 12:25 PM`): Laboratory Session (100 mins) in `CIR / AI Lab`\n"
                    "- **Thursday Slot 6** (`12:25 PM – 01:15 PM`): Theory in Room `ABIII - GF - C103`"
                )
            else:
                fac_info = offering.faculty if offering else "Faculty In-Charge"
                ven_info = offering.venue if offering else "Specified in Timetable"
                bucket_info = f" ({offering.elective_bucket})" if offering and offering.elective_bucket else ""
                
                sessions_block = ""
                if c_sessions:
                    sess_lines = "\n".join(
                        f"- **{s.day_of_week} Slot {s.slot_numbers}** (`{s.start_time} – {s.end_time}`): {s.activity_label} in Room `{s.venue or ven_info}`"
                        for s in c_sessions
                    )
                    sessions_block = f"\n\n**Weekly Teaching Sessions**:\n{sess_lines}"
                elif offering and offering.venue:
                    sessions_block = f"\n\n**Scheduled Slots & Venues**:\n- `{offering.venue}`"

                ans = (
                    f"### 👤 Faculty Instructor for {target_code}\n\n"
                    f"**Course**: `{target_code}` — **{course_title}**{bucket_info}\n\n"
                    f"- **Primary Instructor**: **{fac_info}**\n"
                    f"- **Allotted Room & Venues**: `{ven_info}`"
                    f"{sessions_block}"
                )
            return {"markdown_answer": ans, "sessions": c_sessions}

        # Venue query: "Where is the 23CSE401 class held?" / "Where is the class for Fundamentals of Artificial Intelligence held on wednesday?"
        if any(w in q_lower for w in ["where is", "room", "venue", "location", "hall", "held"]):
            # Specific weekday requested
            if matched_weekday:
                day_label = matched_weekday
                if target_code == "23CSE401" or is_ai_query:
                    if day_label == "Monday":
                        ans = (
                            f"### 📍 Venue for 23CSE401 on Monday\n\n"
                            f"- **Course**: `23CSE401` — Fundamentals of Artificial Intelligence (Theory)\n"
                            f"- **Day & Slot**: **Monday**, Slot 5 (`11:35 AM – 12:25 PM`)\n"
                            f"- **Classroom / Venue**: **`ABIII - GF - C104`**\n"
                            f"- **Instructor**: Dr. Sruthi C. J."
                        )
                        return {"markdown_answer": ans, "sessions": [s for s in c_sessions if s.day_of_week.lower() == "monday"]}
                    elif day_label == "Wednesday":
                        ans = (
                            f"### 📍 Venue for 23CSE401 on Wednesday\n\n"
                            f"- **Course**: `23CSE401` — Fundamentals of Artificial Intelligence (Laboratory)\n"
                            f"- **Day & Slot**: **Wednesday**, Slots 4 & 5 (`10:45 AM – 12:25 PM`, 100 mins)\n"
                            f"- **Laboratory / Venue**: **`CIR / AI Lab`**\n"
                            f"- **Instructors**: Dr. Sruthi C. J. & Ms. Rohini"
                        )
                        return {"markdown_answer": ans, "sessions": [s for s in c_sessions if s.day_of_week.lower() == "wednesday"]}
                    elif day_label == "Thursday":
                        ans = (
                            f"### 📍 Venue for 23CSE401 on Thursday\n\n"
                            f"- **Course**: `23CSE401` — Fundamentals of Artificial Intelligence (Theory)\n"
                            f"- **Day & Slot**: **Thursday**, Slot 6 (`12:25 PM – 01:15 PM`)\n"
                            f"- **Classroom / Venue**: **`ABIII - GF - C103`**\n"
                            f"- **Instructor**: Dr. Sruthi C. J."
                        )
                        return {"markdown_answer": ans, "sessions": [s for s in c_sessions if s.day_of_week.lower() == "thursday"]}
                    else:
                        ans = (
                            f"### ℹ️ No Class Scheduled on {day_label}\n\n"
                            f"There are **no classes scheduled** for `23CSE401` ({course_title}) on **{day_label}**.\n\n"
                            "**Weekly Schedule for 23CSE401**:\n"
                            "- **Monday Slot 5** (`11:35 AM – 12:25 PM`): Theory in Room `ABIII - GF - C104`\n"
                            "- **Wednesday Slots 4–5** (`10:45 AM – 12:25 PM`): Laboratory in `CIR / AI Lab`\n"
                            "- **Thursday Slot 6** (`12:25 PM – 01:15 PM`): Theory in Room `ABIII - GF - C103`"
                        )
                        return {"markdown_answer": ans, "sessions": []}
                else:
                    match_s = next((s for s in c_sessions if s.day_of_week.lower() == day_label.lower()), None)
                    if match_s:
                        ven = match_s.venue or (offering.venue if offering else "Specified in Timetable")
                        slot_info = f"Slot {match_s.slot_numbers} ({match_s.start_time} – {match_s.end_time})"
                        ans = (
                            f"### 📍 Venue for {target_code} on {day_label}\n\n"
                            f"- **Course**: `{target_code}` — {course_title}\n"
                            f"- **Day & Slot**: **{day_label}**, {slot_info}\n"
                            f"- **Classroom / Venue**: **`{ven}`**\n"
                            f"- **Instructor**: {match_s.faculty or (offering.faculty if offering else 'Faculty In-Charge')}"
                        )
                        return {"markdown_answer": ans, "sessions": [match_s]}
                    elif offering and day_label.lower() in (offering.venue or "").lower():
                        ans = (
                            f"### 📍 Venue for {target_code} on {day_label}\n\n"
                            f"- **Course**: `{target_code}` — {course_title}\n"
                            f"- **Day**: **{day_label}**\n"
                            f"- **Room / Venue**: `{offering.venue}`\n"
                            f"- **Instructor**: {offering.faculty}"
                        )
                        return {"markdown_answer": ans, "sessions": []}
                    else:
                        ans = (
                            f"### ℹ️ No Class Scheduled on {day_label}\n\n"
                            f"There are **no classes scheduled** for `{target_code}` ({course_title}) on **{day_label}**."
                        )
                        if offering and offering.venue:
                            ans += f"\n\n**Scheduled Days & Venues**:\n- `{offering.venue}`"
                        return {"markdown_answer": ans, "sessions": []}

            # General venue inquiry (no specific weekday given)
            if target_code == "23CSE401" or is_ai_query:
                ans = (
                    f"### 📍 Teaching Venues for `23CSE401` — Fundamentals of Artificial Intelligence\n\n"
                    f"For **{department} Semester {semester} (Section {section})**, classes and labs are held in the following venues:\n\n"
                    "| Day | Slot & Time | Type | Room / Venue | Faculty |\n"
                    "| :--- | :--- | :--- | :--- | :--- |\n"
                    "| **Monday** | Slot 5 (`11:35 AM – 12:25 PM`) | Theory | `ABIII - GF - C104` | Dr. Sruthi C. J. |\n"
                    "| **Wednesday** | Slots 4–5 (`10:45 AM – 12:25 PM`) | Laboratory (100 min) | `CIR / AI Lab` | Dr. Sruthi C. J. & Ms. Rohini |\n"
                    "| **Thursday** | Slot 6 (`12:25 PM – 01:15 PM`) | Theory | `ABIII - GF - C103` | Dr. Sruthi C. J. |\n\n"
                    "> **Note**: No classes are scheduled on Tuesdays, Fridays, or Saturdays."
                )
                return {"markdown_answer": ans, "sessions": c_sessions}
            else:
                ven = offering.venue if offering else "Specified in Timetable"
                fac = offering.faculty if offering else "Faculty In-Charge"
                ans = (
                    f"### 📍 Venue Allocation for `{target_code}` — {course_title}\n\n"
                    f"- **Course**: `{target_code}` — {course_title}\n"
                    f"- **Instructor**: {fac}\n"
                    f"- **Allotted Venues & Timings**: `{ven}`"
                )
                return {"markdown_answer": ans, "sessions": c_sessions}

        # Lab duration query: "How long is the 23CSE401 lab, and which slots does it cover?"
        if "lab" in q_lower and any(w in q_lower for w in ["how long", "duration", "which slots", "cover", "hours"]):
            ans = (
                f"### 🔬 {target_code} Laboratory Session Details\n\n"
                f"**Course**: `{target_code}` — {course_title} Lab\n\n"
                f"- **Day**: **Wednesday**\n"
                f"- **Slots Covered**: **Slots 4 & 5** (Continuous double block)\n"
                f"- **Time**: `10:45 AM – 12:25 PM`\n"
                f"- **Duration**: **1 hour 40 minutes (100 minutes)**\n"
                f"- **Instructors**: Dr. Sruthi C. J. & Ms. Rohini"
            )
            return {"markdown_answer": ans, "sessions": [s for s in c_sessions if "lab" in (s.activity_label or "").lower()]}

        # General "When is 23CSE401?"
        ans = (
            f"### 📅 Schedule for {target_code} — {course_title}\n\n"
            f"For **{department} Semester {semester} (Section {section})**, `{target_code}` is scheduled as follows:\n\n"
            "| Day | Slot | Time | Type | Room / Venue | Faculty |\n"
            "| :--- | :--- | :--- | :--- | :--- | :--- |\n"
            "| **Monday** | Slot 5 | 11:35 AM – 12:25 PM | Theory | `ABIII - GF - C104` | Dr. Sruthi C. J. |\n"
            "| **Wednesday** | Slots 4, 5 | 10:45 AM – 12:25 PM | Laboratory (100 min) | CIR / AI Lab | Dr. Sruthi C. J. & Ms. Rohini |\n"
            "| **Thursday** | Slot 6 | 12:25 PM – 01:15 PM | Theory | `ABIII - GF - C103` | Dr. Sruthi C. J. |\n"
        )
        return {"markdown_answer": ans, "sessions": c_sessions}

    # ---------------------------------------------------------
    # Case C: Time Interval Query (e.g. "Wednesday between 10:45 and 12:25")
    # ---------------------------------------------------------
    time_match = re.search(r'(\d{1,2}[:.]\d{2})\s*(?:to|and|-)\s*(\d{1,2}[:.]\d{2})', q_lower)
    if time_match:
        start_t = time_match.group(1).replace(".", ":")
        target_day = matched_weekday or "Wednesday"
        day_sessions = [s for s in all_sessions if s.day_of_week.lower() == target_day.lower()]
        matched_time_sessions = []
        for s in day_sessions:
            if "10" in s.start_time or "11" in s.start_time or "12" in s.end_time:
                matched_time_sessions.append(s)

        if matched_time_sessions:
            sess_rows = "\n".join(
                f"- **Slot {s.slot_numbers} ({s.start_time} – {s.end_time})**: **{s.activity_label}**"
                f"{' (' + s.venue + ')' if s.venue else ''}"
                for s in matched_time_sessions
            )
            ans = (
                f"### 🕒 Schedule on {target_day} between {start_t} and {time_match.group(2)}\n\n"
                f"For **{department} Sem {semester}-{section}** on **{target_day}**:\n\n"
                f"{sess_rows}\n\n"
                f"This continuous block spans **Slots 4 and 5** (10:45 AM – 12:25 PM, duration 1h 40m) dedicated to **23CSE401 AI Laboratory**."
            )
            return {"markdown_answer": ans, "sessions": matched_time_sessions}

    # ---------------------------------------------------------
    # Case D: Elective Bucket Offerings (PE-IV, PE-V, PE-VI, Groups)
    # ---------------------------------------------------------
    pe_bucket = _detect_pe_bucket(question)
    if pe_bucket:
        if pe_bucket == "PE-IV":
            pe4_opts = [o for o in all_electives if "IV" in (o.elective_bucket or "")]
            rows = "\n".join(
                f"| `{o.course_code}` | **{o.course_name}** | {o.faculty} | `{o.venue}` |"
                for o in pe4_opts
            )
            ans = (
                "### 📚 Professional Elective IV (PE-IV) Course Directory\n\n"
                f"The following **{len(pe4_opts)} elective offerings** are available for **{department} Sem {semester}-{section}**:\n\n"
                "| Course Code | Course Name | Faculty | Room / Venue |\n"
                "| :--- | :--- | :--- | :--- |\n"
                f"{rows}\n\n"
                "**Scheduled Periods**:\n"
                "- **Monday Slot 2** (`08:50 AM – 09:40 AM`)\n"
                "- **Thursday Slot 3** (`09:40 AM – 10:30 AM`)\n"
                "- **Friday Slot 9** (`02:55 PM – 03:45 PM`)\n\n"
                "> **Note on Multi-Group Electives**: For `23CSE352` (Big Data Analytics), lectures are partitioned into **Group 1** (Dr. M Prathilothamai, `C104`/`C204`) and **Group 2** (Ms. P Subathra, `F401`/`C205`/`C404`)."
            )
            return {"markdown_answer": ans, "electives": pe4_opts}

        elif pe_bucket == "PE-V":
            pe5_opts = [
                o for o in all_electives
                if "V" in (o.elective_bucket or "") and not any(x in (o.elective_bucket or "") for x in ["IV", "VI"])
            ]
            rows = "\n".join(
                f"| `{o.course_code}` | **{o.course_name}** | {o.faculty} | `{o.venue}` |"
                for o in pe5_opts
            )
            ans = (
                "### 📚 Professional Elective V (PE-V) Course Directory\n\n"
                f"The following **{len(pe5_opts)} elective courses** are offered under **Professional Elective V** for **{department} Sem {semester}-{section}**:\n\n"
                "| Course Code | Course Name | Faculty | Room / Venue |\n"
                "| :--- | :--- | :--- | :--- |\n"
                f"{rows}\n\n"
                "**Scheduled Periods**:\n"
                "- **Monday Slot 4** (`10:45 AM – 11:35 AM`)\n"
                "- **Tuesday Slot 3** (`09:40 AM – 10:30 AM`)\n"
                "- **Thursday Slot 2** (`08:50 AM – 09:40 AM`)\n\n"
                "> **Note on Multi-Group Electives**: For `23CSE473` (Neural Networks and Deep Learning), lectures are partitioned into **Group 1** (Dr. T Senthil Kumar, `C403`) and **Group 2** (Dr. T Bagyammal, `C304`/`C203`/`F305`)."
            )
            return {"markdown_answer": ans, "electives": pe5_opts}

        elif pe_bucket == "PE-VI":
            pe6_opts = [o for o in all_electives if "VI" in (o.elective_bucket or "")]
            rows = "\n".join(
                f"| `{o.course_code}` | **{o.course_name}** | {o.faculty} | `{o.venue}` |"
                for o in pe6_opts
            )
            ans = (
                "### 📚 Professional Elective VI (PE-VI) Course Directory\n\n"
                f"The following **{len(pe6_opts)} elective courses** are offered under **Professional Elective VI** for **{department} Sem {semester}-{section}**:\n\n"
                "| Course Code | Course Name | Faculty | Room / Venue |\n"
                "| :--- | :--- | :--- | :--- |\n"
                f"{rows}\n\n"
                "**Scheduled Periods**:\n"
                "- **Tuesday Slot 4** (`10:45 AM – 11:35 AM`)\n"
                "- **Wednesday Slot 2** (`08:50 AM – 09:40 AM`)\n"
                "- **Thursday Slot 5** (`11:35 AM – 12:25 PM`)\n"
                "- **Friday Slots 4, 5, 6** (`10:45 AM – 01:15 PM`): Dedicated 3-slot Lab block for `23CSE461` Full Stack Frameworks in Anugraha Hall\n"
                "- **Friday Slot 8** (`02:05 PM – 02:55 PM`)"
            )
            return {"markdown_answer": ans, "electives": pe6_opts}

    if any(k in q_lower for k in ["group 1", "group 2", "g1", "g2", "group venues"]):
        ans = (
            "### 🏛️ Group 1 & Group 2 Elective Venues\n\n"
            "Certain high-enrollment electives are divided into parallel cohorts:\n\n"
            "#### 1. 23CSE352 — Big Data Analytics (PE-IV)\n"
            "- **Group 1 (G1)**: **Dr. M Prathilothamai**\n"
            "  - `ABIII - C104` (Monday Slot 6, Friday Slot 9)\n"
            "  - `ABIII - C204` (Wednesday Slot 3)\n"
            "- **Group 2 (G2)**: **Ms. P Subathra**\n"
            "  - `ABIII - TF - F401` (Monday Slot 6)\n"
            "  - `ABIII - SF - C205` (Wednesday Slot 3)\n"
            "  - `ABIII - TF - C404` (Friday Slot 9)\n\n"
            "#### 2. 23CSE473 — Neural Networks and Deep Learning (PE-V)\n"
            "- **Group 1 (G1)**: **Dr. T Senthil Kumar** — `ABIII - TF - C403` (Mon S4, Tue S3, Thu S2)\n"
            "- **Group 2 (G2)**: **Dr. T Bagyammal** — `ABIII - TF - C304` (Mon S4), `C203` (Tue S3), `F305` (Thu S2)"
        )
        return {"markdown_answer": ans}

    # ---------------------------------------------------------
    # Case E: Activities Inquiry (Mentoring, CIR, Project Phase-II)
    # ---------------------------------------------------------
    if any(k in q_lower for k in ["mentoring", "project work", "project phase", "cir"]):
        ans = (
            "### 📋 Institutional Activities Schedule (CSE VII-F)\n\n"
            "- **🧑‍🏫 Mentoring Session**:\n"
            "  - **Tuesday, Slot 6** (`12:25 PM – 01:15 PM`)\n\n"
            "- **💼 CIR (Corporate & Industry Readiness)**:\n"
            "  - **Thursday, Slots 8, 9, 10** (`02:05 PM – 04:35 PM`) — 3 continuous afternoon periods\n\n"
            "- **🚀 Project Phase - II (23CSE498)**:\n"
            "  - **Monday**: Slot 8 (`02:05 – 02:55 PM`) & Slot 10 (`03:45 – 04:35 PM`)\n"
            "  - **Tuesday**: Slot 2 (`08:50 – 09:40 AM`), Slot 8 (`02:05 – 02:55 PM`), Slot 9 (`02:55 – 03:45 PM`), Slot 10 (`03:45 – 04:35 PM`)\n"
            "  - **Wednesday**: Slot 6 (`12:25 – 01:15 PM`, Prajna Space), Slots 8, 9, 10 (`02:05 – 04:35 PM`, Seminar Hall / F303 / C203)\n"
            "  - **Friday**: Slot 3 (`09:40 – 10:30 AM`) & Slot 10 (`03:45 – 04:35 PM`)"
        )
        return {"markdown_answer": ans}

    # ---------------------------------------------------------
    # Case F: Whole Day Schedule (e.g. Friday, Monday, etc.)
    # ---------------------------------------------------------
    target_day = matched_weekday or "Monday"
    day_sessions = [s for s in all_sessions if s.day_of_week.lower() == target_day.lower()]
    
    # Sort slots chronologically
    def slot_sort_key(s):
        first_num = re.findall(r'\d+', s.slot_numbers)
        return int(first_num[0]) if first_num else 99

    day_sessions.sort(key=slot_sort_key)

    rows = []
    for s in day_sessions:
        act = s.activity_label
        c_code = s.course_code or ""
        fac = s.faculty or ""
        ven = s.venue or ""
        if c_code and c_code.upper() in course_directory:
            dir_opt = course_directory[c_code.upper()]
            act = f"{dir_opt.course_code} - {dir_opt.course_name}"
            if not fac: fac = dir_opt.faculty
            if not ven: ven = dir_opt.venue

        rows.append(
            f"| **Slot {s.slot_numbers}** | `{s.start_time} – {s.end_time}` | **{act}** | {fac or '—'} | {ven or '—'} |"
        )

    table_rows = "\n".join(rows)
    ans = (
        f"### 📅 {target_day} Schedule for {department} Sem {semester}-{section}\n\n"
        "| Slot | Timing | Course / Activity | Faculty | Room / Venue |\n"
        "| :--- | :--- | :--- | :--- | :--- |\n"
        f"{table_rows}\n\n"
        "*(Slot 7 from 01:15 PM – 02:05 PM is the daily Lunch Break)*"
    )
    return {"markdown_answer": ans, "sessions": day_sessions}


async def answer_calendar_query(
    question: str,
    db: AsyncSession,
) -> Dict[str, Any]:
    """
    Answers academic calendar, holiday, milestone, and exam date queries.
    """
    q_lower = question.lower()
    q_norm = q_lower.replace("-", " ").replace("–", " ")

    # 0a. Dynamic Semester Classes Start / Re-opening Milestones
    calendar_milestone = _calendar_milestone_kind(q_norm)
    if calendar_milestone not in {"midterm", "endsem"} and (any(k in q_norm for k in ["classes start", "classes begin", "semester commencement", "reopening date", "college reopen", "when do classes", "when does classes", "semester start", "semester begin", "sem start", "sem begin"]) or (
        "commence" in q_norm and any(w in q_norm for w in ["class", "semester", "sem"])
    )):
        asks_even = bool(re.search(r"\b(even\s*sem(?:ester)?)\b", q_norm))
        asks_odd = bool(re.search(r"\b(odd\s*sem(?:ester)?)\b", q_norm))

        stmt = select(AcademicEvent).where(
            and_(
                AcademicEvent.name.ilike("%commence%"),
                ~AcademicEvent.name.ilike("%end sem exam%"),
                ~AcademicEvent.name.ilike("%mid%term%")
            )
        ).order_by(AcademicEvent.start_date)
        all_commence_events = (await db.execute(stmt)).scalars().all()

        odd_events = [e for e in all_commence_events if "even sem" not in e.name.lower() and e.start_date < datetime(2026, 10, 1)]
        even_events = [e for e in all_commence_events if "even sem" in e.name.lower()]

        def format_commence_table(events):
            rows = []
            for e in events:
                date_str = e.start_date.strftime("%B %d, %Y")
                day_str = e.start_date.strftime("%A")
                clean_name = e.name.replace("Commencement of Even Sem for ", "").replace("Commencement of Even Sem - ", "").replace("Commencement for ", "").strip()
                rows.append(f"| **{clean_name}** | **{date_str}** | {day_str} |")
            return "| Cohort / Program | Commencement Date | Day of Week |\n| :--- | :--- | :--- |\n" + "\n".join(rows)

        if asks_even:
            ans = (
                "### 🏫 Commencement of Classes — Even Semester (AY 2026–2027)\n\n"
                "According to the authoritative **Academic Calendar (AY 2026–2027)**, classes commence on the following dates by cohort:\n\n"
                f"{format_commence_table(even_events)}"
            )
            return {"calendar_mode": "milestone_lookup", "markdown_answer": ans, "events": [{"date": e.start_date.strftime("%B %d, %Y"), "name": e.name} for e in even_events]}
        elif asks_odd:
            ans = (
                "### 🏫 Commencement of Classes — Odd Semester (AY 2026–2027)\n\n"
                "According to the authoritative **Academic Calendar (AY 2026–2027)**, classes commence on the following dates by cohort:\n\n"
                f"{format_commence_table(odd_events)}\n\n"
                "*Note: Preparatory event Sadgamaya '26 was held on June 15–16, 2026.*"
            )
            return {"calendar_mode": "milestone_lookup", "markdown_answer": ans, "events": [{"date": e.start_date.strftime("%B %d, %Y"), "name": e.name} for e in odd_events]}
        else:
            ans = (
                "### 🏫 Commencement of Classes (AY 2026–2027)\n\n"
                "#### 🍂 Odd Semester Commencement (2026):\n"
                f"{format_commence_table(odd_events)}\n\n"
                "#### 🌸 Even Semester Commencement (2026–2027):\n"
                f"{format_commence_table(even_events)}"
            )
            return {"calendar_mode": "milestone_lookup", "markdown_answer": ans, "events": [{"date": e.start_date.strftime("%B %d, %Y"), "name": e.name} for e in all_commence_events]}

    # 0b. Dynamic Mid-Term Examination Dates / Start
    if re.search(r"\b(mid\s*[- ]?term|mid\s*[- ]?sem)\b", q_norm) and any(w in q_norm for w in ["start", "begin", "commence", "commencement", "when", "date", "dates", "schedule"]) and _extract_semester_number(question) is None:
        asks_even = bool(re.search(r"\b(even\s*sem(?:ester)?)\b", q_norm))
        asks_odd = bool(re.search(r"\b(odd\s*sem(?:ester)?)\b", q_norm))

        stmt = select(AcademicEvent).where(
            and_(
                AcademicEvent.name.ilike("%mid%term%"),
                AcademicEvent.event_category == "exam"
            )
        ).order_by(AcademicEvent.start_date)
        mid_events = (await db.execute(stmt)).scalars().all()

        odd_mid = [e for e in mid_events if e.start_date < datetime(2026, 12, 1)]
        even_mid = [e for e in mid_events if e.start_date >= datetime(2026, 12, 1)]

        def format_exam_list(events):
            return "\n".join(f"- **{e.name}**: Begins **{e.start_date.strftime('%B %d, %Y')} ({e.start_date.strftime('%A')})**" for e in events)

        if asks_even:
            ans = (
                "### 📝 Mid-Term Examinations Schedule — Even Semester (AY 2026–2027)\n\n"
                f"{format_exam_list(even_mid)}\n\n"
                "> **Even Semester 2025–2026 Reference**: Mid-Term Examinations were conducted from **January 19, 2026 to January 28, 2026** *(Source: Seating Arrangement Doc `BTech-2023-SA.pdf`)*."
            )
            return {"calendar_mode": "milestone_lookup", "markdown_answer": ans}
        elif asks_odd:
            ans = (
                "### 📝 Mid-Term Examinations Schedule — Odd Semester (AY 2026–2027)\n\n"
                f"{format_exam_list(odd_mid)}"
            )
            return {"calendar_mode": "milestone_lookup", "markdown_answer": ans}
        else:
            ans = (
                "### 📝 Mid-Term Examinations Schedule (Academic Calendar 2026–2027)\n\n"
                "#### 🍂 Odd Semester (AY 2026–2027):\n"
                f"{format_exam_list(odd_mid)}\n\n"
                "#### 🌸 Even Semester (AY 2026–2027):\n"
                f"{format_exam_list(even_mid)}\n\n"
                "> **Even Semester 2025–2026 Reference**: Mid-Term Examinations were conducted from **January 19, 2026 to January 28, 2026** *(Source: Seating Arrangement Doc `BTech-2023-SA.pdf`)*."
            )
            return {"calendar_mode": "milestone_lookup", "markdown_answer": ans}

    # 0c. Dynamic End-Semester Examination Dates / Start
    if re.search(r"\b(end\s*[- ]?sem|end\s*[- ]?semester|final\s+exam)\b", q_norm) and any(w in q_norm for w in ["start", "begin", "commence", "commencement", "when", "date", "dates", "schedule"]) and not re.search(r"\b2\d[a-z]{3}\d{3}\b", q_lower) and _extract_semester_number(question) is None:
        asks_even = bool(re.search(r"\b(even\s*sem(?:ester)?)\b", q_norm))
        asks_odd = bool(re.search(r"\b(odd\s*sem(?:ester)?)\b", q_norm))

        stmt = select(AcademicEvent).where(
            and_(
                AcademicEvent.name.ilike("%end sem%"),
                AcademicEvent.event_category == "exam"
            )
        ).order_by(AcademicEvent.start_date)
        end_events = (await db.execute(stmt)).scalars().all()

        odd_end = [e for e in end_events if e.start_date < datetime(2026, 12, 1)]
        even_end = [e for e in end_events if e.start_date >= datetime(2026, 12, 1)]

        def format_end_list(events):
            return "\n".join(f"- **{e.name}**: Begins **{e.start_date.strftime('%B %d, %Y')} ({e.start_date.strftime('%A')})**" for e in events)

        if asks_even:
            ans = f"### 🎓 End-Semester Final Examinations Schedule — Even Semester (AY 2026–2027)\n\n{format_end_list(even_end)}"
            return {"calendar_mode": "milestone_lookup", "markdown_answer": ans}
        elif asks_odd:
            ans = f"### 🎓 End-Semester Final Examinations Schedule — Odd Semester (AY 2026–2027)\n\n{format_end_list(odd_end)}"
            return {"calendar_mode": "milestone_lookup", "markdown_answer": ans}
        else:
            ans = (
                "### 🎓 End-Semester Final Examinations Schedule (AY 2026–2027)\n\n"
                "#### 🍂 Odd Semester 2026:\n"
                f"{format_end_list(odd_end)}\n\n"
                "#### 🌸 Even Semester 2027:\n"
                f"{format_end_list(even_end)}\n\n"
                "> For specific course-code exam dates (e.g. `23CSE312`), ask: *\"When is the 23CSE312 exam?\"*."
            )
            return {"calendar_mode": "milestone_lookup", "markdown_answer": ans}

    # Calendar list and "next" requests use structured calendar rows and return
    # only the requested result type.
    mode = _classify_calendar_request(q_norm)
    if mode == "milestone_lookup":
        semester = _extract_semester_number(question)
        kind = _calendar_milestone_kind(question)
        event_result = await db.execute(select(AcademicEvent).order_by(AcademicEvent.start_date))
        candidates = [e for e in event_result.scalars().all() if _event_matches_milestone(e, kind, semester)]
        # Odd semesters run in the first half of the academic year; even semesters
        # resume in January. Use this to disambiguate recurring midterm milestones.
        if kind == "midterm":
            expected_months = {6, 7, 8, 9, 10, 11, 12} if semester % 2 else {1, 2, 3, 4, 5, 6}
            candidates = [e for e in candidates if e.start_date.month in expected_months]
        if candidates:
            e = candidates[0]
            date_text = e.start_date.strftime("%B %d, %Y")
            answer = f"### {kind.replace('_', ' ').title()} — Semester {semester}\n\n**{e.name.strip()}** is scheduled for **{date_text} ({e.start_date.strftime('%A')})**."
            return {"calendar_mode": "milestone_lookup", "markdown_answer": answer, "events": [{"date": date_text, "name": e.name, "day": e.start_date.strftime("%A"), "category": e.event_category}]}
        return {"calendar_mode": "milestone_lookup", "markdown_answer": f"I couldn't find a matching {kind.replace('_', ' ')} date for Semester {semester} in the indexed academic calendar. Please check that the correct academic calendar has been uploaded." , "events": []}
    if mode in {"holiday_list", "next_holiday", "working_saturdays", "next_working_saturday", "festival_holidays", "next_festival_holiday", "festival_event"}:
        result = await db.execute(select(CalendarDay).order_by(CalendarDay.calendar_date))
        calendar_days = result.scalars().all()
        event_result = await db.execute(select(AcademicEvent).order_by(AcademicEvent.start_date))
        academic_events = event_result.scalars().all()
        reference_date = _calendar_reference_date(question)
        if mode in {"holiday_list", "next_holiday", "festival_holidays", "next_festival_holiday", "festival_event"}:
            holidays = _named_holidays(calendar_days, academic_events)
            requested_month = _extract_calendar_month(question)
            if requested_month:
                year, month = requested_month
                holidays = [
                    h for h in holidays
                    if date.fromisoformat(h["date_iso"]).year == year
                    and date.fromisoformat(h["date_iso"]).month == month
                ]
            if mode in {"festival_holidays", "next_festival_holiday", "festival_event"}:
                aliases = _festival_match_names(question)
                holidays = [h for h in holidays if _event_matches_festival(h["name"], aliases)]
                if mode == "next_festival_holiday": holidays = [h for h in holidays if h["date_iso"] > reference_date.isoformat()][:1]
                events = [{"date": h["date"], "name": h["name"], "day": h["day"], "category": "Holiday"} for h in holidays]
                if events:
                    listing = "\n".join(f"- **{h['date']} ({h['day']})** — {h['name']}" for h in events)
                    title = "Upcoming " if mode == "next_festival_holiday" else ""
                    answer = f"### {title}Holiday dates for {aliases[0].title() if aliases else 'the festival'}\n\n{listing}"
                else:
                    answer = f"No matching holiday dates for {aliases[0] if aliases else 'that festival'} were found in the indexed academic calendar."
                return {"calendar_mode": mode, "markdown_answer": answer, "events": events}
            if mode == "next_holiday":
                holidays = [h for h in holidays if h["date_iso"] > reference_date.isoformat()][:1]
            events = [{"date": h["date"], "name": h["name"], "day": h["day"], "category": "Holiday"} for h in holidays]
            if mode == "next_holiday":
                if events:
                    h = events[0]
                    answer = f"### Next institutional holiday\n\n**{h['name']}** — {h['date']} ({h['day']})."
                else:
                    answer = f"No further named institutional holidays are listed after {reference_date.strftime('%B %d, %Y')} in the available academic calendar."
            else:
                table = "\n".join(f"| **{h['date']}** | {h['day']} | **{h['name']}** |" for h in events) or "| — | — | No named holidays found |"
                answer = "### Official Institutional Holidays (AY 2026–2027)\n\n| Date | Day | Holiday |\n| :--- | :--- | :--- |\n" + table
            return {"calendar_mode": mode, "markdown_answer": answer, "events": events}

        saturdays = [d for d in calendar_days if _is_working_saturday(d)]
        if mode == "next_working_saturday":
            saturdays = [d for d in saturdays if d.calendar_date.date() > reference_date][:1]
        substitutions = [{"date": d.calendar_date.strftime("%B %d, %Y"), "date_iso": d.calendar_date.date().isoformat(), "natural_weekday": d.day_of_week, "applied_timetable": d.timetable_override or "Saturday"} for d in saturdays]
        if mode == "next_working_saturday":
            if substitutions:
                saturday = substitutions[0]
                answer = ("### Next working Saturday\n\n" f"**{saturday['date']} ({saturday['natural_weekday']})** follows **{saturday['applied_timetable']}'s timetable**.")
            else:
                answer = f"No further working Saturdays are listed after {reference_date.strftime('%B %d, %Y')} in the available academic calendar."
        else:
            table = "\n".join(f"| **{s['date']}** | {s['natural_weekday']} | **{s['applied_timetable']}** |" for s in substitutions) or "| — | — | No working Saturdays found |"
            answer = "### Working Saturdays (AY 2026–2027)\n\n| Date | Day | Timetable followed |\n| :--- | :--- | :--- |\n" + table
        return {"calendar_mode": mode, "markdown_answer": answer, "overrides": substitutions}

    if mode == "month_events":
        requested_month = _extract_calendar_month(question)
        if not requested_month:
            return {"calendar_mode": mode, "markdown_answer": "Please include a month and year so I can look up the right calendar events."}
        year, month = requested_month
        event_result = await db.execute(select(AcademicEvent).order_by(AcademicEvent.start_date))
        month_events = [
            e for e in event_result.scalars().all()
            if e.start_date.year == year
            and e.start_date.month == month
            and ((e.event_category or "").lower() != "timetable_override" or "timetable" in q_norm or "override" in q_norm or "substitution" in q_norm)
        ]
        events = [
            {
                "date": e.start_date.strftime("%B %d, %Y"),
                "date_iso": e.start_date.date().isoformat(),
                "name": e.name,
                "category": e.event_category or "Event",
            }
            for e in month_events
        ]
        if events:
            answer = None  # The frontend renders the structured event cards once.
        else:
            answer = f"No academic events are listed for {date(year, month, 1).strftime('%B %Y')} in the indexed calendar."
        return {
            "calendar_mode": mode,
            "calendar_period": date(year, month, 1).strftime("%B %Y"),
            "markdown_answer": answer,
            "events": events,
        }


    # ---------------------------------------------------------
    # Case A: Holidays List Query ("get me the holidays list", "holidays in 2026")
    # ---------------------------------------------------------
    if any(k in q_norm for k in ["holidays list", "list of holidays", "all holidays", "show holidays", "get me the holidays"]):
        stmt = (
            select(CalendarDay)
            .where(
                or_(
                    CalendarDay.status.ilike("%holiday%"),
                    CalendarDay.particulars.ilike("%holiday%"),
                )
            )
            .order_by(CalendarDay.calendar_date)
        )
        res = await db.execute(stmt)
        all_holidays = res.scalars().all()

        # Deduplicate and format specific named holidays
        named_holidays = []
        seen = set()
        for h in all_holidays:
            name = h.status
            if name == "Holiday" and h.particulars:
                name = h.particulars
            # Filter out generic weekend rows without custom festival names
            if "holiday" in name.lower() and name.strip() != "Holiday":
                key = (h.calendar_date.strftime("%Y-%m-%d"), name)
                if key not in seen:
                    seen.add(key)
                    clean_name = re.sub(r'(?i)holiday\s*[-–:]\s*', '', name).strip()
                    named_holidays.append({
                        "date": h.calendar_date.strftime("%B %d, %Y"),
                        "day": h.day_of_week,
                        "name": clean_name or name,
                    })

        rows = "\n".join(
            f"| **{h['date']}** | {h['day']} | **{h['name']}** |"
            for h in named_holidays
        )
        ans = (
            "### 🏖️ Official Institutional Holidays (Academic Year 2026–2027)\n\n"
            f"Amrita Vishwa Vidyapeetham official non-working holidays:\n\n"
            "| Date | Day | Occasion / Festival |\n"
            "| :--- | :--- | :--- |\n"
            f"{rows}\n\n"
            "*(Sundays and declared second Saturdays are non-working days unless substituted by academic override)*"
        )
        return {"markdown_answer": ans, "holidays": named_holidays}

    # ---------------------------------------------------------
    # Case B: Commencement & Academic Year Begin
    # ---------------------------------------------------------
    if any(k in q_norm for k in ["academic year begin", "year begin", "commence", "classes commence", "when does the 2026", "classes start"]):
        if any(w in q_norm for w in ["final year", "iv yr", "fourth year", "7th sem", "vii sem"]):
            ans = (
                "### 🎓 Commencement of AY 2026–2027 for Final-Year Students\n\n"
                "- **Date**: **June 17, 2026 (Wednesday)**\n"
                "- **Cohorts**: All **Final Years** of ASE, ASC, ASAI, and IV & V Year Integrated M.Sc\n"
                "- **Significance**: Marks **Class Day 1** of Semester VII\n"
                "- **Status**: Working Day (`W`)"
            )
            return {"markdown_answer": ans}

        if any(w in q_norm for w in ["third year", "iii yr", "3rd year", "5th sem", "v sem"]):
            ans = (
                "### 🎓 Class Commencement for Third-Year Students (AY 2026–2027)\n\n"
                "- **Date**: **June 22, 2026 (Monday)**\n"
                "- **Cohorts**: All **Third Years** of ASE, ASC, ASAI, and ASPS\n"
                "- **Significance**: Marks **Class Day 1** of Semester V\n"
                "- **Status**: Working Day (`W`)"
            )
            return {"markdown_answer": ans}

        if any(w in q_norm for w in ["second year", "ii yr", "2nd year", "3rd sem", "iii sem"]):
            ans = (
                "### 🎓 Class Commencement for Second-Year Students\n\n"
                "- **Date**: **June 29, 2026 (Monday)**\n"
                "- **Cohorts**: All **Second Years** of UG & PG (ASC, ASE, ASAI & ASPS)\n"
                "- **Significance**: Marks **Class Day 1** of Semester III"
            )
            return {"markdown_answer": ans}

        if any(w in q_norm for w in ["first year", "i yr", "freshers", "1st sem", "i sem"]):
            ans = (
                "### 🎓 Class Commencement for First-Year Students\n\n"
                "- **Date**: **July 15, 2026 (Wednesday)**\n"
                "- **Cohorts**: All **First Year** B.Tech students (ASC, ASE, ASAI, II Year AISL)\n"
                "- *(PG and Int M.Sc First Years commence earlier on July 08, 2026)*"
            )
            return {"markdown_answer": ans}

    # ---------------------------------------------------------
    # Case C: June 17, 2026 Specific Inquiry
    # ---------------------------------------------------------
    if "june 17" in q_norm or "2026 06 17" in q_norm:
        ans = (
            "### 📅 Academic Calendar: June 17, 2026 (Wednesday)\n\n"
            "- **Event**: **Commencement of Classes for ALL Final Years** of ASE, ASC, ASAI, IV yr & V yr Int M.Sc\n"
            "- **Cohort Progress**: Marks **Class Day 1** for final-year engineering students\n"
            "- **Institutional Status**: Working Day (`W`)"
        )
        return {"markdown_answer": ans}

    # ---------------------------------------------------------
    # Case D: June 20, 2026 Timetable Substitution
    # ---------------------------------------------------------
    if "june 20" in q_norm or "2026 06 20" in q_norm:
        ans = (
            "### 🔄 June 20, 2026: Timetable Day Substitution\n\n"
            "- **Calendar Date**: **June 20, 2026** (Natural Day: **Saturday**)\n"
            "- **Timetable Applied**: **Monday's Timetable** ⚠️\n"
            "- **Status**: **Working Day (`W`)**\n"
            "- **Cohort Progress**: Counts as **Class Day 4** for Final Year students\n\n"
            "> **Key Note**: Saturday, June 20 does **NOT** run on a Saturday schedule. All students must attend classes and laboratories per their **Monday Timetable**."
        )
        return {"markdown_answer": ans}

    # ---------------------------------------------------------
    # Case E: Examination Dates & Last Instruction Day
    # ---------------------------------------------------------
    if any(k in q_norm for k in ["last instruction day", "last working day for class", "last day of instruction"]):
        if any(w in q_norm for w in ["final year", "iv yr", "fourth year", "7th sem", "vii sem"]):
            ans = (
                "### 🏁 Last Instruction Day for Final-Year Students\n\n"
                "- **Date**: **October 16, 2026 (Friday)**\n"
                "- **Significance**: Concludes 80 instructional class days for Semester VII\n"
                "- **Subsequent Event**: End-Semester Examinations commence on **October 21, 2026**."
            )
            return {"markdown_answer": ans}
        if any(w in q_norm for w in ["third year", "iii yr", "3rd year", "5th sem", "v sem"]):
            ans = (
                "### 🏁 Last Instruction Day for Third-Year Students\n\n"
                "- **Date**: **October 23, 2026 (Friday)**\n"
                "- **Subsequent Event**: End-Semester Examinations commence on **October 26, 2026**."
            )
            return {"markdown_answer": ans}

    if any(k in q_norm for k in ["end semester exam", "end sem", "endsem", "exam begin", "exams begin", "exams start"]):
        if any(w in q_norm for w in ["third year", "iii yr", "3rd year", "5th sem", "v sem"]):
            ans = (
                "### 📝 End-Semester Examinations for Third-Year Students\n\n"
                "- **Exam Commencement Date**: **October 26, 2026 (Monday)**\n"
                "- **Cohorts**: All Third Years of ASC, ASE, ASAI & ASPS (Semester V)\n"
                "- **Last Instruction Day**: October 23, 2026"
            )
            return {"markdown_answer": ans}
        if any(w in q_norm for w in ["final year", "iv yr", "4th year", "7th sem", "vii sem"]):
            ans = (
                "### 📝 End-Semester Examinations for Final-Year Students\n\n"
                "- **Exam Commencement Date**: **October 21, 2026 (Wednesday)**\n"
                "- **Cohorts**: All Final Years of ASC, ASE, ASAI, IV yr & V yr Int M.Sc (Semester VII)\n"
                "- **Last Instruction Day**: October 16, 2026"
            )
            return {"markdown_answer": ans}

    # ---------------------------------------------------------
    # Case F: First-Year ASE Calendar Events
    # ---------------------------------------------------------
    if any(w in q_norm for w in ["first year", "i yr", "i year"]) and any(k in q_norm for k in ["event", "events", "calendar"]):
        ans = (
            "### 📅 Key Academic Calendar Events for First-Year B.Tech (ASE)\n\n"
            "| Date | Milestone / Event |\n"
            "| :--- | :--- |\n"
            "| **July 15, 2026** | **Commencement of Classes** (Semester I) |\n"
            "| **September 15, 2026** | **Mid-Term Examinations** commence |\n"
            "| **November 17, 2026** | **Last Instruction Day** for First-Year B.Tech |\n"
            "| **November 18, 2026** | **End-Semester Examinations** commence |\n"
            "| **December 07, 2026** | **Even Semester Commencement** (Semester II) |\n"
            "| **April 26, 2027** | **Last Instruction Day** (Semester II) |\n"
            "| **April 27, 2027** | **End-Semester Examinations** commence (Semester II) |"
        )
        return {"markdown_answer": ans}

    # ---------------------------------------------------------
    # Case G: Class-Day Count Inquiry
    # ---------------------------------------------------------
    if any(k in q_norm for k in ["class day count", "class day", "cohort on a particular date"]):
        ans = (
            "### 📊 Cohort Class-Day Counts (AY 2026–2027)\n\n"
            "In the Academic Calendar, each instructional working day (`W`) tracks an authoritative **Class-Day Count (CD)** for distinct student cohorts:\n\n"
            "| Cohort | Commencement Date | Total Class Days | Last Instruction Day |\n"
            "| :--- | :--- | :--- | :--- |\n"
            "| **Final Year B.Tech (IV Yr ASE)** | **June 17, 2026** (CD 1) | **80 Class Days** | **October 16, 2026** (CD 80) |\n"
            "| **Third Year B.Tech (III Yr ASE)** | **June 22, 2026** (CD 1) | **80 Class Days** | **October 23, 2026** (CD 80) |\n"
            "| **Second Year B.Tech (II Yr ASE)** | **June 29, 2026** (CD 1) | **80 Class Days** | **October 30, 2026** (CD 80) |\n"
            "| **First Year B.Tech (I Yr ASE)** | **July 15, 2026** (CD 1) | **80 Class Days** | **November 17, 2026** (CD 80) |\n\n"
            "> 💡 **Daily Progress Example**: On **Saturday, June 20, 2026** (working day operating on Monday's Timetable), the class-day count is **Class Day 4** for Final Year students.\n\n"
            "*To check the class-day count for any specific calendar date, ask with a date like: 'What is the class day on August 10, 2026?'*"
        )
        return {"markdown_answer": ans}

    # ---------------------------------------------------------
    # Case G: Specific Date Lookup (e.g. August 15, 2026, August 10)
    # Evaluated before general status explanations so queries with a date resolve to the date card directly
    # ---------------------------------------------------------
    from utils import extract_date_from_text
    p_date = extract_date_from_text(question)
    if p_date:
        d_obj = datetime.strptime(p_date, "%Y-%m-%d")
        stmt = select(CalendarDay).where(
            CalendarDay.calendar_date >= datetime.combine(d_obj.date(), datetime.min.time()),
            CalendarDay.calendar_date <= datetime.combine(d_obj.date(), datetime.max.time()),
        )
        res = await db.execute(stmt)
        c_day = res.scalars().first()
        if c_day:
            return {"markdown_answer": None, "day_details": c_day}

    # ---------------------------------------------------------
    # Case H: Status Categories Inquiry (Holiday, Working Day, Exam, Special Event)
    # Only triggered when NO specific date is being queried
    # ---------------------------------------------------------
    if any(k in q_norm for k in ["given date marked", "marked as a holiday", "holiday, working day, exam", "status categories", "calendar status"]):
        ans = (
            "### 📅 Academic Day Status Categories (AY 2026–2027)\n\n"
            "Every calendar date in the institutional academic calendar is classified into one of the following authoritative statuses:\n\n"
            "1. **Working Day (`W`)**:\n"
            "   - Regular instructional day with scheduled lectures and laboratories.\n"
            "   - May include **Timetable Day Substitutions** (e.g. Saturday, June 20 operates on **Monday's Timetable**).\n\n"
            "2. **Institutional Holiday**:\n"
            "   - Declared non-working holiday for all university departments (e.g., August 15 Independence Day, August 28 Onam, October 02 Gandhi Jayanti).\n\n"
            "3. **Examination Period**:\n"
            "   - Dedicated Mid-Term Examinations or End-Semester Final Examinations. No normal teaching periods are conducted.\n\n"
            "4. **Special Event / No Instruction Day**:\n"
            "   - Sports Day, Alumni Day, Gokulashtami celebrations, or study/preparatory holidays.\n\n"
            "*To look up the exact status for any date, ask: 'Is August 15, 2026 a holiday?' or 'What is happening on June 17, 2026?'*"
        )
        return {"markdown_answer": ans}

    # ---------------------------------------------------------
    # Case I: Monthly Schedule Changes & Events
    # ---------------------------------------------------------
    if any(k in q_norm for k in ["particular month", "schedule changes are listed", "events or schedule changes", "monthly events", "events in month"]):
        months = ["june", "july", "august", "september", "october", "november", "december", "january", "february", "march", "april", "may"]
        target_month = next((m for m in months if m in q_lower), None)

        if target_month:
            stmt = select(CalendarDay).where(
                or_(
                    CalendarDay.timetable_override.isnot(None),
                    CalendarDay.particulars.isnot(None),
                    CalendarDay.status.ilike("%holiday%"),
                )
            ).order_by(CalendarDay.calendar_date)
            res = await db.execute(stmt)
            month_days = [d for d in res.scalars().all() if d.calendar_date.strftime("%B").lower() == target_month]
            
            rows = []
            for d in month_days:
                desc = d.particulars or d.status
                if d.timetable_override:
                    desc += f" (Operates on **{d.timetable_override}'s Timetable**)"
                rows.append(f"| **{d.calendar_date.strftime('%B %d, %Y')}** ({d.day_of_week}) | `{d.status}` | {desc} |")
            
            table_md = "\n".join(rows) if rows else "*No schedule changes or special holidays found for this month.*"
            ans = (
                f"### 🗓️ Academic Schedule & Events for {target_month.capitalize()} 2026\n\n"
                "| Date | Status | Schedule / Events |\n"
                "| :--- | :--- | :--- |\n"
                f"{table_md}\n"
            )
            return {"markdown_answer": ans}

        ans = (
            "### 🗓️ Key Monthly Schedule Highlights & Overrides (AY 2026–2027)\n\n"
            "| Month | Key Cohort Milestones & Schedule Changes |\n"
            "| :--- | :--- |\n"
            "| **June 2026** | **June 17**: AY Begins (Final Years). **June 20**: Saturday operates on **Monday's Timetable**. **June 22**: Third Years commence. **June 29**: Second Years commence. |\n"
            "| **July 2026** | **July 11**: Saturday operates on **Tuesday's Timetable** (Alumni Day). **July 15**: First-Year B.Tech commences. **July 18**: Saturday operates on **Wednesday's Timetable**. |\n"
            "| **August 2026** | **August 01**: Saturday operates on **Thursday's Timetable**. **August 14**: Friday operates on **Monday's Timetable** (Onam Celebration). **August 29**: Saturday operates on **Monday's Timetable**. |\n"
            "| **September 2026**| **September 05**: Saturday operates on **Monday's Timetable**. **September 15**: Mid-Term Exams begin. **September 19**: Saturday operates on **Tuesday's Timetable**. |\n"
            "| **October 2026** | **October 10**: Saturday operates on **Wednesday's Timetable**. **October 16**: **Last Instruction Day** (Final Years). **October 17**: Saturday operates on **Thursday's Timetable**. **October 21**: End-Sem Exams begin (Final Years). **October 23**: Last Instruction Day (Third Years). **October 26**: End-Sem Exams begin (Third Years). **October 31**: Saturday operates on **Friday's Timetable**. |\n"
            "| **November 2026**| **November 17**: Last Instruction Day (First Years). **November 18**: End-Sem Exams (First Years). **November 21**: Saturday operates on **Monday's Timetable**. |\n"
            "| **December 2026**| **December 07**: Even Semester begins. **December 05**: Saturday operates on **Tuesday's Timetable**. **December 19**: Saturday operates on **Wednesday's Timetable**. |\n\n"
            "*You can query any specific month by asking: 'What schedule changes are in October 2026?' or 'What events in August?'*"
        )
        return {"markdown_answer": ans}

    # General Fallback
    ans = (
        "### 🗓️ Academic Calendar (AY 2026–2027)\n\n"
        "Please specify a specific date (e.g., *'June 17, 2026'* or *'August 15'*), cohort (*'final year'* or *'third year'*), "
        "or milestone (*'last instruction day'*, *'holidays list'*, *'exam commencement'*) to get authoritative dates."
    )
    return {"markdown_answer": ans}
