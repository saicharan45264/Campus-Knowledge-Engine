"""backend/services/topic_mapper.py — Title-resolved, multimodal & tie-broken topic mapper.

Maps exam questions cleanly to Neo4j curriculum Topic nodes via:
  1. Course-code + normalized course-title alias resolution (e.g. 23CSE104 -> 23EEE104)
  2. Batched EmbeddingGemma-2 topic & joint text+diagram embeddings + acronym expansion
  3. Selective gemma4 tie-breaker only on low-margin / ambiguous top candidates
"""
from __future__ import annotations

import json
import logging
import re

from backend.config import settings
from backend.core.http_client import GPU_SEMAPHORE, get_shared_http_client
from backend.services.embedding_service import (
    cosine_similarity,
    embed_multimodal,
    embed_texts,
    format_doc_embedding_text,
    format_query_embedding_text,
)

logger = logging.getLogger("mach_ai.topic_mapper")

ACRONYMS_TOPIC_MAP = {
    "pda": "pushdown automata",
    "nfa": "nondeterministic finite automata",
    "dfa": "deterministic finite automata",
    "cfg": "context free grammar",
    "cnf": "chomsky normal form",
    "pcp": "post correspondence problem",
    "kcl": "kirchhoff current law kirchhoffs rules",
    "kvl": "kirchhoff voltage law kirchhoffs rules",
    "kirchhoff": "kirchhoffs laws kcl kvl dc circuits",
    "kirchhoff's": "kirchhoffs laws kcl kvl dc circuits",
    "superposition": "superposition theorem network theorems dc circuits",
    "mesh": "mesh and node methods analysis dc circuits",
    "nodal": "mesh and node methods analysis dc circuits",
    "thevenin": "thevenin theorem network theorems",
    "norton": "norton theorem network theorems",
    "phasor": "phasor representation sinusoidal ac circuits",
    "rms": "root mean square average value rectifier ac waveforms",
    "rectifier": "full wave half wave rectifier diode",
    "bjt": "transistor bipolar junction",
    "fet": "field effect transistor",
    "opamp": "operational amplifier",
    "psd": "power spectral density",
    "wss": "wide sense stationary",
    "2pc": "two phase commit",
    "reluctance": "magnetic circuits flux mmf reluctance",
    "flux": "magnetic circuits flux density field strength",
    "faraday": "faradays laws electromagnetic induction self mutual inductance",
}


def _norm_course_title(title: str) -> str:
    """Normalize course title so '&' matches 'and' cleanly."""
    s = (title or "").lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9\s]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def resolve_course_and_topics(
    neo4j_driver,
    course_code: str,
    course_title: str = "",
) -> tuple[str, list[dict]]:
    """Fetch Topic nodes for course_code, resolving title aliases (e.g. 23CSE104 -> 23EEE104)."""
    c_upper = (course_code or "").upper().strip()
    with neo4j_driver.session() as session:
        records = session.run(
            """
            MATCH (c:Course {code: $c_code})-[:HAS_UNIT]->(u:Unit)-[:HAS_TOPIC]->(t:Topic)
            RETURN c.code AS code, u.title AS unit, u.number AS unit_num,
                   t.topic_id AS topic_id, t.name AS topic_name
            ORDER BY u.number, t.order
            """,
            c_code=c_upper,
        ).data()
        if records:
            return c_upper, records

        # Also check explicit ALIAS_OF link if present
        alias_records = session.run(
            """
            MATCH (c:Course {code: $c_code})-[:ALIAS_OF]->(target:Course)-[:HAS_UNIT]->(u:Unit)-[:HAS_TOPIC]->(t:Topic)
            RETURN target.code AS code, u.title AS unit, u.number AS unit_num,
                   t.topic_id AS topic_id, t.name AS topic_name
            ORDER BY u.number, t.order
            """,
            c_code=c_upper,
        ).data()
        if alias_records:
            return alias_records[0]["code"], alias_records

        # Fallback: match against syllabus Courses that own Units/Topics using normalized course_title
        if course_title:
            norm_target = _norm_course_title(course_title)
            target_tokens = {w for w in norm_target.split() if len(w) > 2 and w not in {"and", "the", "for", "of"}}
            candidates = session.run(
                """
                MATCH (c:Course)-[:HAS_UNIT]->(:Unit)-[:HAS_TOPIC]->(:Topic)
                RETURN DISTINCT c.code AS code, coalesce(nullif(c.name, ''), nullif(c.title, ''), '') AS name
                """
            ).data()

            best_code = None
            best_score = 0.0
            for cand in candidates:
                cand_norm = _norm_course_title(cand["name"])
                if not cand_norm:
                    continue
                if cand_norm == norm_target:
                    best_code = cand["code"]
                    best_score = 1.0
                    break
                cand_tokens = {w for w in cand_norm.split() if len(w) > 2 and w not in {"and", "the", "for", "of"}}
                if target_tokens and cand_tokens:
                    overlap = len(target_tokens & cand_tokens) / len(target_tokens | cand_tokens)
                    if overlap > best_score:
                        best_score = overlap
                        best_code = cand["code"]

            if best_code and best_score >= 0.65:
                resolved_records = session.run(
                    """
                    MATCH (c:Course {code: $c_code})-[:HAS_UNIT]->(u:Unit)-[:HAS_TOPIC]->(t:Topic)
                    RETURN c.code AS code, u.title AS unit, u.number AS unit_num,
                           t.topic_id AS topic_id, t.name AS topic_name
                    ORDER BY u.number, t.order
                    """,
                    c_code=best_code,
                ).data()
                if resolved_records:
                    logger.info("Resolved PYQ course %s ('%s') -> syllabus %s (score=%.2f)", c_upper, course_title, best_code, best_score)
                    return best_code, resolved_records

    return c_upper, []


