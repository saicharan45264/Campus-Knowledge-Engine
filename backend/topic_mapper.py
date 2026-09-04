"""
topic_mapper.py — Semantic + keyword Question → Topic mapping.

Creates (:Question)-[:TESTS_TOPIC { confidence, semantic_score,
keyword_score, mapping_method, review_status, mapped_at }]->(:Topic)

Thresholds (from .env):
  TOPIC_MAP_AUTO_APPROVE_THRESHOLD  = 0.80  (primary mapping: >= 0.80 → approved)
  TOPIC_MAP_SECONDARY_MIN_THRESHOLD = 0.70  (secondary: >= 0.70 allowed)
  TOPIC_MAP_SECONDARY_MAX_GAP       = 0.15  (secondary: must be within 0.15 of primary)
  TOPIC_MAP_MAX_TOPICS               = 2    (at most 2 mappings per question)

Only Topics belonging to the same canonical course_code as the Question are
considered. Topics with approved=False (LLM-derived, pending admin review)
are excluded from automatic mapping.

Topic embeddings are cached on the Topic node as t.embedding (JSON string)
to avoid re-generating them on every mapping run.
"""

import os
import json
import math
import time
import asyncio
import httpx
import re
from datetime import datetime

from embedder import embed_text, cosine_similarity

from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'), override=True)

OLLAMA_BASE_URL    = os.getenv("OLLAMA_BASE_URL",    "http://localhost:11434")
OLLAMA_MODEL       = os.getenv("OLLAMA_MODEL",       "gemma4:12b-it-qat")

MAX_TOPICS         = int(os.getenv("TOPIC_MAP_MAX_TOPICS",                 "2"))


# ---------------------------------------------------------------------------
# Keyword score (Jaccard overlap on lowercase tokens)
# ---------------------------------------------------------------------------

_STOP_WORDS = {
    "a", "an", "the", "and", "or", "of", "in", "to", "is", "are", "for",
    "with", "on", "by", "from", "at", "it", "this", "that", "be", "as",
    "find", "show", "prove", "explain", "describe", "determine", "calculate",
    "design", "derive", "obtain", "sketch", "draw", "construct", "convert",
    "minimize", "using", "following", "given", "state"
}

def _keyword_score(q_text: str, t_name: str) -> float:
    q_tokens = {w.lower().strip(".,;:()-") for w in q_text.split()
                if len(w) > 2 and w.lower() not in _STOP_WORDS}
    t_tokens = {w.lower().strip(".,;:()-") for w in t_name.split()
                if len(w) > 2 and w.lower() not in _STOP_WORDS}
    if not q_tokens or not t_tokens:
        return 0.0
    intersection = q_tokens & t_tokens
    union        = q_tokens | t_tokens
    return len(intersection) / len(union)


def _question_topic_match_score(q_text: str, t_name: str) -> float:
    """Strong direct-match heuristic for exact topic names in question text."""
    q_lower = " ".join(w.lower().strip(".,;:()-[]{}'\"") for w in q_text.split())
    t_lower = " ".join(w.lower().strip(".,;:()-[]{}'\"") for w in t_name.split())

    if not q_lower or not t_lower:
        return 0.0

    exact = 1.0 if t_lower in q_lower else 0.0
    if exact:
        return 1.0

    q_tokens = {w for w in q_lower.split() if len(w) > 2 and w not in _STOP_WORDS}
    t_tokens = {w for w in t_lower.split() if len(w) > 2 and w not in _STOP_WORDS}
    if not q_tokens or not t_tokens:
        return 0.0

    overlap = len(q_tokens & t_tokens)
    return (overlap / max(len(t_tokens), 1)) * 0.85 if overlap else 0.0


# ---------------------------------------------------------------------------
# Neo4j helpers
# ---------------------------------------------------------------------------

def _load_approved_topics(driver, course_code: str) -> list[dict]:
    """
    Returns all approved Topic nodes for the given course.
    Each dict: {id, name, normalized_name, embedding_json}
    """
    with driver.session() as session:
        result = session.run("""
            MATCH (c:Course {code: $code})-[:HAS_UNIT]->(u:Unit)-[:HAS_TOPIC]->(t:Topic)
            WHERE t.approved = true
            RETURN t.id AS id, t.name AS name,
                   t.normalized_name AS normalized_name,
                   t.embedding AS embedding_json
        """, code=course_code)
        return [dict(r) for r in result]


