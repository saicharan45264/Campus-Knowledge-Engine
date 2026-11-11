"""
timetable_parser.py — Parser for Class Timetable PDFs (e.g. BTechCSEF7.pdf).
Reconstructs:
1. Header metadata (Dept, Semester, Section, Building, Class Advisors)
2. Weekly slot schedule (12 slots, recognizing Slot 7 as Lunch Break, interval break)
3. Weekly grid sessions (Monday-Friday, expanding merged slots like AI Lab or Full Stack Frameworks)
4. Cross-page Course & Elective Directories (Theory, PE IV/V/VI, Free Electives, CIR, Lab)
"""
import re
from typing import Dict, List, Any, Optional
import pdfplumber

SLOT_TIMINGS = {
    1: ("08.00 am", "08.50 am"),
    2: ("08.50 am", "09.40 am"),
    3: ("09.40 am", "10.30 am"),
    # 10.30 - 10.45 Interval Break
    4: ("10.45 am", "11.35 am"),
    5: ("11.35 am", "12.25 pm"),
    6: ("12.25 pm", "01.15 pm"),
    # 01.15 - 02.05 Lunch Break (Slot 7)
    7: ("01.15 pm", "02.05 pm"),
    8: ("02.05 pm", "02.55 pm"),
    9: ("02.55 pm", "03.45 pm"),
    10: ("03.45 pm", "04.35 pm"),
    11: ("04.35 pm", "05.25 pm"),
    12: ("05.25 pm", "06.15 pm"),
}

# Mapping table columns (0-indexed) to slot numbers on Page 1 Table 1:
# Col 0: Day
# Col 1: Slot 1
# Col 2: Slot 2
# Col 3: Slot 3
# Col 4: Interval Break
# Col 5: Slot 4
# Col 6: Slot 5
# Col 7: Slot 6
# Col 8: Lunch Break (Slot 7)
# Col 9: Slot 8
# Col 10: Slot 9
# Col 11: Slot 10
# Col 12: Slot 11
# Col 13: Slot 12
COL_TO_SLOT = {
    1: 1, 2: 2, 3: 3,
    5: 4, 6: 5, 7: 6,
    9: 8, 10: 9, 11: 10, 12: 11, 13: 12
}


