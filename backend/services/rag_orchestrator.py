"""backend/services/rag_orchestrator.py — RAG Retrieval Orchestrator & LLM Inference Streamer.

Orchestrates multi-turn conversation memory, hybrid PostgreSQL RRF search,
topic extraction from queries, and SSE token streaming with Gemma <think> blocks.
"""
from __future__ import annotations

import json
import logging
import re
import time
from collections import defaultdict
from collections.abc import AsyncGenerator

from sqlalchemy import text

from backend.config import settings
from backend.core.http_client import GPU_SEMAPHORE, get_shared_http_client

logger = logging.getLogger("mach_ai.rag")

# -----------------------------------------------------------------------------
# Conversational Memory (Multi-Turn Sliding Window)
# -----------------------------------------------------------------------------
_session_history: dict = defaultdict(list)
_session_last_active: dict = {}
MAX_HISTORY_TURNS = 6  # Last 3 user + 3 assistant turns


def get_session_history(session_id: str | None) -> list[dict]:
    """Retrieve sliding window conversation history for a given session."""
    if not session_id:
        return []
    now = time.time()
    if session_id in _session_last_active:
        if now - _session_last_active[session_id] > 7200:  # 2-hour TTL
            _session_history[session_id] = []
    _session_last_active[session_id] = now
    return list(_session_history[session_id][-MAX_HISTORY_TURNS:])


def append_session_message(session_id: str | None, role: str, content: str) -> None:
    """Append a user or assistant message to the session's conversational memory."""
    if not session_id or not content.strip():
        return
    _session_history[session_id].append({"role": role, "content": content.strip()})
    _session_last_active[session_id] = time.time()
    if len(_session_history[session_id]) > MAX_HISTORY_TURNS:
        _session_history[session_id] = _session_history[session_id][-MAX_HISTORY_TURNS:]


def clear_session_history(session_id: str | None) -> None:
    """Clear conversation history for a given session."""
    if session_id and session_id in _session_history:
        del _session_history[session_id]
    if session_id and session_id in _session_last_active:
        del _session_last_active[session_id]


# -----------------------------------------------------------------------------
# Course Abbreviations & Aliases
# -----------------------------------------------------------------------------
COURSE_ALIASES = {
    "daa": "Design and Analysis of Algorithms",
    "os": "Operating Systems",
    "dsa": "Data Structures and Algorithms",
    "dbms": "Database Management Systems",
    "cn": "Computer Networks",
    "toc": "Theory of Computation",
    "cd": "Compiler Design",
    "ds": "Distributed Systems",
    "dist systems": "Distributed Systems",
    "maths 1": "Calculus",
    "maths 2": "Linear Algebra",
    "maths 3": "Discrete Mathematics",
    "maths 4": "Probability and Random Processes",
    "prp": "Probability and Random Processes",
    "prob": "Probability and Random Processes",
    "probability": "Probability and Random Processes",
    "ml": "Machine Learning",
    "ai": "Artificial Intelligence",
    "crypto": "Foundations of Cyber Security",
    "bee": "Basic Electrical and Electronics Engineering",
    "beee": "Basic Electrical and Electronics Engineering",
    "eee": "Introduction to Electrical and Electronics Engineering",
    "ieee": "Introduction to Electrical and Electronics Engineering",
    "coa": "Computer Organization and Architecture",
    "ca": "Computer Architecture",
    "se": "Software Engineering",
    "oop": "Object Oriented Programming",
}