def _save_topic_embedding(driver, topic_id: str, embedding: list[float]):
    """Persist the topic embedding on the Topic node for caching."""
    embedding_json = json.dumps(embedding)
    with driver.session() as session:
        session.run("""
            MATCH (t:Topic {id: $tid})
            SET t.embedding = $emb
        """, tid=topic_id, emb=embedding_json)


def _write_tests_topic(driver, q_id: str, t_id: str,
                       confidence: float, semantic_score: float,
                       keyword_score: float, review_status: str):
    now_iso = datetime.utcnow().isoformat()
    with driver.session() as session:
        session.run("""
            MATCH (q:Question {id: $q_id})
            MATCH (t:Topic {id: $t_id})
            MERGE (q)-[r:TESTS_TOPIC]->(t)
            SET r.confidence      = $conf,
                r.semantic_score  = $sem,
                r.keyword_score   = $kw,
                r.mapping_method  = 'semantic_and_keyword',
                r.review_status   = $status,
                r.mapped_at       = $now
        """, q_id=q_id, t_id=t_id,
            conf=confidence, sem=semantic_score,
            kw=keyword_score, status=review_status,
            now=now_iso)


# ---------------------------------------------------------------------------
# Core mapping function
# ---------------------------------------------------------------------------

async def map_question_to_topics(
    driver,
    question: dict,
) -> list[dict]:
    """
    Maps a single Question to up to MAX_TOPICS approved Topics using
    two-stage retrieval (dense vector similarity + LLM re-ranking).

    question dict must have: {id, text, course_code}

    Returns a list of mapping result dicts:
      [{topic_id, topic_name, confidence, semantic_score, keyword_score, review_status}]
    """
    q_id       = question["id"]
    q_text     = question.get("text", "")
    c_code     = question.get("course_code", "")

    if not q_text or not c_code:
        return []

    # Load candidate topics for this course
    topics = _load_approved_topics(driver, c_code)
    if not topics:
        return []

    # ── Stage 1: Dense Retrieval ─────────────────────────────────────────────
    q_emb = await embed_text(q_text)
    if not q_emb:
        return []

    candidate_topics = []
    for t in topics:
        t_emb_str = t.get("embedding_json")
        if t_emb_str:
            try:
                t_emb = json.loads(t_emb_str)
            except Exception:
                t_emb = []
        else:
            t_emb = await embed_text(t["name"])
            if t_emb:
                _save_topic_embedding(driver, t["id"], t_emb)
        
        if t_emb:
            sim = cosine_similarity(q_emb, t_emb)
            candidate_topics.append((sim, t))

    candidate_topics.sort(key=lambda x: x[0], reverse=True)
    top_k_candidates = candidate_topics[:8]  # Keep top 8 candidates

    if not top_k_candidates:
        return []

    topic_names = [t["name"] for _, t in top_k_candidates]
    topic_list_str = "\n".join(f"- {name}" for name in topic_names)

    # ── Stage 2: LLM Re-ranking ──────────────────────────────────────────────
    prompt = f"""You are a curriculum-aware PYQ topic classification system.

Your task is to determine which EXISTING curriculum Topic node(s) are
tested by the given previous-year question.

The curriculum topics provided below are the ONLY valid topics that may
be returned.

IMPORTANT RULES:
1. Understand the meaning, concepts, terminology, and intent of the question.
2. Select the most appropriate existing curriculum topic(s) from the list below.
3. You MUST use ONLY topic names from the provided topic list. NEVER invent a new topic.
4. For each selected topic, provide a confidence score from 0 to 100.
5. If none of the topics is a good match, return an empty list.

COURSE:
{c_code}

CANDIDATE CURRICULUM TOPICS:
{topic_list_str}

QUESTION:
{q_text}

Return JSON in exactly this format:
{{
  "topics": [
    {{
      "name": "Exact Topic Name",
      "confidence": 90
    }}
  ]
}}
"""

    mappings = []
    try:
        async with httpx.AsyncClient(headers={"ngrok-skip-browser-warning": "true"}) as client:
            resp = await client.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": prompt,
                    "format": "json",
                    "stream": False,
                    "options": {"temperature": 0.1}
                },
                timeout=180.0
            )
            resp.raise_for_status()
            result_text = resp.json().get("response", "{}")
            
            # Robust JSON extraction (handles markdown ```json ... ``` or trailing explanations)
            match = re.search(r'\{.*\}', result_text, re.DOTALL)
            if match:
                clean_json_str = match.group(0)
            else:
                clean_json_str = result_text

            try:
                result_json = json.loads(clean_json_str)
            except Exception:
                result_json = {}

            selected_topics = result_json.get("topics", [])
            
            # Map returned names back to Top-K Topic IDs and similarities
            name_to_info = {t["name"].lower().strip(): {"id": t["id"], "sim": sim, "name": t["name"]} for sim, t in top_k_candidates}
            
            for t_item in selected_topics:
                t_name = t_item.get("name", "")
                llm_conf = t_item.get("confidence", 0)
                if not t_name: continue
                
                # Clean accidental LLM formatting like "name: topic_name" or quotes
                t_clean = re.sub(r'^(name|topic)\s*:\s*', '', t_name, flags=re.IGNORECASE).strip().strip('"\'')
                t_lower = t_clean.lower().strip()
                
                # Direct match
                info = name_to_info.get(t_lower)
                
                # Substring/fuzzy match fallback among candidates
                if not info:
                    for cand_lower, cand_info in name_to_info.items():
                        if cand_lower in t_lower or t_lower in cand_lower:
                            info = cand_info
                            break

                if info:
                    semantic_score = info["sim"]
                    llm_score = float(llm_conf) / 100.0
                    final_confidence = 0.6 * semantic_score + 0.4 * llm_score
                    
                    if final_confidence >= 0.70:
                        review_status = "approved" if final_confidence >= 0.80 else "pending_review"
                        mappings.append({
                            "topic_id": info["id"],
                            "topic_name": info["name"],
                            "confidence": final_confidence,
                            "semantic_score": semantic_score,
                            "keyword_score": 0.0,
                            "review_status": review_status
                        })
                        _write_tests_topic(
                            driver, q_id, info["id"], final_confidence, semantic_score, 0.0, review_status
                        )
                else:
                    print(f"[TopicMapper] Warning: LLM returned invalid or unranked topic '{t_name}'")
                    
    except Exception as e:
        print(f"[TopicMapper] LLM Generation error ({type(e).__name__}): {e}")

    return mappings