def parse_timetable_pdf(pdf_path: str) -> Dict[str, Any]:
    """
    Parses a 3-page timetable PDF into metadata, sessions, and course directories.
    """
    metadata = {
        "department": "CSE",
        "semester": "VII",
        "section": "F",
        "program": "B.Tech CSE",
        "building": "AB III",
        "academic_year": "2026-2027",
        "class_advisors": "Dr. R. Karthi, Ms. Krishna Priya G",
    }
    sessions: List[Dict[str, Any]] = []
    directory_courses: List[Dict[str, Any]] = []

    with pdfplumber.open(pdf_path) as pdf:
        # 1. Parse Page 1 (Header + Weekly Grid + Initial Directory)
        p1 = pdf.pages[0]
        tables1 = p1.extract_tables()
        if tables1:
            t1 = tables1[0]
            # Extract header from Row 1
            if len(t1) > 1 and t1[1]:
                row1_text = " ".join([c for c in t1[1] if c])
                dept_m = re.search(r"Dept:\s*([A-Za-z0-9]+)", row1_text)
                sem_m = re.search(r"Semester:\s*([A-Za-z0-9]+)", row1_text)
                sec_m = re.search(r"Section:\s*([A-Za-z0-9]+)", row1_text)
                bld_m = re.search(r"(AB\s*[I|V|X]+)", row1_text)
                if dept_m: metadata["department"] = dept_m.group(1).strip()
                if sem_m: metadata["semester"] = sem_m.group(1).strip()
                if sec_m: metadata["section"] = sec_m.group(1).strip()
                if bld_m: metadata["building"] = bld_m.group(1).strip()

            # Rows 4 to 8 are Monday through Friday
            day_rows = {
                "Monday": 4,
                "Tuesday": 5,
                "Wednesday": 6,
                "Thursday": 7,
                "Friday": 8,
            }

            for day_name, r_idx in day_rows.items():
                if r_idx >= len(t1):
                    continue
                row = t1[r_idx]

                # Identify sessions, including horizontally merged cells (which appear as None in subsequent cols)
                col = 1
                while col < len(row):
                    if col not in COL_TO_SLOT:
                        col += 1
                        continue

                    cell_text = row[col]
                    if not cell_text or not cell_text.strip():
                        col += 1
                        continue

                    raw_text = cell_text.strip()
                    first_slot = COL_TO_SLOT[col]
                    covered_slots = [first_slot]

                    # Lookahead to detect merged cells (None in subsequent slot columns)
                    next_col = col + 1
                    while next_col < len(row):
                        if next_col in (4, 8):  # Skip breaks
                            next_col += 1
                            continue
                        if next_col in COL_TO_SLOT and row[next_col] is None:
                            covered_slots.append(COL_TO_SLOT[next_col])
                            next_col += 1
                        else:
                            break

                    start_time = SLOT_TIMINGS[covered_slots[0]][0]
                    end_time = SLOT_TIMINGS[covered_slots[-1]][1]

                    # Classify activity, elective bucket, course code
                    bucket = None
                    course_code = None
                    if "PE - IV" in raw_text or "PE-IV" in raw_text:
                        bucket = "PE - IV"
                    elif "PE - V" in raw_text or "PE-V" in raw_text:
                        bucket = "PE - V"
                    elif "PE - VI" in raw_text or "PE-VI" in raw_text:
                        bucket = "PE - VI"
                    elif "Free Elective" in raw_text:
                        bucket = "Free Elective - 2"

                    # Find explicit course code
                    code_match = re.search(r"\b(23[A-Z]{3}\d{3}|CIR\d{4}|19[A-Z]{3}\d{3})\b", raw_text)
                    if code_match:
                        course_code = code_match.group(1)

                    # Extract venue or faculty if in cell
                    venue = None
                    faculty = None
                    if "ABIII" in raw_text or "C104" in raw_text or "C203" in raw_text or "Prajna" in raw_text:
                        lines = [l.strip() for l in raw_text.split("\n") if l.strip()]
                        for l in lines:
                            if any(rm in l for rm in ["C104", "C203", "C402", "C403", "C404", "D406", "F305", "Prajna", "A304", "Seminar"]):
                                venue = l
                                break
                    if "Dr." in raw_text or "Ms." in raw_text:
                        for l in raw_text.split("\n"):
                            if "Dr." in l or "Ms." in l:
                                faculty = l.strip()
                                break

                    sessions.append({
                        "day_of_week": day_name,
                        "slot_numbers": ",".join(map(str, covered_slots)),
                        "start_time": start_time,
                        "end_time": end_time,
                        "activity_label": raw_text.split("\n")[0].strip(),
                        "course_code": course_code,
                        "elective_bucket": bucket,
                        "group_label": "G1 & G2" if "(G1 &G2)" in raw_text or "(G1 & G2)" in raw_text else None,
                        "faculty": faculty,
                        "venue": venue,
                    })

                    col = next_col

        # 2. Parse Course Directories across Pages 1, 2, and 3
        # IMPORTANT: Professional Elective VI must precede Professional Elective V to prevent substring collisions
        category_keywords = [
            ("Professional Elective IV", "PE - IV"),
            ("PE - IV", "PE - IV"),
            ("PE IV", "PE - IV"),
            ("Professional Elective VI", "PE - VI"),
            ("PE - VI", "PE - VI"),
            ("PE VI", "PE - VI"),
            ("Professional Elective V", "PE - V"),
            ("PE - V", "PE - V"),
            ("PE V", "PE - V"),
            ("Free Elective", "Free Elective - 2"),
            ("Theory", "Theory"),
            ("Lab", "Lab"),
            ("CIR", "CIR"),
            ("Mandatory", "Mandatory AICTE"),
            ("Project", "Project"),
        ]

        for p_idx in range(len(pdf.pages)):
            tables = pdf.pages[p_idx].extract_tables()
            if not tables:
                continue

            current_bucket = "General"
            rows_to_scan = tables[0][10:] if p_idx == 0 else tables[0]
            for row in rows_to_scan:
                text_cells = [c.strip() for c in row if c and c.strip()]
                if not text_cells:
                    continue

                first_text = text_cells[0]
                # Check for category header row
                for kw, b_name in category_keywords:
                    if kw.lower() in first_text.lower():
                        current_bucket = b_name
                        break

                # Check if this row is a course offering (contains a code like 23CSE401)
                code_match = None
                for c in text_cells:
                    m = re.match(r"^(19|22|23|24|25|[A-Z]{3})[A-Z0-9]{3,7}$", c)
                    if m and not c.startswith("AB"):
                        code_match = c
                        break

                if code_match:
                    code = code_match
                    name = ""
                    fac = ""
                    venue = ""

                    # Find position of code in text_cells
                    c_idx = text_cells.index(code)
                    if c_idx + 1 < len(text_cells):
                        name = text_cells[c_idx + 1]
                    if c_idx + 2 < len(text_cells):
                        fac = text_cells[c_idx + 2]
                    if c_idx + 3 < len(text_cells):
                        venue = text_cells[c_idx + 3]

                    directory_courses.append({
                        "elective_bucket": current_bucket,
                        "course_code": code,
                        "course_name": name.replace("\n", " ").strip(),
                        "faculty": fac.replace("\n", ", ").strip(),
                        "venue": venue.replace("\n", " | ").strip(),
                        "group_label": "G1/G2" if "G1" in fac or "G1" in venue else None,
                    })

    return {
        "metadata": metadata,
        "total_sessions": len(sessions),
        "total_offerings": len(directory_courses),
        "weekly_sessions": sessions,
        "elective_options": directory_courses,
    }
