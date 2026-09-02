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
from datetime import datetime

from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'), override=True)

OLLAMA_BASE_URL    = os.getenv("OLLAMA_BASE_URL",    "http://localhost:11434")
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")

AUTO_APPROVE_THRESHOLD   = float(os.getenv("TOPIC_MAP_AUTO_APPROVE_THRESHOLD",   "0.80"))
SECONDARY_MIN_THRESHOLD  = float(os.getenv("TOPIC_MAP_SECONDARY_MIN_THRESHOLD",  "0.70"))
SECONDARY_MAX_GAP        = float(os.getenv("TOPIC_MAP_SECONDARY_MAX_GAP",        "0.15"))
MAX_TOPICS               = int(os.getenv("TOPIC_MAP_MAX_TOPICS",                 "2"))


# ---------------------------------------------------------------------------
# Embedding helpers
# ---------------------------------------------------------------------------

async def _get_embedding(text: str) -> list[float]:
    """Fetch a 768-dimensional embedding via nomic-embed-text."""
    try:
        async with httpx.AsyncClient(headers={"ngrok-skip-browser-warning": "true"}) as client:
            resp = await client.post(
                f"{OLLAMA_BASE_URL}/api/embeddings",
                json={"model": OLLAMA_EMBED_MODEL, "prompt": text},
                timeout=60.0
            )
            resp.raise_for_status()
            return resp.json().get("embedding", [])
    except Exception as e:
        print(f"[TopicMapper] Embedding error: {e}")
        return []


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot   = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


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
    semantic similarity + keyword overlap.

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

    # Get question embedding
    q_emb = await _get_embedding(q_text)
    if not q_emb:
        return []

    # Score each topic
    scored = []
    for topic in topics:
        # Get or compute topic embedding
        emb_json = topic.get("embedding_json")
        if emb_json:
            try:
                t_emb = json.loads(emb_json)
            except Exception:
                t_emb = []
        else:
            t_emb = []

        if not t_emb:
            t_emb = await _get_embedding(topic["name"])
            if t_emb:
                _save_topic_embedding(driver, topic["id"], t_emb)

        sem_score = _cosine_similarity(q_emb, t_emb)
        kw_score  = _keyword_score(q_text, topic["name"])
        confidence = 0.80 * sem_score + 0.20 * kw_score

        scored.append({
            "topic_id":      topic["id"],
            "topic_name":    topic["name"],
            "confidence":    round(confidence, 4),
            "semantic_score": round(sem_score, 4),
            "keyword_score":  round(kw_score, 4),
        })

    # Sort descending by confidence
    scored.sort(key=lambda x: x["confidence"], reverse=True)

    mappings = []
    primary_conf = None

    for rank, s in enumerate(scored):
        if rank == 0:
            # Primary mapping
            if s["confidence"] >= AUTO_APPROVE_THRESHOLD:
                primary_conf = s["confidence"]
                s["review_status"] = "approved"
                mappings.append(s)
                _write_tests_topic(driver, q_id, s["topic_id"],
                                   s["confidence"], s["semantic_score"],
                                   s["keyword_score"], "approved")
            else:
                break  # Primary didn't make threshold — no secondary either

        elif rank == 1 and primary_conf is not None:
            # Secondary mapping — stricter rules
            gap = primary_conf - s["confidence"]
            if (s["confidence"] >= SECONDARY_MIN_THRESHOLD
                    and gap <= SECONDARY_MAX_GAP):
                s["review_status"] = "approved"
                mappings.append(s)
                _write_tests_topic(driver, q_id, s["topic_id"],
                                   s["confidence"], s["semantic_score"],
                                   s["keyword_score"], "approved")
            break  # At most 2 mappings regardless

        if len(mappings) >= MAX_TOPICS:
            break

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