# ---------------------------------------------------------------------------
# Batch mapping for a whole document
# ---------------------------------------------------------------------------

async def run_for_document(driver, document_id: str, report=None) -> dict:
    """
    Loads all Question nodes for the given document_id and maps each one
    to approved Topics using semantic + keyword similarity.

    Updates report.mapped_to_topic if a report object is provided.

    Returns: {mapped: N, unmapped: N, total: N}
    """
    t0 = time.time()

    # Fetch questions for this document
    with driver.session() as session:
        questions = session.run("""
            MATCH (q:Question)-[:EXTRACTED_FROM]->(doc:Document {id: $doc_id})
            RETURN q.id AS id, q.text AS text, q.course_code AS course_code
        """, doc_id=document_id).data()

    if not questions:
        print(f"[TopicMapper] No questions found for document {document_id}.")
        return {"mapped": 0, "unmapped": 0, "total": 0}

    print(f"[TopicMapper] Mapping {len(questions)} questions for document {document_id}...")

    mapped_count   = 0
    unmapped_count = 0

    for q in questions:
        try:
            results = await map_question_to_topics(driver, q)
            if results:
                mapped_count += 1
            else:
                unmapped_count += 1
        except Exception as e:
            print(f"[TopicMapper] Error mapping question {q.get('id', '?')}: {e}")
            unmapped_count += 1

    elapsed = round(time.time() - t0, 2)

    if report is not None:
        report.mapped_to_topic   = mapped_count
        report.timings["topic_map_s"] = elapsed

    print(f"[TopicMapper] Done in {elapsed}s — "
          f"mapped={mapped_count}, unmapped={unmapped_count}")

    return {
        "mapped":   mapped_count,
        "unmapped": unmapped_count,
        "total":    len(questions),
    }
