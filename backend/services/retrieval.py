"""
Vanilla Python Hybrid Retrieval
--------------------------------
Instead of relying on LangChain or LlamaIndex for retrieval, we manually write 
the complex PostgreSQL query that combines:
1. pgvector cosine similarity (Semantic)
2. tsvector full-text search (Lexical)
3. Reciprocal Rank Fusion (RRF) to merge the two result sets.
4. Neo4j Knowledge Graph retrieval — topics, questions, and their relationships
   are pulled at query time to supplement the vector results.
"""
import re

# maps the router's label to which postgres partitions we search
_LABEL_TO_TYPES = {
    "Evaluation":  ["evaluation", "pyq"],
    "Curriculum":  ["curriculum", "syllabus"],
    "Regulations": ["regulations"],
}

def hybrid_retrieve(
    query: str,
    query_embedding: list[float],
    target_labels: list[str],
    db_conn,
    top_k: int = 10,
    rrf_k: int = 60,
) -> list[str]:
    # hybrid = pgvector cosine similarity + tsvector keyword search
    # merged with reciprocal rank fusion (RRF, k=60 from the original paper)

    doc_types: list[str] = []
    for label in target_labels:
        doc_types.extend(_LABEL_TO_TYPES.get(label, []))

    if not doc_types:
        return []

    sql = """
        WITH semantic AS (
            SELECT chunks.id, chunks.content, documents.course_code,
                   ROW_NUMBER() OVER (
                       ORDER BY embedding <=> CAST(%s AS vector)
                   ) AS rank
            FROM   chunks
            INNER JOIN documents ON chunks.document_id = documents.id
            WHERE  documents.doc_type = ANY(%s)
              AND  embedding IS NOT NULL
            LIMIT  20
        ),
        lexical AS (
            SELECT chunks.id, chunks.content, documents.course_code,
                   ROW_NUMBER() OVER (
                       ORDER BY ts_rank_cd(to_tsvector('english', chunks.content), plainto_tsquery('english', %s)) DESC
                   ) AS rank
            FROM   chunks
            INNER JOIN documents ON chunks.document_id = documents.id
            WHERE  documents.doc_type = ANY(%s)
              AND  to_tsvector('english', chunks.content) @@ plainto_tsquery('english', %s)
            LIMIT  20
        ),
        fused AS (
            SELECT
                COALESCE(s.id,          l.id)          AS id,
                COALESCE(s.content,     l.content)     AS content,
                COALESCE(s.course_code, l.course_code) AS course_code,
                COALESCE(1.0 / (%s + s.rank), 0.0)
                + COALESCE(1.0 / (%s + l.rank), 0.0) AS rrf_score
            FROM   semantic s
            FULL OUTER JOIN lexical l ON s.id = l.id
        )
        SELECT content, course_code, rrf_score
        FROM   fused
        ORDER  BY rrf_score DESC
        LIMIT  %s
    """

    try:
        from psycopg2.extras import RealDictCursor
        with db_conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, (
                str(query_embedding), doc_types,
                query, doc_types, query,
                rrf_k, rrf_k, top_k
            ))
            rows = cur.fetchall()
            return [
                f"[Score: {row['rrf_score']:.4f} | Course: {row['course_code']}]\n{row['content']}"
                for row in rows
            ]
    except Exception as e:
        print(f"hybrid retrieve failed: {e}")
        return []


