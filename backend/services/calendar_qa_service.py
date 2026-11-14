"""Calendar question answering restored from asofNov13."""
import re
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from typing import Dict, Any, List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_
from backend.database import CalendarDay, CalendarDayCohort, AcademicEvent

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
    if re.search(r"\b(end\s*sem|endsemester|end\s*semester|final exams?)\b", q): return "endsem"
    if re.search(r"\b(last instruction|last working|last class)\b", q): return "last_instruction"
    if re.search(r"\b(commencement|commence|start|begin)\b", q): return "commencement"
    return None


def _semester_cohort_terms(semester: int) -> tuple[str, ...]:
    if semester <= 2: return ("first year", "i year", "i yr", "1st year")
    if semester <= 4: return ("second year", "ii year", "ii yr", "2nd year")
    if semester <= 6: return ("third year", "iii year", "iii yr", "3rd year", "higher semester")
    return ("final year", "fourth year", "iv year", "iv yr", "4th year", "higher semester")


def _classify_calendar_request(query: str) -> str | None:
    from backend.services.date_resolution import extract_date_from_text
    # Specific date or relative day lookup (e.g. tomorrow, next saturday, august 10)
    # must resolve to an individual day details card, not a generic holiday list
    if extract_date_from_text(query) is not None:
        return "date_lookup"

    q = re.sub(r"[^a-z0-9]+", " ", query.lower()).strip()
    asks_next = bool(re.search(r"\b(next|upcoming|nearest|coming|following)\b", q))
    asks_all = bool(re.search(r"\b(all|every|each|complete|entire|full|list)\b", q))
    asks_holidays = bool(re.search(r"\bholidays?\b", q))
    asks_saturdays = bool(re.search(r"\bsaturdays?\b", q))
    if "timetable" in q and re.search(r"\b(substitutions?|overrides?)\b", q):
        return "timetable_substitutions"
    festivals = _festival_topic_tokens(q)
    # 1. Festival inquiries (e.g. when is Onam?, dates for Diwali, when is Independence Day?, upcoming festival holidays)
    if "festival" in q and asks_next:
        return "next_festival_holiday"
    if festivals:
        if asks_next and not any(k in q for k in ["when", "dates", "onam", "diwali", "deepavali", "independence"]):
            return "next_festival_holiday"
        return "festival_holidays"

    # 2. General holidays
    if asks_holidays:
        return "next_holiday" if asks_next else ("holiday_list" if asks_all or "holidays" in q or "holiday" in q else None)

    # 3. Working Saturdays
    if asks_saturdays and re.search(r"\b(working|instructional|class)\b", q):
        if asks_next: return "next_working_saturday"
        if asks_all or "saturdays" in q: return "working_saturdays"

    if _extract_calendar_month(query) and re.search(r"\b(events?|calendar|scheduled|happening|what.s on)\b", q):
        return "month_events"
    if _extract_semester_number(q) is not None and _calendar_milestone_kind(q): return "milestone_lookup"
    return None


def _festival_match_names(query: str) -> list[str]:
    q = re.sub(r"[^a-z0-9]+", " ", query.lower()).strip()
    if "first onam" in q:
        return ["first onam"]
    if "thiruvonam" in q:
        return ["thiruvonam"]
    return _festival_topic_tokens(query)


def _extract_calendar_year(query: str) -> int | None:
    if re.search(r"\b20\d{2}\s*[-–/]\s*20\d{2}\b", query):
        return None
    match = re.search(r"\b(20\d{2})\b", query)
    return int(match.group(1)) if match else None

def _calendar_reference_date(question: str) -> date:
    """Use an explicit query date, or today's date in the campus timezone."""
    from backend.services.date_resolution import extract_date_from_text
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


