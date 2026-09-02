"""
curriculum_extractor.py — Curriculum PDF parsing, syllabus structure extraction,
and Neo4j graph construction.

Replaces extract_syllabus_structure() and build_syllabus_kg() in utils.py.
Public signatures are preserved; utils.py re-exports these functions.

Design:
  1. Parse PDF page-by-page (never join pages into one string before boundary detection).
  2. Detect course headings with course code + "L-T-P-C" as signal.
  3. Detect unit headings (Unit 1, Unit 2, ...).
  4. For each unit, apply a stop-boundary regex BEFORE extracting topics.
  5. Extract topic candidates line-by-line from the safe zone.
  6. Validate each candidate (length, not a year, not an author, not a fragment).
  7. Ambiguous units (too few or too many single-word candidates) → LLM fallback.
  8. LLM-derived topics are always set approved=False; require admin review.
  9. Write Document → Course → Unit → Topic graph to Neo4j.
"""

import os
import re
import json
import uuid
import hashlib
import asyncio
import httpx
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv
from pydantic import BaseModel, field_validator

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'), override=True)

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

OLLAMA_BASE_URL    = os.getenv("OLLAMA_BASE_URL",    "http://localhost:11434")
OLLAMA_MODEL       = os.getenv("OLLAMA_MODEL",       "gemma4:12b-it-qat")

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# Course heading: e.g.  "23CSE303 Theory of Computation  L-T-P-C: 3-0-0-3"
COURSE_HEADING_RE = re.compile(
    r'^([0-9]{2}[A-Za-z]{2,4}[0-9]{2,3})\s+(.+?)\s+L[\s\-]T[\s\-]P[\s\-]C',
    re.IGNORECASE | re.MULTILINE
)

# Unit heading: "Unit 1", "Unit 2", "SyllabusUnit 1", etc.
UNIT_HEADING_RE = re.compile(
    r'(?:Syllabus\s*)?Unit\s+(\d+)',
    re.IGNORECASE
)

# Stop boundary — anything on or after this line is out of bounds for topic extraction.
STOP_BOUNDARY_RE = re.compile(
    r'^(?:Text\s*Book|Textbook|Reference|Evaluation\s*Pattern|Course\s*Outcome'
    r'|CO\s*Table|Bloom|Mapping|L\s*T\s*P\s*C|Prerequisite)',
    re.IGNORECASE
)

# A 4-digit publication year standing alone on a word boundary
YEAR_RE = re.compile(r'\b(19|20)\d{2}\b')

# Author-like pattern: a single capitalized word followed by a comma (e.g. "Hopcroft,")
AUTHOR_RE = re.compile(r'^[A-Z][a-z]+,\s*$')

# Edition/ISBN noise
EDITION_RE = re.compile(r'\b(Edition|ISBN|Vol\.?|Volume|Press|Publisher|PHI|TMH|McGraw|Pearson|Tata|Wiley|Oxford|Cambridge|Springer)\b', re.IGNORECASE)

# ---------------------------------------------------------------------------
# Pydantic model for LLM response validation
# ---------------------------------------------------------------------------

class TopicExtractionResult(BaseModel):
    topics: list[str]
    extraction_notes: str = ""

    @field_validator("topics")
    @classmethod
    def topics_must_be_strings(cls, v):
        return [str(t).strip() for t in v if t and str(t).strip()]


# ---------------------------------------------------------------------------
# Step 1 — Extract raw text per page from PDF (page-boundary preserving)
# ---------------------------------------------------------------------------

def extract_pages(file_path: str) -> list[str]:
    """
    Returns a list of raw text strings, one per PDF page.
    Never joins pages; preserves line structure.
    """
    if not fitz:
        print("[CurriculumExtractor] PyMuPDF not available. Cannot read PDF.")
        return []
    pages = []
    try:
        doc = fitz.open(file_path)
        for page_num in range(len(doc)):
            pages.append(doc[page_num].get_text("text"))
        doc.close()
    except Exception as e:
        print(f"[CurriculumExtractor] Failed to open PDF: {e}")
    return pages


# ---------------------------------------------------------------------------
# Step 2 — Locate course and unit boundaries in the page list
# ---------------------------------------------------------------------------

