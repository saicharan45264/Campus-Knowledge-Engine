import io
import os
import re
import json
import uuid
import base64
import threading

import httpx
from PIL import Image

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

from core.config import OLLAMA_BASE_URL, OLLAMA_MODEL
from services.graph import map_questions_to_kg


def process_pdf(file_path: str) -> list[str]:
    if not fitz:
        print("pymupdf not installed")
        return []

    chunks = []
    try:
        doc = fitz.open(file_path)
        for page in doc:
            for para in page.get_text("text").split("\n\n"):
                cleaned = para.strip()
                if len(cleaned) > 20:
                    chunks.append(cleaned)
        doc.close()
    except Exception as e:
        print(f"pdf read error: {e}")
    return chunks


def extract_syllabus_structure(text: str, dept: str, year: str) -> dict:
    # regex parser for the amrita syllabus format
    # we use regex instead of the llm here because the syllabus follows a strict
    # template (L-T-P-C, Unit headers, CO codes) and regex is much faster and repeatable

    # build a semester→course_code map from semester headings in the document
    sem_map: dict[str, str] = {}
    for m in re.finditer(r"Semester\s+(I{1,3}V?|IV|V|VI{0,3})\b", text, re.IGNORECASE):
        sem_name = m.group(0).strip().upper()
        rest     = text[m.end():]
        next_m   = re.search(r"Semester\s+(I{1,3}V?|IV|V|VI{0,3})\b", rest, re.IGNORECASE)
        block    = rest[:next_m.start()] if next_m else rest
        for cc in re.findall(r"\b[0-9]{2}[a-zA-Z]{3}[0-9]{3}\b", block):
            sem_map[cc.upper()] = sem_name

    course_pattern = re.compile(
        r"([0-9]{2}[a-zA-Z]{3}[0-9]{3})\s+(.*?)\s+L-T-P-C:?\s*([\d\-\[\]\s]+)",
        re.IGNORECASE
    )
    matches = list(course_pattern.finditer(text))
    courses = []

    for i, m in enumerate(matches):
        code     = m.group(1).strip().upper()
        name     = m.group(2).strip()
        ltpc     = re.findall(r"\d+", m.group(3))
        credits  = ltpc[3] if len(ltpc) >= 4 else None

        s_idx = m.end()
        e_idx = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body  = text[s_idx:e_idx]

        prereq_m = re.search(r"Pre-?requisite\(?s?\)?:\s*(.*?)(?=\n|$)", body, re.IGNORECASE)
        eval_m   = re.search(r"Evaluation Pattern:\s*([0-9:a-zA-Z/]+)", body, re.IGNORECASE)
        obj_m    = re.search(
            r"Course Objectives\s*(.*?)(?=Pre-?Requisite|Course Outcomes|Syllabus|CO1)",
            body, re.IGNORECASE | re.DOTALL
        )

        objectives = []
        if obj_m:
            objectives = [
                o.strip() for o in re.split(r"\n●|\n-|\n\s*\d+\.", obj_m.group(1))
                if len(o.strip()) > 10
            ]

        outcomes = [
            {"code": cm.group(1).upper(), "text": cm.group(2).strip().replace("\n", " ")}
            for cm in re.finditer(
                r"(CO\d+):\s*(.*?)(?=\nCO\d+:|\nCO\s*-|CO-PO Mapping|$|\nSyllabus)",
                body, re.IGNORECASE | re.DOTALL
            )
        ]

        tb_m = re.search(r"Textbook\(s\)\s*(.*?)(?=Reference\(s\)|Evaluation Pattern|$)",
                         body, re.IGNORECASE | re.DOTALL)
        textbooks = ([t.strip().replace("\n", " ")
                      for t in re.split(r"\n\d+\.", tb_m.group(1)) if len(t.strip()) > 5]
                     if tb_m else [])

        ref_m = re.search(r"Reference\(s\)\s*(.*?)(?=Evaluation Pattern|$)",
                          body, re.IGNORECASE | re.DOTALL)
        references = ([r.strip().replace("\n", " ")
                       for r in re.split(r"\n\d+\.", ref_m.group(1)) if len(r.strip()) > 5]
                      if ref_m else [])

        unit_matches = list(re.compile(r"(?:Syllabus)?Unit\s*(\d)", re.IGNORECASE).finditer(body))
        units = []
        for j, um in enumerate(unit_matches):
            u_num   = um.group(1)
            u_start = um.end()
            if j + 1 < len(unit_matches):
                u_end = unit_matches[j + 1].start()
            else:
                stop  = re.compile(r"^(?:TEXTBOOK|REFERENCE|Evaluation Pattern)",
                                   re.IGNORECASE | re.MULTILINE).search(body, u_start)
                u_end = stop.start() if stop else len(body)

            unit_text = body[u_start:u_end].strip()
            topics = [
                {"name": re.sub(r"\s+", " ", t.strip().replace("\n", " ")), "subtopics": []}
                for t in re.split(r"[,;\.\-\:]", unit_text)
                if 3 < len(re.sub(r"\s+", " ", t.strip())) < 150
            ]
            if topics:
                units.append({"number": int(u_num), "title": f"Unit {u_num}", "topics": topics})

        if units:
            courses.append({
                "code":               code,
                "name":               name,
                "credits":            credits,
                "semester":           sem_map.get(code),
                "evaluation_pattern": eval_m.group(1).strip() if eval_m else None,
                "prerequisites":      prereq_m.group(1).strip() if prereq_m else None,
                "objectives":         objectives,
                "outcomes":           outcomes,
                "textbooks":          textbooks,
                "references":         references,
                "units":              units,
            })

    print(f"syllabus parser found {len(courses)} courses")
    return {"courses": courses}