def extract_topic_from_query(question: str) -> str:
    """
    Extracts the academic topic or course name from a student query.
    Resolves deictic pronouns ('this', 'that', 'circuit') against image context.
    """
    img_context_match = re.search(
        r'\[Uploaded Image Context:\s*(.*?)\]', question, flags=re.DOTALL | re.IGNORECASE
    )
    img_content_match = re.search(
        r'^Image Content:\s*(.*)', question, flags=re.DOTALL | re.IGNORECASE
    )
    image_context_raw = ""
    if img_context_match:
        image_context_raw = img_context_match.group(1).strip()
    elif img_content_match:
        image_context_raw = img_content_match.group(1).strip()

    user_text = re.sub(
        r'\[Uploaded Image Context:.*?\]', '', question, flags=re.DOTALL | re.IGNORECASE
    )
    user_text = re.sub(
        r'^Image Content:.*', '', user_text, flags=re.DOTALL | re.IGNORECASE
    ).strip()
    q_lower = user_text.lower()

    deictic_words = {
        "this", "that", "it", "these", "those", "here", "figure", "fig",
        "diagram", "circuit", "image", "pic", "picture", "schematic", "attachment"
    }

    extracted = ""
    markers = [
        "questions only on", "only questions on", "problems only on", "only problems on",
        "problems on", "problem on", "questions on", "question on",
        "pyqs on", "pyq on", "problems about", "questions about",
        "questions for", "problems for", "find questions on",
        "find problems on", "pyqs for",
    ]
    for marker in markers:
        if marker in q_lower:
            idx = q_lower.find(marker)
            extracted = user_text[idx + len(marker) :].strip("? .!").strip()
            break

    # Strip trailing course code / marks / "only" modifiers from extracted topic
    if extracted:
        extracted = re.sub(r"\b(?:in|of|for|from)\s+\d{2}[A-Z]{3}\d{3}\b", "", extracted, flags=re.IGNORECASE)
        extracted = re.sub(r"\b\d{2}[A-Z]{3}\d{3}\b", "", extracted, flags=re.IGNORECASE)
        extracted = re.sub(r"\b(?:for|with|of)?\s*\d+\s*marks?\b", "", extracted, flags=re.IGNORECASE)
        extracted = re.sub(r"\b(?:only|specifically|strictly|please)\b", "", extracted, flags=re.IGNORECASE)
        extracted = re.sub(r"\s+", " ", extracted).strip(" ?.,!-")

    if not extracted:
        stop_words_for_topic = {
            "get", "me", "a", "the", "all", "questions", "question", "problems",
            "problem", "pyqs", "pyq", "on", "about", "for", "find", "show",
            "list", "give", "related", "are", "there", "any", "is", "what",
            "how", "why", "who", "where", "can", "you", "tell", "explain",
            "describe", "provide", "please", "past", "year", "solve", "practice",
            "paper", "papers", "exam", "only", "specifically", "strictly", "marks", "mark",
        }
        words = user_text.split()
        clean_words = [
            w for w in words
            if w.strip(".,!?-'\"").lower() not in stop_words_for_topic
            and not re.fullmatch(r"\d{2}[a-z]{3}\d{3}", w.strip(".,!?-'\""), re.IGNORECASE)
            and not re.fullmatch(r"\d+", w.strip(".,!?-'\""))
        ]
        if clean_words:
            extracted = " ".join(clean_words).strip("? .!")

    if extracted.lower() in COURSE_ALIASES:
        return COURSE_ALIASES[extracted.lower()]

    extracted_tokens = set(re.findall(r'\b\w+\b', extracted.lower()))
    is_deictic = not extracted or bool(extracted_tokens & deictic_words)

    if is_deictic:
        if image_context_raw and not image_context_raw.startswith("Error analyzing image:"):
            topic_json_m = re.search(
                r'"topic":\s*"([^"]+)"', image_context_raw, re.IGNORECASE
            )
            if topic_json_m:
                cand = topic_json_m.group(1).strip()
                if cand and len(cand) > 2:
                    return cand.title()

            kw_match = re.search(
                r'(?:keywords?|components?|concept|topic):\s*(.*)',
                image_context_raw,
                re.IGNORECASE,
            )
            if kw_match:
                candidate = kw_match.group(1).split(",")[0].split("\n")[0].strip()
                if candidate and len(candidate) > 2:
                    return candidate.title()

            clean_img = re.sub(
                r'(?i)^(thinking process:?|the image shows|this is a|circuit diagram of|core concept:)\s*',
                '',
                image_context_raw,
            )
            clean_img = re.sub(r'^\d+[\.\)]\s*\*+[^*]+\*+:?\s*', '', clean_img)
            broad_domains = {
                "electrical engineering", "electronics engineering", "computer science",
                "circuit analysis", "dc circuit analysis", "ac circuit analysis",
                "engineering", "physics", "mathematics", "other",
            }
            for part in clean_img.replace("—", ",").replace("\n", ",").split(","):
                part_clean = re.sub(r'^\d+[\.\)]\s*', '', part.strip(" *#-\t.!"))
                if (
                    part_clean
                    and len(part_clean) > 2
                    and part_clean.lower() not in broad_domains
                    and not part_clean.lower().startswith("thinking")
                    and not part_clean.lower().startswith("analyze")
                ):
                    return part_clean.title()
        return ""

    return extracted.title()


