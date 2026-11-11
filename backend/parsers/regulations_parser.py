"""
regulations_parser.py — Parser for Academic Regulations documents (e.g. BTech-ASE-regulations-2023.pdf).
Extracts Ordinances (O.1..O.5) and Regulations (R.1..R.31), preserving hierarchy,
cross-page clause continuations, subclauses, conditions, and clause cross-references.
"""
import re
from typing import Dict, List, Any, Optional
import pymupdf as fitz


HEADER_PATTERN = re.compile(
    r"Amrita Vishwa Vidyapeetham\s+B\.Tech\.\s+Regulations\s+2023\s+Page\s+\d+\s+of\s+\d+",
    re.I,
)
PAGE_FOOTER_PATTERN = re.compile(r"^Page \d+ of \d+$", re.I)
CLAUSE_START_PATTERN = re.compile(r"^(O\.\d+|R\.\d+(?:\.\d+)*)\.?\s*(.*)$")
CROSS_REF_PATTERN = re.compile(r"\b(R\.\d+(?:\.\d+)*|O\.\d+)\b")


def parse_regulations_pdf(pdf_path: str) -> Dict[str, Any]:
    """
    Parses an academic regulations PDF into structured clauses and references.
    Skips the Page 1 Contents page.
    """
    doc = fitz.open(pdf_path)
    clauses: List[Dict[str, Any]] = []
    current_clause: Optional[Dict[str, Any]] = None

    # Skip page 0 (Contents page)
    for page_idx in range(1, len(doc)):
        page = doc[page_idx]
        text = page.get_text()
        raw_lines = text.split("\n")

        clean_lines: List[str] = []
        for line in raw_lines:
            s = line.strip()
            if not s or HEADER_PATTERN.search(s) or PAGE_FOOTER_PATTERN.match(s):
                continue
            if s.upper() in ("ORDINANCES", "REGULATIONS", "CONTENTS"):
                continue
            clean_lines.append(s)

        for line in clean_lines:
            match = CLAUSE_START_PATTERN.match(line)
            # Ensure it is a valid clause id like O.1 or R.12.1, not a range like 'R.1 to R.31'
            if match and not line.startswith("R.1 to") and len(match.group(1)) <= 10:
                cid = match.group(1)
                rest = match.group(2).strip()

                parts = cid.split(".")
                if cid.startswith("O"):
                    parent_id = "O" if len(parts) > 1 else None
                else:
                    # R.12.1 -> parent R.12; R.12 -> parent R
                    if len(parts) > 2:
                        parent_id = ".".join(parts[:2])
                    elif len(parts) == 2:
                        parent_id = parts[0]
                    else:
                        parent_id = None

                if current_clause:
                    current_clause["clause_text"] = " ".join(current_clause["_lines"]).strip()
                    clauses.append(current_clause)

                # Check if rest looks like a title (e.g. 'Attendance', 'Assessment Procedure')
                is_title = bool(
                    rest
                    and len(rest) < 55
                    and not rest.endswith(".")
                    and not rest.startswith(("A student", "Candidates", "The", "In case", "Every"))
                )
                title = rest if is_title else None
                initial_lines = []
                if rest and not is_title:
                    initial_lines.append(rest)

                current_clause = {
                    "clause_id": cid,
                    "parent_clause_id": parent_id,
                    "page_number": page_idx + 1,
                    "title": title,
                    "_lines": initial_lines,
                    "clause_text": "",
                }
            else:
                if current_clause:
                    current_clause["_lines"].append(line)

    if current_clause:
        current_clause["clause_text"] = " ".join(current_clause["_lines"]).strip()
        clauses.append(current_clause)

    # Clean up internal temporary lines list and populate titles from parents where missing
    title_lookup = {}
    for c in clauses:
        c.pop("_lines", None)
        if c.get("title"):
            title_lookup[c["clause_id"]] = c["title"]

    for c in clauses:
        if not c.get("title") and c.get("parent_clause_id") in title_lookup:
            c["title"] = title_lookup[c["parent_clause_id"]]

    # Extract cross-references between clauses
    all_clause_ids = {c["clause_id"] for c in clauses}
    references: List[Dict[str, str]] = []
    seen_refs = set()

    for c in clauses:
        src_id = c["clause_id"]
        for ref_match in CROSS_REF_PATTERN.finditer(c["clause_text"]):
            target_id = ref_match.group(1)
            if target_id != src_id and (target_id in all_clause_ids or any(cid.startswith(target_id) for cid in all_clause_ids)):
                pair = (src_id, target_id)
                if pair not in seen_refs:
                    seen_refs.add(pair)
                    references.append({
                        "from_clause_id": src_id,
                        "to_clause_id": target_id,
                    })

    return {
        "clauses": clauses,
        "references": references,
        "total_clauses": len(clauses),
        "total_references": len(references),
    }