def _named_holidays(
    days: list[CalendarDay],
    academic_events: list[AcademicEvent] | None = None,
    include_weekends: bool = False,
) -> list[dict[str, str]]:
    """Combine named holiday day rows and indexed event rows, deduplicated by date."""
    holidays_by_date: dict[str, dict[str, str]] = {}

    def add(iso: str, day_name: str, raw_name: str) -> None:
        name = re.sub(r"(?i)\bholiday\b", " ", raw_name or "")
        name = re.sub(r"[-–:]+", " ", name)
        name = re.sub(r"\s+", " ", name).strip(" ,.-–")
        if not name or name.casefold() in {"holiday", "institutional", "sunday", "saturday"}:
            name = "Sunday Holiday" if day_name == "Sun" else "Saturday Holiday" if day_name == "Sat" else "Holiday"
        parsed = date.fromisoformat(iso)
        entry = {"date_iso": iso, "date": parsed.strftime("%B %d, %Y"), "day": day_name, "name": name}
        if iso not in holidays_by_date:
            holidays_by_date[iso] = entry
        else:
            existing_name = holidays_by_date[iso]["name"].lower()
            if existing_name in {"sunday holiday", "saturday holiday", "holiday", "sunday", "saturday"}:
                holidays_by_date[iso] = entry

    for d in days:
        if include_weekends:
            if _is_day_a_holiday(d):
                add(d.calendar_date.date().isoformat(), d.day_of_week, _get_holiday_name(d))
        else:
            status, particulars = (d.status or "").strip(), (d.particulars or "").strip()
            if re.search(r"\bholiday\b", status + " " + particulars, re.I):
                add(d.calendar_date.date().isoformat(), d.day_of_week, particulars or status)

    for e in academic_events or []:
        if (getattr(e, "event_category", None) or "").lower() == "holiday":
            add(e.start_date.date().isoformat(), e.start_date.strftime("%a"), e.name)

    return sorted(holidays_by_date.values(), key=lambda h: h["date_iso"])


def _is_day_a_holiday(d: CalendarDay) -> bool:
    """Returns True if the calendar day is a declared holiday or a non-working weekend."""
    status = (d.status or "").strip()
    particulars = (d.particulars or "").strip()
    if status == "W" or d.timetable_override:
        return False
    if re.search(r"\bholiday\b", status + " " + particulars, re.I):
        return True
    if d.day_of_week == "Sun" and status != "W":
        return True
    if d.day_of_week == "Sat" and status != "W":
        return True
    return False


def _get_holiday_name(d: CalendarDay) -> str:
    """Returns the descriptive or occasion name of a holiday day."""
    particulars = (d.particulars or "").strip()
    status = (d.status or "").strip()
    name = particulars if particulars and particulars.lower() not in {"holiday", "institutional holiday", "sunday", "saturday"} else status
    clean_name = re.sub(r"(?i)\bholiday\b", " ", name)
    clean_name = re.sub(r"[-–:]+", " ", clean_name)
    clean_name = re.sub(r"\s+", " ", clean_name).strip(" ,.-–")
    if clean_name and clean_name.lower() not in {"institutional", "holiday", "w"}:
        return clean_name
    if d.day_of_week == "Sun":
        return "Sunday Holiday"
    if d.day_of_week == "Sat":
        return "Saturday Holiday"
    return "Holiday"


