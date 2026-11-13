"""backend/parsers/pyq_parser.py — Past Year Question (PYQ) structured parser.

Extracts questions, metadata tags (Bloom's Taxonomy BTL1-BTL6, Course Outcomes CO1-CO6,
inline + Part-header marks, and course title aliases) using a 2-pass bounding-box algorithm
with selective high-resolution diagram crops.
"""
from __future__ import annotations

import hashlib
import io
import logging
import re
import unicodedata

from PIL import Image

try:
    import pymupdf as fitz
except ImportError:
    try:
        import fitz
    except ImportError:
        fitz = None

from backend.parsers.visual_cropper import (
    _expand_bbox_for_graphics,
    _render_page_image,
    region_has_graphics,
)
from backend.services.storage_service import upload_question_image_to_cloudinary

logger = logging.getLogger("mach_ai.pyq_parser")

_PART_MARKS_RE = re.compile(
    r"Part\s+[A-Z]\b[\s\S]{0,60}?\(\s*\d+\s*[x×\*]\s*(\d+)\s*=\s*\d+\s*Marks?\s*\)",
    re.IGNORECASE,
)
_FOOTER_STOP_RE = re.compile(
    r"(?:^\s*\*{4,}|^\s*Course\s+Outcome\s*/\s*Bloom|^\s*CO\s*\n\s*Course\s+Outcomes\b|^\s*Page\s+\d+\s+of\s+\d+\s*$)",
    re.IGNORECASE | re.MULTILINE,
)