def _repair_json_batch(raw: str, n: int) -> dict | None:
    """
    Try to salvage a batch JSON response, bypassing any Gemma4 "thinking" tokens.
    Gemma4 often outputs internal thoughts before the actual JSON block.
    We search for the LAST valid JSON object in the string.
    """
    # Find all potential { ... } blocks from right to left
    for m in reversed(list(re.finditer(r'\{', raw))):
        candidate = raw[m.start():]
        try:
            # Try to parse up to the last closing brace
            last_brace = candidate.rindex('}')
            data = json.loads(candidate[:last_brace+1])
            # Validate it's the actual result (keys should be numeric strings)
            if data and all(k.strip().isdigit() for k in data.keys()):
                return data
        except (json.JSONDecodeError, ValueError):
            pass

    # If full parse failed, try the truncation repair on the rightmost block
    candidate = raw.strip()
    for _ in range(10):
        last_comma = candidate.rfind(',')
        if last_comma == -1:
            break
        candidate = candidate[:last_comma].rstrip() + '}'
        if not candidate.lstrip().startswith('{'):
            start = candidate.find('{')
            if start == -1: break
            candidate = candidate[start:]
        try:
            data = json.loads(candidate)
            if data and all(k.strip().isdigit() for k in data.keys()):
                return data
        except json.JSONDecodeError:
            continue

    return None


# Semaphore: cap concurrent topic-matching LLM calls to 2.
# Gemma4 is a large model — running 6 simultaneous inference requests
# saturates CPU/GPU memory and produces garbled outputs.
_llm_topic_semaphore = threading.Semaphore(2)