def _filter_cohorts_for_scope(
    cohort_rows: list,
    semester: str | None = None,
    question: str | None = None,
) -> list[dict[str, Any]]:
    """
    Filters calendar day cohort class-day counts to the student's cohort/year,
    or the specific cohort requested in the query. Falls back to all if unmatched.
    """
    if not cohort_rows:
        return []

    all_cohorts = [
        {"cohort": cr.cohort, "class_day_count": cr.class_day_number}
        if hasattr(cr, "cohort") else cr
        for cr in cohort_rows
    ]
    q = (question or "").lower()

    # 1. Did the user query explicitly ask about a specific cohort?
    if re.search(r"\b(first|1st|i)\s*(?:year|yr)\b", q) or re.search(r"\bsem(?:ester)?\s*[12]\b", q):
        matched = [c for c in all_cohorts if "i year" in c["cohort"].lower() and "aisl" not in c["cohort"].lower() and "msc" not in c["cohort"].lower()]
        if matched: return matched
    if re.search(r"\b(second|2nd|ii)\s*(?:year|yr)\b", q) or re.search(r"\bsem(?:ester)?\s*[34]\b", q):
        matched = [c for c in all_cohorts if "ii year" in c["cohort"].lower()]
        if matched: return matched
    if re.search(r"\b(third|3rd|iii)\s*(?:year|yr)\b", q) or re.search(r"\bsem(?:ester)?\s*[56]\b", q):
        matched = [c for c in all_cohorts if "iii yr" in c["cohort"].lower()]
        if matched: return matched
    if re.search(r"\b(final|fourth|4th|iv)\s*(?:year|yr)\b", q) or re.search(r"\bsem(?:ester)?\s*[78]\b", q):
        matched = [c for c in all_cohorts if "iv yr" in c["cohort"].lower()]
        if matched: return matched

    # 2. Use student's profile semester / study year
    sem_num = None
    if semester:
        sem_num = _extract_semester_number(f"semester {semester}")
    if sem_num is not None:
        year_num = (sem_num + 1) // 2
        year_cohort_map = {
            4: "iv yr",
            3: "iii yr",
            2: "ii year",
            1: "i year",
        }
        target_token = year_cohort_map.get(year_num)
        if target_token:
            matched = [c for c in all_cohorts if target_token in c["cohort"].lower()]
            # If the student belongs to a specific study year, return their matching cohort chip.
            # If that cohort has no class day scheduled on this date, return empty list (do not show another year).
            return matched

    return all_cohorts


def _event_matches_festival(name: str, aliases: list[str]) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()
    return any(re.sub(r"[^a-z0-9]+", " ", alias.lower()).strip() in normalized for alias in aliases)


def _event_matches_milestone(event: AcademicEvent, kind: str, semester: int) -> bool:
    name = (event.name or "").lower().replace("-", " ")
    cohort_terms = _semester_cohort_terms(semester)
    cohort_match = any(re.search(r"(?<![a-z])" + re.escape(term) + r"s?(?![a-z])", name) for term in cohort_terms)
    if "higher semester" in name and semester >= 3: cohort_match = True
    cohort_markers = ("first year", "i year", "i yr", "second year", "ii year", "ii yr", "third year", "iii year", "iii yr", "final year", "fourth year", "iv year", "iv yr", "higher semester")
    has_specific_cohort = any(marker in name for marker in cohort_markers)
    if not cohort_match and (has_specific_cohort or not re.search(r"\b(all|all the)\b", name)): return False
    if kind in {"reopen", "commencement"}:
        if re.search(r"\b(end\s*sem|end semester|mid\s*term|midterm|exam)\b", name):
            return False
        return any(term in name for term in ("commencement", "reopen", "re opening"))
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



