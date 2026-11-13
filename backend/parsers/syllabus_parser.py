"""
syllabus_parser.py — Campus-Wide GraphRAG Syllabus Ingestion Pipeline

Four-phase pipeline:
  Phase 1: Fast text-only pass -> page-type classification (~1s for 300+ pages)
  Phase 2: Targeted table pass -> only pages needing table extraction
  Phase 3: Structural extraction -> regex + geometry + rule-based, zero-LLM
  Phase 4: Batched Neo4j UNWIND writes -> strict hierarchical knowledge graph
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time

import pymupdf as fitz  # PyMuPDF

logger = logging.getLogger("mach_ai.syllabus_parser")

# Page Type Constants
PAGE_COVER = "COVER"
PAGE_GENERAL_INFO = "GENERAL_INFO"
PAGE_SEMESTER_TABLE = "SEMESTER_TABLE"
PAGE_COURSE_DETAIL = "COURSE_DETAIL"
PAGE_ELECTIVE = "ELECTIVE_BASKET"
PAGE_RULE = "RULE"
PAGE_OTHER = "OTHER"

# Compiled Patterns
_COURSE_HEADER_RE = re.compile(
    r"^\s*(\d{2}[A-Z]{3}\d{3})\s+([^\n]+?)\s+"
    r"(?:L-T-P-C\s*:\s*(?:([\d\[\]]+)[\s\-]+([\d\[\]]+)[\s\-]+([\d\[\]]+)[\s\-]+(P/F|[\d\[\]]+)|(P/F))|\s{3,}(P/F))\s*$",
    re.IGNORECASE | re.MULTILINE,
)
# Secondary header matcher for AVP/ADM syllabus blocks lacking L-T-P-C (e.g. pp. 203-205)
_SHORT_AVP_HEADER_RE = re.compile(
    r"^\s*(22(?:AVP|ADM)\d{3})\s+([A-Z][A-Za-z0-9’'&\-\s,:()]{4,90}?)\s*$",
    re.MULTILINE,
)
_COURSE_CODE_RE = re.compile(r"\b\d{2}[A-Z]{3}\d{3}\b", re.IGNORECASE)
_UNIT_RE = re.compile(
    r"^\s*(?:Syllabus\s*)?Unit\s*[-–:]?\s*([IVX]+|\d+)\b",
    re.IGNORECASE | re.MULTILINE,
)
_PO_LINE_RE = re.compile(r"^(PO\d+)\s*[:\-]\s*(.+)", re.IGNORECASE | re.MULTILINE)
_PSO_LINE_RE = re.compile(r"^(PSO\d+)\s*[:\-]\s*(.+)", re.IGNORECASE | re.MULTILINE)
_PEO_BULLET_RE = re.compile(r"^[•●\-\*]\s*(.+)", re.MULTILINE)
_CO_LINE_RE = re.compile(
    r"^\s*(CO\s*\d+)\s*[:\-]\s*(.+?)(?=\n\s*(?:CO\s*\d+\s*[:\-]|CO[\s\-–]*PO|Syllabus|Unit\s*[-–:]?\s*(?:[IVX]+|\d+)|Course\s+Objective|Text\s*Books?|Reference|Evaluation\s+Pattern|Lab\s+Experiments|$)|\Z)",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)
_EVAL_RATIO_STRICT_RE = re.compile(
    r"Evaluation\s+Pattern\s*[:\-]?\s*(?:(\d{1,3})\s*:\s*(\d{1,3})|(P/F))",
    re.IGNORECASE,
)
_EVAL_RATIO_RE = re.compile(r"\b(\d{2})\s*:\s*(\d{2})\b")
_SEMESTER_HDR_RE = re.compile(
    r"^\s*Semester\s*(I{1,3}V?|VI{0,3}|IX|X{0,2}I{0,3}|\d+)\s*$", re.IGNORECASE | re.MULTILINE
)
_BASKET_HDR_RE = re.compile(
    r"^\s*(?:Electives?\s+in\s+[A-Za-z\s/&]+|General\s+Electives|Professional\s+Electives\s+for\s+Other\s+Branches|"
    r"List\s+of\s+courses\s+in\s+Amrita\s+Value\s+Programme(?:\s+I\s*&\s*II)?|Amrita\s+Value\s+Programme(?:\s+I\s*&\s*II)?|"
    r"PROFESSIONAL\s+ELECTIVES\s+UNDER\s+SCIENCE\s+STREAM|"
    r"FREE\s+ELECTIVES\s+OFFERED\s+UNDER\s+[A-Za-z\s/]+STREAM(?:S)?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_STOP_SECTION_RE = re.compile(
    r"^\s*(?:TEXT\s*BOOKS?(?:\s*/\s*REFERENCES?)?|REFERENCES?(?:\s+BOOKS?)?|ESSENTIAL\s+READING|"
    r"Evaluation\s+Pattern|Assessment\b|Lab\s+Experiments?|List\s+of\s+Experiments?|"
    r"Course\s+Objectives?|Course\s+Outcomes?|CO[\s\-–]*PO\s*Mapping)\b",
    re.IGNORECASE | re.MULTILINE,
)
_TOTAL_CREDITS_RE = re.compile(
    r"Total\s*\([^)]*\)\s*(\d+)(?:\s*\+\s*\[(\d+)\])?", re.IGNORECASE
)
_COPO_KEYWORD_RE = re.compile(r"CO[\s\-–]*PO\s+Mapping", re.IGNORECASE)

_JUNK_PATTERNS = [
    re.compile(r"\b\d{4}\b"),
    re.compile(r"publishers?", re.IGNORECASE),
    re.compile(r"\bedition\b", re.IGNORECASE),
    re.compile(r"\bpress\b", re.IGNORECASE),
    re.compile(r"\bISBN\b", re.IGNORECASE),
    re.compile(r"Text\s*Book", re.IGNORECASE),
    re.compile(r"\bReference\b", re.IGNORECASE),
    re.compile(r"Evaluation\s+Pattern", re.IGNORECASE),
    re.compile(r"\bAssessment\b", re.IGNORECASE),
    re.compile(r"McGraw|Pearson|Wiley|Springer|Elsevier|Tata|Jones", re.IGNORECASE),
]

# Assessment Component Canonicalization Map
_ASSESS_CANONICAL = (
    ("periodical 1", ("p1", "Periodical 1 (P1)")),
    ("periodical1", ("p1", "Periodical 1 (P1)")),
    ("p1", ("p1", "Periodical 1 (P1)")),
    ("periodical 2", ("p2", "Periodical 2 (P2)")),
    ("periodical2", ("p2", "Periodical 2 (P2)")),
    ("p2", ("p2", "Periodical 2 (P2)")),
    ("midterm", ("midterm", "Mid-Term Exam")),
    ("mid term", ("midterm", "Mid-Term Exam")),
    ("mid-term", ("midterm", "Mid-Term Exam")),
    ("continuous assessment theory", ("cat", "Continuous Assessment Theory (CAT)")),
    ("(cat)", ("cat", "Continuous Assessment Theory (CAT)")),
    ("continuous assessment lab", ("cal", "Continuous Assessment Lab (CAL)")),
    ("(cal)", ("cal", "Continuous Assessment Lab (CAL)")),
    ("continuous assessment", ("ca", "Continuous Assessment (CA)")),
    ("(ca)", ("ca", "Continuous Assessment (CA)")),
    ("end semester", ("end_sem", "End Semester Exam")),
    ("endsemester", ("end_sem", "End Semester Exam")),
    ("online examination", ("online_exam", "Online Examination")),
    ("online exam", ("online_exam", "Online Examination")),
    ("project", ("project_viva", "Project / Viva Evaluation")),
    ("viva", ("project_viva", "Project / Viva Evaluation")),
)


def _canonicalize_assessment(raw_name: str, branch: str) -> tuple[str, str]:
    """Normalize noisy PDF component headings into a canonical (key, display_name)."""
    clean = re.sub(r"[\*•●\r\n]+", " ", raw_name or "")
    clean = re.sub(r"\s+", " ", clean).strip(" –-:")
    compact = re.sub(r"[^a-z0-9]+", " ", clean.lower()).strip()
    compact_nospace = compact.replace(" ", "")

    for needle, target in _ASSESS_CANONICAL:
        n_clean = needle.replace(" ", "").strip("()")
        if needle in clean.lower() or n_clean in compact_nospace:
            return target

    if not clean:
        return (f"{branch}_assessment", f"{branch.title()} Assessment")
    slug = re.sub(r"[^a-z0-9]+", "_", clean.lower()).strip("_")[:48]
    return (slug or f"{branch}_assessment", clean)


def _valid_eval_ratio(a: int, b: int) -> bool:
    """Validate internal:external ratio sums to 100 and is non-zero internal."""
    return 0 < a <= 100 and 0 <= b <= 100 and (a + b) == 100


def _clean_page_text(raw: str) -> str:
    """Remove standalone top/bottom page number lines without altering alignment."""
    lines = raw.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    if lines and re.match(r"^\s*\d{1,3}\s*$", lines[0]):
        lines.pop(0)
    if lines and re.match(r"^\s*\d{1,3}\s*$", lines[-1]):
        lines.pop()
    return "\n".join(lines)


def _roman_to_int(s: str) -> int:
    roman = {"I": 1, "V": 5, "X": 10}
    s = s.upper().strip()
    try:
        return int(s)
    except ValueError:
        pass
    result, prev = 0, 0
    for ch in reversed(s):
        val = roman.get(ch, 0)
        if val < prev:
            result -= val
        else:
            result += val
        prev = val
    return result if result > 0 else 1


def _is_junk_topic(text: str) -> bool:
    return any(pat.search(text) for pat in _JUNK_PATTERNS)


def _sv_id(dept: str, year: str) -> str:
    return f"{dept.upper()}_{year}"


# -----------------------------------------------------------------------------
# Phase 1 & Phase 2 — Fast Page Classification & Targeted Table Detection
# -----------------------------------------------------------------------------


def _classify_page(page_num: int, text: str) -> str:
    if page_num == 0:
        return PAGE_COVER
    if _COURSE_HEADER_RE.search(text):
        return PAGE_COURSE_DETAIL
    tu = text.upper()
    if any(
        k in tu
        for k in [
            "GENERAL INFORMATION",
            "ABBREVIATIONS USED",
            "PROGRAM OUTCOMES FOR",
            "PROGRAM EDUCATIONAL OBJECTIVE",
            "PROGRAM SPECIFIC OUTCOME",
            "GRADUATE WILL",
            "GENERAL FRAMEWORK",
        ]
    ):
        return PAGE_GENERAL_INFO
    if _SEMESTER_HDR_RE.search(text) and _COURSE_CODE_RE.search(text):
        return PAGE_SEMESTER_TABLE
    if _BASKET_HDR_RE.search(text) and not (
        "COURSE OBJECTIVES" in tu or "COURSE OUTCOMES" in tu or _UNIT_RE.search(text)
    ):
        return PAGE_ELECTIVE
    if any(
        k in tu
        for k in [
            "NPTEL",
            "CGPA",
            "LIVE-IN",
            "LIVE IN LABS",
            "EXEMPTION",
            "ELIGIBILITY",
            "CREDIT LIMIT",
            "SPECIALIZATION",
        ]
    ) and not ("COURSE OBJECTIVES" in tu or "COURSE OUTCOMES" in tu or _UNIT_RE.search(text) or "REFERENCE(S)" in tu):
        return PAGE_RULE
    return PAGE_OTHER


def phase1_fast_pass(doc) -> tuple[list[str], dict[int, str]]:
    page_texts = []
    page_type_idx = {}
    for i in range(len(doc)):
        raw = doc[i].get_text("text", sort=True)
        text = _clean_page_text(raw)
        if len(text.strip()) < 40 and doc[i].get_images():
            logger.info("Detected image-only/scanned page %d during syllabus parse.", i + 1)
        page_texts.append(text)
        page_type_idx[i] = _classify_page(i, text)
    return page_texts, page_type_idx


_TABLE_PAGES = {PAGE_SEMESTER_TABLE, PAGE_GENERAL_INFO, PAGE_ELECTIVE}


def phase2_table_pass(doc, page_type_idx: dict[int, str]) -> dict[int, list]:
    page_tables = {}
    for page_num, ptype in page_type_idx.items():
        if ptype not in _TABLE_PAGES:
            continue
        try:
            found = doc[page_num].find_tables()
            if found and found.tables:
                page_tables[page_num] = [t.extract() for t in found.tables]
        except Exception as e:
            logger.warning("Table detection failed on page %s: %s", page_num + 1, e)
    return page_tables


_live_doc = None
_page_table_cache: dict[int, list] = {}


def set_live_doc(doc) -> None:
    global _live_doc, _page_table_cache
    _live_doc = doc
    _page_table_cache = {}


def _get_page_tables_inline(page_num: int) -> list:
    if page_num in _page_table_cache:
        return _page_table_cache[page_num]
    result = []
    if _live_doc is not None:
        try:
            found = _live_doc[page_num].find_tables()
            if found and found.tables:
                result = [t.extract() for t in found.tables]
        except Exception:
            pass
    _page_table_cache[page_num] = result
    return result


# -----------------------------------------------------------------------------
# Phase 3 — Structural Extraction
# -----------------------------------------------------------------------------


def _extract_section(text: str, start_markers: list[str], end_markers: list[str]) -> str:
    start_idx = -1
    for marker in start_markers:
        idx = text.find(marker)
        if idx >= 0:
            start_idx = idx + len(marker)
            break
    if start_idx < 0:
        return ""
    end_idx = len(text)
    for marker in end_markers:
        idx = text.find(marker, start_idx)
        if 0 <= idx < end_idx:
            end_idx = idx
    return text[start_idx:end_idx].strip()


def _parse_general_framework(page_texts: list[str]) -> list[dict]:
    """Parse General Framework credit table across pp. 2-3."""
    snippet = "\n".join(page_texts[1:4])
    idx = snippet.upper().find("GENERAL FRAMEWORK")
    if idx < 0:
        return []
    end_idx = snippet.upper().find("SEMESTER I", idx)
    block = snippet[idx : end_idx if end_idx > idx else len(snippet)]

    rows: list[dict] = []
    row_re = re.compile(
        r"^\s*(HUMANITIES|SCIENCES|ENGINEERING|CSE|PROJECT|Mandatory\s+Courses|TOTAL)\b(.*)$",
        re.IGNORECASE | re.MULTILINE,
    )
    for m in row_re.finditer(block):
        cat = m.group(1).upper()
        rest = re.sub(r"\s+", " ", m.group(2)).strip()
        nums = re.findall(r"\b\d+\b", rest)
        desc = re.sub(r"\b\d+\b", "", rest).strip(" -–[](),.")
        if cat == "TOTAL" and nums:
            rows.append({
                "category": "TOTAL",
                "description": "Total Program Credits",
                "courses": None,
                "credits": int(nums[0]),
                "aicte_recommended": int(nums[1]) if len(nums) > 1 else None,
            })
        elif "MANDATORY" in cat:
            rows.append({
                "category": "MANDATORY",
                "description": "Mandatory Non-Credit Courses (Environmental Sciences, Induction, Indian Constitution, Essence of Indian Knowledge Tradition)",
                "courses": int(nums[0]) if nums else 2,
                "credits": 0,
                "aicte_recommended": 0,
            })
        elif len(nums) >= 2:
            rows.append({
                "category": cat,
                "description": desc or cat.title(),
                "courses": int(nums[0]),
                "credits": int(nums[1]),
                "aicte_recommended": int(nums[2]) if len(nums) >= 3 else None,
            })
    return rows


def _parse_general_info(page_texts: list[str], page_type_idx: dict[int, str], page_tables: dict) -> dict:
    cover_text = page_texts[0] if page_texts else ""
    campuses = ""
    campus_m = re.search(r"\(([A-Z,\s]+(?:CHENNAI|COIMBATORE|BANGALORE|AMRITAPURI|AMARAVATI)[A-Z,\s]*)\)", cover_text)
    if campus_m:
        campuses = re.sub(r"\s+", " ", campus_m.group(1)).strip().title()
    school_m = re.search(r"^\s*(School\s+of\s+[^\n]+)", cover_text, re.IGNORECASE | re.MULTILINE)
    degree_m = re.search(r"^\s*(B\.Tech[^\n]+)", cover_text, re.IGNORECASE | re.MULTILINE)

    result = {
        "school": school_m.group(1).strip() if school_m else "School of Computing",
        "campuses": campuses or "Amaravati, Amritapuri, Bangalore, Coimbatore, Chennai",
        "degree": degree_m.group(1).strip() if degree_m else "B.Tech - Computer Science and Engineering",
        "abbreviations": {},
        "program_outcomes": [],
        "program_educational_objectives": [],
        "program_specific_outcomes": [],
        "framework": _parse_general_framework(page_texts),
    }

    gi_pages = sorted(i for i, t in page_type_idx.items() if t == PAGE_GENERAL_INFO)
    combined = "\n".join(page_texts[i] for i in gi_pages)

    # Abbreviations
    abbrev_re = re.compile(r"^([A-Z]{2,6})\s*[:\-–]\s*(.+)", re.MULTILINE)
    for m in abbrev_re.finditer(combined):
        abbr, desc = m.group(1).strip(), re.sub(r"\s+", " ", m.group(2)).strip()
        if len(abbr) >= 2 and len(desc) > 3:
            result["abbreviations"][abbr] = desc

    # POs
    for m in _PO_LINE_RE.finditer(combined):
        result["program_outcomes"].append(
            {"id": m.group(1).upper(), "description": re.sub(r"\s+", " ", m.group(2)).strip()}
        )

    if not result["program_outcomes"]:
        collecting = False
        current_id = None
        buf: list[str] = []
        for raw_line in combined.split("\n"):
            line = raw_line.strip()
            upper_l = line.upper()
            if "PROGRAM OUTCOMES" in upper_l or "PROGRAM OUTCOME (PO)" in upper_l:
                collecting = True
                continue
            if collecting:
                if "PROGRAM EDUCATIONAL" in upper_l or "PROGRAM SPECIFIC" in upper_l:
                    if current_id and buf:
                        result["program_outcomes"].append(
                            {"id": current_id, "description": re.sub(r"\s+", " ", " ".join(buf)).strip()}
                        )
                    collecting = False
                    current_id, buf = None, []
                    continue
                nm = re.match(r"^(\d+)\.\s+(.+)", line)
                if nm:
                    if current_id and buf:
                        result["program_outcomes"].append(
                            {"id": current_id, "description": re.sub(r"\s+", " ", " ".join(buf)).strip()}
                        )
                    current_id = f"PO{nm.group(1)}"
                    buf = [nm.group(2).strip()]
                elif current_id and line:
                    buf.append(line)
        if collecting and current_id and buf:
            result["program_outcomes"].append(
                {"id": current_id, "description": re.sub(r"\s+", " ", " ".join(buf)).strip()}
            )

    def _collect_bullet_block(start_marker: str, stop_markers: list[str], prefix: str) -> list[dict]:
        items: list[dict] = []
        upper_all = combined.upper()
        idx = upper_all.find(start_marker)
        if idx < 0:
            return items
        end = len(combined)
        for sm in stop_markers:
            s_idx = upper_all.find(sm, idx + len(start_marker))
            if 0 <= s_idx < end:
                end = s_idx
        block = combined[idx:end]
        buf: list[str] = []
        counter = 1
        for raw_line in block.splitlines()[1:]:
            line = raw_line.strip()
            if not line:
                continue
            bm = _PEO_BULLET_RE.match(line)
            if bm:
                if buf:
                    desc = re.sub(r"\s+", " ", " ".join(buf)).strip()
                    if len(desc) > 10:
                        items.append({"id": f"{prefix}{counter}", "description": desc})
                        counter += 1
                buf = [bm.group(1).strip()]
            elif buf:
                if re.match(r"^(Department of|General Framework|2023 Onwards)", line, re.IGNORECASE):
                    break
                buf.append(line)
        if buf:
            desc = re.sub(r"\s+", " ", " ".join(buf)).strip()
            if len(desc) > 10:
                items.append({"id": f"{prefix}{counter}", "description": desc})
        return items

    for m in _PSO_LINE_RE.finditer(combined):
        result["program_specific_outcomes"].append(
            {"id": m.group(1).upper(), "description": re.sub(r"\s+", " ", m.group(2)).strip()}
        )
    if not result["program_specific_outcomes"]:
        result["program_specific_outcomes"] = _collect_bullet_block(
            "PROGRAM SPECIFIC OUTCOME",
            ["DEPARTMENT OF", "GENERAL FRAMEWORK", "CURRICULUM"],
            "PSO",
        )

    result["program_educational_objectives"] = _collect_bullet_block(
        "PROGRAM EDUCATIONAL OBJECTIVES",
        ["PROGRAM SPECIFIC OUTCOME", "DEPARTMENT OF", "GENERAL FRAMEWORK"],
        "PEO",
    )

    return result


def _parse_semester_tables(page_texts: list[str], page_type_idx: dict[int, str], page_tables: dict) -> list[dict]:
    """Parse semester course lists and electiveCourse slots without slot_id truncation collisions."""
    semesters: dict[int, dict] = {}
    sem_pages = sorted(i for i, t in page_type_idx.items() if t == PAGE_SEMESTER_TABLE)
    extra_pages = [
        i
        for i in range(2, min(15, len(page_texts)))
        if page_type_idx.get(i) not in {PAGE_GENERAL_INFO, PAGE_COURSE_DETAIL}
    ]
    check_pages = sorted(set(sem_pages + extra_pages))

    current_sem = None
    stop_parsing = False

    for page_num in check_pages:
        if stop_parsing:
            break
        lines = page_texts[page_num].split("\n")

        for i, line in enumerate(lines):
            line_stripped = re.sub(r"\s+", " ", line.strip())
            if not line_stripped:
                continue

            if re.match(r"^(?:Professional\s+Electives|Electives?\s+in\s+|Total\s+Credits\b)", line_stripped, re.IGNORECASE):
                current_sem = None
                stop_parsing = True
                break

            sem_m = _SEMESTER_HDR_RE.match(line_stripped)
            if sem_m:
                sem_label = sem_m.group(1)
                sem_num = _roman_to_int(sem_label)
                if sem_num not in semesters:
                    semesters[sem_num] = {
                        "number": sem_num,
                        "label": f"Semester {sem_label.upper() if not sem_label.isdigit() else sem_label}",
                        "courses": [],
                        "elective_slots": [],
                        "total_credits": None,
                        "total_credits_optional": None,
                    }
                current_sem = semesters[sem_num]
                continue

            if current_sem is None:
                continue

            tot_m = _TOTAL_CREDITS_RE.search(line_stripped)
            if tot_m:
                try:
                    current_sem["total_credits"] = int(tot_m.group(1))
                    if tot_m.group(2):
                        current_sem["total_credits_optional"] = int(tot_m.group(2))
                except Exception:
                    pass
                current_sem = None
                continue

            code_m = _COURSE_CODE_RE.search(line_stripped)
            if code_m:
                code = code_m.group(0).upper()
                cat = line_stripped[: code_m.start()].strip()
                rest = line_stripped[code_m.end() :].strip()

                ltp_m = re.search(r"((?:\[\d+\]|\d+)[-\s]+(?:\[\d+\]|\d+)[-\s]+(?:\[\d+\]|\d+))", rest)
                title = rest[: ltp_m.start()].strip() if ltp_m else rest
                if ltp_m is None and i + 1 < len(lines):
                    next_line = re.sub(r"\s+", " ", lines[i + 1].strip())
                    if next_line and not _COURSE_CODE_RE.search(next_line):
                        ltp_m2 = re.search(r"((?:\[\d+\]|\d+)[-\s]+(?:\[\d+\]|\d+)[-\s]+(?:\[\d+\]|\d+))", next_line)
                        if ltp_m2:
                            title = (title + " " + next_line[: ltp_m2.start()].strip()).strip()
                            ltp_m = ltp_m2
                            rest = next_line[ltp_m2.start() :]

                cred = ""
                eval_ = ""
                l_val, t_val, p_val = None, None, None
                if ltp_m:
                    ltp_parts = re.findall(r"\[\d+\]|\d+", ltp_m.group(1))
                    if len(ltp_parts) == 3:
                        l_val, t_val, p_val = ltp_parts
                    after_ltp = rest[ltp_m.end() :].strip()
                    parts = after_ltp.split()
                    if parts:
                        cred = parts[0]
                    er = _EVAL_RATIO_RE.search(after_ltp)
                    if er and _valid_eval_ratio(int(er.group(1)), int(er.group(2))):
                        eval_ = f"{er.group(1)}:{er.group(2)}"
                elif rest.endswith("P/F") or " P/F" in rest:
                    title = re.sub(r"\s*P/F.*$", "", title).strip()
                    cred = "P/F"
                    eval_ = "P/F"

                title = re.sub(r"\s+", " ", title).strip()
                if not any(c["code"] == code for c in current_sem["courses"]):
                    current_sem["courses"].append(
                        {
                            "code": code,
                            "title": title,
                            "category": cat,
                            "L": l_val,
                            "T": t_val,
                            "P": p_val,
                            "credits": cred,
                            "evaluation_pattern_ratio": eval_,
                        }
                    )
                continue

            # Elective slot lines -> electiveCourse nodes (e.g. Professional Elective I..VI, AVP I..II, Free Elective I..II)
            slot_m = re.search(
                r"(Professional\s+Elective\s+[IVX]+|Free\s+Elective\s+[IVX]+\**|Amrita\s+Value\s+Programme\s+[IVX]+)",
                line_stripped,
                re.IGNORECASE,
            )
            if slot_m:
                slot_title = re.sub(r"\*+", "", slot_m.group(1)).strip()
                cat = re.sub(r"\s+", " ", line_stripped[: slot_m.start()]).strip()
                after_slot = line_stripped[slot_m.end() :].strip()
                # Look ahead 1 line if Cat wrapped (e.g. CSE/ ... ENGG on next line)
                if not cat and i + 1 < len(lines):
                    nxt = lines[i + 1].strip()
                    if re.fullmatch(r"[A-Z/]{2,10}", nxt):
                        cat = nxt

                ltp_m = re.search(r"(\d+[-\s]+\d+[-\s]+\d+)", after_slot)
                cred = ""
                eval_ = ""
                if ltp_m:
                    after_ltp = after_slot[ltp_m.end() :].strip()
                    parts = after_ltp.split()
                    if parts:
                        cred = parts[0]
                    er = _EVAL_RATIO_RE.search(after_ltp)
                    if er and _valid_eval_ratio(int(er.group(1)), int(er.group(2))):
                        eval_ = f"{er.group(1)}:{er.group(2)}"

                # Determine elective group type for automatic basket mapping
                sl_lower = slot_title.lower()
                if "amrita value programme" in sl_lower:
                    slot_type = "AVP"
                elif "free elective" in sl_lower:
                    slot_type = "FREE"
                else:
                    slot_type = "PE"

                if not any(s["name"].lower() == slot_title.lower() for s in current_sem["elective_slots"]):
                    current_sem["elective_slots"].append(
                        {
                            "name": slot_title,
                            "label": line_stripped,
                            "slot_type": slot_type,
                            "category": cat,
                            "credits": cred,
                            "evaluation_pattern_ratio": eval_,
                        }
                    )

    return sorted(semesters.values(), key=lambda s: s["number"])


def _parse_elective_baskets(page_texts: list[str], page_type_idx: dict[int, str], page_tables: dict) -> list[dict]:
    """Extract all 12 elective baskets cleanly across page breaks."""
    baskets: dict[str, dict] = {}
    current_basket = None

    def norm_basket_name(name: str) -> str:
        name = re.sub(r"^List\s+of\s+courses\s+in\s+", "", name, flags=re.IGNORECASE)
        name = re.sub(r"\s+", " ", name).strip()
        if name.isupper():
            name = name.title()
        return name

    elective_start = next((p for p, t in sorted(page_type_idx.items()) if t == PAGE_ELECTIVE), 7)
    first_course_page = next((p for p, t in sorted(page_type_idx.items()) if t == PAGE_COURSE_DETAIL), len(page_texts))

    for page_num in range(elective_start, min(first_course_page + 1, len(page_texts))):
        lines = page_texts[page_num].split("\n")
        for i, raw_line in enumerate(lines):
            line_stripped = re.sub(r"\s+", " ", raw_line.strip())
            if not line_stripped:
                continue

            if re.match(r"^(?:B\.Tech\s*-|SYLLABUS\s+\d{4}\s+onwards)", line_stripped, re.IGNORECASE) or _COURSE_HEADER_RE.match(raw_line):
                break

            if _BASKET_HDR_RE.match(line_stripped):
                bname = norm_basket_name(line_stripped)
                current_basket = bname
                if current_basket not in baskets:
                    baskets[current_basket] = {
                        "name": current_basket,
                        "target_audience": "other_branches" if "other branches" in bname.lower() else "cse",
                        "courses": [],
                    }
                continue

            if not current_basket:
                continue

            code_m = _COURSE_CODE_RE.search(line_stripped)
            if code_m:
                code = code_m.group(0).upper()
                cat = line_stripped[: code_m.start()].strip()
                rest = line_stripped[code_m.end() :].strip()

                ltp_m = re.search(r"(\d+[-\s]+\d+[-\s]+\d+)", rest)
                title = rest[: ltp_m.start()].strip() if ltp_m else rest

                if ltp_m is None and i + 1 < len(lines):
                    next_line = re.sub(r"\s+", " ", lines[i + 1].strip())
                    if next_line and not _COURSE_CODE_RE.search(next_line):
                        ltp_m2 = re.search(r"(\d+[-\s]+\d+[-\s]+\d+)", next_line)
                        if ltp_m2:
                            title = (title + " " + next_line[: ltp_m2.start()].strip()).strip()
                            ltp_m = ltp_m2
                            rest = next_line[ltp_m2.start() :]

                cred = ""
                eval_ = ""
                l_val, t_val, p_val = None, None, None
                if ltp_m:
                    ltp_parts = re.findall(r"\d+", ltp_m.group(1))
                    if len(ltp_parts) == 3:
                        l_val, t_val, p_val = ltp_parts
                    after_ltp = rest[ltp_m.end() :].strip()
                    parts = after_ltp.split()
                    if parts:
                        cred = parts[0]
                    er = _EVAL_RATIO_RE.search(after_ltp)
                    if er and _valid_eval_ratio(int(er.group(1)), int(er.group(2))):
                        eval_ = f"{er.group(1)}:{er.group(2)}"

                title = re.sub(r"\s+", " ", title).strip()
                if not any(ec["code"] == code for ec in baskets[current_basket]["courses"]):
                    baskets[current_basket]["courses"].append(
                        {
                            "code": code,
                            "title": title,
                            "category": cat,
                            "L": l_val,
                            "T": t_val,
                            "P": p_val,
                            "credits": cred,
                            "evaluation_pattern_ratio": eval_,
                        }
                    )

    return [b for b in baskets.values() if b["courses"]]


def _parse_copo_table(tables: list) -> list[dict]:
    mappings = []
    for table in tables:
        if not table or len(table) < 2:
            continue
        header_row_idx = None
        header_cols = {}
        for ri, row in enumerate(table):
            cells = [str(c or "").strip() for c in (row or [])]
            po_hits = [
                (ci, c)
                for ci, c in enumerate(cells)
                if re.match(r"^P[OS]\d+$", c.strip(), re.IGNORECASE)
            ]
            if len(po_hits) >= 3:
                header_row_idx = ri
                header_cols = {ci: c.upper() for ci, c in po_hits}
                break
        if header_row_idx is None:
            continue
        for row in table[header_row_idx + 1 :]:
            cells = [str(c or "").strip() for c in (row or [])]
            co_id = None
            for c in cells:
                if re.match(r"^CO\d+$", c.strip(), re.IGNORECASE):
                    co_id = c.strip().upper()
                    break
            if not co_id:
                continue
            for ci, po_label in header_cols.items():
                if ci < len(cells):
                    val = cells[ci].strip()
                    if val and val not in ("-", "", "None", "null"):
                        try:
                            weight = int(val)
                            if weight > 0:
                                mappings.append(
                                    {"co_id": co_id, "po_id": po_label, "weight": weight}
                                )
                        except ValueError:
                            pass
    return mappings


def _parse_copo_from_words(course_pages: list[int]) -> list[dict]:
    """Geometry-based word-coordinate CO-PO parser for borderless or stacked-header tables."""
    if _live_doc is None or not course_pages:
        return []

    mappings: list[dict] = []
    for p_num in course_pages:
        try:
            words = _live_doc[p_num].get_text("words")  # (x0, y0, x1, y1, word, block, line, word_no)
        except Exception as exc:
            logger.debug("Word extraction failed on page %d: %s", p_num + 1, exc)
            continue
        if not words:
            continue

        # Locate PO/PSO header y-band
        po_tokens = [w for w in words if re.match(r"^(?:PO\d{1,2}|PSO\d|PO\s*)$", w[4], re.IGNORECASE)]
        if len(po_tokens) < 4:
            continue

        # Cluster into line bands by y0
        po_tokens.sort(key=lambda w: (round(w[1] / 6), w[0]))
        # Find direct single-line headers like PO1..PO12 PSO1..PSO2
        direct_hdrs = [w for w in words if re.match(r"^(?:PO|PSO)\d{1,2}$", w[4], re.IGNORECASE)]
        col_centers: list[tuple[float, str]] = []

        if len(direct_hdrs) >= 5:
            # Take tightest y-cluster of headers
            hdr_y = min(w[1] for w in direct_hdrs)
            row_hdrs = [w for w in direct_hdrs if abs(w[1] - hdr_y) <= 10]
            # Handle stacked split headers like PO1 / 0 (for PO10) right below
            if len(row_hdrs) >= 5:
                for w in row_hdrs:
                    cx = (w[0] + w[2]) / 2.0
                    lbl = w[4].upper()
                    # Check if a digit sits within 14px below & 10px x-radius (e.g. PO + 1, PO1 + 0)
                    below = [
                        dw[4]
                        for dw in words
                        if 4 <= (dw[1] - w[1]) <= 18
                        and abs(((dw[0] + dw[2]) / 2.0) - cx) <= 10
                        and re.fullmatch(r"\d", dw[4])
                    ]
                    if below and lbl in ("PO1", "PSO", "PO"):
                        lbl = lbl + below[0]
                    col_centers.append((cx, lbl))
        else:
            # Handle fully split 2-line headers: line1 = PO / PO1 / PSO, line2 = 1..12
            hdr_words = [w for w in words if w[4].upper() in ("PO", "PO1", "PSO")]
            if len(hdr_words) >= 5:
                hy = hdr_words[0][1]
                hdr_words = [w for w in hdr_words if abs(w[1] - hy) <= 6]
                for w in hdr_words:
                    cx = (w[0] + w[2]) / 2.0
                    below = [
                        dw[4]
                        for dw in words
                        if 4 <= (dw[1] - w[1]) <= 18
                        and abs(((dw[0] + dw[2]) / 2.0) - cx) <= 12
                        and re.fullmatch(r"\d", dw[4])
                    ]
                    if below:
                        col_centers.append((cx, f"{w[4].upper()}{below[0]}"))

        if len(col_centers) < 4:
            continue

        col_centers.sort(key=lambda x: x[0])
        min_hdr_y = max(w[1] for w in words if re.match(r"^(?:PO|PSO)\d{0,2}$", w[4], re.IGNORECASE) and any(abs(((w[0] + w[2]) / 2.0) - cx) < 12 for cx, _ in col_centers))

        # Locate CO1..CO6 rows below header
        co_rows = sorted(
            [w for w in words if re.fullmatch(r"CO\d+", w[4], re.IGNORECASE) and w[1] > min_hdr_y],
            key=lambda w: w[1],
        )
        for idx, co_w in enumerate(co_rows):
            co_id = co_w[4].upper()
            cy = co_w[1]
            max_y = co_rows[idx + 1][1] - 3 if idx + 1 < len(co_rows) else cy + 16
            row_digits = [
                w
                for w in words
                if cy - 5 <= w[1] <= min(cy + 10, max_y)
                and re.fullmatch(r"[1-3]", w[4])
                and w[0] > co_w[2] + 5
            ]
            for dw in row_digits:
                dx = (dw[0] + dw[2]) / 2.0
                nearest_cx, nearest_po = min(col_centers, key=lambda c: abs(c[0] - dx))
                if abs(nearest_cx - dx) <= 22:
                    mappings.append({"co_id": co_id, "po_id": nearest_po, "weight": int(dw[4])})

        if mappings:
            return mappings
    return mappings


def _split_outside_parens(text: str) -> list[str]:
    """Split on comma, semicolon, en-dash, or spaced/unspaced dash outside parentheses."""
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    n = len(text)
    i = 0
    while i < n:
        ch = text[i]
        if ch in "([":
            depth += 1
            buf.append(ch)
        elif ch in ")]":
            depth = max(0, depth - 1)
            buf.append(ch)
        elif depth == 0 and ch in ",;–":
            parts.append("".join(buf))
            buf = []
        elif depth == 0 and ch == "-":
            prev_ch = text[i - 1] if i > 0 else " "
            next_ch = text[i + 1] if i + 1 < n else " "
            # Split on " - ", " -X", "X- " but preserve hyphenated tokens like "p-n" or "real-time" or "4-bit"
            if prev_ch.isspace() or next_ch.isspace():
                parts.append("".join(buf))
                buf = []
            else:
                buf.append(ch)
        else:
            buf.append(ch)
        i += 1
    if buf:
        parts.append("".join(buf))
    return parts


_SHORT_TECH_TOPICS = {
    "html", "css", "dom", "xml", "sql", "api", "rpc", "mac", "tcp", "udp",
    "arp", "rsa", "aes", "des", "svd", "pca", "svm", "cnn", "rnn", "gan",
    "llm", "nlp", "iot", "iaas", "paas", "saas", "rtos", "uml", "jvm",
    "cpu", "gpu", "alu", "ram", "rom", "bfs", "dfs", "dag", "bst", "avl",
    "b+ tree", "kmp", "np", "p", "sat", "cfg", "pda", "dfa", "nfa", "tm",
    "kcl", "kvl", "bjt", "fet", "rms",
}
_TOPIC_STOP_WORDS = {
    "introduction", "overview", "summary", "conclusion", "and", "the", "of",
    "in", "ways", "self", "back", "best", "mean", "loss", "flop", "etc",
}


def _clean_topic_list(raw_text: str) -> list[str]:
    """Extract clean, normalized syllabus topics without parentheses/hyphen breakage."""
    cleaned = re.sub(r"\s*\n\s*", " ", raw_text)
    # Remove CO markers like (CO2), hour markers like "12 hours", and numbered experiment prefixes
    cleaned = re.sub(r"\(CO\s*\d+\)", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b\d+\s+hours?\b", "", cleaned, flags=re.IGNORECASE)

    topics: list[str] = []
    seen_lower: set[str] = set()
    for chunk in _split_outside_parens(cleaned):
        # Further split dot-separated multi-concept lines (e.g. "Phasor representation. Introduction to 3-phase systems")
        sub_pieces = re.split(r"\.\s+(?=[A-Z])", chunk) if len(chunk) > 35 else [chunk]
        for rt in sub_pieces:
            t = re.sub(r"^(?:\d{1,2}[\.\)]\s+|[•●\*]\s+)", "", rt.strip())
            t = re.sub(r"\s+", " ", t.strip(" .:\t\r\n•●*-–"))
            if not t or len(t) > 160 or _is_junk_topic(t):
                continue
            # Reject stray unmatched parentheses artifacts like "GTK)" or "(HTML"
            if (t.count("(") != t.count(")")) and len(t) < 25:
                t = t.strip("()")
            tl = t.lower()
            if tl in _TOPIC_STOP_WORDS:
                continue
            if (len(t) >= 4 or tl in _SHORT_TECH_TOPICS) and tl not in seen_lower:
                seen_lower.add(tl)
                topics.append(t)
    return topics


def _parse_units(body: str) -> list[dict]:
    """Parse syllabus units preserving descriptive titles and clean topic lists."""
    units: list[dict] = []
    syl_hdr = re.search(r"^\s*Syllabus\b", body, re.IGNORECASE | re.MULTILINE)
    search_start = syl_hdr.end() if syl_hdr else 0
    sub_body = body[search_start:]

    stop_match = _STOP_SECTION_RE.search(sub_body)
    safe_end = stop_match.start() if stop_match else len(sub_body)
    body_trimmed = sub_body[:safe_end]

    unit_matches = list(_UNIT_RE.finditer(body_trimmed))
    if unit_matches:
        for idx, u_match in enumerate(unit_matches):
            unit_num = _roman_to_int(u_match.group(1))
            u_start = u_match.end()
            u_end = unit_matches[idx + 1].start() if idx + 1 < len(unit_matches) else safe_end

            unit_body = body_trimmed[u_start:u_end]
            inner_stop = _STOP_SECTION_RE.search(unit_body)
            if inner_stop:
                unit_body = unit_body[: inner_stop.start()]

            # Extract descriptive subtitle if the first line after Unit N is a short heading
            unit_title = f"Unit {unit_num}"
            u_lines = [ln.strip() for ln in unit_body.splitlines() if ln.strip()]
            if u_lines:
                first_ln = re.sub(r"^[:\-–\s]+", "", u_lines[0]).strip()
                if (
                    3 <= len(first_ln) <= 75
                    and not first_ln.endswith((",", ";", "."))
                    and "–" not in first_ln
                    and len(u_lines) >= 2
                ):
                    unit_title = f"Unit {unit_num}: {first_ln}"

            topics = _clean_topic_list(unit_body)
            if topics:
                units.append({"number": unit_num, "title": unit_title, "topics": topics})
        return units

    if syl_hdr and body_trimmed.strip():
        topics = _clean_topic_list(body_trimmed)
        if topics:
            units.append({"number": 1, "title": "Syllabus", "topics": topics})

    return units


def _parse_lab_experiments(body: str) -> list[str]:
    """Extract practical/lab experiment items when present (e.g. 23MAT107, 23CSE111, 23CSE211)."""
    m = re.search(
        r"^\s*(?:Lab\s+Experiments?|List\s+of\s+Experiments?|Laboratory\s+Experiments?)\s*[:\-]?\s*([\s\S]*?)(?=\n\s*(?:TEXT\s*BOOKS?|REFERENCES?|Evaluation\s+Pattern|CO[\s\-–]*PO)\b|\Z)",
        body,
        re.IGNORECASE | re.MULTILINE,
    )
    if not m:
        return []
    block = m.group(1).strip()
    if not block:
        return []

    experiments: list[str] = []
    buf: list[str] = []
    for raw_line in block.splitlines():
        line = re.sub(r"\s+", " ", raw_line.strip())
        if not line:
            continue
        if re.match(r"^(?:\d{1,2}[\.\)]|[•●\*])\s+", line):
            if buf:
                experiments.append(" ".join(buf))
            buf = [re.sub(r"^(?:\d{1,2}[\.\)]|[•●\*])\s+", "", line).strip()]
        elif buf:
            buf.append(line)
        elif len(line) > 8:
            buf = [line]
    if buf:
        experiments.append(" ".join(buf))
    return [e for e in experiments if len(e) > 5]


def _parse_evaluation_pattern(body: str, tables: list, fallback_ratio: str = "") -> dict | None:
    """Parse structured + borderless + text-only evaluation patterns without false ISBN/ISO ratios."""
    pattern: dict = {"ratio": None, "internal": [], "external": []}

    # 1. Context-anchored ratio extraction
    ratio_m = _EVAL_RATIO_STRICT_RE.search(body)
    if ratio_m:
        if ratio_m.group(3):  # P/F
            pattern["ratio"] = "P/F"
        else:
            a, b = int(ratio_m.group(1)), int(ratio_m.group(2))
            if _valid_eval_ratio(a, b):
                pattern["ratio"] = f"{a}:{b}"

    def _add_comp(branch: str, name: str, raw_marks: str, note: str = "") -> None:
        key, canonical = _canonicalize_assessment(name, branch)
        num_m = re.search(r"(\d+)", raw_marks)
        marks_val = int(num_m.group(1)) if num_m else raw_marks
        # Extract parenthetical exam note e.g. "(50 Marks; 2 hours exam)"
        extra_note = note
        if not extra_note:
            pm = re.search(r"\(([^)]*(?:mark|hour|exam|project)[^)]*)\)", raw_marks, re.IGNORECASE)
            if pm:
                extra_note = re.sub(r"\s+", " ", pm.group(1)).strip()
        exam_m = re.search(r"(\d+)\s*marks?", extra_note, re.IGNORECASE)
        exam_marks = int(exam_m.group(1)) if exam_m else None

        existing = next((c for c in pattern[branch] if c["key"] == key), None)
        if existing:
            return
        pattern[branch].append({
            "key": key,
            "name": canonical,
            "marks": marks_val,
            "exam_marks": exam_marks,
            "note": extra_note,
        })

    # 2. Try PyMuPDF tables first
    for table in tables:
        if not table or len(table) < 2:
            continue
        header = [str(c or "").strip().lower() for c in (table[0] or [])]
        has_assessment = any("assessment" in h for h in header)
        has_internal = any("internal" in h for h in header)
        has_external = any(k in " ".join(header) for k in ["external", "end semester", "end\nsemester"])
        if not (has_assessment or (has_internal and has_external)):
            continue

        assessment_col = next((i for i, h in enumerate(header) if "assessment" in h), 0)
        internal_col = next((i for i, h in enumerate(header) if "internal" in h), None)
        external_col = next((i for i, h in enumerate(header) if "external" in h or "end" in h), None)

        for row in table[1:]:
            cells = [str(c or "").strip() for c in (row or [])]
            if not cells or len(cells) <= assessment_col:
                continue
            comp_name = cells[assessment_col]
            if not comp_name or comp_name.lower() == "assessment":
                continue

            int_marks = cells[internal_col].strip() if internal_col is not None and internal_col < len(cells) else ""
            ext_marks = cells[external_col].strip() if external_col is not None and external_col < len(cells) else ""
            if int_marks and int_marks not in ("-", "") and re.search(r"\d+", int_marks):
                _add_comp("internal", comp_name, int_marks)
            if ext_marks and ext_marks not in ("-", "") and re.search(r"\d+", ext_marks):
                _add_comp("external", comp_name, ext_marks)

        if pattern["internal"] or pattern["external"]:
            break

    # 3. Borderless text-block fallback (handles 23PHY242, 23EEE184, 23CSE399, 23LAW300, etc.)
    if not pattern["internal"] and not pattern["external"]:
        ev_idx = body.lower().find("evaluation pattern")
        if ev_idx < 0:
            ev_idx = body.lower().find("internal marks")
        if ev_idx >= 0:
            ev_block = body[ev_idx : ev_idx + 900]
            # Format A: "Internal Marks: 70\nExternal Marks: 30"
            im = re.search(r"Internal\s+Marks\s*[:\-]\s*(\d+)", ev_block, re.IGNORECASE)
            em = re.search(r"External\s+Marks\s*[:\-]\s*(\d+)", ev_block, re.IGNORECASE)
            if im:
                _add_comp("internal", "Internal Assessment", im.group(1))
            if em:
                _add_comp("external", "External Assessment", em.group(1))

            # Format B: "Online Examination - 100 marks"
            om = re.search(r"(Online\s+Exam(?:ination)?)\s*[-–:]\s*(\d+)\s*marks?", ev_block, re.IGNORECASE)
            if om:
                _add_comp("external", om.group(1), om.group(2))

            # Format C: Space-aligned lines ("Periodical 1 (P1)  15", "End Semester  30 (50 Marks; 2 hours exam)")
            if not pattern["internal"] and not pattern["external"]:
                lines = ev_block.splitlines()
                for li, raw_l in enumerate(lines):
                    ls = re.sub(r"\s+", " ", raw_l.strip())
                    if not ls or re.match(r"^(?:Evaluation\s+Pattern|Assessment\b|\*(?:CA|CAT|CAL)\s*[–-])", ls, re.IGNORECASE):
                        continue
                    # Merge wrapped line like "(CA)" or "(CAL)" onto previous line if needed
                    if re.match(r"^\([A-Z]+\)$", ls):
                        continue
                    row_m = re.match(
                        r"^([\*A-Za-z0-9\s\(\)\-\–/]+?)\s+(\d{1,3})(?:\s+(\([^)]+\)))?$",
                        ls,
                    )
                    if not row_m and li + 1 < len(lines) and re.search(r"\d{1,3}", ls):
                        # Handle "(50 Marks;\n2 hours exam)" wrapped note
                        combined_l = ls + " " + re.sub(r"\s+", " ", lines[li + 1].strip())
                        row_m = re.match(
                            r"^([\*A-Za-z0-9\s\(\)\-\–/]+?)\s+(\d{1,3})\s*(\([^)]+\))",
                            combined_l,
                        )
                    if row_m:
                        cname = row_m.group(1).strip()
                        cmarks = row_m.group(2).strip()
                        cnote = (row_m.group(3) or "").strip("() ")
                        if "end" in cname.lower() and "semester" in cname.lower() or "external" in cname.lower():
                            _add_comp("external", cname, f"{cmarks} ({cnote})" if cnote else cmarks, note=cnote)
                        elif any(k in cname.lower() for k in ("periodical", "mid", "continuous", "ca", "cat", "cal", "quiz", "internal")):
                            _add_comp("internal", cname, cmarks, note=cnote)

    # 4. Derive or backfill ratio cleanly
    if not pattern["ratio"] and fallback_ratio:
        pattern["ratio"] = fallback_ratio
    if not pattern["ratio"] and (pattern["internal"] or pattern["external"]):
        int_sum = sum(c["marks"] for c in pattern["internal"] if isinstance(c["marks"], int))
        ext_sum = sum(c["marks"] for c in pattern["external"] if isinstance(c["marks"], int))
        if _valid_eval_ratio(int_sum, ext_sum):
            pattern["ratio"] = f"{int_sum}:{ext_sum}"

    return (
        pattern
        if (pattern["internal"] or pattern["external"] or pattern["ratio"])
        else None
    )


def _parse_bibliography_section(body: str, markers: list[str], end_markers: list[str] | None = None) -> list[str]:
    section = _extract_section(
        body,
        markers,
        end_markers or ["Reference", "REFERENCE", "Evaluation Pattern", "Lab Experiments", "Unit "],
    )
    if not section:
        return []
    section = re.sub(r"^\(s\)\s*", "", section.strip(), flags=re.IGNORECASE)
    entries: list[str] = []
    buf: list[str] = []
    for raw_line in section.split("\n"):
        line = re.sub(r"\s+", " ", raw_line.strip())
        if not line:
            if buf:
                entries.append(" ".join(buf))
                buf = []
            continue
        if re.match(r"^(?:Evaluation\s+Pattern|Lab\s+Experiments|Assessment|\*CA|\*\*End)", line, re.IGNORECASE):
            break
        starts_new = bool(re.match(r"^(?:\d+\.|[•●\*])\s+", line))
        cleaned = re.sub(r"^(?:\d+\.|[•●\*])\s+", "", line).strip()
        if not cleaned:
            continue
        if starts_new or (buf and buf[-1].endswith(".")):
            if buf:
                entries.append(" ".join(buf))
                buf = []
        buf.append(cleaned)
    if buf:
        entries.append(" ".join(buf))
    return [e for e in entries if len(e) > 10]


def _get_course_tables(page_texts: list[str], block: list[int]) -> list:
    tables = []
    for page_num in block:
        if _COPO_KEYWORD_RE.search(page_texts[page_num]) or "evaluation pattern" in page_texts[page_num].lower():
            tables.extend(_get_page_tables_inline(page_num))
    return tables


def _find_course_header_spans(full_text: str) -> list[dict]:
    """Locate both standard L-T-P-C headers and short AVP/ADM syllabus headers without false hits."""
    spans: list[dict] = []
    for m in _COURSE_HEADER_RE.finditer(full_text):
        spans.append({
            "start": m.start(),
            "end": m.end(),
            "code": m.group(1).upper(),
            "name": re.sub(r"\s+", " ", m.group(2)).strip(),
            "L": m.group(3),
            "T": m.group(4),
            "P": m.group(5),
            "credits": m.group(6) or m.group(7) or m.group(8) or "",
        })

    # Secondary pass for AVP/ADM syllabus section (e.g. pp. 203-205) lacking L-T-P-C
    avp_marker = re.search(r"Courses\s+offered\s+under\s+the\s+framework\s+of\s+Amrita\s+Values?\s+Programmes", full_text, re.IGNORECASE)
    if avp_marker:
        avp_search_start = avp_marker.start()
        for m in _SHORT_AVP_HEADER_RE.finditer(full_text, avp_search_start):
            if any(abs(s["start"] - m.start()) < 5 for s in spans):
                continue
            name_cand = re.sub(r"\s+", " ", m.group(2)).strip()
            # Ensure next line contains prose (not a basket table row with L-T-P)
            after_snippet = full_text[m.end() : m.end() + 180].strip()
            if len(after_snippet) >= 25 and not re.search(r"\d-\d-\d", name_cand):
                spans.append({
                    "start": m.start(),
                    "end": m.end(),
                    "code": m.group(1).upper(),
                    "name": name_cand,
                    "L": "1",
                    "T": "0",
                    "P": "0",
                    "credits": "1",
                })

    spans.sort(key=lambda s: s["start"])
    return spans


def _parse_courses(
    page_texts: list[str],
    page_type_idx: dict[int, str],
    semesters: list[dict],
    baskets: list[dict],
) -> list[dict]:
    courses: list[dict] = []
    seen_codes: set[str] = set()

    # Build lookup of semester/basket evaluation ratios & credits for fallback enrichment
    table_meta: dict[str, dict] = {}
    for sem in semesters:
        for c in sem.get("courses", []):
            table_meta[c["code"]] = c
    for b in baskets:
        for c in b.get("courses", []):
            table_meta.setdefault(c["code"], c)

    first_course_page = next((i for i, t in sorted(page_type_idx.items()) if t == PAGE_COURSE_DETAIL), None)
    if first_course_page is None:
        return courses

    block = list(range(first_course_page, len(page_texts)))
    page_offsets = []
    full_text = ""
    for p in block:
        page_offsets.append((len(full_text), p))
        full_text += page_texts[p] + "\n\x0c\n"

    hdr_spans = _find_course_header_spans(full_text)
    for idx, hdr in enumerate(hdr_spans):
        code = hdr["code"]
        if code in seen_codes:
            continue
        seen_codes.add(code)

        start = hdr["end"]
        end = hdr_spans[idx + 1]["start"] if idx + 1 < len(hdr_spans) else len(full_text)
        body = full_text[start:end]

        course_pages = []
        for oi, (offset, p) in enumerate(page_offsets):
            next_offset = page_offsets[oi + 1][0] if oi + 1 < len(page_offsets) else len(full_text)
            if next_offset > hdr["start"] and offset < end:
                course_pages.append(p)

        need_tables = bool(_COPO_KEYWORD_RE.search(body) or "evaluation pattern" in body.lower())
        course_tables = _get_course_tables(page_texts, course_pages) if need_tables else []

        course = {
            "code": code,
            "name": hdr["name"],
            "L": hdr["L"],
            "T": hdr["T"],
            "P": hdr["P"],
            "credits": hdr["credits"],
            "objectives": [],
            "prerequisites_courses": [],
            "prerequisites_knowledge": [],
            "prereq_raw_parts": [],
            "outcomes": [],
            "co_po_mapping": [],
            "units": [],
            "lab_experiments": [],
            "textbooks": [],
            "references": [],
            "evaluation_pattern": None,
            "description": "",
            "source_pages": course_pages,
        }

        # Objectives
        obj_text = _extract_section(
            body,
            ["Course Objectives", "Course Objective"],
            ["Course Outcomes", "CO1", "Pre-Requisite", "Syllabus", "Unit "],
        )
        if obj_text:
            obj_buf: list[str] = []
            for raw_line in obj_text.split("\n"):
                stripped = re.sub(r"\s+", " ", raw_line.strip())
                if not stripped:
                    continue
                is_bullet = bool(re.match(r"^[•●\-\*]\s+", stripped))
                cleaned = stripped.lstrip("•●-* ").strip()
                if is_bullet:
                    if obj_buf:
                        joined = " ".join(obj_buf)
                        if len(joined) > 15:
                            course["objectives"].append(joined)
                    obj_buf = [cleaned]
                elif obj_buf:
                    obj_buf.append(cleaned)
                elif len(cleaned) > 15:
                    obj_buf = [cleaned]
            if obj_buf:
                joined = " ".join(obj_buf)
                if len(joined) > 15:
                    course["objectives"].append(joined)

        # Prerequisites
        pre_block_m = re.search(
            r"^\s*Pre[-\s]*Requisite(?:\(s\)|s)?\s*[:\-]?\s*([\s\S]*?)(?=\n\s*(?:[•●\*]|Course\s+Objectives?|Course\s+Outcomes?|Syllabus|CO1)\b|[•●]|\Z)",
            body,
            re.IGNORECASE | re.MULTILINE,
        )
        if pre_block_m:
            raw_pre = re.sub(r"\s+", " ", pre_block_m.group(1)).strip(" :-")
            if raw_pre and raw_pre.lower() not in {"nil", "none", "n/a", "-", "na"}:
                parts = re.split(r"[,;]|\s+and\s+(?=\d{2}[A-Za-z]{3}(?:XXX|\d{3})\b)", raw_pre)
                for part in parts:
                    part = part.strip()
                    if not part or part.lower() in {"nil", "none", "n/a", "-"}:
                        continue
                    course["prereq_raw_parts"].append(part)
                    cm = _COURSE_CODE_RE.search(part)
                    if cm:
                        c_code = cm.group(0).upper()
                        if c_code not in course["prerequisites_courses"]:
                            course["prerequisites_courses"].append(c_code)
                    elif len(part) > 3:
                        course["prerequisites_knowledge"].append(part)

        # Course Outcomes
        co_seen = set()
        for m in _CO_LINE_RE.finditer(body):
            co_id = re.sub(r"\s+", "", m.group(1)).upper()
            co_desc = re.sub(r"\s+", " ", m.group(2)).strip()
            if co_id not in co_seen and len(co_desc) > 5:
                co_seen.add(co_id)
                course["outcomes"].append({"id": co_id, "description": co_desc})

        # CO-PO mapping (table pass + borderless word-coordinate fallback)
        course["co_po_mapping"] = _parse_copo_table(course_tables)
        if not course["co_po_mapping"] and _COPO_KEYWORD_RE.search(body):
            course["co_po_mapping"] = _parse_copo_from_words(course_pages)

        # Units, Topics, and Lab Experiments
        course["units"] = _parse_units(body)
        course["lab_experiments"] = _parse_lab_experiments(body)

        # Narrative description fallback for AVP prose courses without Unit/CO structure
        if not course["units"] and not course["objectives"] and not course["outcomes"]:
            prose = re.sub(r"\s+", " ", body.strip())
            if len(prose) > 30:
                course["description"] = prose[:1500]

        # Textbooks & References (including combined "Text Books / References" header)
        course["textbooks"] = _parse_bibliography_section(
            body, ["Textbook", "TEXTBOOK", "TEXT BOOK"]
        )
        if not course["textbooks"]:
            course["textbooks"] = _parse_bibliography_section(
                body,
                ["TEXTBOOKS / REFERENCES", "TEXTBOOKS/REFERENCES", "Text Books / References", "Text Books/References"],
                end_markers=["Evaluation Pattern", "Lab Experiments", "Unit "],
            )
        course["references"] = _parse_bibliography_section(
            body, ["Reference", "REFERENCE", "REFERENCE BOOK"]
        )

        # Evaluation Pattern (including fallback ratio from Semester/Basket overview table)
        fb_ratio = table_meta.get(code, {}).get("evaluation_pattern_ratio", "")
        course["evaluation_pattern"] = _parse_evaluation_pattern(body, course_tables, fallback_ratio=fb_ratio)

        courses.append(course)

    # Resolve wildcard prerequisites (23CSEXXX) & legacy 19MAT* codes dynamically against courses in this PDF
    def _norm_cname(s: str) -> str:
        s = re.sub(r"\b\d{2}[A-Za-z]{3}(?:XXX|\d{3})\b", " ", s, flags=re.IGNORECASE)
        s = re.sub(r"\bstructure\b", "structures", s.lower())
        return re.sub(r"\s+", " ", s).strip(" .,-:")

    pdf_title_to_code: dict[str, str] = {}
    all_valid_codes: set[str] = set(table_meta.keys()) | {c["code"] for c in courses}
    for c in courses:
        nc = _norm_cname(c.get("name", ""))
        if nc and nc not in pdf_title_to_code:
            pdf_title_to_code[nc] = c["code"]
    for code_k, meta_v in table_meta.items():
        nc = _norm_cname(meta_v.get("title", ""))
        if nc and nc not in pdf_title_to_code:
            pdf_title_to_code[nc] = code_k

    for c in courses:
        resolved_codes: list[str] = []
        unresolved_notes: list[str] = []
        for pc in c["prerequisites_courses"]:
            if pc in all_valid_codes and pc != c["code"]:
                if pc not in resolved_codes:
                    resolved_codes.append(pc)
            else:
                # Legacy code (e.g. 19MAT112 Linear Algebra, 19MAT205 Probability and Random Processes)
                matching_part = next((p for p in c["prereq_raw_parts"] if pc in p.upper()), pc)
                norm_title = _norm_cname(matching_part)
                matched = pdf_title_to_code.get(norm_title)
                if matched and matched != c["code"]:
                    if matched not in resolved_codes:
                        resolved_codes.append(matched)
                else:
                    unresolved_notes.append(matching_part)

        for pk in c.get("prerequisites_knowledge", []):
            norm_pk = _norm_cname(pk)
            matched_code = pdf_title_to_code.get(norm_pk)
            if not matched_code and norm_pk:
                pk_words = [w for w in norm_pk.split() if len(w) > 2]
                if len(pk_words) >= 2:
                    candidates = [
                        code_v
                        for title_key, code_v in pdf_title_to_code.items()
                        if all(w in title_key.split() for w in pk_words)
                    ]
                    if len(candidates) == 1:
                        matched_code = candidates[0]
            if matched_code and matched_code != c["code"]:
                if matched_code not in resolved_codes:
                    resolved_codes.append(matched_code)
            else:
                # Filter out pure non-academic soft-skill fluff
                if not re.match(r"^(?:an?\s+(?:open|inquisitive)\s+mind|willingness\s+to\s+learn|self-confidence)$", pk.strip(), re.IGNORECASE):
                    unresolved_notes.append(pk.strip())

        c["prerequisites_courses"] = resolved_codes
        c["prerequisites_knowledge"] = unresolved_notes

    return courses


def _parse_curriculum_rules(page_texts: list[str], page_type_idx: dict[int, str]) -> list[dict]:
    rules = []
    seen_hashes = set()

    def _add_rule(txt: str, page_num: int):
        cleaned = re.sub(r"\s+", " ", txt).strip()
        if len(cleaned) <= 30:
            return
        h = hashlib.md5(cleaned.encode()).hexdigest()
        if h not in seen_hashes:
            seen_hashes.add(h)
            rules.append({"text": cleaned, "text_hash": h, "source_page": page_num})

    for page_num, ptype in page_type_idx.items():
        if ptype != PAGE_RULE:
            continue
        for para in page_texts[page_num].split("\n\n"):
            _add_rule(para, page_num)

    early_text_pages = [i for i in range(1, min(15, len(page_texts)))]
    early_combined = "\n".join(page_texts[i] for i in early_text_pages)
    for m in re.finditer(
        r"(?:^|\n)\s*(\*{1,3}\s*(?:Free\s+Electives|Professional\s+Electives|Live-in-Labs)[\s\S]*?)(?=\n\s*(?:\*{1,3}|Professional\s+Electives|Electives?\s+in\s+|Cat\s+Code)|\Z)",
        early_combined,
        re.IGNORECASE,
    ):
        _add_rule(m.group(1), 7)

    return rules


# -----------------------------------------------------------------------------
# Phase 4 — Batched Neo4j UNWIND Writes (Strict Hierarchy, Zero Bloat)
# -----------------------------------------------------------------------------


def _assign_baskets_for_slot(slot_type: str, baskets: list[dict]) -> list[str]:
    """Map every electiveCourse slot deterministically to its assigned ElectiveBaskets."""
    assigned: list[str] = []
    for b in baskets:
        bname = b["name"]
        bl = bname.lower()
        if b.get("target_audience") == "other_branches" or "other branches" in bl:
            continue
        if slot_type == "AVP" and "amrita value programme" in bl or slot_type == "FREE" and "free electives" in bl:
            assigned.append(bname)
        elif slot_type == "PE" and not ("amrita value programme" in bl or "free electives" in bl):
            # Covers all 6 specialization baskets + General Electives + Science Stream
            assigned.append(bname)
    return assigned


def _write_neo4j(
    driver,
    dept: str,
    year: str,
    sv_id: str,
    general_info: dict,
    semesters: list[dict],
    baskets: list[dict],
    courses: list[dict],
    rules: list[dict],
    page_texts: list[str] | None = None,
    page_type_idx: dict[int, str] | None = None,
) -> None:
    """Write normalized curriculum graph in batched UNWIND transactions."""
    with driver.session() as session:
        # 0. Clean refresh for this dept+year while preserving PYQ Question links
        logger.info("Refreshing syllabus subgraph for %s %s...", dept, year)
        session.run(
            "MATCH (n {syllabus_version_id: $sv_id}) DETACH DELETE n",
            sv_id=sv_id,
        )
        session.run(
            "MATCH (sv:SyllabusVersion {dept: $dept, year: $year}) DETACH DELETE sv",
            dept=dept,
            year=year,
        )
        session.run(
            """
            MATCH (c:Course {dept: $dept, year: $year})
            OPTIONAL MATCH (c)-[:HAS_OBJECTIVE|HAS_OUTCOME|USES_TEXTBOOK|HAS_TEXTBOOK|HAS_REFERENCE|USES_REFERENCE]->(child)
            DETACH DELETE child
            """,
            dept=dept,
            year=year,
        )
        session.run(
            """
            MATCH (c:Course {dept: $dept, year: $year})-[:HAS_UNIT]->(u:Unit)
            OPTIONAL MATCH (u)-[:HAS_TOPIC]->(t:Topic)
            WITH collect(DISTINCT u) AS units,
                 [x IN collect(DISTINCT t) WHERE x IS NOT NULL AND NOT (:Question)-[:TESTS]->(x)] AS orphan_topics
            FOREACH (x IN orphan_topics | DETACH DELETE x)
            FOREACH (u IN units | DETACH DELETE u)
            """,
            dept=dept,
            year=year,
        )

        # 1. Program -> SyllabusVersion -> GeneralInformation
        session.run(
            """
            MERGE (p:Program {name: $dept})
            SET p.school = $school, p.campuses = $campuses, p.degree = $degree
            MERGE (sv:SyllabusVersion {dept: $dept, year: $year})
            SET sv.id = $sv_id, sv.degree = $degree, sv.created_at = datetime()
            MERGE (p)-[:HAS_SYLLABUS_VERSION]->(sv)
            MERGE (gi:GeneralInformation {syllabus_version_id: $sv_id})
            SET gi.abbreviations = $abbrevs, gi.framework = $framework
            MERGE (sv)-[:HAS_GENERAL_INFORMATION]->(gi)
            """,
            dept=dept,
            year=year,
            sv_id=sv_id,
            school=general_info.get("school", ""),
            campuses=general_info.get("campuses", ""),
            degree=general_info.get("degree", ""),
            abbrevs=json.dumps(general_info.get("abbreviations", {})),
            framework=json.dumps(general_info.get("framework", [])),
        )

        # 2. POs, PEOs, PSOs, Rules (batched)
        po_rows = [{"id": po["id"], "desc": po["description"]} for po in general_info.get("program_outcomes", [])]
        if po_rows:
            session.run(
                """
                MATCH (sv:SyllabusVersion {dept: $dept, year: $year})
                UNWIND $rows AS row
                MERGE (po:ProgramOutcome {id: row.id, syllabus_version_id: $sv_id})
                SET po.description = row.desc
                MERGE (sv)-[:HAS_PO]->(po)
                """,
                dept=dept,
                year=year,
                sv_id=sv_id,
                rows=po_rows,
            )

        peo_rows = [{"id": p["id"], "desc": p["description"]} for p in general_info.get("program_educational_objectives", [])]
        if peo_rows:
            session.run(
                """
                MATCH (sv:SyllabusVersion {dept: $dept, year: $year})
                UNWIND $rows AS row
                MERGE (peo:ProgramEducationalObjective {id: row.id, syllabus_version_id: $sv_id})
                SET peo.description = row.desc
                MERGE (sv)-[:HAS_PEO]->(peo)
                """,
                dept=dept,
                year=year,
                sv_id=sv_id,
                rows=peo_rows,
            )

        pso_rows = [{"id": p["id"], "desc": p["description"]} for p in general_info.get("program_specific_outcomes", [])]
        if pso_rows:
            session.run(
                """
                MATCH (sv:SyllabusVersion {dept: $dept, year: $year})
                UNWIND $rows AS row
                MERGE (pso:ProgramSpecificOutcome {id: row.id, syllabus_version_id: $sv_id})
                SET pso.description = row.desc
                MERGE (sv)-[:HAS_PSO]->(pso)
                """,
                dept=dept,
                year=year,
                sv_id=sv_id,
                rows=pso_rows,
            )

        if rules:
            session.run(
                """
                MATCH (sv:SyllabusVersion {dept: $dept, year: $year})
                UNWIND $rows AS row
                MERGE (r:CurriculumRule {text_hash: row.text_hash, syllabus_version_id: $sv_id})
                SET r.text = row.text, r.source_page = row.source_page
                MERGE (sv)-[:HAS_RULE]->(r)
                """,
                dept=dept,
                year=year,
                sv_id=sv_id,
                rows=rules,
            )

        # 3. Semesters -> Core Courses & electiveCourse slots
        sem_rows = [
            {
                "num": s["number"],
                "label": s.get("label", f"Semester {s['number']}"),
                "tc": s.get("total_credits"),
                "tco": s.get("total_credits_optional"),
            }
            for s in semesters
        ]
        if sem_rows:
            session.run(
                """
                MATCH (sv:SyllabusVersion {dept: $dept, year: $year})
                UNWIND $rows AS row
                MERGE (sem:Semester {number: row.num, syllabus_version_id: $sv_id})
                SET sem.label = row.label, sem.total_credits = row.tc, sem.total_credits_optional = row.tco
                MERGE (sv)-[:HAS_SEMESTER]->(sem)
                """,
                dept=dept,
                year=year,
                sv_id=sv_id,
                rows=sem_rows,
            )

        sem_course_rows = []
        slot_rows = []
        slot_basket_links = []
        for sem in semesters:
            s_num = sem["number"]
            for c in sem.get("courses", []):
                ratio = c.get("evaluation_pattern_ratio", "")
                ei, ee = None, None
                if ratio and ":" in ratio:
                    parts = ratio.split(":")
                    try:
                        ei, ee = int(parts[0].strip()), int(parts[1].strip())
                    except Exception:
                        pass
                sem_course_rows.append({
                    "sem_num": s_num,
                    "code": c["code"],
                    "title": c.get("title", ""),
                    "cat": c.get("category", ""),
                    "L": c.get("L"),
                    "T": c.get("T"),
                    "P": c.get("P"),
                    "cred": c.get("credits", ""),
                    "ep": ratio,
                    "eval_int": ei,
                    "eval_end": ee,
                })

            for slot in sem.get("elective_slots", []):
                slug = re.sub(r"[^a-z0-9]+", "_", slot["name"].lower()).strip("_")
                slot_id = f"{sv_id}_sem{s_num}_{slug}"
                slot_rows.append({
                    "sem_num": s_num,
                    "slot_id": slot_id,
                    "name": slot["name"],
                    "label": slot["label"],
                    "slot_type": slot["slot_type"],
                    "category": slot.get("category", ""),
                    "credits": slot.get("credits", ""),
                    "eval_pattern": slot.get("evaluation_pattern_ratio", ""),
                })
                for bname in _assign_baskets_for_slot(slot["slot_type"], baskets):
                    slot_basket_links.append({"slot_id": slot_id, "basket_name": bname})

        if sem_course_rows:
            session.run(
                """
                UNWIND $rows AS row
                MATCH (sem:Semester {number: row.sem_num, syllabus_version_id: $sv_id})
                MERGE (c:Course {code: row.code})
                SET c.dept = $dept, c.year = $year,
                    c.semester = row.sem_num,
                    c.title = row.title,
                    c.name = coalesce(nullif(c.name, ''), row.title),
                    c.category = coalesce(nullif(row.cat, ''), c.category),
                    c.L = coalesce(row.L, c.L), c.T = coalesce(row.T, c.T), c.P = coalesce(row.P, c.P),
                    c.credits = coalesce(nullif(row.cred, ''), c.credits),
                    c.evaluation_pattern_ratio = coalesce(nullif(row.ep, ''), c.evaluation_pattern_ratio),
                    c.eval_internal = coalesce(row.eval_int, c.eval_internal),
                    c.eval_endsem = coalesce(row.eval_end, c.eval_endsem)
                MERGE (sem)-[:OFFERS]->(c)
                """,
                sv_id=sv_id,
                dept=dept,
                year=year,
                rows=sem_course_rows,
            )

        if slot_rows:
            session.run(
                """
                UNWIND $rows AS row
                MATCH (sem:Semester {number: row.sem_num, syllabus_version_id: $sv_id})
                MERGE (ec:electiveCourse {slot_id: row.slot_id})
                SET ec.name = row.name, ec.label = row.label, ec.slot_type = row.slot_type,
                    ec.category = row.category, ec.credits = row.credits,
                    ec.evaluation_pattern_ratio = row.eval_pattern,
                    ec.syllabus_version_id = $sv_id
                MERGE (sem)-[:OFFERS]->(ec)
                """,
                sv_id=sv_id,
                rows=slot_rows,
            )

        # 4. ElectiveBaskets & Course containment
        basket_rows = [
            {
                "name": b["name"],
                "target_audience": b.get("target_audience", "cse"),
                "is_other_branch": b.get("target_audience") == "other_branches",
            }
            for b in baskets
        ]
        if basket_rows:
            session.run(
                """
                UNWIND $rows AS row
                MERGE (eb:ElectiveBasket {name: row.name, syllabus_version_id: $sv_id})
                SET eb.target_audience = row.target_audience
                WITH eb, row
                WHERE row.is_other_branch
                MATCH (sv:SyllabusVersion {dept: $dept, year: $year})
                MERGE (sv)-[:HAS_ELECTIVE_BASKET]->(eb)
                """,
                dept=dept,
                year=year,
                sv_id=sv_id,
                rows=basket_rows,
            )

        if slot_basket_links:
            session.run(
                """
                UNWIND $rows AS row
                MATCH (ec:electiveCourse {slot_id: row.slot_id})
                MATCH (eb:ElectiveBasket {name: row.basket_name, syllabus_version_id: $sv_id})
                MERGE (ec)-[:HAS_BASKET]->(eb)
                """,
                sv_id=sv_id,
                rows=slot_basket_links,
            )

        basket_course_rows = []
        for b in baskets:
            for c in b.get("courses", []):
                basket_course_rows.append({
                    "basket_name": b["name"],
                    "code": c["code"],
                    "title": c.get("title", ""),
                    "cat": c.get("category", ""),
                    "L": c.get("L"),
                    "T": c.get("T"),
                    "P": c.get("P"),
                    "cred": c.get("credits", ""),
                    "ep": c.get("evaluation_pattern_ratio", ""),
                })
        if basket_course_rows:
            session.run(
                """
                UNWIND $rows AS row
                MATCH (eb:ElectiveBasket {name: row.basket_name, syllabus_version_id: $sv_id})
                MERGE (c:Course {code: row.code})
                ON CREATE SET c.title = row.title, c.name = row.title, c.credits = row.cred
                SET c.dept = coalesce(c.dept, $dept),
                    c.year = coalesce(c.year, $year),
                    c.name = coalesce(nullif(c.name, ''), row.title),
                    c.title = coalesce(nullif(row.title, ''), c.title),
                    c.L = coalesce(row.L, c.L), c.T = coalesce(row.T, c.T), c.P = coalesce(row.P, c.P),
                    c.credits = coalesce(nullif(row.cred, ''), c.credits),
                    c.category = coalesce(nullif(row.cat, ''), c.category, 'Elective'),
                    c.evaluation_pattern_ratio = coalesce(nullif(row.ep, ''), c.evaluation_pattern_ratio),
                    c.elective_basket = row.basket_name
                MERGE (eb)-[:CONTAINS]->(c)
                """,
                dept=dept,
                year=year,
                sv_id=sv_id,
                rows=basket_course_rows,
            )

        # 5. Detailed Courses (batched UNWIND)
        detail_rows = []
        prereq_rows = []
        obj_rows = []
        co_rows = []
        copo_rows = []
        unit_rows = []
        topic_rows = []
        tb_rows = []
        ref_rows = []
        eval_pattern_links = []
        assess_rows = []

        for course in courses:
            code = course["code"]
            ep = course.get("evaluation_pattern")
            ratio = ep.get("ratio", "") if ep else ""
            ei, ee = None, None
            if ratio and ":" in ratio:
                parts = ratio.split(":")
                try:
                    ei, ee = int(parts[0].strip()), int(parts[1].strip())
                except Exception:
                    pass

            detail_rows.append({
                "code": code,
                "name": course.get("name", ""),
                "L": course.get("L"),
                "T": course.get("T"),
                "P": course.get("P"),
                "cred": course.get("credits", ""),
                "ep": ratio,
                "eval_int": ei,
                "eval_end": ee,
                "prereq_notes": course.get("prerequisites_knowledge", []),
                "lab_experiments": course.get("lab_experiments", []),
                "description": course.get("description", ""),
            })

            for pc in course.get("prerequisites_courses", []):
                prereq_rows.append({"code": code, "pre_code": pc})

            for i, obj in enumerate(course.get("objectives", []), start=1):
                obj_rows.append({"code": code, "order": i, "desc": obj})

            for co in course.get("outcomes", []):
                co_rows.append({"code": code, "co_id": co["id"], "desc": co["description"]})

            for mp in course.get("co_po_mapping", []):
                copo_rows.append({
                    "code": code,
                    "co_id": mp["co_id"],
                    "po_id": mp["po_id"],
                    "target_label": "ProgramSpecificOutcome" if mp["po_id"].startswith("PSO") else "ProgramOutcome",
                    "weight": mp["weight"],
                })

            for u in course.get("units", []):
                u_num = u["number"]
                unit_rows.append({
                    "code": code,
                    "num": u_num,
                    "unit_id": f"{code}_u{u_num}",
                    "title": u.get("title", f"Unit {u_num}"),
                })
                for order, tname in enumerate(u.get("topics", []), start=1):
                    topic_rows.append({
                        "code": code,
                        "num": u_num,
                        "topic_id": f"{code}_{dept}_{year}_u{u_num}_t{order}",
                        "name": tname,
                        "order": order,
                    })

            for i, tb in enumerate(course.get("textbooks", []), start=1):
                if len(tb) > 5:
                    tb_rows.append({"code": code, "order": i, "text": tb})

            for i, rf in enumerate(course.get("references", []), start=1):
                if len(rf) > 5:
                    ref_rows.append({"code": code, "order": i, "text": rf})

            if ep:
                if ratio:
                    eval_pattern_links.append({
                        "code": code,
                        "ratio": ratio,
                        "int_pct": ei,
                        "ext_pct": ee,
                    })
                for branch in ("internal", "external"):
                    for comp in ep.get(branch, []):
                        assess_rows.append({
                            "code": code,
                            "key": comp["key"],
                            "name": comp["name"],
                            "branch": branch,
                            "marks": comp.get("marks"),
                            "exam_marks": comp.get("exam_marks"),
                            "note": comp.get("note", ""),
                        })

        if detail_rows:
            session.run(
                """
                UNWIND $rows AS row
                MERGE (c:Course {code: row.code})
                ON CREATE SET c.title = row.name
                SET c.dept = $dept, c.year = $year,
                    c.name = row.name,
                    c.title = row.name,
                    c.L = coalesce(row.L, c.L), c.T = coalesce(row.T, c.T), c.P = coalesce(row.P, c.P),
                    c.credits = coalesce(nullif(row.cred, ''), c.credits),
                    c.eval_internal = coalesce(row.eval_int, c.eval_internal),
                    c.eval_endsem = coalesce(row.eval_end, c.eval_endsem),
                    c.evaluation_pattern_ratio = coalesce(nullif(row.ep, ''), c.evaluation_pattern_ratio),
                    c.prerequisite_notes = row.prereq_notes,
                    c.lab_experiments = row.lab_experiments,
                    c.description = CASE WHEN row.description <> '' THEN row.description ELSE c.description END
                """,
                dept=dept,
                year=year,
                rows=detail_rows,
            )

        if prereq_rows:
            session.run(
                """
                UNWIND $rows AS row
                MATCH (c:Course {code: row.code})
                MATCH (pre:Course {code: row.pre_code})
                MERGE (c)-[:REQUIRES_PREREQUISITE]->(pre)
                MERGE (c)-[:REQUIRES]->(pre)
                """,
                rows=prereq_rows,
            )

        if obj_rows:
            session.run(
                """
                UNWIND $rows AS row
                MATCH (c:Course {code: row.code, dept: $dept, year: $year})
                MERGE (o:CourseObjective {course_code: row.code, dept: $dept, year: $year, order: row.order})
                SET o.description = row.desc
                MERGE (c)-[:HAS_OBJECTIVE]->(o)
                """,
                dept=dept,
                year=year,
                rows=obj_rows,
            )

        if co_rows:
            session.run(
                """
                UNWIND $rows AS row
                MATCH (c:Course {code: row.code, dept: $dept, year: $year})
                MERGE (co:CourseOutcome {id: row.co_id, course_code: row.code, dept: $dept, year: $year})
                SET co.description = row.desc
                MERGE (c)-[:HAS_OUTCOME]->(co)
                MERGE (co)-[:BELONGS_TO]->(c)
                """,
                dept=dept,
                year=year,
                rows=co_rows,
            )

        # Map CO -> PO / PSO cleanly
        po_map_rows = [r for r in copo_rows if r["target_label"] == "ProgramOutcome"]
        pso_map_rows = [r for r in copo_rows if r["target_label"] == "ProgramSpecificOutcome"]
        if po_map_rows:
            session.run(
                """
                UNWIND $rows AS row
                MATCH (co:CourseOutcome {id: row.co_id, course_code: row.code, dept: $dept, year: $year})
                MATCH (po:ProgramOutcome {id: row.po_id, syllabus_version_id: $sv_id})
                MERGE (co)-[r:MAPS_TO {po_id: row.po_id}]->(po)
                SET r.weight = row.weight
                """,
                dept=dept,
                year=year,
                sv_id=sv_id,
                rows=po_map_rows,
            )
        if pso_map_rows:
            session.run(
                """
                UNWIND $rows AS row
                MATCH (co:CourseOutcome {id: row.co_id, course_code: row.code, dept: $dept, year: $year})
                MATCH (pso:ProgramSpecificOutcome {id: row.po_id, syllabus_version_id: $sv_id})
                MERGE (co)-[r:MAPS_TO {po_id: row.po_id}]->(pso)
                SET r.weight = row.weight
                """,
                dept=dept,
                year=year,
                sv_id=sv_id,
                rows=pso_map_rows,
            )

        if unit_rows:
            session.run(
                """
                UNWIND $rows AS row
                MATCH (c:Course {code: row.code, dept: $dept, year: $year})
                MERGE (u:Unit {number: row.num, course_code: row.code, dept: $dept, year: $year})
                SET u.title = row.title, u.unit_id = row.unit_id
                MERGE (c)-[:HAS_UNIT]->(u)
                """,
                dept=dept,
                year=year,
                rows=unit_rows,
            )

        if topic_rows:
            session.run(
                """
                UNWIND $rows AS row
                MATCH (u:Unit {number: row.num, course_code: row.code, dept: $dept, year: $year})
                MERGE (t:Topic {topic_id: row.topic_id})
                SET t.name = row.name, t.order = row.order, t.course_code = row.code
                MERGE (u)-[:HAS_TOPIC]->(t)
                """,
                dept=dept,
                year=year,
                rows=topic_rows,
            )

        if tb_rows:
            session.run(
                """
                UNWIND $rows AS row
                MATCH (c:Course {code: row.code, dept: $dept, year: $year})
                MERGE (tb:Textbook {course_code: row.code, dept: $dept, year: $year, order: row.order})
                SET tb.text = row.text, tb.title = row.text
                MERGE (c)-[:USES_TEXTBOOK]->(tb)
                MERGE (c)-[:HAS_TEXTBOOK]->(tb)
                """,
                dept=dept,
                year=year,
                rows=tb_rows,
            )

        if ref_rows:
            session.run(
                """
                UNWIND $rows AS row
                MATCH (c:Course {code: row.code, dept: $dept, year: $year})
                MERGE (ref:Reference {course_code: row.code, dept: $dept, year: $year, order: row.order})
                SET ref.text = row.text, ref.title = row.text
                MERGE (c)-[:HAS_REFERENCE]->(ref)
                MERGE (c)-[:USES_REFERENCE]->(ref)
                """,
                dept=dept,
                year=year,
                rows=ref_rows,
            )

        if eval_pattern_links:
            session.run(
                """
                UNWIND $rows AS row
                MATCH (c:Course {code: row.code, dept: $dept, year: $year})
                MERGE (ep:EvaluationPattern {ratio: row.ratio})
                SET ep.internal_pct = coalesce(row.int_pct, ep.internal_pct),
                    ep.external_pct = coalesce(row.ext_pct, ep.external_pct)
                MERGE (c)-[:HAS_EVALUATION_PATTERN]->(ep)
                """,
                dept=dept,
                year=year,
                rows=eval_pattern_links,
            )

        if assess_rows:
            session.run(
                """
                UNWIND $rows AS row
                MATCH (c:Course {code: row.code, dept: $dept, year: $year})
                MERGE (ac:AssessmentComponent {key: row.key})
                SET ac.name = row.name
                MERGE (c)-[r:ASSESSED_BY {branch: row.branch}]->(ac)
                SET r.marks = row.marks, r.exam_marks = row.exam_marks, r.note = row.note
                """,
                dept=dept,
                year=year,
                rows=assess_rows,
            )

        # Prune any shared EvaluationPattern / AssessmentComponent nodes left unreferenced after refresh
        session.run("MATCH (ep:EvaluationPattern) WHERE NOT ()-[:HAS_EVALUATION_PATTERN]->(ep) DETACH DELETE ep")
        session.run("MATCH (ac:AssessmentComponent) WHERE NOT ()-[:ASSESSED_BY]->(ac) DETACH DELETE ac")


# -----------------------------------------------------------------------------
# Zero-Truncation Semantic Chunking (<= 1,650 chars per chunk)
# -----------------------------------------------------------------------------


def _split_lines_to_chunks(
    header: str,
    lines: list[str],
    course_code: str,
    title: str = "",
    max_chars: int = 1600,
) -> list[dict]:
    """Pack lines cleanly into <= max_chars chunks with header context on each chunk."""
    chunks: list[dict] = []
    current_lines: list[str] = [header]
    current_len = len(header)

    for raw in lines:
        item = raw.strip()
        if not item:
            continue
        # If a single line exceeds max_chars - len(header), slice safely on word boundaries
        avail = max(200, max_chars - len(header) - 2)
        if len(item) > avail:
            words = item.split()
            buf = ""
            sub_items = []
            for w in words:
                if len(buf) + len(w) + 1 > avail:
                    if buf:
                        sub_items.append(buf)
                    buf = w
                else:
                    buf = f"{buf} {w}".strip()
            if buf:
                sub_items.append(buf)
        else:
            sub_items = [item]

        for sub in sub_items:
            if current_len + len(sub) + 1 > max_chars and len(current_lines) > 1:
                chunks.append({
                    "course_code": course_code,
                    "title": title,
                    "content_type": "syllabus",
                    "content": "\n".join(current_lines),
                })
                cont_hdr = f"{header} (cont.)"
                current_lines = [cont_hdr, sub]
                current_len = len(cont_hdr) + len(sub) + 1
            else:
                current_lines.append(sub)
                current_len += len(sub) + 1

    if len(current_lines) > 1:
        chunks.append({
            "course_code": course_code,
            "title": title,
            "content_type": "syllabus",
            "content": "\n".join(current_lines),
        })
    return chunks


def build_syllabus_chunks(
    dept: str,
    year: str,
    general_info: dict,
    semesters: list[dict],
    baskets: list[dict],
    courses: list[dict],
    rules: list[dict],
) -> list[dict]:
    """Build zero-truncation, <= 1,600-char semantic chunks covering 100% of the PDF."""
    chunks: list[dict] = []

    # 1. Abbreviations
    if general_info.get("abbreviations"):
        abbr_lines = [f"- {k}: {v}" for k, v in general_info["abbreviations"].items()]
        chunks.extend(_split_lines_to_chunks(f"[Curriculum Abbreviations - {dept} {year}]", abbr_lines, f"{dept}_{year}_ABBREV", title=f"{dept} {year} Abbreviations"))

    # 2. General Framework + Program Metadata
    fw_lines = [
        f"Program: {general_info.get('degree', '')} | School: {general_info.get('school', '')}",
        f"Campuses: {general_info.get('campuses', '')}",
    ]
    for fw in general_info.get("framework", []):
        fw_lines.append(
            f"- {fw['category']} ({fw['description']}): Courses={fw.get('courses') or '-'}, Credits={fw.get('credits')}, AICTE={fw.get('aicte_recommended')}"
        )
    chunks.extend(_split_lines_to_chunks(f"[Curriculum General Framework - {dept} {year}]", fw_lines, f"{dept}_{year}_FRAMEWORK", title=f"{dept} {year} General Framework"))

    # 3. POs, PEOs, PSOs
    if general_info.get("program_outcomes"):
        po_lines = [f"- {po['id']}: {po['description']}" for po in general_info["program_outcomes"]]
        chunks.extend(_split_lines_to_chunks(f"[Program Outcomes (POs) - {dept} {year}]", po_lines, f"{dept}_{year}_GENERAL", title=f"{dept} {year} Program Outcomes"))

    peo_pso_lines = []
    for peo in general_info.get("program_educational_objectives", []):
        peo_pso_lines.append(f"- {peo['id']}: {peo['description']}")
    for pso in general_info.get("program_specific_outcomes", []):
        peo_pso_lines.append(f"- {pso['id']}: {pso['description']}")
    if peo_pso_lines:
        chunks.extend(_split_lines_to_chunks(f"[PEOs and PSOs - {dept} {year}]", peo_pso_lines, f"{dept}_{year}_GENERAL", title=f"{dept} {year} PEOs and PSOs"))

    # 4. Semester Structures
    for sem in semesters:
        tc_str = str(sem.get("total_credits") or "")
        if sem.get("total_credits_optional"):
            tc_str += f"+[{sem['total_credits_optional']}]"
        hdr = f"[Curriculum {dept} {year} - {sem.get('label', 'Semester ' + str(sem['number']))} | Total Credits: {tc_str}]"
        s_lines = []
        for c in sem.get("courses", []):
            ltp = f"{c.get('L') or '?'}-{c.get('T') or '?'}-{c.get('P') or '?'}"
            s_lines.append(
                f"- {c['code']} | {c.get('title', '')} | Cat: {c.get('category', '')} | L-T-P: {ltp} | Credits: {c.get('credits', '')} | Eval: {c.get('evaluation_pattern_ratio', '')}"
            )
        for slot in sem.get("elective_slots", []):
            s_lines.append(f"- Elective Slot: {slot.get('name', slot.get('label', ''))} (Credits: {slot.get('credits', '')}, Eval: {slot.get('evaluation_pattern_ratio', '')})")
        chunks.extend(_split_lines_to_chunks(hdr, s_lines, f"{dept}_{year}_SEM{sem['number']}", title=f"{dept} {year} Semester {sem['number']}"))

    # 5. Elective Baskets (paginated cleanly — zero truncation on 45-course baskets!)
    for b in baskets:
        b_lines = [
            f"- {c['code']}: {c.get('title', '')} (Credits: {c.get('credits', '')}, Eval: {c.get('evaluation_pattern_ratio', '')})"
            for c in b.get("courses", [])
        ]
        chunks.extend(_split_lines_to_chunks(f"[Elective Basket - {dept} {year} - {b['name']}]", b_lines, f"{dept}_{year}_ELECTIVES", title=b["name"]))

    # 6. Full Course Details (with Eval Components, CO-PO, Lab Experiments, Units & Bibliography)
    for c in courses:
        code = c["code"]
        name = c.get("name", "")
        ltpc = f"{c.get('L') or '-'}-{c.get('T') or '-'}-{c.get('P') or '-'}-{c.get('credits') or '-'}"
        ep = c.get("evaluation_pattern") or {}
        ratio = ep.get("ratio", "")
        ep_parts = []
        for br in ("internal", "external"):
            for comp in ep.get(br, []):
                ep_parts.append(f"{comp['name']}: {comp.get('marks')}")
        eval_str = f"{ratio or 'N/A'}" + (f" ({', '.join(ep_parts)})" if ep_parts else "")

        hdr = f"[Syllabus - {dept} {year} - {code} {name}]"
        overview_lines = [
            f"Course Code: {code} | Title: {name} | L-T-P-C: {ltpc} | Evaluation: {eval_str}",
        ]
        if c.get("prerequisites_courses") or c.get("prerequisites_knowledge"):
            prereqs = c.get("prerequisites_courses", []) + c.get("prerequisites_knowledge", [])
            overview_lines.append(f"Pre-Requisites: {', '.join(prereqs)}")
        if c.get("description"):
            overview_lines.append(c["description"])
        for o in c.get("objectives", []):
            overview_lines.append(f"Objective: {o}")
        for co in c.get("outcomes", []):
            overview_lines.append(f"{co['id']}: {co['description']}")
        if c.get("co_po_mapping"):
            co_groups: dict[str, list[str]] = {}
            for mp in c["co_po_mapping"]:
                co_groups.setdefault(mp["co_id"], []).append(f"{mp['po_id']}={mp['weight']}")
            overview_lines.append("CO-PO Mapping: " + " | ".join(f"{k}({', '.join(v)})" for k, v in co_groups.items()))
        for tb in c.get("textbooks", []):
            overview_lines.append(f"Textbook: {tb}")
        for rf in c.get("references", []):
            overview_lines.append(f"Reference: {rf}")
        chunks.extend(_split_lines_to_chunks(hdr, overview_lines, code, title=f"{code} {name}"))

        unit_lines = []
        for u in c.get("units", []):
            unit_lines.append(f"{u.get('title', 'Unit ' + str(u['number']))}: {', '.join(u.get('topics', []))}")
        for idx_l, exp in enumerate(c.get("lab_experiments", []), start=1):
            unit_lines.append(f"Lab Experiment {idx_l}: {exp}")
        if unit_lines:
            chunks.extend(_split_lines_to_chunks(f"[Syllabus Units - {dept} {year} - {code} {name}]", unit_lines, code, title=f"{code} {name} Units"))

    # 7. Curriculum Rules
    if rules:
        r_lines = [f"- {r['text']}" for r in rules]
        chunks.extend(_split_lines_to_chunks(f"[Curriculum Academic Rules - {dept} {year}]", r_lines, f"{dept}_{year}_RULES", title=f"{dept} {year} Academic Rules"))

    return chunks


# -----------------------------------------------------------------------------
# Main Entry — SyllabusParser
# -----------------------------------------------------------------------------


class SyllabusParser:
    """Campus-wide GraphRAG syllabus ingestion."""

    def __init__(self, neo4j_driver):
        self.driver = neo4j_driver
        self.last_chunks: list[dict] = []

    def parse_and_ingest(self, file_path: str, dept: str, year: str) -> dict:
        t0 = time.time()
        dept = (dept or "UNKNOWN").upper().strip()
        year = (year or "UNKNOWN").strip()
        sv_id = _sv_id(dept, year)

        logger.info("Parsing syllabus handbook for %s %s", dept, year)
        doc = fitz.open(file_path)
        try:
            total_pages = len(doc)
            t1 = time.time()
            page_texts, page_type_idx = phase1_fast_pass(doc)
            type_counts: dict[str, int] = {}
            for pt in page_type_idx.values():
                type_counts[pt] = type_counts.get(pt, 0) + 1
            logger.info("Phase 1 fast pass: %.2fs | %s", time.time() - t1, type_counts)

            t2 = time.time()
            page_tables = phase2_table_pass(doc, page_type_idx)
            set_live_doc(doc)
            logger.info("Phase 2 table pass: %.2fs | %s pages with tables", time.time() - t2, len(page_tables))

            t3 = time.time()
            general_info = _parse_general_info(page_texts, page_type_idx, page_tables)
            semesters = _parse_semester_tables(page_texts, page_type_idx, page_tables)
            baskets = _parse_elective_baskets(page_texts, page_type_idx, page_tables)
            courses = _parse_courses(page_texts, page_type_idx, semesters, baskets)
            rules = _parse_curriculum_rules(page_texts, page_type_idx)
        finally:
            set_live_doc(None)
            doc.close()

        logger.info(
            "Phase 3 structural extraction: %.2fs | POs=%s PSOs=%s Sems=%s Baskets=%s Courses=%s Rules=%s",
            time.time() - t3,
            len(general_info["program_outcomes"]),
            len(general_info["program_specific_outcomes"]),
            len(semesters),
            len(baskets),
            len(courses),
            len(rules),
        )

        self.last_chunks = build_syllabus_chunks(
            dept, year, general_info, semesters, baskets, courses, rules
        )

        t4 = time.time()
        _write_neo4j(
            self.driver,
            dept,
            year,
            sv_id,
            general_info,
            semesters,
            baskets,
            courses,
            rules,
        )
        logger.info("Phase 4 batched Neo4j writes: %.2fs", time.time() - t4)

        total = time.time() - t0
        summary = {
            "dept": dept,
            "year": year,
            "pages": total_pages,
            "pos": len(general_info["program_outcomes"]),
            "peos": len(general_info["program_educational_objectives"]),
            "psos": len(general_info["program_specific_outcomes"]),
            "semesters": len(semesters),
            "elective_baskets": len(baskets),
            "courses": len(courses),
            "rules": len(rules),
            "chunks_generated": len(self.last_chunks),
            "elapsed_seconds": round(total, 2),
        }
        logger.info("Syllabus ingestion finished in %.2fs | %s", total, summary)
        return summary
