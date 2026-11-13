"""backend/parsers/pdf_classifier.py — Fast deterministic PDF classifier.

Auto-classifies uploaded academic documents into 'syllabus' or 'pyq'
and extracts metadata (department, revision year, or course code).
"""
from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger("mach_ai.pdf_classifier")

try:
    import pymupdf as fitz
except ImportError:
    try:
        import fitz
    except ImportError:
        fitz = None


def classify_pdf_type(file_path: str) -> dict:
    """Classify a PDF document as 'syllabus' or 'pyq' by inspecting first 3 pages and metadata."""
    if not fitz:
        return {"doc_type": "pyq", "course_code": "UNKNOWN", "pages": 1}

    try:
        with fitz.open(file_path) as doc:
            page_count = len(doc)
            text = ""
            for p in range(min(3, page_count)):
                text += doc[p].get_text() + " "
    except Exception as e:
        logger.warning("Error reading PDF %s: %s", file_path, e)
        return {"doc_type": "pyq", "course_code": "UNKNOWN", "pages": 1}

    fname = os.path.basename(file_path).upper()
    text_upper = text.upper()

    # 1. Syllabus detection
    if page_count > 25 or "CURRICULUM" in text_upper or "SYLLABUS" in fname or "SCHEME OF INSTRUCTION" in text_upper:
        dept = "CSE"
        if "COMPUTER SCIENCE" in text_upper or "CSE" in fname:
            dept = "CSE"
        elif "ELECTRICAL" in text_upper or "EEE" in fname:
            dept = "EEE"
        elif "ELECTRONICS" in text_upper or "ECE" in fname:
            dept = "ECE"
        elif "MECHANICAL" in text_upper or "MECH" in fname:
            dept = "MECH"
        elif "ARTIFICIAL INTELLIGENCE" in text_upper or "AIE" in fname:
            dept = "AIE"
        elif "CYBER" in text_upper or "CYS" in fname:
            dept = "CYS"

        year = "2023"
        ym = re.search(r"\b(20\d{2})\b", fname) or re.search(r"\b(20\d{2})\b", text_upper)
        if ym:
            year = ym.group(1)
        return {"doc_type": "syllabus", "department": dept, "year": year, "pages": page_count}

    # 2. PYQ (Question Paper) detection
    cc_match = re.search(r"(\d{2}[A-Z]{3}\d{3})", fname)
    if not cc_match:
        cc_match = re.search(r"Course\s*(?:Code)?\s*[:\-]?\s*(\d{2}[A-Z]{3}\d{3})", text, re.IGNORECASE)
    if not cc_match:
        cc_match = re.search(r"\b(\d{2}[A-Z]{3}\d{3})\b", text)
    if not cc_match:
        cc_match = re.search(r"\b([A-Z]{2,4}\d{3})\b", fname) or re.search(r"\b([A-Z]{2,4}\d{3})\b", text)

    course_code = cc_match.group(1).upper() if cc_match else "UNKNOWN"
    course_title = ""
    if course_code != "UNKNOWN":
        tm = re.search(
            rf"\b{re.escape(course_code)}\b\s*[:\-–]?\s*([^\n]+)",
            text,
            re.IGNORECASE,
        )
        if tm:
            cand = re.sub(r"\s+", " ", tm.group(1)).strip(" :-–")
            # Strip trailing boilerplate like "Duration:" or "Maximum:"
            cand = re.split(r"\b(?:Duration|Maximum|Time|Max\.?\s*Marks)\b", cand, flags=re.IGNORECASE)[0].strip(" :-–")
            if 3 <= len(cand) <= 120:
                course_title = cand
    return {
        "doc_type": "pyq",
        "course_code": course_code,
        "course_title": course_title,
        "pages": page_count,
    }