def _find_course_spans(pages: list[str]) -> list[dict]:
    """
    Finds every course heading across all pages.
    Returns a list of:
      {code, name, start_page, start_char, end_page, end_char}
    where start/end_char are character offsets within that page's text.
    """
    # Build a single offset map: flat_char_offset -> (page_num, line_char)
    # We need to search across page joins, so we process page-by-page.
    spans = []
    for page_num, page_text in enumerate(pages):
        for m in COURSE_HEADING_RE.finditer(page_text):
            spans.append({
                "code":  m.group(1).strip().upper(),
                "name":  m.group(2).strip().title(),
                "page":  page_num,
                "start": m.end(),   # char index in this page where course body begins
            })
    return spans


def _unit_text_blocks(course_body: str) -> list[dict]:
    """
    Given the text of a single course (after its heading), splits it into
    unit blocks.  Returns:
      [{ number, title, raw_text }]
    raw_text is the text BEFORE the first stop-boundary line.
    """
    unit_matches = list(UNIT_HEADING_RE.finditer(course_body))
    blocks = []

    for idx, m in enumerate(unit_matches):
        num = int(m.group(1))
        body_start = m.end()
        body_end = unit_matches[idx + 1].start() if idx + 1 < len(unit_matches) else len(course_body)
        raw_unit_text = course_body[body_start:body_end]

        # Apply stop boundary line-by-line
        safe_lines = []
        for line in raw_unit_text.splitlines():
            if STOP_BOUNDARY_RE.match(line.strip()):
                break
            safe_lines.append(line)

        safe_text = "\n".join(safe_lines).strip()

        blocks.append({
            "number":   num,
            "title":    f"Unit {num}",
            "raw_text": safe_text,   # stored on Unit node
        })

    return blocks


# ---------------------------------------------------------------------------
# Step 3 — Topic candidate extraction (deterministic, line-based)
# ---------------------------------------------------------------------------

def _is_valid_topic(candidate: str) -> bool:
    """
    Returns True if the candidate string looks like a genuine academic topic.
    Rejects: bare numbers, years, author names, edition/ISBN markers,
             strings shorter than 4 chars or longer than 90 chars,
             single lowercase words (likely sentence fragments).
    """
    t = candidate.strip()
    if not t or len(t) < 4 or len(t) > 90:
        return False
    # Purely numeric
    if t.replace(" ", "").isdigit():
        return False
    # Publication year standing alone
    if re.fullmatch(r'(19|20)\d{2}', t):
        return False
    # Year embedded in very short strings
    if YEAR_RE.search(t) and len(t.split()) <= 3:
        return False
    # Author-like ("Hopcroft," or "Sipser,")
    if AUTHOR_RE.match(t):
        return False
    # Textbook/publisher noise
    if EDITION_RE.search(t):
        return False
    # Single lowercase word — almost certainly a fragment from a split sentence
    if re.fullmatch(r'[a-z]+', t):
        return False
    # Very short single word (≤ 3 chars after stripping)
    if len(t.split()) == 1 and len(t) <= 3:
        return False
    return True


def _extract_topics_deterministic(unit_text: str) -> list[str]:
    """
    Adaptive multi-tier deterministic topic extraction:
    Tier 1 (Structural):
      - Unwraps soft PDF line-wraps.
      - Splits on dashes (–, —, spaced -), semicolons, list-colons, sentence boundaries, and newlines.
    Tier 2 (Comma-separated lists):
      - If Tier 1 yields < 2 topics on non-trivial text, splits by commas while preserving compound names.
    """
    if not unit_text or not unit_text.strip():
        return []

    # Unwrap soft line-wraps (newlines where subsequent line starts with a lowercase letter)
    unwrapped = re.sub(r'\n(?=[a-z])', ' ', unit_text.strip())

    # Tier 1: Structural delimiters
    delims = r'\n+|[–—]+|\s+-\s*|\s*-\s+|;\s*|\.\s+(?=[A-Z0-9])|:\s+'
    segments = re.split(delims, unwrapped)

    topics = []
    for seg in segments:
        clean = re.sub(r'^[\s\-\*\•\d\.\(\)]+', '', seg).strip()
        clean = re.sub(r'[\s\.\,\;\:]+$', '', clean).strip()
        clean = clean.strip('\'"“”()[]{}')
        if _is_valid_topic(clean):
            clean = re.sub(r'\s+', ' ', clean)
            topics.append(clean)

    # Tier 2: If structural delimiters yielded fewer than 2 topics, fall back to comma splitting
    if len(topics) < 2 and ',' in unwrapped:
        comma_delims = r'\n+|[–—]+|\s+-\s*|\s*-\s+|;\s*|\.\s+(?=[A-Z0-9])|:\s+|,\s*'
        comma_segments = re.split(comma_delims, unwrapped)
        comma_topics = []
        for seg in comma_segments:
            clean = re.sub(r'^[\s\-\*\•\d\.\(\)]+', '', seg).strip()
            clean = re.sub(r'[\s\.\,\;\:]+$', '', clean).strip()
            clean = clean.strip('\'"“”()[]{}')
            if _is_valid_topic(clean):
                clean = re.sub(r'\s+', ' ', clean)
                comma_topics.append(clean)
        if len(comma_topics) >= 2:
            topics = comma_topics

    return topics


