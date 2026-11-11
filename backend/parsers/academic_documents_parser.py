"""Parsers for scoped lab timetables, seating plans, and exam schedules."""
import json
import re
from typing import Any

import fitz
import pdfplumber

SLOTS = {
    1: ("08:00 AM", "08:50 AM"), 2: ("08:50 AM", "09:40 AM"),
    3: ("09:40 AM", "10:30 AM"), 4: ("10:45 AM", "11:35 AM"),
    5: ("11:35 AM", "12:25 PM"), 6: ("12:25 PM", "01:15 PM"),
    7: ("01:15 PM", "02:05 PM"), 8: ("02:05 PM", "02:55 PM"),
    9: ("02:55 PM", "03:45 PM"), 10: ("03:45 PM", "04:35 PM"),
    11: ("04:35 PM", "05:25 PM"), 12: ("05:25 PM", "06:15 PM"),
}


_rapid_ocr_engine = None


def _ocr_page_lines(page: fitz.Page) -> list[str]:
    native_text = (page.get_text("text") or "").strip()
    if native_text:
        return [line.strip() for line in native_text.splitlines() if line.strip()]
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
        words.append((center_y, min(xs), height, raw_text.strip()))
    words.sort(key=lambda item: (item[0], item[1]))
    rows: list[list[tuple[float, float, float, str]]] = []
    for word in words:
        row_center = sum(w[0] for w in rows[-1]) / len(rows[-1]) if rows else 0
        row_height = sum(w[2] for w in rows[-1]) / len(rows[-1]) if rows else 0
        if not rows or abs(word[0] - row_center) > max(18, row_height * 0.65):
            rows.append([word])
        else:
            rows[-1].append(word)
    return [" ".join(w[3] for w in sorted(row, key=lambda item: item[1])) for row in rows if row]


def _parse_lab_timetable(path: str, department: str, semester: str) -> dict[str, Any]:
    # pdfplumber's extracted timetable preserves one column per printed slot.
    # Slot 7 is the lunch break; it remains searchable when a class uses it.
    slot_columns = {slot: slot for slot in SLOTS}
    entries = []
    with pdfplumber.open(path) as pdf:
        for page_no, page in enumerate(pdf.pages, 1):
            for table in page.extract_tables() or []:
                for row in table:
                    if not row or not row[0]:
                        continue
                    day = row[0].strip().title()
                    if day not in {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"}:
                        continue
                    for slot, col in slot_columns.items():
                        if col >= len(row) or not row[col] or not row[col].strip():
                            continue
                        text = re.sub(r"\s+", " ", row[col]).strip()
                        # The admin-selected department and semester define the
                        # document's scope. PDF headings/codes are content and
                        # must not silently reclassify or reject the upload.
                        start, end = SLOTS[slot]
                        entries.append({
                            "page": page_no, "day": day, "slots": [slot],
                            "time": f"{start}–{end}", "text": text,
                        })
    if not entries:
        raise ValueError(f"No lab timetable rows matched {department} Semester {semester}. Check the admin-selected scope and PDF.")
    records = []
    for idx, entry in enumerate(entries):
        label = f"{entry['day']} · Slots {', '.join(map(str, entry['slots']))} · {entry['time']} · {entry['text']}"
        records.append({"page": entry["page"], "line": idx, "text": label, "payload": entry})
    return {"records": records, "academic_year": None, "summary": f"{len(records)} lab timetable entries indexed for {department} Semester {semester}."}


def parse_academic_document(path: str, doc_type: str, department: str, semester: str) -> dict[str, Any]:
    """Extract structured lab rows and OCR/native lines for scanned schedules."""
    if doc_type == "lab_timetable":
        return _parse_lab_timetable(path, department, semester)

    document = fitz.open(path)
    records = []
    full_text_by_page = []
    for page_no, page in enumerate(document, 1):
        lines = _ocr_page_lines(page)
        page_text = "\n".join(lines)
        full_text_by_page.append(page_text)
        for line_no, line in enumerate(lines):
            records.append({"page": page_no, "line": line_no, "text": line, "payload": {"line_number": line_no}})
    full_text = "\n".join(full_text_by_page)
    if not records:
        raise ValueError("No readable text was extracted from this PDF.")
    summary = f"{len(records)} OCR/text lines indexed for {department} Semester {semester}."
    if doc_type == "seating_arrangement":
        summary = f"Seating arrangement indexed for {department} Semester {semester}; student queries are resolved by register-number range."
    elif doc_type == "exam_schedule":
        summary = f"Exam schedule indexed for {department} Semester {semester}; dates and course entries are searchable."
    return {"records": records, "academic_year": None, "summary": summary}