def extract_course_code_from_query(question: str) -> str | None:
    """Extract an explicit 8-char course code (e.g. 23CSE104) from a student query."""
    m = re.search(r"\b(\d{2}[a-z]{3}\d{3})\b", question or "", re.IGNORECASE)
    return m.group(1).upper() if m else None


def resolve_followup_search_query(session_id: str | None, question: str) -> str:
    """Carry over the previous turn's course code only for pronoun/bare follow-ups."""
    if extract_course_code_from_query(question):
        return question
    q_lower = (question or "").lower()
    explicit_pronoun = bool(
        re.search(r"\b(?:it|its|this course|that course|this subject|that subject|this one)\b", q_lower)
    )
    if not explicit_pronoun:
        # Check if the query is a bare follow-up (e.g., "what are the textbooks?" with no course name)
        followup_stop = {
            "what", "are", "is", "the", "of", "for", "in", "on", "to", "a", "an", "and", "or",
            "give", "show", "list", "tell", "me", "all", "get", "please", "course", "subject",
            "unit", "units", "textbook", "textbooks", "texbook", "texbooks", "reference", "references",
            "evaluation", "pattern", "assessment", "marks", "mark", "split", "internal", "internals",
            "external", "externals", "end", "sem", "semester", "endsem", "end-sem", "ca", "cat", "cal",
            "prerequisite", "prerequisites", "prereq", "prereqs", "prerequite", "credits", "ltpc",
            "syllabus", "topics", "topic", "outcomes", "outcome", "objectives", "objective",
            "cos", "co", "details", "detail", "code", "lab", "experiment", "experiments", "its", "it",
            "this", "that", "1", "2", "3", "4", "5", "6", "7", "8",
            "i", "ii", "iii", "iv", "v", "vi", "vii", "viii",
            "first", "second", "third", "fourth", "fifth",
        }
        remaining = [
            w.strip(".,!?'\"()")
            for w in q_lower.split()
            if w.strip(".,!?'\"()") and w.strip(".,!?'\"()") not in followup_stop
        ]
        if remaining:
            return question

    history = get_session_history(session_id)
    for msg in reversed(history[:-1] if len(history) > 1 else history):
        code = extract_course_code_from_query(msg.get("content", ""))
        if code:
            return f"{question} ({code})"
    return question


def _build_or_tsquery(query_text: str) -> str:
    """Build an OR-relaxed Postgres tsquery string so natural questions don't fail AND matching."""
    stop = {
        "what", "is", "are", "the", "for", "of", "in", "on", "to", "a", "an",
        "and", "or", "can", "you", "tell", "me", "give", "show", "explain",
        "how", "why", "which", "about", "please", "does", "do", "list", "get",
    }
    tokens = [
        t for t in re.findall(r"[a-zA-Z0-9]+", query_text.lower())
        if len(t) > 1 and t not in stop
    ]
    if not tokens:
        return ""
    return " | ".join(dict.fromkeys(tokens[:12]))