def graph_retrieve(query: str, neo4j_driver, top_k: int = 50) -> list[str]:
    """
    Query the Neo4j KG for topics and PYQ questions that are relevant to the
    user's query. We use simple keyword matching against topic names (fast,
    no embedding needed) and then pull the questions linked to those topics.

    When the user asks for "all" / "every" / "each" questions, we remove the
    LIMIT so every matching question is returned.
    """
    # Detect "give me ALL questions" intent — remove cap in that case
    _ALL_INTENT = re.compile(
        r"\b(all|every|each|list all|show all|give all|complete list)\b", re.IGNORECASE
    )
    want_all = bool(_ALL_INTENT.search(query))

    # Tokenise query into meaningful words (drop stopwords + short tokens)
    _STOPWORDS = {
        "what", "is", "are", "the", "a", "an", "in", "of", "for", "to",
        "how", "does", "do", "me", "my", "i", "on", "with", "about",
        "can", "you", "please", "give", "show", "list", "tell", "find",
        "and", "or", "not", "this", "that", "all", "every", "each",
    }
    tokens = [
        w for w in re.findall(r"[a-zA-Z0-9]+", query.lower())
        if len(w) > 2 and w not in _STOPWORDS
    ]
    if not tokens:
        return []

    results: list[str] = []

    try:
        with neo4j_driver.session() as session:
            # ── 1. Find topics whose name contains any query token ─────────────
            # We build a simple regex pattern for Cypher's =~ operator.
            # This is equivalent to a keyword search over topic names.
            token_pattern = "(?i).*("
            token_pattern += "|".join(re.escape(t) for t in tokens)
            token_pattern += ").*"

            topic_rows = session.run("""
                MATCH (t:Topic)
                WHERE t.name =~ $pattern
                RETURN t.name AS topic, t.course_code AS course
                LIMIT 20
            """, pattern=token_pattern).data()

            if not topic_rows:
                # try SubTopics as fallback
                topic_rows = session.run("""
                    MATCH (st:SubTopic)
                    WHERE st.name =~ $pattern
                    RETURN st.name AS topic, st.course_code AS course
                    LIMIT 20
                """, pattern=token_pattern).data()

            # ── 2. Pull questions linked to those topics OR matching the course ────
            matched_topics = [r["topic"] for r in topic_rows]

            # Also check if any tokens look like a course code (e.g. 23EEE104)
            matched_courses = session.run("""
                MATCH (c:Course)
                WHERE c.code =~ $pattern OR c.name =~ $pattern
                RETURN c.code AS code
            """, pattern=token_pattern).data()
            matched_course_codes = [r["code"] for r in matched_courses]

            if matched_topics or matched_course_codes:
                if want_all:
                    q_rows = session.run("""
                        MATCH (q:Question)
                        WHERE q.course_code IN $courses
                           OR EXISTS { MATCH (t)-[:HAS_QUESTION]->(q) WHERE t.name IN $topics }
                        OPTIONAL MATCH (t)-[:HAS_QUESTION]->(q)
                        RETURN q.text         AS text,
                               q.marks        AS marks,
                               q.co           AS co,
                               q.btl          AS btl,
                               q.image_path   AS img,
                               q.course_code  AS course,
                               COALESCE(t.name, 'Unmatched Topic') AS topic
                        ORDER BY q.course_code, topic
                    """, topics=matched_topics, courses=matched_course_codes).data()
                else:
                    q_rows = session.run("""
                        MATCH (q:Question)
                        WHERE q.course_code IN $courses
                           OR EXISTS { MATCH (t)-[:HAS_QUESTION]->(q) WHERE t.name IN $topics }
                        OPTIONAL MATCH (t)-[:HAS_QUESTION]->(q)
                        RETURN q.text         AS text,
                               q.marks        AS marks,
                               q.co           AS co,
                               q.btl          AS btl,
                               q.image_path   AS img,
                               q.course_code  AS course,
                               COALESCE(t.name, 'Unmatched Topic') AS topic
                        ORDER BY q.course_code, topic
                        LIMIT $k
                    """, topics=matched_topics, courses=matched_course_codes, k=top_k).data()

                for row in q_rows:
                    meta = f"Marks: {row['marks'] or 'N/A'} | CO: {row['co'] or 'N/A'} | BTL: {row['btl'] or 'N/A'}"
                    img_note = f" | Image: /static/{row['img']}" if row.get("img") else ""
                    results.append(
                        f"[KG | Topic: {row['topic']} | Course: {row['course']}]\n"
                        f"PYQ Question ({meta}{img_note}): {row['text']}"
                    )

            # ── 3. Pull course/unit/topic structure for curriculum questions ───
            course_rows = session.run("""
                MATCH (c:Course)-[:HAS_UNIT]->(u:Unit)-[:HAS_TOPIC]->(t:Topic)
                WHERE c.name =~ $pattern OR t.name =~ $pattern OR u.title =~ $pattern
                RETURN c.code AS code, c.name AS course_name,
                       u.title AS unit, t.name AS topic
                LIMIT 8
            """, pattern=token_pattern).data()

            for row in course_rows:
                results.append(
                    f"[KG | Curriculum | Course: {row['code']}]\n"
                    f"Course: {row['course_name']} | {row['unit']} → Topic: {row['topic']}"
                )

    except Exception as e:
        print(f"graph retrieve failed: {e}")

    return results


def build_context(pg_results: list[str], kg_results: list[str]) -> str:
    """
    Merge PostgreSQL hybrid results and Neo4j KG results into a single
    context string for the LLM. KG results come first because they are
    more precise (exact topic/question matches).
    """
    parts: list[str] = []
    if kg_results:
        parts.append("=== Knowledge Graph Results ===")
        parts.extend(kg_results)
    if pg_results:
        parts.append("=== Document Chunk Results ===")
        parts.extend(pg_results)
    return "\n\n".join(parts)
