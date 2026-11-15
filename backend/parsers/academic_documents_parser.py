"""Parsers for scoped lab timetables, seating plans, and exam schedules."""
import re
from typing import Any

import pdfplumber
import pymupdf as fitz

SLOTS = {
    1: ("08:00 AM", "08:50 AM"), 2: ("08:50 AM", "09:40 AM"),
    3: ("09:40 AM", "10:30 AM"), 4: ("10:45 AM", "11:35 AM"),
    5: ("11:35 AM", "12:25 PM"), 6: ("12:25 PM", "01:15 PM"),
    7: ("01:15 PM", "02:05 PM"), 8: ("02:05 PM", "02:55 PM"),
    9: ("02:55 PM", "03:45 PM"), 10: ("03:45 PM", "04:35 PM"),
    11: ("04:35 PM", "05:25 PM"), 12: ("05:25 PM", "06:15 PM"),
}

_ROMAN_SEMESTERS = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8}
# These are distinct printed columns in the campus exam schedule. Only MEE is
# also commonly labelled MECH; never merge ECE/ELC or AIE/AID.
_DEPARTMENT_ALIASES = {"MECH": ("MECH", "MEE"), "MEE": ("MECH", "MEE")}


_rapid_ocr_engine = None


def _horizontal_rule_centers(image) -> list[float]:
    """Find long horizontal table rules so OCR text can be grouped by row.

    In scanned exam timetables, the date/session cell is often vertically
    centered while the course text starts at the top of a tall row. Sorting OCR
    lines by y therefore makes the course appear before its date. The printed
    grid gives us a more reliable row boundary than OCR reading order.
    """
    import cv2
    import numpy as np

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    binary = cv2.threshold(gray, 220, 255, cv2.THRESH_BINARY_INV)[1]
    width = image.shape[1]
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(40, width // 30), 1))
    horizontal = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
    coverage = np.count_nonzero(horizontal, axis=1)
    candidates = np.where(coverage >= width * 0.12)[0]
    groups: list[list[int]] = []
    for y in candidates:
        if not groups or int(y) > groups[-1][-1] + 5:
            groups.append([int(y)])
        else:
            groups[-1].append(int(y))
    return [sum(group) / len(group) for group in groups]


def _ocr_page_line_data(page: fitz.Page) -> list[dict[str, Any]]:
    native_text = (page.get_text("text") or "").strip()
    if native_text:
        return [
            {"text": line.strip(), "y_center": None, "table_row": None,
             "table_row_date": None, "table_row_sessions": [], "table_row_weekday": None}
            for line in native_text.splitlines() if line.strip()
        ]
    try:
        import cv2
        import numpy as np
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as exc:
        raise RuntimeError("OCR dependencies are missing. Install project requirements, including rapidocr-onnxruntime.") from exc

    global _rapid_ocr_engine
    if _rapid_ocr_engine is None:
        _rapid_ocr_engine = RapidOCR()

    pix = page.get_pixmap(matrix=fitz.Matrix(3, 3), alpha=False)
    image = cv2.imdecode(np.frombuffer(pix.tobytes("png"), dtype=np.uint8), cv2.IMREAD_COLOR)
    detections, _ = _rapid_ocr_engine(image)
    words = []
    for box, raw_text, confidence in detections or []:
        if not raw_text or confidence < 0.15:
            continue
        xs = [point[0] for point in box]
        ys = [point[1] for point in box]
        center_y = sum(ys) / len(ys)
        height = max(ys) - min(ys)
        words.append((center_y, min(xs), max(xs), height, raw_text.strip()))
    words.sort(key=lambda item: (item[0], item[1]))
    rows: list[list[tuple[float, float, float, float, str]]] = []
    for word in words:
        row_center = sum(w[0] for w in rows[-1]) / len(rows[-1]) if rows else 0
        row_height = sum(w[3] for w in rows[-1]) / len(rows[-1]) if rows else 0
        if not rows or abs(word[0] - row_center) > max(18, row_height * 0.65):
            rows.append([word])
        else:
            rows[-1].append(word)
    line_data = [
        {
            "text": " ".join(w[4] for w in sorted(row, key=lambda item: item[1])),
            "y_center": sum(w[0] for w in row) / len(row),
            "x_min": min(w[1] for w in row),
            "x_max": max(w[2] for w in row),
            "word_positions": [
                {"text": w[4], "x_center": (w[1] + w[2]) / 2}
                for w in sorted(row, key=lambda item: item[1])
            ],
            "table_row": None,
            "table_row_date": None,
            "table_row_sessions": [],
            "table_row_weekday": None,
        }
        for row in rows if row
    ]

    # Assign each OCR line to the printed grid row. Date and session text may
    # be vertically centered in the leftmost cell, after course lines in the
    # same row have already appeared in top-to-bottom OCR order.
    boundaries = _horizontal_rule_centers(image)
    if len(boundaries) >= 4:
        column_rules = _vertical_rule_centers(image)
        row_groups: dict[int, list[dict[str, Any]]] = {}
        for item in line_data:
            y = item["y_center"]
            row_index = next(
                (index for index in range(len(boundaries) - 1)
                 if boundaries[index] <= y <= boundaries[index + 1]),
                None,
            )
            if row_index is not None:
                item["table_row"] = row_index
                row_groups.setdefault(row_index, []).append(item)
        for group in row_groups.values():
            row_text = " ".join(item["text"] for item in group)
            date_match = re.search(r"\b\d{1,2}[/-]\d{1,2}[/-]20\d{2}\b", row_text)
            sessions = sorted({value.upper() for value in re.findall(r"\b(FN|AN)\b", row_text, re.IGNORECASE)})
            weekday = next(
                (day.title() for day in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
                 if re.search(rf"\b{day}\b", row_text, re.IGNORECASE)),
                None,
            )
            for item in group:
                item["table_row_date"] = date_match.group(0) if date_match else None
                item["table_row_sessions"] = sessions
                item["table_row_weekday"] = weekday
                item["table_row_text"] = row_text
                item["table_header_text"] = row_text if re.search(r"\bdate\b", row_text, re.IGNORECASE) and re.search(r"\bsession\b", row_text, re.IGNORECASE) else None
                item["table_column_rules"] = column_rules
    return line_data


def _vertical_rule_centers(image) -> list[float]:
    """Find table column boundaries in a scanned schedule page."""
    import cv2
    import numpy as np

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    binary = cv2.threshold(gray, 220, 255, cv2.THRESH_BINARY_INV)[1]
    height = image.shape[0]
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(40, height // 30)))
    vertical = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
    coverage = np.count_nonzero(vertical, axis=0)
    candidates = np.where(coverage >= height * 0.12)[0]
    groups: list[list[int]] = []
    for x in candidates:
        if not groups or int(x) > groups[-1][-1] + 5:
            groups.append([int(x)])
        else:
            groups[-1].append(int(x))
    return [sum(group) / len(group) for group in groups]


def _department_column_bounds(line_data: list[dict[str, Any]], department: str) -> tuple[float, float] | None:
    """Locate the requested department column from the repeated table header."""
    header_groups: dict[int, list[dict[str, Any]]] = {}
    for item in line_data:
        if item.get("table_row") is not None:
            header_groups.setdefault(int(item["table_row"]), []).append(item)

    header_group = next((
        group for group in header_groups.values()
        if "date" in " ".join(item["text"] for item in group).casefold()
        and "session" in " ".join(item["text"] for item in group).casefold()
    ), None)
    if not header_group:
        return None

    aliases = _DEPARTMENT_ALIASES.get(department.strip().upper(), (department.strip().upper(),))
    labels: list[tuple[str, float]] = []
    for item in header_group:
        for word in item.get("word_positions", []):
            label = re.sub(r"[^A-Z]", "", str(word["text"]).upper())
            if label in {"CSE", "CYS", "ARE", "CHE", "AEE", "MEE", "ECE", "CCE", "EEE", "ELC", "AIE", "AID", "CIE", "MECH"}:
                labels.append((label, float(word["x_center"])))
    selected = [x for label, x in labels if label in aliases]
    if not selected:
        return None

    rules = sorted({float(x) for item in header_group for x in item.get("table_column_rules", [])})
    if len(rules) < 2:
        centers = sorted(x for _label, x in labels)
        target = selected[0]
        pos = centers.index(target)
        left = (centers[pos - 1] + target) / 2 if pos else target - 500
        right = (target + centers[pos + 1]) / 2 if pos + 1 < len(centers) else target + 500
        return left, right

    target = selected[0]
    left_candidates = [x for x in rules if x < target]
    right_candidates = [x for x in rules if x > target]
    if not left_candidates or not right_candidates:
        return None
    left = max(left_candidates)
    right = min(right_candidates)

    # Some tables merge adjacent department cells (e.g. AIE/AID). Split that
    # shared ruled region at the midpoint between its printed header labels.
    sibling_centers = sorted(x for _label, x in labels if left < x < right)
    if len(sibling_centers) > 1:
        position = sibling_centers.index(target)
        if position > 0:
            left = (sibling_centers[position - 1] + target) / 2
        if position + 1 < len(sibling_centers):
            right = (target + sibling_centers[position + 1]) / 2
    return left, right


def _scope_exam_page_lines(line_data: list[dict[str, Any]], department: str) -> list[dict[str, Any]] | None:
    """Return row-level records from the admin-selected department column.

    `None` means no usable department header was detected, so the caller should
    retain the general OCR lines. An empty list means a department header was
    detected but the selected department is not present on this page.
    """
    column = _department_column_bounds(line_data, department)
    if column is None:
        has_department_header = any(
            "date" in item.get("table_header_text", "").casefold()
            and re.search(r"\b(?:CSE|CYS|ARE|CHE|AEE|MEE|ECE|CCE|EEE|ELC|AIE|AID|CIE|MECH)\b", item.get("table_header_text", ""), re.IGNORECASE)
            for item in line_data
        )
        return [] if has_department_header else None

    groups: dict[int, list[dict[str, Any]]] = {}
    for item in line_data:
        if item.get("table_row") is not None:
            groups.setdefault(int(item["table_row"]), []).append(item)
    header_row = next((
        index for index, group in groups.items()
        if any(item.get("table_header_text") for item in group)
    ), None)
    if header_row is None:
        return None
    header_text = " ".join(item["text"] for item in groups[header_row])
    left, right = column
    records: list[dict[str, Any]] = []
    # Life Skills is printed in a cell spanning every department. Humanities
    # and Law papers can appear in only one department's column, so they must
    # stay scoped to their actual table column.
    shared_code = re.compile(r"\b(?:\d{2})?LSE\d{3}[A-Z]?\b", re.IGNORECASE)
    any_course_code = re.compile(r"\b(?:\d{2})?[A-Z]{3,4}\d{3,4}[A-Z]?\b", re.IGNORECASE)
    for row_index, group in groups.items():
        if row_index == header_row:
            continue
        row_words = [word for item in group for word in item.get("word_positions", [])]
        scoped_words = [word["text"] for word in row_words if left <= float(word["x_center"]) <= right]
        cell_text = " ".join(scoped_words).strip()
        all_text = " ".join(item["text"] for item in group)
        shared_match = shared_code.search(all_text)
        if shared_match and shared_match.group(0).casefold() not in cell_text.casefold():
            next_code = any_course_code.search(all_text, shared_match.end())
            end = next_code.start() if next_code else len(all_text)
            shared_fragment = all_text[shared_match.start():end].strip()
            cell_text = f"{cell_text} {shared_fragment}".strip()
        if not cell_text:
            continue
        first = group[0]
        records.append({
            "text": cell_text,
            "y_center": first.get("y_center"),
            "x_min": left,
            "x_max": right,
            "word_positions": [],
            "table_row": row_index,
            "table_row_text": cell_text,
            "table_header_text": header_text,
            "table_row_date": first.get("table_row_date"),
            "table_row_sessions": first.get("table_row_sessions") or [],
            "table_row_weekday": first.get("table_row_weekday"),
            "table_column_rules": [],
        })
    return records


def _ocr_page_lines(page: fitz.Page) -> list[str]:
    """Compatibility helper used by callers that only need extracted text."""
    return [item["text"] for item in _ocr_page_line_data(page)]


def _parse_lab_timetable(path: str, department: str, semester: str) -> dict[str, Any]:
    # Lab room timetables can contain several departments and cohorts on one
    # page. Preserve the complete room schedule; admin scope remains attached
    # to the Document row and is never inferred from PDF text.
    entries = []
    metadata = {"facility": None, "location": None, "room": None, "capacity": None,
                "academic_term": None, "academic_year": None}

    with pdfplumber.open(path) as pdf:
        for page_no, page in enumerate(pdf.pages, 1):
            page_text = page.extract_text() or ""
            if not metadata["location"]:
                heading = re.search(r"Academic\s+Block\s*[- ]?\s*(\d+)\s+(Ground|First|Second|Third|Fourth)\s+Floor", page_text, re.IGNORECASE)
                if heading:
                    metadata["location"] = f"Academic Block-{heading.group(1)}, {heading.group(2).title()} Floor"
                room = re.search(r"\b(IT\s*Lab\s*[-–]?\s*I)\s*\(([^)]+)\)", page_text, re.IGNORECASE)
                if room:
                    metadata["facility"], metadata["room"] = room.group(1).strip(), room.group(2).strip()
                capacity = re.search(r"\[(\d+)\s+(?:desktop\s+)?(?:PCs|systems|computers)\]", page_text, re.IGNORECASE)
                if capacity:
                    metadata["capacity"] = int(capacity.group(1))
                term = re.search(r"\b(EVEN|ODD)\s+SEMESTER\s+LAB\s+TIMETABLE\s+(20\d{2})\s*[-–]\s*(20\d{2})", page_text, re.IGNORECASE)
                if term:
                    metadata["academic_term"] = f"{term.group(1).title()} Semester"
                    metadata["academic_year"] = f"{term.group(2)}–{term.group(3)}"

            for table in page.find_tables():
                extracted = table.extract()
                # Map slot header centers to the actual merged cell extents.
                # This recognizes 2–3, 4–6, and 8–10 blocks without assuming
                # that every non-empty table cell occupies only one slot.
                slot_centers: dict[int, float] = {}
                if len(table.rows) > 1:
                    header_cells = table.rows[1].cells
                    for slot in SLOTS:
                        col = slot
                        if col < len(header_cells) and header_cells[col]:
                            x0, _, x1, _ = header_cells[col]
                            slot_centers[slot] = (x0 + x1) / 2
                for row_index, row in enumerate(extracted):
                    if not row or not row[0]:
                        continue
                    day = row[0].strip().title()
                    if day not in {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"}:
                        continue
                    cells = table.rows[row_index].cells if row_index < len(table.rows) else []
                    for col, cell_text in enumerate(row[1:], start=1):
                        if col >= len(cells) or not cells[col] or not cell_text or not cell_text.strip():
                            continue
                        text = re.sub(r"\s+", " ", cell_text).strip()
                        x0, _, x1, _ = cells[col]
                        occupied = [slot for slot, center in slot_centers.items() if x0 - 1 <= center <= x1 + 1]
                        slots = occupied or ([col] if col in SLOTS else [])
                        if not slots:
                            continue
                        start, end = SLOTS[min(slots)][0], SLOTS[max(slots)][1]
                        entries.append({
                            "page": page_no, "day": day, "slots": slots,
                            "time": f"{start}–{end}", "text": text,
                            **metadata,
                        })
    if not entries:
        raise ValueError("No weekday lab timetable rows were found in the PDF. Check that it contains a readable schedule table.")
    records = []
    for idx, entry in enumerate(entries):
        label = f"{entry['day']} · Slots {', '.join(map(str, entry['slots']))} · {entry['time']} · {entry['text']}"
        records.append({"page": entry["page"], "line": idx, "text": label, "payload": entry})
    return {"records": records, "academic_year": metadata["academic_year"], "summary": f"{len(records)} lab timetable entries indexed from the uploaded room schedule."}


def parse_academic_document(path: str, doc_type: str, department: str, semester: str) -> dict[str, Any]:
    """Extract structured lab rows and OCR/native lines for scanned schedules."""
    if doc_type == "lab_timetable":
        return _parse_lab_timetable(path, department, semester)

    document = fitz.open(path)
    records = []
    full_text_by_page = []
    for page_no, page in enumerate(document, 1):
        line_data = _ocr_page_line_data(page)
        admin_department = (department or "").strip().upper()
        if doc_type == "exam_schedule" and admin_department in {"ALL", "ALL DEPARTMENTS"}:
            has_dept_header = any(
                item.get("table_header_text")
                and re.search(r"\b(?:CSE|CYS|ARE|CHE|AEE|MEE|ECE|CCE|EEE|ELC|AIE|AID|CIE)\b", item["table_header_text"], re.IGNORECASE)
                for item in line_data
            )
            if has_dept_header:
                all_department_rows = []
                for column_department in ("CSE", "CYS", "ARE", "CHE", "AEE", "MEE", "ECE", "CCE", "EEE", "ELC", "AIE", "AID", "CIE"):
                    scoped = _scope_exam_page_lines(line_data, column_department)
                    if scoped:
                        for item in scoped:
                            item["table_department"] = column_department
                        all_department_rows.extend(scoped)
                if all_department_rows:
                    line_data = all_department_rows
                else:
                    for item in line_data:
                        item["table_department"] = "ALL"
            else:
                for item in line_data:
                    item["table_department"] = "ALL"
        elif doc_type == "exam_schedule":
            scoped = _scope_exam_page_lines(line_data, admin_department)
            if scoped is not None:
                line_data = scoped
            for item in line_data:
                item["table_department"] = admin_department
        lines = [item["text"] for item in line_data]
        page_text = "\n".join(lines)
        full_text_by_page.append(page_text)
        for line_no, item in enumerate(line_data):
            payload = {
                "line_number": line_no,
                "table_row": item.get("table_row"),
                "table_row_date": item.get("table_row_date"),
                "table_row_sessions": item.get("table_row_sessions") or [],
                "table_row_weekday": item.get("table_row_weekday"),
                "table_row_text": item.get("table_row_text"),
                "table_header_text": item.get("table_header_text"),
                "table_department": item.get("table_department"),
            }
            records.append({"page": page_no, "line": line_no, "text": item["text"], "payload": payload})
    if not records:
        raise ValueError("No readable text was extracted from this PDF.")
    summary = f"{len(records)} OCR/text lines indexed for {department} Semester {semester}."
    if doc_type == "seating_arrangement":
        summary = f"Seating arrangement indexed for {department} Semester {semester}; student queries are resolved by register-number range."
    elif doc_type == "exam_schedule":
        summary = f"Exam schedule indexed for {department} Semester {semester}; dates and course entries are searchable."
    return {"records": records, "academic_year": None, "summary": summary}