async def hybrid_search_rrf(
    db,
    query_text: str,
    query_embedding: list[float],
    k: int = 5,
    rrf_k: int = 60,
    course_code: str | None = None,
    content_type_filter: str | None = None,
    department: str | None = None,
    semester: str | None = None,
    section: str | None = None,
    doc_type: str | None = None,
    document_id=None,
) -> list[dict]:
    """Perform Reciprocal Rank Fusion over halfvec dense vectors + OR-relaxed GIN fulltext."""
    if not query_embedding:
        return []

    embedding_str = "[" + ",".join(map(str, query_embedding)) + "]"
    or_query = _build_or_tsquery(query_text)
    c_code = course_code.upper().strip() if course_code else None
    dept = department.upper().strip() if department else None
    sem = semester.upper().strip() if semester else None
    sec = section.upper().strip() if section else None
    document_type = doc_type.lower().strip() if doc_type else None

    sql_query = text("""
        WITH vector_ranked AS (
            SELECT id, content, course_code, content_type,
                   (1.0 - (embedding <=> CAST(:embedding AS halfvec(768)))) AS vec_sim,
                   ROW_NUMBER() OVER (ORDER BY embedding <=> CAST(:embedding AS halfvec(768))) AS rank
            FROM document_chunks
            WHERE embedding IS NOT NULL
              AND (CAST(:ct_filter AS TEXT) IS NULL OR content_type = :ct_filter)
              AND (CAST(:c_code AS TEXT) IS NULL OR upper(coalesce(course_code, '')) = :c_code)
              AND (CAST(:dept AS TEXT) IS NULL OR EXISTS (
                    SELECT 1 FROM documents d WHERE d.id = document_chunks.document_id
                    AND upper(coalesce(d.department, '')) = :dept
              ))
              AND (CAST(:sem AS TEXT) IS NULL OR EXISTS (
                    SELECT 1 FROM documents d WHERE d.id = document_chunks.document_id
                    AND upper(coalesce(d.semester, '')) = :sem
              ))
              AND (CAST(:sec AS TEXT) IS NULL OR EXISTS (
                    SELECT 1 FROM documents d WHERE d.id = document_chunks.document_id
                    AND upper(coalesce(d.section, '')) = :sec
              ))
              AND (CAST(:document_id AS UUID) IS NULL OR document_chunks.document_id = CAST(:document_id AS UUID))
              AND (CAST(:doc_type AS TEXT) IS NULL OR EXISTS (
                    SELECT 1 FROM documents d WHERE d.id = document_chunks.document_id
                    AND lower(coalesce(d.doc_type, '')) = :doc_type
              ))
            ORDER BY embedding <=> CAST(:embedding AS halfvec(768))
            LIMIT 50
        ),
        text_ranked AS (
            SELECT id, content, course_code, content_type,
                   ROW_NUMBER() OVER (ORDER BY ts_rank_cd(tsv_content, query) DESC) AS rank
            FROM document_chunks,
                 to_tsquery('english', :or_query) query
            WHERE :or_query <> ''
              AND tsv_content @@ query
              AND (CAST(:ct_filter AS TEXT) IS NULL OR content_type = :ct_filter)
              AND (CAST(:c_code AS TEXT) IS NULL OR upper(coalesce(course_code, '')) = :c_code)
              AND (CAST(:dept AS TEXT) IS NULL OR EXISTS (
                    SELECT 1 FROM documents d WHERE d.id = document_chunks.document_id
                    AND upper(coalesce(d.department, '')) = :dept
              ))
              AND (CAST(:sem AS TEXT) IS NULL OR EXISTS (
                    SELECT 1 FROM documents d WHERE d.id = document_chunks.document_id
                    AND upper(coalesce(d.semester, '')) = :sem
              ))
              AND (CAST(:sec AS TEXT) IS NULL OR EXISTS (
                    SELECT 1 FROM documents d WHERE d.id = document_chunks.document_id
                    AND upper(coalesce(d.section, '')) = :sec
              ))
              AND (CAST(:document_id AS UUID) IS NULL OR document_chunks.document_id = CAST(:document_id AS UUID))
              AND (CAST(:doc_type AS TEXT) IS NULL OR EXISTS (
                    SELECT 1 FROM documents d WHERE d.id = document_chunks.document_id
                    AND lower(coalesce(d.doc_type, '')) = :doc_type
              ))
            ORDER BY ts_rank_cd(tsv_content, query) DESC
            LIMIT 50
        ),
        rrf AS (
            SELECT
                COALESCE(v.id, t.id) AS id,
                COALESCE(v.content, t.content) AS content,
                COALESCE(v.course_code, t.course_code) AS course_code,
                COALESCE(v.content_type, t.content_type) AS content_type,
                COALESCE(v.vec_sim, 0.0) AS vec_sim,
                (COALESCE(1.0/(:rrf_k + v.rank), 0.0) + COALESCE(1.0/(:rrf_k + t.rank), 0.0)) AS rrf_score
            FROM vector_ranked v
            FULL OUTER JOIN text_ranked t ON v.id = t.id
        )
        SELECT id, content, course_code, content_type, vec_sim, rrf_score
        FROM rrf
        ORDER BY rrf_score DESC, vec_sim DESC
        LIMIT :k
    """)

    try:
        async with db.begin_nested():
            result = await db.execute(
                sql_query,
                {
                    "embedding": embedding_str,
                    "or_query": or_query,
                    "rrf_k": rrf_k,
                    "k": k,
                    "c_code": c_code,
                    "ct_filter": content_type_filter,
                    "dept": dept,
                    "sem": sem,
                    "sec": sec,
                    "doc_type": document_type,
                    "document_id": document_id,
                },
            )
        chunks = [
            {
                "id": row.id,
                "content": row.content,
                "course_code": row.course_code,
                "content_type": row.content_type,
                "vec_sim": float(row.vec_sim or 0.0),
                "rrf_score": float(row.rrf_score or 0.0),
            }
            for row in result
        ]
        return chunks
    except Exception as e:
        logger.warning("Hybrid search RRF notice: %s", e)
        return []