def fetch_course_topics(neo4j_driver, course_code: str, course_title: str = "") -> str:
    """Fetch formatted Unit and Topic nodes for a course (with title alias resolution)."""
    _, records = resolve_course_and_topics(neo4j_driver, course_code, course_title)
    if not records:
        return "No syllabus found."
    return "\n".join(
        f"[{r['topic_id']}] {r['unit']} - {r['topic_name']}"
        for r in records
    )


def _expand_topic_acronyms(text: str) -> str:
    """Expand engineering domain acronyms and key terms for lexical and semantic matching."""
    words = text.lower().split()
    expanded = []
    for w in words:
        w_clean = w.strip(".,()[]:\"'")
        if w_clean in ACRONYMS_TOPIC_MAP:
            expanded.append(f"{w} {ACRONYMS_TOPIC_MAP[w_clean]}")
        else:
            expanded.append(w)
    return " ".join(expanded)


def _topic_keyword_overlap(q_text: str, topic_name: str) -> float:
    """Compute stemmed/token overlap between expanded question text and candidate topic."""
    stop = {
        "and", "the", "for", "with", "in", "of", "to", "a", "an",
        "on", "by", "from", "at", "as", "is", "are", "using", "laws",
        "law", "rules", "rule", "representation", "analysis",
        "method", "methods", "introduction", "basic", "basics", "systems", "system",
    }
    t_words = [
        re.sub(r"[^a-z0-9]+", "", w.lower())
        for w in topic_name.split()
    ]
    t_words = [w for w in t_words if len(w) > 2 and w not in stop]
    if not t_words:
        return 0.0

    q_exp = _expand_topic_acronyms(q_text).lower()
    q_norm = re.sub(r"[^a-z0-9\s]+", " ", q_exp)

    matches = 0.0
    for tw in t_words:
        if tw in q_norm:
            matches += 1.0
        elif len(tw) >= 5 and tw[:5] in q_norm:
            # Handle morphological matches e.g. "inductance" <-> "inductor", "rectified" <-> "rectifier"
            matches += 0.75
    return matches / len(t_words)


def _topic_cosine_similarity(v1: list[float], v2: list[float]) -> float:
    return cosine_similarity(v1, v2)


async def _tiebreak_topic_with_llm(q_text: str, candidates: list[dict]) -> list[str]:
    """Ask gemma4 to pick the most accurate Topic(s) from top 5 close candidates."""
    if not candidates:
        return []
    cand_lines = "\n".join(f"{idx+1}. [{c['id']}] {c['full']}" for idx, c in enumerate(candidates[:5]))
    prompt = (
        "Select the 1 or 2 most accurate syllabus topics for this exam question.\n"
        f"Question: {q_text[:500]}\n\n"
        f"Candidate Topics:\n{cand_lines}\n\n"
        'Return ONLY JSON: {"selected_ids": ["<topic_id>"]}'
    )
    try:
        async with GPU_SEMAPHORE:
            client = get_shared_http_client()
            resp = await client.post(
                f"{settings.ollama_base_url}/api/generate",
                json={
                    "model": settings.ollama_model,
                    "prompt": prompt,
                    "format": "json",
                    "think": False,
                    "stream": False,
                    "options": {"num_ctx": 2048, "num_predict": 80, "temperature": 0.0},
                },
                timeout=20.0,
            )
            resp.raise_for_status()
            parsed = json.loads((resp.json().get("response") or "{}").strip())
            valid_ids = {c["id"] for c in candidates[:5]}
            chosen = [tid for tid in parsed.get("selected_ids", []) if tid in valid_ids]
            return chosen[:2]
    except Exception as e:
        logger.debug("Topic tie-breaker skipped: %s", e)
        return []


