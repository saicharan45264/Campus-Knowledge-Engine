"""
schedule_service.py — Service combining Academic Calendar and Timetable to answer dated schedule queries.
Handles:
1. Resolving the calendar date (checking if it is a holiday or has a timetable-weekday override).
2. Fetching weekly sessions for the applied weekday.
3. Incorporating student elective / group choices.
"""
from datetime import datetime, date
from typing import Dict, Any, List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

from database import CalendarDay, TimetableVersion, WeeklySession, ElectiveOption


async def get_schedule_for_date(
    target_date_str: Optional[str],
    db: AsyncSession,
    weekday: Optional[str] = None,
    department: str = "CSE",
    semester: str = "VII",
    section: str = "F",
    selected_electives: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Returns the schedule for a given date (YYYY-MM-DD) or weekday, applying calendar overrides and holidays.
    """
    t_date = None
    if target_date_str:
        try:
            t_date = datetime.strptime(target_date_str, "%Y-%m-%d").date()
        except Exception:
            t_date = None

    if not t_date and not weekday:
        t_date = date.today()

    if t_date:
        # 1. Look up Calendar Day
        cal_stmt = select(CalendarDay).where(
            CalendarDay.calendar_date >= datetime.combine(t_date, datetime.min.time()),
            CalendarDay.calendar_date <= datetime.combine(t_date, datetime.max.time()),
        )
        cal_res = await db.execute(cal_stmt)
        cal_day = cal_res.scalars().first()

        natural_weekday = t_date.strftime("%A")
        applied_weekday = natural_weekday
        status = "W"
        particulars = None
        override_note = None

        if cal_day:
            status = cal_day.status or "W"
            particulars = cal_day.particulars
            if cal_day.timetable_override:
                applied_weekday = cal_day.timetable_override
                override_note = f"Note: {natural_weekday} operates on {cal_day.timetable_override}'s Timetable."

        if "holiday" in status.lower() or (particulars and "holiday" in particulars.lower()):
            return {
                "date": str(t_date),
                "natural_weekday": natural_weekday,
                "applied_weekday": applied_weekday,
                "is_holiday": True,
                "holiday_name": particulars or status or "Holiday",
                "sessions": [],
                "department": department,
                "semester": semester,
                "section": section,
                "message": f"{t_date.strftime('%B %d, %Y')} ({natural_weekday}) is a Holiday: {particulars or status or 'Institution Holiday'}.",
            }
    else:
        natural_weekday = weekday.capitalize() if weekday else "Monday"
        applied_weekday = natural_weekday
        status = "W"
        particulars = None
        override_note = None

    # 2. Look up Timetable Sessions for applied_weekday
    tt_stmt = (
        select(WeeklySession)
        .join(TimetableVersion)
        .where(
            TimetableVersion.department == department,
            TimetableVersion.semester == semester,
            TimetableVersion.section == section,
            WeeklySession.day_of_week.ilike(applied_weekday),
        )
        .order_by(WeeklySession.slot_numbers)
    )
    res = await db.execute(tt_stmt)
    sessions = res.scalars().all()

    # 3. Fetch elective options for any elective slots
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
    all_options = opt_res.scalars().all()

    options_by_bucket: Dict[str, List[Dict[str, str]]] = {}
    for o in all_options:
        options_by_bucket.setdefault(o.elective_bucket, []).append({
            "course_code": o.course_code,
            "course_name": o.course_name,
            "faculty": o.faculty or "",
            "venue": o.venue or "",
        })

    formatted_sessions = []
    for s in sessions:
        entry = {
            "slot": s.slot_numbers,
            "time": f"{s.start_time} - {s.end_time}",
            "activity": s.activity_label,
            "course_code": s.course_code,
            "elective_bucket": s.elective_bucket,
            "faculty": s.faculty,
            "venue": s.venue,
        }

        # If it's an elective slot and student picked an elective
        if s.elective_bucket and selected_electives and s.elective_bucket in selected_electives:
            chosen_code = selected_electives[s.elective_bucket]
            for opt in options_by_bucket.get(s.elective_bucket, []):
                if opt["course_code"] == chosen_code:
                    entry["activity"] = f"{opt['course_code']} - {opt['course_name']}"
                    entry["faculty"] = opt["faculty"]
                    entry["venue"] = opt["venue"]
                    break

        formatted_sessions.append(entry)

    return {
        "date": str(t_date) if t_date else None,
        "natural_weekday": natural_weekday,
        "applied_weekday": applied_weekday,
        "is_holiday": False,
        "override_note": override_note,
        "particulars": particulars,
        "department": department,
        "semester": semester,
        "section": section,
        "sessions": formatted_sessions,
        "available_electives": options_by_bucket,
    }