def _is_ambiguous(topics: list[str], unit_text: str) -> bool:
    """
    Heuristic: consider a unit extraction ambiguous if:
    - Fewer than 2 valid topics were found despite the unit having lines/content, OR
    - More than 70% of the extracted topics are single words with count > 3.
    """
    text_lines = [l for l in unit_text.splitlines() if l.strip()]
    if len(text_lines) > 3 and len(topics) < 2:
        return True
    if len(unit_text.strip()) > 30 and len(topics) == 0:
        return True
    if topics and len(topics) > 3:
        single_word_ratio = sum(1 for t in topics if len(t.split()) == 1) / len(topics)
        if single_word_ratio > 0.7:
            return True
    return False


# ---------------------------------------------------------------------------
# Step 4 — LLM fallback for ambiguous units
# ---------------------------------------------------------------------------

async def _extract_topics_llm(unit_text: str, course_code: str, unit_num: int) -> TopicExtractionResult:
    """
    Sends the unit's raw syllabus text to the LLM and asks for a structured
    JSON list of topic names. LLM-derived topics are always set approved=False
    and require admin approval before being used for PYQ mapping.
    """
    prompt = f"""You are an expert academic curriculum analyst.

Below is the raw syllabus text for Unit {unit_num} of course {course_code}.
Extract ONLY the distinct academic topic names actually present in this text.

Rules:
- Return each topic exactly as it appears (preserve hyphens and multi-word phrases).
- Do NOT invent topics not in the text.
- Do NOT include textbook titles, author names, publication years, edition numbers,
  publisher names, evaluation criteria, CO mappings, or any non-topic content.
- Preserve meaningful multi-word concepts (e.g., "Non-Deterministic Finite State Machine").
- Each topic should be a standalone academic concept, not a sentence fragment.

Syllabus text:
{unit_text}

Return ONLY valid JSON with this exact structure:
{{"topics": ["Topic A", "Topic B", ...], "extraction_notes": "brief note"}}
"""
    try:
        async with httpx.AsyncClient(headers={"ngrok-skip-browser-warning": "true"}) as client:
            response = await client.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json={
                    "model":  OLLAMA_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json",
                    "options": {"num_ctx": 4096}
                },
                timeout=90.0
            )
            response.raise_for_status()
            raw = response.json().get("response", "{}")
            data = json.loads(raw)
            return TopicExtractionResult(**data)
    except Exception as e:
        print(f"[CurriculumExtractor] LLM fallback failed for unit {unit_num}: {e}")
        return TopicExtractionResult(topics=[], extraction_notes=f"LLM error: {e}")


# ---------------------------------------------------------------------------
# Step 5 — Main extraction entry point
# ---------------------------------------------------------------------------