async def semantic_map_questions_to_topics(
    course_code: str, syllabus_context: str, questions: list[dict]
) -> dict[int, list[str]]:
    """Map questions accurately to Neo4j Topic nodes using batched + multimodal embeddings & tie-breaking."""
    if not syllabus_context or syllabus_context == "No syllabus found." or not questions:
        return {}

    topics: list[dict] = []
    for line in syllabus_context.split("\n"):
        line_clean = line.strip()
        if not line_clean:
            continue
        m = re.match(r"\[(.*?)\]\s*(.*)", line_clean)
        if m:
            tid = m.group(1)
            full_name = m.group(2)
            tname = full_name.split(" - ")[-1] if " - " in full_name else full_name
            topics.append({"id": tid, "name": tname, "full": full_name})

    if not topics:
        return {}

    # Stage 1: Batch-embed all syllabus topics in 1-2 requests
    topic_prompts = [format_doc_embedding_text(t["full"][:300], title=course_code) for t in topics]
    topic_vecs = await embed_texts(topic_prompts, batch_size=24)
    topic_embeddings = list(zip(topics, topic_vecs))

    # Batch-embed text-only questions; use multimodal embed when a diagram crop is present
    q_combined_texts: list[str] = []
    for q in questions:
        qt = (q.get("question_text") or q.get("text") or "").strip()
        diag = (q.get("diagram_summary") or "").strip()
        combined = f"{qt} {diag}".strip()
        q_combined_texts.append(combined)

    text_only_indices = [
        i for i, q in enumerate(questions)
        if q_combined_texts[i] and not (q.get("has_figure") and q.get("image_b64"))
    ]
    text_only_prompts = [
        format_query_embedding_text(_expand_topic_acronyms(q_combined_texts[i])[:550])
        for i in text_only_indices
    ]
    text_only_vecs = await embed_texts(text_only_prompts, batch_size=24) if text_only_prompts else []
    q_vecs: dict[int, list[float]] = {
        idx: vec for idx, vec in zip(text_only_indices, text_only_vecs)
    }

    for i, q in enumerate(questions):
        if i in q_vecs or not q_combined_texts[i]:
            continue
        prompt_q = format_query_embedding_text(_expand_topic_acronyms(q_combined_texts[i])[:550])
        if q.get("has_figure") and q.get("image_b64"):
            q_vecs[i] = await embed_multimodal(prompt_q, [q["image_b64"]])
        else:
            q_vecs[i] = await embed_texts([prompt_q], batch_size=1)
            q_vecs[i] = q_vecs[i][0] if q_vecs[i] else []

    # Stage 2 & 3: Score + selective tie-breaker
    final_mapping: dict[int, list[str]] = {}
    for i, _q in enumerate(questions):
        combined_text = q_combined_texts[i]
        if not combined_text:
            continue
        q_emb = q_vecs.get(i) or []

        scored = []
        for t, t_emb in topic_embeddings:
            cos = _topic_cosine_similarity(q_emb, t_emb) if q_emb and t_emb else 0.0
            kw = _topic_keyword_overlap(combined_text, t["name"])
            hybrid = 0.55 * cos + 0.45 * kw
            scored.append((hybrid, t))

        scored.sort(key=lambda x: x[0], reverse=True)
        top_score, top_topic = scored[0]
        if top_score < 0.22:
            # Avoid polluting unrelated syllabus topics if relevance is too weak
            continue

        selected_tids = [top_topic["id"]]
        second_score = scored[1][0] if len(scored) > 1 else 0.0

        # Selective tie-breaker only on close top candidates or moderate confidence
        if (top_score - second_score < 0.04 or top_score < 0.45) and len(scored) > 1:
            tb_ids = await _tiebreak_topic_with_llm(
                combined_text,
                [item[1] for item in scored[:5]],
            )
            if tb_ids:
                selected_tids = tb_ids
        elif len(scored) > 1 and second_score >= 0.62 and second_score >= top_score * 0.88:
            selected_tids.append(scored[1][1]["id"])

        final_mapping[i] = selected_tids

    return final_mapping