def batch_match_topics_with_llm(
    questions: list[str],
    syllabus_topics: list[str] | None = None,
) -> list[list[str]]:
    """
    Match all questions to syllabus topics in a SINGLE LLM call.
    This is the key ingestion speedup: instead of N sequential calls
    (each up to 3 min timeout), we make 1 call with all questions.

    Returns a list aligned with `questions`, where each element is the
    list of matched topic strings for that question.
    """
    if not questions:
        return []
    if not syllabus_topics:
        return [[] for _ in questions]

    topics_str = json.dumps(syllabus_topics)
    # Build a numbered question list for the prompt
    q_list = "\n".join(f"{i}. {q}" for i, q in enumerate(questions))

    prompt = f"""You are an expert academic assistant mapping university exam questions to syllabus topics.

Syllabus topics (use EXACT strings only from this list):
{topics_str}

Exam questions (numbered 0 to {len(questions) - 1}):
{q_list}

Instructions:
1. For EACH question, identify ALL syllabus topics it touches.
2. If a question has sub-parts (a, b, c OR i, ii, iii OR part A/B), treat each sub-part separately
   and include the topic for EVERY sub-part in the list for that question index.
3. Return ALL matching topics for the question (not just the first one).
4. Use ONLY exact topic strings from the syllabus list above.
5. If truly no topic matches, use an empty list.

Output a JSON object and NOTHING else — no explanation, no markdown, no extra text:
{{
  "0": ["Topic A", "Topic B"],
  "1": ["Topic C"],
  "2": []
}}
"""
    # Limit concurrent Ollama calls to avoid memory saturation
    with _llm_topic_semaphore:
        try:
            # Guard: if prompt is very long, trim topics to avoid context overflow
            # Gemma4 on Colab may have a smaller effective context than 8192
            prompt_chars = len(prompt)
            if prompt_chars > 12000 and syllabus_topics and len(syllabus_topics) > 40:
                trimmed_topics = syllabus_topics[:40]
                print(f"[LLM] Prompt too long ({prompt_chars} chars, {len(syllabus_topics)} topics) "
                      f"— trimming to 40 topics")
                topics_str = json.dumps(trimmed_topics)
                prompt = f"""You are an expert academic assistant mapping university exam questions to syllabus topics.

Syllabus topics (use EXACT strings only from this list):
{topics_str}

Exam questions (numbered 0 to {len(questions) - 1}):
{q_list}

Instructions:
1. For EACH question, identify ALL syllabus topics it touches.
2. If a question has sub-parts (a, b, c OR i, ii, iii OR part A/B), treat each sub-part separately
   and include the topic for EVERY sub-part in the list for that question index.
3. Return ALL matching topics for the question (not just the first one).
4. Use ONLY exact topic strings from the syllabus list above.
5. If truly no topic matches, use an empty list.

Output a JSON object and NOTHING else — no explanation, no markdown, no extra text:
{{
  "0": ["Topic A", "Topic B"],
  "1": ["Topic C"],
  "2": []
}}
"""
            else:
                print(f"[LLM] Sending batch prompt ({prompt_chars} chars, "
                      f"{len(syllabus_topics or [])} topics, {len(questions)} questions)")

            with httpx.Client() as client:
                r = client.post(
                    f"{OLLAMA_BASE_URL}/api/generate",
                    json={
                        "model": OLLAMA_MODEL,
                        "prompt": prompt,
                        # DO NOT use "format": "json" — Gemma4 is a thinking model and
                        # leaks internal chain-of-thought tokens as garbage JSON keys
                        # when forced into json-mode (e.g. {"thought3847...": -0.000...}).
                        # We extract the JSON block ourselves with a regex instead.
                        "stream": False,
                        "options": {"num_ctx": 8192, "num_predict": 4096},
                    },
                    timeout=300.0,
                )
                r.raise_for_status()
                raw = r.json().get("response", "")
                print(f"[LLM] Raw response length: {len(raw)} chars")
                if not raw:
                    raise ValueError("Model returned empty response — Ollama may have run out of context")

                # Extract the first { ... } block from the free-text response
                data = _repair_json_batch(raw, len(questions))
                if data is not None:
                    # Validate: keys must be numeric strings, values must be lists
                    if all(k.isdigit() and isinstance(v, list) for k, v in data.items()):
                        results = []
                        for i in range(len(questions)):
                            matched = data.get(str(i), [])
                            results.append([t for t in matched if isinstance(t, str)])
                        return results

                raise ValueError(f"Bad batch response: {raw[:200]}")

        except Exception as e:
            print(f"Batch topic matching failed ({e}), falling back to individual calls")
            # Fallback: one call per question (still under the semaphore slot)
            out = []
            for q in questions:
                p = f"""Match this exam question to syllabus topics.

Syllabus topics:
{json.dumps(syllabus_topics)}

Question: "{q}"

Output ONLY a JSON object, nothing else:
{{"matched_topics": ["Exact Topic Name"]}}
If no match: {{"matched_topics": []}}
"""
                try:
                    with httpx.Client() as client:
                        r = client.post(
                            f"{OLLAMA_BASE_URL}/api/generate",
                            json={"model": OLLAMA_MODEL, "prompt": p,
                                  "stream": False,
                                  "options": {"num_ctx": 4096, "num_predict": 512}},
                            timeout=180.0,
                        )
                        r.raise_for_status()
                        raw = r.json().get("response", "")
                        m   = re.search(r'\{[^{}]*"matched_topics"[^{}]*\}', raw, re.DOTALL)
                        if m:
                            d = json.loads(m.group(0))
                            out.append(d.get("matched_topics", []))
                        else:
                            out.append([])
                except Exception as e2:
                    print(f"Individual topic matching also failed: {e2}")
                    out.append([])
            return out


