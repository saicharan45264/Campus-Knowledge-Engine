"""
calendar_parser.py — Parser for Academic Calendar PDFs (e.g. Academic-Calendar-AY-2026-2027-2.pdf).
Extracts:
1. Every calendar day (Date, Day of Week, Status: Working/Holiday, Particulars, Month)
2. Crucial timetable overrides (e.g. Saturday with 'Monday's Timetable')
3. Cohort-specific class day counters (IV yr, III Yr, II year, I Year, AISL)
4. Key academic events (commencements, exam periods, last instruction days, holidays)
"""
import re
from datetime import date
from typing import Dict, List, Any, Optional
import pdfplumber


MONTH_MAP = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}

COHORT_HEADERS = [
    "IV yr", "III Yr", "II year", "I Yr Int MSc", "I Year", "AISL I Yr"
]


def _parse_month_header(text: str) -> tuple[Optional[int], Optional[int], Optional[str]]:
    m = re.search(r"([A-Za-z]{3})-(\d{2})", text or "")
    if m:
        mon_str = m.group(1).upper()
        yr = 2000 + int(m.group(2))
        return MONTH_MAP.get(mon_str), yr, f"{m.group(1).capitalize()}-20{m.group(2)}"
    return None, None, None


def _detect_timetable_override(text: str) -> Optional[str]:
    if not text:
        return None
    for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]:
        if re.search(rf"{day}'s\b", text, re.I):
            return day
    return None


def parse_academic_calendar_pdf(pdf_path: str) -> Dict[str, Any]:
    """
    Parses the 13-page academic calendar PDF.
    Returns:
    - 'days': List of day-level records
    - 'events': List of extracted academic milestones and holidays
    - 'academic_year': Extracted academic year (e.g. '2026-2027')
    """
    days: List[Dict[str, Any]] = []
    events: List[Dict[str, Any]] = []
    academic_year = "2026-2027"

    with pdfplumber.open(pdf_path) as pdf:
        for page_idx, page in enumerate(pdf.pages):
            tables = page.extract_tables()
            if not tables:
                continue
            table = tables[0]

            # Detect academic year and month header
            mon_num, year, month_label = None, None, None
            for row in table[:4]:
                first_cell = row[0] or ""
                ay_match = re.search(r"AY\s*(\d{4}\s*-\s*\d{4})", first_cell)
                if ay_match:
                    academic_year = ay_match.group(1).replace(" ", "")

                m, y, l = _parse_month_header(first_cell)
                if m:
                    mon_num, year, month_label = m, y, l
                    break

            if not mon_num or not year:
                continue

            for row in table:
                if not row[0] or not row[0].strip().isdigit():
                    continue

                day_num = int(row[0].strip())
                weekday = (row[1] or "").strip()
                status = (row[2] or "").strip()
                particulars = (row[-1] or "").strip()

                # In holiday rows, particulars might be in the middle columns
                if not particulars or status.lower() == "holiday":
                    for c in row[3:]:
                        if c and ("Holiday" in c or "Break" in c or "Commencement" in c):
                            particulars = c.strip()
                            if "Holiday" in particulars and not status:
                                status = "Holiday"
                            break

                override = _detect_timetable_override(particulars)

                cohort_cds: Dict[str, int] = {}
                for c_idx, c_name in enumerate(COHORT_HEADERS, start=3):
                    if c_idx < len(row) and row[c_idx] and "CD" in row[c_idx]:
                        cd_m = re.search(r"CD\s*(\d+)", row[c_idx])
                        if cd_m:
                            cohort_cds[c_name] = int(cd_m.group(1))

                cal_date = date(year, mon_num, day_num)
                day_record = {
                    "calendar_date": cal_date.isoformat(),
                    "day_of_week": weekday,
                    "status": "Holiday" if (status == "Holiday" or "Holiday" in particulars) else (status or "W"),
                    "particulars": particulars or None,
                    "timetable_override": override,
                    "academic_year": academic_year,
                    "month_name": month_label,
                    "cohort_class_days": cohort_cds,
                }
                days.append(day_record)

                # Event extraction
                if particulars:
                    cat = "milestone"
                    if "Holiday" in particulars or status == "Holiday":
                        cat = "holiday"
                    elif "Exam" in particulars or "Mid Term" in particulars or "End Sem" in particulars:
                        cat = "exam"
                    elif "Timetable" in particulars:
                        cat = "timetable_override"
                    elif "Celebration" in particulars or "Day" in particulars or "Anokha" in particulars:
                        cat = "celebration"

                    events.append({
                        "name": particulars.replace("\n", " ").strip(),
                        "start_date": cal_date.isoformat(),
                        "end_date": cal_date.isoformat(),
                        "event_category": cat,
                        "cohort_applicability": "ALL",
                    })

    return {
        "academic_year": academic_year,
        "total_days": len(days),
        "total_events": len(events),
        "days": days,
        "events": events,
    }