async def extract_syllabus_structure(
    file_path: str,
    dept: str,
    year: str,
    document_id: str = None
) -> dict:
    """
    Main entry point.  Parses a curriculum PDF and returns a structured dict:

    {
        "courses": [
            {
                "code": "23CSE303",
                "name": "Theory of Computation",
                "units": [
                    {
                        "number": 1,
                        "title": "Unit 1",
                        "raw_text": "...",
                        "topics": [
                            {
                                "name": "Finite State Machines",
                                "extraction_method": "regex",
                                "extraction_confidence": 1.0,
                                "approved": True
                            },
                            ...
                        ]
                    }
                ]
            }
        ]
    }

    LLM-derived topics always have approved=False.
    """
    pages = extract_pages(file_path)
    if not pages:
        return {"courses": []}

    # Join pages with a sentinel so we can locate boundaries, but store
    # per-page info for precise span computation.
    PAGE_SEP = "\n\u00b6PAGE\u00b6\n"  # paragraph mark sentinel
    full_text = PAGE_SEP.join(pages)

    # Find all course headings
    course_spans = _find_course_spans(pages)
    if not course_spans:
        print("[CurriculumExtractor] No course headings found. Check PDF format.")
        return {"courses": []}

    print(f"[CurriculumExtractor] Found {len(course_spans)} courses in PDF.")
    courses_data = []

    for i, span in enumerate(course_spans):
        # Yield to event loop to prevent blocking HTTP responses in FastAPI
        await asyncio.sleep(0.01)
        
        code = span["code"]
        name = span["name"]

        # Collect course body: from end of this heading to start of next heading
        # (or end of document), but only within the same page and onwards.
        body_parts = []

        page_start = span["page"]
        char_start = span["start"]

        if i + 1 < len(course_spans):
            next_span = course_spans[i + 1]
            page_end  = next_span["page"]
            char_end  = next_span["start"]
        else:
            page_end  = len(pages) - 1
            char_end  = len(pages[-1]) if pages else 0

        # Collect text across possibly multiple pages
        if page_start == page_end:
            body_parts.append(pages[page_start][char_start:char_end])
        else:
            body_parts.append(pages[page_start][char_start:])
            for p in range(page_start + 1, page_end):
                body_parts.append(pages[p])
            if page_end < len(pages):
                body_parts.append(pages[page_end][:char_end])

        course_body = "\n".join(body_parts)

        units = _unit_text_blocks(course_body)
        if not units:
            print(f"[CurriculumExtractor]   {code}: no unit headings found — skipping.")
            continue

        units_data = []
        for unit in units:
            raw_text = unit["raw_text"]

            # Deterministic extraction first
            topics = _extract_topics_deterministic(raw_text)
            method = "regex"
            confidence = 1.0
            approved = True

            if _is_ambiguous(topics, raw_text):
                print(f"[CurriculumExtractor]   {code} Unit {unit['number']}: ambiguous — calling LLM fallback.")
                llm_result = await _extract_topics_llm(raw_text, code, unit["number"])
                if llm_result.topics:
                    # Validate each LLM topic
                    llm_topics = [t for t in llm_result.topics if _is_valid_topic(t)]
                    if llm_topics:
                        topics = llm_topics
                        method = "llm_validated"
                        confidence = 0.85
                        approved = False   # Always False for LLM-derived topics
                        print(f"[CurriculumExtractor]     LLM produced {len(topics)} topics (pending admin approval).")

            topic_dicts = []
            for t_name in topics:
                topic_dicts.append({
                    "name":                 t_name,
                    "normalized_name":      re.sub(r'\s+', ' ', t_name.lower().strip()),
                    "extraction_method":    method,
                    "extraction_confidence": confidence,
                    "approved":             approved,
                })

            units_data.append({
                "number":   unit["number"],
                "title":    unit["title"],
                "raw_text": raw_text,
                "topics":   topic_dicts,
            })

        if units_data:
            courses_data.append({
                "code":  code,
                "name":  name,
                "units": units_data,
            })
            print(f"[CurriculumExtractor]   {code}: {len(units_data)} units, "
                  f"{sum(len(u['topics']) for u in units_data)} topics extracted.")

    print(f"[CurriculumExtractor] Extraction complete: {len(courses_data)} courses processed.")
    return {"courses": courses_data}


# ---------------------------------------------------------------------------
# Step 6 — Write to Neo4j
# ---------------------------------------------------------------------------

def _topic_id(course_code: str, unit_num: int, name: str, document_id: str) -> str:
    key = f"{document_id}_{course_code}_{unit_num}_{name.lower().strip()}"
    return hashlib.md5(key.encode()).hexdigest()