def clean_formula_text(text: str) -> str:
    """Sanitize mathematical expressions and normalize Unicode mathematical italics."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\t", "\\t")
    text = text.replace("\x00", "")
    text = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", text)
    text = re.sub(r"\bimes\b", r"\\times", text)
    text = re.sub(r"\brac\b", r"\\frac", text)
    text = re.sub(r"[^\w\s\+\-\*\/\=\(\)\[\]\{\}\\\$\.\,\:\_\^\@\%Ωωπµ]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def process_pdf(file_path: str) -> list[str]:
    """Read every page of a PDF and split text into paragraph chunks."""
    if not fitz:
        logger.warning("PyMuPDF not installed.")
        return []

    chunks = []
    try:
        with fitz.open(file_path) as doc:
            for page_num in range(len(doc)):
                page_text = doc[page_num].get_text()
                paragraphs = page_text.split("\n\n")
                for para in paragraphs:
                    cleaned = para.strip().replace("\n", " ")
                    if len(cleaned) > 50:
                        chunks.append(f"[Page {page_num + 1}] {cleaned}")
    except Exception as e:
        logger.warning("Error processing PDF: %s", e)
    return chunks


def _extract_pyq_course_title(first_page_text: str, course_code: str) -> str:
    """Extract the full course title from the PYQ exam header for syllabus alias matching."""
    if not first_page_text or not course_code:
        return ""
    tm = re.search(
        rf"\b{re.escape(course_code)}\b\s*[:\-–]?\s*([^\n]+)",
        first_page_text,
        re.IGNORECASE,
    )
    if not tm:
        return ""
    cand = re.sub(r"\s+", " ", tm.group(1)).strip(" :-–")
    cand = re.split(r"\b(?:Duration|Maximum|Time|Max\.?\s*Marks)\b", cand, flags=re.IGNORECASE)[0].strip(" :-–")
    return cand if 3 <= len(cand) <= 120 else ""


def _find_question_y(page, q_num: str, search_str: str, min_y: float = 0.0) -> float:
    """Locate the true top Y coordinate of a question header, ignoring 'Fig 1.' false hits."""
    # Prefer line-level geometry from text dict so "1." never matches "Fig 1."
    num_token = re.escape(q_num.strip())
    line_re = re.compile(rf"^\s*{num_token}[\.\)]\s*")
    try:
        tdict = page.get_text("dict")
        for block in tdict.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                line_txt = "".join(sp.get("text", "") for sp in line.get("spans", []))
                if line_re.match(line_txt):
                    y0 = float(line["bbox"][1])
                    if y0 >= min_y - 2:
                        return y0
    except Exception:
        pass

    rects = page.search_for(search_str)
    for r in rects:
        if r.y0 >= min_y - 2:
            return float(r.y0)
    return float(rects[0].y0) if rects else min_y


def _find_footer_ceiling(page, min_y: float) -> float:
    """Return the top Y coordinate of CO/BTL footer tables so crops stop cleanly above them."""
    page_h = float(page.rect.height)
    ceiling = page_h
    markers = (
        "Course Outcome /",
        "Course Outcome /Bloom",
        "******",
        "Course Outcomes",
    )
    for marker in markers:
        for r in page.search_for(marker):
            if r.y0 > min_y + 20:
                ceiling = min(ceiling, float(r.y0) - 4)
    return ceiling


def extract_pyq_structured(
    file_path: str, course_code: str, document_id: str = ""
) -> list[dict]:
    """Extract structured questions from PYQ PDFs with Part marks, CO/BTL tags, and selective diagram crops."""
    if not fitz:
        logger.warning("PyMuPDF not installed.")
        return []

    CO_TAG_RE = re.compile(r"\[\s*CO\s*0*(\d+)\s*\]", re.IGNORECASE)
    BTL_TAG_RE = re.compile(r"\[\s*BTL\s*(\d+)\s*\]", re.IGNORECASE)
    MARKS_RE = re.compile(r"\[(\d+)\]|\((\d+)\s*[Mm]arks?\)", re.IGNORECASE)
    FIG_RE = re.compile(r"\bFig\.?\s*\d+\b|\bfigure\b|\bcircuit\b|\bwaveform\b", re.IGNORECASE)
    Q_NUM_RE = re.compile(
        r"^[ \t]{0,5}((?:Q(?:uestion)?\s*)?\d{1,2})[\.\)]\s+",
        re.MULTILINE | re.IGNORECASE,
    )
    SCALE = 2.0

    all_spans = []
    doc_separator = None
    active_part_marks: int | None = None

    try:
        with fitz.open(file_path) as doc:
            num_pages = len(doc)
            first_text = doc[0].get_text("text") if num_pages > 0 else ""
            course_title = _extract_pyq_course_title(first_text, course_code)

            for page_num in range(num_pages):
                page = doc[page_num]
                page_text = unicodedata.normalize("NFKC", page.get_text("text"))

                # Track Part-level marks (e.g. "Part A (3 x 6 = 18 Marks)") across pages
                part_markers = [(pm.start(), int(pm.group(1))) for pm in _PART_MARKS_RE.finditer(page_text)]
                matches = list(Q_NUM_RE.finditer(page_text))
                prev_y = 0.0

                for idx, match in enumerate(matches):
                    q_num = match.group(1).strip()
                    if q_num.lower() in ("", "q", "question"):
                        continue

                    # Update active Part marks if a Part header appeared before this question
                    for p_off, p_mk in part_markers:
                        if p_off < match.start():
                            active_part_marks = p_mk

                    search_str = match.group(0).strip()
                    y_start_pdf = _find_question_y(page, q_num, search_str, min_y=prev_y)
                    prev_y = y_start_pdf + 5

                    start_char = match.end()
                    end_char = matches[idx + 1].start() if idx + 1 < len(matches) else len(page_text)
                    raw_text = page_text[start_char:end_char].strip()

                    # Strip trailing footer tables (CO Course Outcomes / ****** / Mark Distribution Table)
                    footer_m = _FOOTER_STOP_RE.search(raw_text)
                    if footer_m:
                        raw_text = raw_text[: footer_m.start()].strip()

                    if len(raw_text) < 8:
                        continue

                    if any(
                        junk in raw_text.lower()[:60]
                        for junk in [
                            "answer all", "part a", "part b", "maximum marks",
                            "time:", "course outcomes", "co |", "all questions",
                        ]
                    ):
                        continue

                    co_m = CO_TAG_RE.search(raw_text)
                    btl_m = BTL_TAG_RE.search(raw_text)
                    mk_m = MARKS_RE.search(raw_text)
                    marks_val = int(mk_m.group(1) or mk_m.group(2)) if mk_m else active_part_marks
                    has_figure = bool(FIG_RE.search(raw_text))

                    clean_text = CO_TAG_RE.sub("", raw_text)
                    clean_text = BTL_TAG_RE.sub("", clean_text)
                    clean_text = MARKS_RE.sub("", clean_text)
                    clean_text = re.sub(r"[ \t]+", " ", clean_text)
                    clean_text = re.sub(r"\n{3,}", "\n\n", clean_text).strip()

                    if not re.search(r"[a-zA-Z]", clean_text):
                        continue

                    separator = "." if "." in match.group(0) else ")"
                    if doc_separator and separator != doc_separator:
                        continue
                    if not doc_separator:
                        doc_separator = separator

                    all_spans.append(
                        {
                            "q_num": q_num,
                            "page_num": page_num,
                            "y_start": y_start_pdf,
                            "q_text": clean_text,
                            "co_tag": f"CO{co_m.group(1)}" if co_m else None,
                            "btl_tag": f"BTL{btl_m.group(1)}" if btl_m else None,
                            "marks": marks_val,
                            "has_figure": has_figure,
                        }
                    )

            structured_questions = []

            for span_idx, span in enumerate(all_spans):
                page_num = span["page_num"]
                page = doc[page_num]
                page_h = float(page.rect.height)
                y_start_pdf = span["y_start"]

                next_same_page = next(
                    (s for s in all_spans[span_idx + 1 :] if s["page_num"] == page_num),
                    None,
                )
                footer_ceiling = _find_footer_ceiling(page, y_start_pdf)
                if next_same_page and next_same_page["y_start"] > y_start_pdf:
                    hard_ceiling_this_page = min(next_same_page["y_start"] - 4, footer_ceiling)
                else:
                    hard_ceiling_this_page = footer_ceiling

                y_end_pdf = _expand_bbox_for_graphics(
                    page,
                    y_start_pdf,
                    min(y_start_pdf + 70, hard_ceiling_this_page),
                    page_h,
                    hard_ceiling_this_page,
                )

                # Confirm graphics presence geometrically (catches unlabeled figures & skips pure text)
                has_gfx = span["has_figure"] or region_has_graphics(page, y_start_pdf, y_end_pdf)
                span["has_figure"] = has_gfx

                CONTINUATION_THRESHOLD_PDF = 100
                next_span = all_spans[span_idx + 1] if span_idx + 1 < len(all_spans) else None
                continues_to_next_page = (
                    next_same_page is None
                    and next_span is not None
                    and next_span["page_num"] == page_num + 1
                    and next_span["y_start"] > CONTINUATION_THRESHOLD_PDF
                )

                image_url = ""
                image_hash = None
                image_b64 = ""
                if document_id and has_gfx:
                    try:
                        part1_img = _render_page_image(page, SCALE)
                        px_ceiling = int(hard_ceiling_this_page * SCALE)
                        crop_top = max(0, int((y_start_pdf - 10) * SCALE))
                        crop_bot = min(
                            part1_img.height, min(px_ceiling, int((y_end_pdf + 6) * SCALE))
                        )
                        part1_crop = part1_img.crop((0, crop_top, part1_img.width, crop_bot))
                        final_img = part1_crop

                        if continues_to_next_page:
                            next_page = doc[page_num + 1]
                            next_page_h = float(next_page.rect.height)
                            next_hard_ceiling = min(
                                next_span["y_start"] - 4 if next_span else next_page_h,
                                _find_footer_ceiling(next_page, 0.0),
                            )
                            if region_has_graphics(next_page, 0.0, next_hard_ceiling):
                                part2_img = _render_page_image(next_page, SCALE)
                                cont_end_pdf = _expand_bbox_for_graphics(
                                    next_page, 0, min(80, next_hard_ceiling), next_page_h, next_hard_ceiling
                                )
                                cont_bot_px = min(part2_img.height, int((cont_end_pdf + 6) * SCALE))
                                part2_crop = part2_img.crop((0, 0, part2_img.width, cont_bot_px))
                                combined = Image.new(
                                    "RGB",
                                    (part1_crop.width, part1_crop.height + part2_crop.height),
                                    (255, 255, 255),
                                )
                                combined.paste(part1_crop, (0, 0))
                                combined.paste(part2_crop, (0, part1_crop.height))
                                final_img = combined

                        if final_img.height > 30:
                            import base64
                            buf = io.BytesIO()
                            final_img.save(buf, format="JPEG", quality=90)
                            img_bytes = buf.getvalue()
                            image_hash = hashlib.md5(img_bytes).hexdigest()
                            image_b64 = base64.b64encode(img_bytes).decode("utf-8")

                            safe_q = span["q_num"].replace(".", "_").replace(" ", "_")
                            clean_q = re.sub(r"[^a-zA-Z0-9_\.-]", "_", safe_q)
                            public_id = f"{document_id}_p{page_num + 1}_q{clean_q}"
                            image_url = upload_question_image_to_cloudinary(img_bytes, public_id)
                    except Exception as crop_err:
                        logger.warning("Crop failed for Q%s page %s: %s", span["q_num"], page_num + 1, crop_err)

                q_id = hashlib.md5(
                    f"{document_id}_{course_code}_{span['q_num']}_p{page_num}".encode()
                ).hexdigest()

                structured_questions.append(
                    {
                        "id": q_id,
                        "question_number": span["q_num"],
                        "question_text": span["q_text"],
                        "co_tag": span["co_tag"],
                        "btl_tag": span["btl_tag"],
                        "marks": span["marks"],
                        "has_figure": has_gfx,
                        "course_code": course_code,
                        "course_title": course_title,
                        "exam_name": "PYQ Exam",
                        "image_url": image_url,
                        "image_hash": image_hash if image_url else None,
                        "image_b64": image_b64,
                    }
                )

            return structured_questions

    except Exception:
        logger.exception("Error in extract_pyq_structured")
        return []