async def generate_answer_stream(
    question: str,
    context: str,
    images: list[str] | None = None,
    think: bool = True,
    history: list[dict] | None = None,
) -> AsyncGenerator[str, None]:
    """Stream AI tokens chunk by chunk with conversation history and <think> traces."""
    history_block = ""
    if history:
        turns = []
        for msg in history[-4:]:
            role = "Student" if msg.get("role") == "user" else "Tutor"
            txt = (msg.get("content") or "").strip()[:500]
            if txt:
                turns.append(f"{role}: {txt}")
        if turns:
            history_block = "Recent Conversation:\n" + "\n".join(turns) + "\n\n"

    prompt = f"""You are MACH AI (M.A.C.H. - Multimodal Academic Cognitive Hub), an intelligent academic AI assistant and expert tutor for university students.
Use the provided Context as your primary authoritative source for course-specific syllabus units, course outcomes, exam questions, and department curriculum.
For conceptual explanations, theoretical derivations, algorithms, or definitions (e.g. comparing concepts, explaining algorithms, or solving problems), provide a clear, comprehensive, and accurate academic explanation using your engineering and scientific knowledge, connecting it directly to any relevant course topics in the context.
If specific course exam questions or practice problems exist in the Context, cite or include them to help the student practice.
If images or circuit diagrams are provided, carefully examine every resistor/capacitor/source value, node label, polarity, and equation to solve or explain the problem with high precision.

IMPORTANT: If the Context contains Markdown image links for diagrams (e.g. `![Diagram for ...](http://...)`), you MUST include EVERY Markdown image link directly under its corresponding question in your final answer so the student can see the circuit/diagram images.

{history_block}Context:
{context}

Question: {question}

Answer:
"""
    try:
        async with GPU_SEMAPHORE:
            client = get_shared_http_client()
            ollama_payload = {
                "model": settings.ollama_model,
                "prompt": prompt,
                "stream": True,
                "think": think,
                "options": {"num_ctx": 8192 if images else 4096, "temperature": 0.2},
            }
            if images:
                ollama_payload["images"] = images

            async with client.stream(
                "POST",
                f"{settings.ollama_base_url}/api/generate",
                json=ollama_payload,
                timeout=300.0,
            ) as response:
                response.raise_for_status()
                in_thinking = False
                async for line in response.aiter_lines():
                    if line:
                        data = json.loads(line)
                        think_text = data.get("thinking", "")
                        resp_text = data.get("response", "")

                        if think_text:
                            if not in_thinking:
                                in_thinking = True
                                yield "<think>"
                            yield think_text
                        elif resp_text:
                            if in_thinking:
                                in_thinking = False
                                yield "</think>\n\n"
                            yield resp_text

                if in_thinking:
                    yield "</think>\n\n"
    except Exception as e:
        logger.warning("Error during generation stream: %s", e)
        yield f"*(AI reformatting unavailable: {type(e).__name__}). Showing database search results below:*\n\n{context}"


async def generate_answer(question: str, context: str) -> str:
    """Non-streaming answer generation helper for fallback and evaluation."""
    chunks = []
    async for chunk in generate_answer_stream(question, context, think=False):
        chunks.append(chunk)
    return "".join(chunks) or "I could not generate an answer."


__all__ = [
    "COURSE_ALIASES",
    "MAX_HISTORY_TURNS",
    "append_session_message",
    "clear_session_history",
    "extract_course_code_from_query",
    "extract_topic_from_query",
    "generate_answer",
    "generate_answer_stream",
    "get_session_history",
    "hybrid_search_rrf",
    "resolve_followup_search_query",
]