async def answer_calendar_query(
    question: str,
    db: AsyncSession,
    semester: str | None = None,
) -> Dict[str, Any]:
    """
    Answers academic calendar, holiday, milestone, and exam date queries.
    """
    q_lower = question.lower()
    q_norm = q_lower.replace("-", " ").replace("–", " ")

    # Resolve an explicitly requested semester or year before general milestone summaries.
    requested_semester = _extract_semester_number(question)
    explicit_year = None
    for pattern, number in ((r"\b(first|1st)\s+years?\b", 1), (r"\b(second|2nd)\s+years?\b", 2), (r"\b(third|3rd)\s+years?\b", 3), (r"\b(final|fourth|4th)\s+years?\b", 4)):
        if re.search(pattern, q_norm):
            explicit_year = number
            break
    milestone = _calendar_milestone_kind(q_norm)
    if (requested_semester or explicit_year) and milestone and not re.search(r"\b(class day|class days|instructional class days)\b", q_norm):
        scope_semester = requested_semester or (explicit_year * 2 - 1)
        records = (await db.execute(select(AcademicEvent).order_by(AcademicEvent.start_date))).scalars().all()
        matches = [e for e in records if _event_matches_milestone(e, milestone, scope_semester)]
        if re.search(r"\bb\s*\.?\s*tech\b", q_norm) and scope_semester <= 2:
            matches = [e for e in matches if "b.tech" in e.name.lower() or re.search(r"\base\b", e.name.lower()) or not any(x in e.name.lower() for x in ("msc", "m sc", "aisl"))]
        if requested_semester:
            if milestone in {"commencement", "reopen"}:
                matches = [e for e in matches if ("even sem" in e.name.lower()) == (requested_semester % 2 == 0)]
            else:
                matches = [e for e in matches if (e.start_date.month <= 6) == (requested_semester % 2 == 0)]
        items = [{"date": e.start_date.strftime("%B %d, %Y"), "name": e.name, "category": e.event_category} for e in matches]
        if items:
            lines = [f"- **{e['date']}** — {e['name']}" for e in items]
            answer = "### Academic calendar milestones\n\n" + "\n".join(lines)
        else:
            answer = "No matching milestone was found for that cohort in the uploaded academic calendar."
        return {"calendar_mode": "milestone_lookup", "markdown_answer": answer, "events": items}

    if re.search(r"\bacademic year\b", q_norm) and re.search(r"\b(begin|begins|start|starts|commence)\b", q_norm):
        rows = (await db.execute(select(AcademicEvent).order_by(AcademicEvent.start_date))).scalars().all()
        starts = [e for e in rows if "commencement" in (e.name or "").lower()]
        years = re.search(r"\b(20\d{2})\s*[-–/]\s*(20\d{2})\b", question)
        if years:
            starts = [e for e in starts if e.start_date.year == int(years.group(1))]
        if starts:
            e = starts[0]
            answer = f"The earliest commencement listed in the uploaded calendar is **{e.start_date.strftime('%B %d, %Y')}** — {e.name}. Individual cohorts can start on different dates."
        else:
            answer = "No academic-year commencement date was found in the uploaded calendar."
        return {"calendar_mode": "milestone_lookup", "markdown_answer": answer}

    if re.search(r"\b(class days|class day count|instructional class days)\b", q_norm) and re.search(r"\b(completed|total|count|how many)\b", q_norm):
        from backend.services.date_resolution import extract_date_from_text
        resolved = extract_date_from_text(question)
        selected_semester = requested_semester or _extract_semester_number(f"semester {semester}") if semester else requested_semester
        if not selected_semester and not explicit_year:
            return {"calendar_mode": "milestone_lookup", "markdown_answer": "Please specify your year or semester so I can find the class-day count."}
        rows = (await db.execute(select(CalendarDay, CalendarDayCohort).join(CalendarDayCohort, CalendarDayCohort.calendar_day_id == CalendarDay.id).order_by(CalendarDay.calendar_date.desc()))).all()
        if resolved:
            target = date.fromisoformat(resolved)
            if not re.search(r"\b20\d{2}\b", question):
                alternatives = sorted({d.calendar_date.date() for d, c in rows if d.calendar_date.month == target.month and d.calendar_date.day == target.day})
                if len(alternatives) == 1:
                    target = alternatives[0]
            rows = [(d,c) for d,c in rows if d.calendar_date.date() <= target]
        if requested_semester and not resolved:
            odd = requested_semester % 2 == 1
            milestones = (await db.execute(select(AcademicEvent).order_by(AcademicEvent.start_date))).scalars().all()
            endings = [e for e in milestones if _event_matches_milestone(e, "last_instruction", requested_semester) and (e.start_date.month >= 6) == odd]
            if not endings:
                return {"calendar_mode": "milestone_lookup", "markdown_answer": "The calendar does not provide a matching last instruction day for this semester, so I cannot determine its total."}
            end_date = endings[-1].start_date.date()
            rows = [(d,c) for d,c in rows if d.calendar_date.date() <= end_date]
        for d, c in rows:
            target_year = explicit_year or ((selected_semester + 1) // 2 if selected_semester else None)
            label = (c.cohort or "").lower().strip()
            cohort_pattern = {1: r"^i\s*(year|yr)\b", 2: r"^ii\s*(year|yr)\b", 3: r"^iii\s*(year|yr)\b", 4: r"^iv\s*(year|yr)\b"}.get(target_year, r"(?!)")
            if re.search(cohort_pattern, label) and not (target_year == 1 and ("msc" in label or "aisl" in label)) and c.class_day_number is not None:
                answer = f"The calendar records **{c.class_day_number} class days** for {c.cohort}, as of **{d.calendar_date.strftime('%B %d, %Y')}**."
                return {"calendar_mode": "milestone_lookup", "markdown_answer": answer}
        return {"calendar_mode": "milestone_lookup", "markdown_answer": "No matching class-day count was found in the uploaded calendar."}

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

    # 0d. Specific Date Lookup (e.g. August 10 2026, tomorrow, next Saturday, August 16)
    # Evaluated before general status explanations so queries with a date resolve to the date card directly
    from backend.services.date_resolution import extract_date_from_text
    p_date = extract_date_from_text(question)
    if p_date:
        d_obj = datetime.strptime(p_date, "%Y-%m-%d")
        stmt = select(CalendarDay).where(
            CalendarDay.calendar_date >= datetime.combine(d_obj.date(), datetime.min.time()),
            CalendarDay.calendar_date <= datetime.combine(d_obj.date(), datetime.max.time()),
        )
        res = await db.execute(stmt)
        c_day = res.scalars().first()
        if not re.search(r"\b20\d{2}\b", question):
            all_days = (await db.execute(select(CalendarDay))).scalars().all()
            candidates = [d for d in all_days if d.calendar_date.month == d_obj.month and d.calendar_date.day == d_obj.day]
            weekday = re.search(r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", question, re.I)
            if weekday:
                candidates = [d for d in candidates if d.calendar_date.strftime("%A").lower() == weekday.group(1).lower()]
            if len(candidates) == 1:
                c_day = candidates[0]
        if c_day:
            cohorts_stmt = select(CalendarDayCohort).where(CalendarDayCohort.calendar_day_id == c_day.id)
            coh_res = await db.execute(cohorts_stmt)
            cohort_rows = coh_res.scalars().all()
            is_hol = _is_day_a_holiday(c_day)
            day_details = {
                "date": c_day.calendar_date.strftime("%Y-%m-%d"),
                "day_of_week": c_day.day_of_week,
                "status": c_day.status,
                "particulars": c_day.particulars,
                "timetable_override": c_day.timetable_override,
                "is_holiday": is_hol,
                "cohorts": _filter_cohorts_for_scope(cohort_rows, semester=semester, question=question),
            }
            return {
                "calendar_mode": "date_lookup",
                "markdown_answer": None,
                "day_details": day_details,
                "events": [],
                "overrides": [],
            }

    # Calendar list and "next" requests use structured calendar rows and return
    # only the requested result type.
    mode = _classify_calendar_request(q_norm)
    if mode == "timetable_substitutions":
        days = (await db.execute(select(CalendarDay).order_by(CalendarDay.calendar_date))).scalars().all()
        selected = [d for d in days if d.timetable_override]
        requested_year = _extract_calendar_year(question)
        if requested_year:
            selected = [d for d in selected if d.calendar_date.year == requested_year]
        rows = [f"- **{d.calendar_date.strftime('%B %d, %Y (%A)')}** — {d.timetable_override} timetable" for d in selected]
        return {"calendar_mode": "timetable_substitutions", "markdown_answer": "### Timetable substitutions\n\n" + "\n".join(rows) if rows else "No timetable substitutions were found in the uploaded calendar."}
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
            holidays = _named_holidays(calendar_days, academic_events, include_weekends=True)
            if mode in {"festival_holidays", "next_festival_holiday", "festival_event"}:
                holidays = [h for h in holidays if h["name"].lower() not in {"sunday holiday", "saturday holiday", "holiday", "sunday", "saturday", "weekend"}]
            requested_month = _extract_calendar_month(question)
            req_year = _extract_calendar_year(question)
            if requested_month:
                year, month = requested_month
                holidays = [
                    h for h in holidays
                    if date.fromisoformat(h["date_iso"]).year == year
                    and date.fromisoformat(h["date_iso"]).month == month
                ]
            elif req_year:
                holidays = [
                    h for h in holidays
                    if date.fromisoformat(h["date_iso"]).year == req_year
                ]

            if mode in {"festival_holidays", "next_festival_holiday", "festival_event"}:
                aliases = _festival_match_names(question)
                if aliases:
                    holidays = [h for h in holidays if _event_matches_festival(h["name"], aliases)]
                    if mode == "next_festival_holiday":
                        holidays = [h for h in holidays if h["date_iso"] > reference_date.isoformat()][:1]
                else:
                    holidays = [
                        h for h in holidays
                        if h["date_iso"] > reference_date.isoformat()
                        and h["name"].lower() not in {"sunday holiday", "saturday holiday", "holiday", "sunday", "saturday", "weekend"}
                    ]
                events = [{"date": h["date"], "date_iso": h["date_iso"], "name": h["name"], "day": h["day"], "category": "Holiday"} for h in holidays]
                if events:
                    listing = "\n".join(f"- **{h['date']} ({h['day']})** — {h['name']}" for h in events)
                    if not aliases:
                        title = "Upcoming Festival Holidays"
                    elif mode == "next_festival_holiday":
                        title = f"Upcoming Holiday dates for {aliases[0].title()}"
                    else:
                        title = f"Holiday dates for {aliases[0].title()}"
                    answer = f"### {title}\n\n{listing}"
                else:
                    answer = f"No matching holiday dates for {aliases[0] if aliases else 'upcoming festivals'} were found in the indexed academic calendar."
                return {"calendar_mode": mode, "markdown_answer": answer, "events": events}

            if mode == "next_holiday":
                future = [h for h in holidays if h["date_iso"] > reference_date.isoformat()]
                if future:
                    h = future[0]
                    answer = f"### Next holiday\n\n**{h['name']}** — {h['date']} ({h['day']})."
                    events = [{"date": h["date"], "name": h["name"], "day": h["day"], "category": "Holiday"}]
                else:
                    answer = f"No further holidays are listed after {reference_date.strftime('%B %d, %Y')} in the available academic calendar."
                    events = []
                return {"calendar_mode": mode, "markdown_answer": answer, "events": events}

            events = [{"date": h["date"], "date_iso": h["date_iso"], "name": h["name"], "day": h["day"], "category": "Holiday"} for h in holidays]
            table = "\n".join(f"| **{h['date']}** | {h['day']} | **{h['name']}** |" for h in events) or "| — | — | No holidays found |"
            if req_year and not requested_month:
                answer = f"### Official Holidays ({req_year})\n\n| Date | Day | Holiday |\n| :--- | :--- | :--- |\n" + table
            elif requested_month:
                from datetime import datetime as _dt
                m_label = _dt(year, month, 1).strftime("%B %Y")
                answer = f"### Official Holidays ({m_label})\n\n| Date | Day | Holiday |\n| :--- | :--- | :--- |\n" + table
            else:
                answer = "### Official Holidays (AY 2026–2027)\n\n| Date | Day | Holiday |\n| :--- | :--- | :--- |\n" + table
            return {"calendar_mode": mode, "markdown_answer": answer, "events": events}

        saturdays = [d for d in calendar_days if _is_working_saturday(d)]
        req_year = _extract_calendar_year(question)
        if req_year:
            saturdays = [d for d in saturdays if d.calendar_date.year == req_year]
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
            if req_year:
                answer = f"### Working Saturdays ({req_year})\n\n| Date | Day | Timetable followed |\n| :--- | :--- | :--- |\n" + table
            else:
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
            "### 🏖️ Official Holidays (Academic Year 2026–2027)\n\n"
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
            "- **Cohort Progress**: Counts as **Class Day 4** for Final Year students"
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
    from backend.services.date_resolution import extract_date_from_text
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
            "2. **Holiday**:\n"
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