def extract_metadata(text: str) -> dict:
    # Format 2 (End-sem): "[6] [CO01] [BTL 2]"
    m = re.search(r"\[(\d+)\]\s*\[CO0?(\d+)\]\s*\[BTL\s*(\d)\]", text)
    if m: return {"marks": m.group(1), "co": m.group(2), "btl": m.group(3)}
    
    # Format 1 (Midterm): "[CO01][ BTL 2]"
    m = re.search(r"\[CO0?(\d+)\]\s*\[\s*BTL\s*(\d)\]", text)
    if m: return {"marks": None, "co": m.group(1), "btl": m.group(2)}
    
    return {"marks": None, "co": None, "btl": None}


def process_and_map_pyq_document(
    file_path: str,
    course_code: str,
    syllabus_topics: list[str],
    neo4j_driver,
    progress_callback=None,
    doc_id: int = None
) -> list[tuple]:
    """
    Three-pass PYQ processor producing exactly ONE image per question.

    Pass 1  — Scan ALL pages; collect question anchors (page, y0_px) in a flat
              list across the whole document and render each page to a PIL image.
    Pass 1b — Compute crop boundaries globally: Q_i ends where Q_{i+1} starts.
              Cross-page questions are detected automatically.
    Pass 2  — Batch-match ALL questions to syllabus topics (ONE LLM call).
    Pass 3  — Stitch page-slices into per-question JPEG images, write to KG.
    """
    if not fitz:
        print("pymupdf not installed — skipping pyq processing")
        return []

    os.makedirs(os.path.join("uploads", "images"), exist_ok=True)
    doc  = fitz.open(file_path)
    zoom = fitz.Matrix(2, 2)   # 2× zoom → pixel_y = pdf_point_y * 2

    # ── regexes ───────────────────────────────────────────────────────────────
    # Matches "1.", "1)", "Q1", "Q1." at start of a block/line.
    # CRITICAL: the character AFTER the number+punctuation must be a letter,
    # quote, or parenthesis — this prevents matching "(3 x 6 = 18 Marks)".
    QUESTION_NUM = re.compile(
        r"(?:^|\n)\s*(?:Q\.?\s*)?\(?(\d{1,2})\)?[\.\)]\s+(?=[A-Za-z\(\"'])"
        r"|(?:^|\n)\s*(?:Q\.?\s*)(\d{1,2})[\.\)]?\s+(?=[A-Za-z\(\"'])",
    )
    # Matches "(a)", "(b)", "a)", "i)", "ii)", "(iv)" sub-part labels
    SUB_PART     = re.compile(r"(?:^|\n)\s*\(([a-ziv]+)\)\s*")
    # "Page N of M" — page number printed at bottom of page
    PAGE_FOOTER  = re.compile(r"^\s*Page\s+\d+\s+of\s+\d+\s*$", re.IGNORECASE)

    # ── PASS 1: parse all pages, collect anchors ───────────────────────────────
    #
    # anchors  – flat ordered list across all pages:
    #            {"q": question_dict, "page": page_num, "y0": int (pixels)}
    # all_pages – per-page metadata:
    #            {"page_img": PIL.Image, "page_h": int, "footer_y": int}

    all_pages: list[dict] = []
    anchors:   list[dict] = []

    for page_num in range(len(doc)):
        page    = doc[page_num]
        pixmap  = page.get_pixmap(matrix=zoom)
        page_img = Image.frombytes("RGB", [pixmap.width, pixmap.height], pixmap.samples)
        page_h   = pixmap.height

        blocks = page.get_text(
            "blocks",
            flags=(fitz.TEXT_PRESERVE_LIGATURES
                   | fitz.TEXT_PRESERVE_WHITESPACE
                   | fitz.TEXT_MEDIABOX_CLIP),
        )
        blocks.sort(key=lambda b: b[1])   # sort top → bottom by y0

        questions:  list[dict] = []
        current_q   = None
        current_sub = None
        footer_y    = page_h   # will be updated when we hit a footer/stop marker

        for b in blocks:
            text = b[4].strip()
            if not text:
                continue

            # Hard stop: CO/BTL distribution table or star separator.
            # Only treat as footer when we have already found at least one
            # question on this page, OR the block appears in the lower half
            # of the page (>50% down) — this prevents the HEADER CO table
            # (which appears before any questions) from falsely stopping us.
            _HARD_STOP_SIGNALS = ("CO1", "CO2", "CO3", "CO01", "CO02", "BTL")
            block_y_px = int(b[1] * 2)
            past_midpage = block_y_px > (page_h * 0.40)
            if "Course Outcome" in text and any(s in text for s in _HARD_STOP_SIGNALS):
                if questions or past_midpage:
                    footer_y = block_y_px
                    break
                else:
                    continue   # skip the header CO table, keep reading
            if re.match(r"^\*{3,}$", text.strip()):
                footer_y = block_y_px
                break

            # Page-number line ("Page 1 of 3") — record y but don't stop
            if PAGE_FOOTER.match(text):
                footer_y = min(footer_y, block_y_px)
                continue

            m_q = QUESTION_NUM.search(text)
            if m_q:
                # Use whichever capture group matched (two alternatives in regex)
                q_num = m_q.group(1) or m_q.group(2)
                # 20 px padding so the question number is never clipped at top
                y0 = max(0, int(b[1] * 2) - 20)

                current_q = {
                    "question_number": f"Q{q_num}",
                    "text":            text[m_q.end():].strip(),
                    "sub_parts":       [],
                }
                questions.append(current_q)
                anchors.append({"q": current_q, "page": page_num, "y0": y0})
                current_sub = None

                # Check for an inline sub-part immediately after the question number
                m_s = SUB_PART.search(current_q["text"])
                if m_s:
                    sub_id = m_s.group(1)
                    current_sub = {
                        "question_number": f"Q{q_num}{sub_id}",
                        "text":            current_q["text"][m_s.end():].strip(),
                    }
                    current_q["sub_parts"].append(current_sub)
                    current_q["text"] = current_q["text"][:m_s.start()].strip()

            elif current_q:
                m_s = SUB_PART.search(text)
                if m_s:
                    sub_id = m_s.group(1)
                    current_sub = {
                        "question_number": f"{current_q['question_number']}{sub_id}",
                        "text":            text[m_s.end():].strip(),
                    }
                    current_q["sub_parts"].append(current_sub)
                else:
                    if current_sub:
                        current_sub["text"] += " " + text
                    else:
                        current_q["text"] += " " + text

        # DEBUG
        print(f"[PYQ DEBUG] Page {page_num + 1}/{len(doc)}: "
              f"{len(questions)} question(s), footer_y={footer_y}px")
        for q in questions:
            subs = [sp["question_number"] for sp in q.get("sub_parts", [])]
            print(f"  {q['question_number']}: \"{q['text'][:70]}\" | sub_parts={subs}")

        if progress_callback:
            progress_callback(f"Parsed page {page_num + 1}/{len(doc)}")

        all_pages.append({"page_img": page_img, "page_h": page_h, "footer_y": footer_y})

    doc.close()

    if not anchors:
        print("[PYQ DEBUG] No questions detected — aborting image extraction.")
        return []

    # ── PASS 1b: compute per-question crop boundaries globally ────────────────
    #
    # Q_i image  =  from (anchors[i].page, anchors[i].y0)
    #               to   (anchors[i+1].page, anchors[i+1].y0 - 10)
    # Last question ends at footer_y of its own page.

    for i, anchor in enumerate(anchors):
        if i < len(anchors) - 1:
            nxt = anchors[i + 1]
            anchor["end_page"] = nxt["page"]
            anchor["end_y"]    = max(nxt["y0"] - 10, 0)
        else:
            anchor["end_page"] = anchor["page"]
            anchor["end_y"]    = all_pages[anchor["page"]]["footer_y"]

    # ── PASS 2: metadata extraction + batch LLM topic matching ────────────────
    all_qs_flat: list[dict] = []

    for anchor in anchors:
        q    = anchor["q"]
        meta = extract_metadata(q["text"])
        q["marks"]             = meta["marks"]
        q["co"]                = meta["co"]
        q["btl"]               = meta["btl"]
        q["implicit_formulas"] = []

        for sq in q["sub_parts"]:
            smeta               = extract_metadata(sq["text"])
            sq["marks"]         = smeta["marks"] or q["marks"]
            sq["co"]            = smeta["co"]    or q["co"]
            sq["btl"]           = smeta["btl"]   or q["btl"]
            sq["implicit_formulas"] = []
            sq["parent_number"]     = q["question_number"]
            sq["parent_text"]       = q["text"]
            sq["full_text_for_llm"] = (
                f"Parent context: {q['text']}  Sub-question: {sq['text']}"
            )
            all_qs_flat.append(sq)

        if not q["sub_parts"]:
            q["full_text_for_llm"] = q["text"]
            all_qs_flat.append(q)

    if progress_callback:
        progress_callback(
            f"Batch-matching {len(all_qs_flat)} questions/sub-parts to topics (1 LLM call)…"
        )

    question_texts  = [q["full_text_for_llm"] for q in all_qs_flat]
    matched_batches = batch_match_topics_with_llm(question_texts, syllabus_topics)

    for q, topics in zip(all_qs_flat, matched_batches):
        q["matched_topics"] = topics

    if progress_callback:
        progress_callback("Topic matching complete — cropping images and writing to KG…")

    # ── PASS 3: stitch / crop, save images, write to KG ───────────────────────
    results: list[tuple] = []

    def _stitch(start_page: int, start_y: int,
                end_page:   int, end_y:   int) -> "Image.Image | None":
        """
        Return one PIL Image covering [start_page, start_y] → [end_page, end_y].

        Single-page question  → simple crop.
        Cross-page question   → bottom-of-page-N  +  full middle pages (rare)
                                + top-of-page-N+1, all pasted vertically.
        """
        parts: list[Image.Image] = []

        if start_page == end_page:
            pg  = all_pages[start_page]["page_img"]
            y_a = max(start_y, 0)
            y_b = min(end_y, pg.height)
            if y_b > y_a:
                parts.append(pg.crop((0, y_a, pg.width, y_b)))
        else:
            # Bottom slice of the starting page
            pg  = all_pages[start_page]["page_img"]
            y_a = max(start_y, 0)
            if pg.height > y_a:
                parts.append(pg.crop((0, y_a, pg.width, pg.height)))

            # Any complete intermediate pages (very rare)
            for pn in range(start_page + 1, end_page):
                parts.append(all_pages[pn]["page_img"])

            # Top slice of the ending page
            if end_page < len(all_pages) and end_y > 0:
                pg  = all_pages[end_page]["page_img"]
                y_b = min(end_y, pg.height)
                if y_b > 0:
                    parts.append(pg.crop((0, 0, pg.width, y_b)))

        if not parts:
            return None

        total_w = max(p.width  for p in parts)
        total_h = sum(p.height for p in parts)
        out     = Image.new("RGB", (total_w, total_h), color=(255, 255, 255))
        y_off   = 0
        for part in parts:
            out.paste(part, (0, y_off))
            y_off += part.height
        return out

    def _write_to_kg(q: dict, fname: "str | None"):
        """
        Write one top-level question to the KG.
        Sub-parts (a, b, …) each become their own KG node but share the
        same image (the combined crop of the whole question).
        """
        flat = q["sub_parts"] if q.get("sub_parts") else [q]
        map_questions_to_kg(neo4j_driver, course_code, flat, fname, doc_id)
        for item in flat:
            results.append((item, fname))

    for anchor in anchors:
        q          = anchor["q"]
        start_page = anchor["page"]
        start_y    = anchor["y0"]
        end_page   = anchor["end_page"]
        end_y      = anchor["end_y"]

        img   = _stitch(start_page, start_y, end_page, end_y)
        fname = None

        if img and img.width > 0 and img.height > 20:
            fname = f"images/{uuid.uuid4().hex}.jpg"
            img.save(os.path.join("uploads", fname), format="JPEG", quality=85)
            print(f"[PYQ DEBUG] Saved  {fname}  ({img.width}×{img.height}px)"
                  f"  [{q['question_number']} "
                  f"p{start_page + 1}:y{start_y} → p{end_page + 1}:y{end_y}]")
        else:
            print(f"[PYQ DEBUG] Empty crop for {q['question_number']} "
                  f"(p{start_page + 1} y{start_y}→p{end_page + 1} y{end_y}) — skipped")

        _write_to_kg(q, fname)

    if progress_callback:
        progress_callback(f"✓ Processed {len(anchors)} questions")

    return results