def build_syllabus_kg(neo4j_driver, dept: str, year: str, courses: list,
                      document_id: str = None):
    """
    Writes the extracted curriculum structure to Neo4j using the canonical schema:

      (:Document)-[:CONTAINS]->(:Course)
      (:Course)-[:HAS_UNIT]->(:Unit)
      (:Unit)-[:HAS_TOPIC]->(:Topic)

    All Topic nodes get full extraction metadata.
    Units get raw_text stored for admin review.
    """
    if not courses:
        return

    doc_id = str(document_id) if document_id else "unknown"
    now_iso = datetime.utcnow().isoformat()

    with neo4j_driver.session() as session:
        # Ensure Department node
        session.run("MERGE (d:Department {name: $dept})", dept=dept)

        for course in courses:
            c_code = course.get("code")
            c_name = course.get("name", "")
            if not c_code:
                continue

            # Course node (MERGE on code for uniqueness)
            session.run("""
                MERGE (c:Course {code: $c_code})
                ON CREATE SET c.name = $c_name, c.year = $year,
                              c.department = $dept, c.document_id = $doc_id
                ON MATCH SET  c.name = $c_name
                WITH c
                MATCH (d:Department {name: $dept})
                MERGE (d)-[:OFFERS]->(c)
            """, c_code=c_code, c_name=c_name, year=year, dept=dept, doc_id=doc_id)

            # Link Document → Course if document_id provided
            if document_id:
                session.run("""
                    MERGE (doc:Document {id: $doc_id})
                    ON CREATE SET doc.course_code = $c_code,
                                  doc.doc_type = 'syllabus',
                                  doc.created_at = $now
                    WITH doc
                    MATCH (c:Course {code: $c_code})
                    MERGE (doc)-[:CONTAINS]->(c)
                """, doc_id=doc_id, c_code=c_code, now=now_iso)

            for unit in course.get("units", []):
                u_num   = str(unit.get("number", ""))
                u_title = unit.get("title", f"Unit {u_num}")
                u_raw   = unit.get("raw_text", "")
                if not u_title:
                    continue

                # Unit node — keyed by (course_code + number) for uniqueness
                unit_id = hashlib.md5(f"{doc_id}_{c_code}_unit{u_num}".encode()).hexdigest()
                session.run("""
                    MERGE (u:Unit {id: $unit_id})
                    ON CREATE SET u.number = $u_num, u.title = $u_title,
                                  u.raw_text = $u_raw, u.course_code = $c_code,
                                  u.document_id = $doc_id
                    ON MATCH SET  u.raw_text = $u_raw
                    WITH u
                    MATCH (c:Course {code: $c_code})
                    MERGE (c)-[:HAS_UNIT]->(u)
                """, unit_id=unit_id, u_num=u_num, u_title=u_title,
                    u_raw=u_raw, c_code=c_code, doc_id=doc_id)

                for topic in unit.get("topics", []):
                    t_name  = topic.get("name", "")
                    t_norm  = topic.get("normalized_name", t_name.lower().strip())
                    t_meth  = topic.get("extraction_method", "regex")
                    t_conf  = topic.get("extraction_confidence", 1.0)
                    t_appr  = topic.get("approved", True)
                    if not t_name:
                        continue

                    t_id = _topic_id(c_code, int(u_num), t_name, doc_id)

                    session.run("""
                        MERGE (t:Topic {id: $t_id})
                        ON CREATE SET
                            t.name = $t_name,
                            t.normalized_name = $t_norm,
                            t.raw_text = $u_raw,
                            t.document_id = $doc_id,
                            t.course_code = $c_code,
                            t.unit_number = $u_num,
                            t.extraction_method = $t_meth,
                            t.extraction_confidence = $t_conf,
                            t.approved = $t_appr,
                            t.created_at = $now
                        WITH t
                        MATCH (u:Unit {id: $unit_id})
                        MERGE (u)-[:HAS_TOPIC]->(t)
                    """, t_id=t_id, t_name=t_name, t_norm=t_norm,
                        u_raw=u_raw, doc_id=doc_id, c_code=c_code,
                        u_num=u_num, t_meth=t_meth, t_conf=t_conf,
                        t_appr=t_appr, now=now_iso, unit_id=unit_id)

    print(f"[CurriculumExtractor] Neo4j write complete for {len(courses)} courses.")
