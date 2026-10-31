import json
from embedder import embed_text, cosine_similarity

async def fetch_problems_by_topic_graph(neo4j_driver, topic: str) -> list[dict]:
    """
    PRIMARY Graph RAG retrieval: Traverses (q:Question)-[:TESTS|TESTS_TOPIC]->(t:Topic).
    Matches topic name via direct substring and Lucene fulltext index on Topic(name).
    """
    if not topic or not topic.strip():
        return []

    clean_topic = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in topic).strip()

    with neo4j_driver.session() as session:
        # 1. Direct name match traversal
        result = session.run(
            """
            MATCH (t:Topic)
            WHERE toLower(t.name) CONTAINS toLower($topic)
               OR (size($topic) > 3 AND toLower($topic) CONTAINS toLower(t.name))
            MATCH (q:Question)-[r:TESTS|TESTS_TOPIC]->(t)
            RETURN DISTINCT q.id AS id, q.question_number AS question_number,
                   q.text AS text, q.marks AS marks, q.btl AS btl,
                   q.has_figure AS has_figure,
                   q.image_url AS image_url, q.course_code AS course_code,
                   t.name AS topic_name
            ORDER BY (CASE WHEN q.has_figure THEN 1 ELSE 0 END) DESC, toInteger(question_number) ASC
            """,
            topic=topic.strip(),
        )
        data = result.data()
        if data:
            return data

        # 2. Lucene fulltext search on Topic.name
        if clean_topic:
            try:
                ft_result = session.run(
                    """
                    CALL db.index.fulltext.queryNodes('topic_name_ft', $clean_topic)
                    YIELD node AS t, score
                    WHERE score > 1.2
                    MATCH (q:Question)-[r:TESTS|TESTS_TOPIC]->(t)
                    RETURN DISTINCT q.id AS id, q.question_number AS question_number,
                           q.text AS text, q.marks AS marks, q.btl AS btl,
                           q.has_figure AS has_figure,
                           q.image_url AS image_url, q.course_code AS course_code,
                           t.name AS topic_name, score
                    ORDER BY (CASE WHEN q.has_figure THEN 1 ELSE 0 END) DESC, score DESC
                    LIMIT 20
                    """,
                    clean_topic=clean_topic,
                )
                ft_data = ft_result.data()
                if ft_data:
                    return ft_data
            except Exception as _fte:
                print(f"[Neo4j] topic_name_ft traversal notice: {_fte}")

        return []




import json
from embedder import embed_text, cosine_similarity

# =============================================================================
# Graph RAG — primary retrieval path via TESTS_TOPIC
# =============================================================================

async def fetch_problems_by_topic_graph(neo4j_driver, topic: str) -> list[dict]:
    """
    PRIMARY retrieval path for topic-based PYQ lookup using Semantic Search.
    
    Traverses: Topic <-[TESTS_TOPIC]- Question
    Returns only auto-approved mappings (review_status = "approved").
    """
    topic_ids_to_query = []
    
    # 1. Try semantic matching first
    topic_emb = await embed_text(topic)
    if topic_emb:
        with neo4j_driver.session() as session:
            topics_result = session.run("MATCH (t:Topic) WHERE t.embedding IS NOT NULL RETURN t.id AS id, t.embedding AS emb")
            best_topics = []
            for r in topics_result:
                try:
                    t_emb = json.loads(r["emb"])
                    sim = cosine_similarity(topic_emb, t_emb)
                    if sim >= 0.65:  # threshold for similarity
                        best_topics.append((sim, r["id"]))
                except Exception:
                    pass
            
            if best_topics:
                best_topics.sort(key=lambda x: x[0], reverse=True)
                topic_ids_to_query = [t_id for _, t_id in best_topics[:3]]

    with neo4j_driver.session() as session:
        if topic_ids_to_query:
            result = session.run("""
                MATCH (t:Topic)
                WHERE t.id IN $topic_ids
                MATCH (q:Question)-[r:TESTS_TOPIC]->(t)
                WHERE r.review_status IN ['approved', 'pending_review']
                OPTIONAL MATCH (q)-[:MAPPED_TO_CO]->(co:CourseOutcome)
                OPTIONAL MATCH (q)-[:EXTRACTED_FROM]->(doc:Document)
                WITH q, doc, collect(DISTINCT co.id) AS course_outcomes, collect(DISTINCT t.name) AS topics, max(r.confidence) AS max_confidence
                WITH q.text AS text, collect(q.id)[0] AS id, collect(q.question_number)[0] AS question_number, collect(q.marks)[0] AS marks, collect(q.btl)[0] AS btl, collect(q.image_url)[0] AS image_url, collect(q.course_code)[0] AS course_code, collect(course_outcomes[0])[0] AS co_id, collect(topics[0])[0] AS topic_name, max(max_confidence) AS mapping_confidence, collect(coalesce(doc.id, ''))[0] AS source_document
                RETURN id, question_number, text, marks, btl, image_url, course_code, co_id, topic_name, mapping_confidence, source_document
                ORDER BY mapping_confidence DESC, toInteger(question_number) ASC
            """, topic_ids=topic_ids_to_query)
            data = result.data()
            if data: return data

        # Fallback to pure string matching
        result = session.run("""
            MATCH (t:Topic)
            WHERE toLower(t.name) CONTAINS toLower($topic)
               OR toLower(t.normalized_name) CONTAINS toLower($topic)
            MATCH (q:Question)-[r:TESTS_TOPIC]->(t)
            WHERE r.review_status IN ['approved', 'pending_review']
            OPTIONAL MATCH (q)-[:MAPPED_TO_CO]->(co:CourseOutcome)
            OPTIONAL MATCH (q)-[:EXTRACTED_FROM]->(doc:Document)
            WITH q, doc, collect(DISTINCT co.id) AS course_outcomes, collect(DISTINCT t.name) AS topics, max(r.confidence) AS max_confidence
            WITH q.text AS text, collect(q.id)[0] AS id, collect(q.question_number)[0] AS question_number, collect(q.marks)[0] AS marks, collect(q.btl)[0] AS btl, collect(q.image_url)[0] AS image_url, collect(q.course_code)[0] AS course_code, collect(course_outcomes[0])[0] AS co_id, collect(topics[0])[0] AS topic_name, max(max_confidence) AS mapping_confidence, collect(coalesce(doc.id, ''))[0] AS source_document
            RETURN id, question_number, text, marks, btl, image_url, course_code, co_id, topic_name, mapping_confidence, source_document
            ORDER BY mapping_confidence DESC, toInteger(question_number) ASC
        """, topic=topic)
        return result.data()


# =============================================================================
# Keyword fallback — existing logic preserved
# =============================================================================

def fetch_all_problems_by_topic(neo4j_driver, topic: str) -> list[dict]:
    """
    Executes a Cypher query to retrieve all Question nodes associated with a specific Topic or keyword.
    Returns complete metadata (marks, btl, has_figure, image_url, course_code).
    """
    stop_words = {
        "get",
        "me",
        "a",
        "the",
        "all",
        "questions",
        "question",
        "problems",
        "problem",
        "solve",
        "solved",
        "solutions",
        "solution",
        "calculate",
        "write",
        "analyse",
        "analysis",
        "determine",
        "derive",
        "sketch",
        "draw",
        "obtain",
        "evaluate",
        "on",
        "about",
        "find",
        "show",
        "list",
        "give",
        "related",
        "are",
        "there",
        "any",
        "is",
        "what",
        "how",
        "why",
        "who",
        "where",
        "can",
        "you",
        "tell",
        "explain",
        "describe",
        "provide",
        "in",
        "of",
        "to",
        "for",
        "with",
        "and",
        "or",
        "not",
        "this",
        "that",
        "do",
        "does",
        "did",
        "have",
        "has",
        "had",
        "would",
        "could",
        "should",
        "some",
        "from",
        "by",
        "an",
        "it",
        "they",
        "we",
        "he",
        "she",
        "which",
        "rule",
        "rules",
        "law",
        "laws",
        "theorem",
        "theorems",
        "method",
        "methods",
        "principle",
        "principles",
        "formula",
        "formulas",
        "technique",
        "techniques",
        "circuit",
        "circuits",
        "system",
        "systems",
        "example",
        "examples",
        "using",
    }

    # Extract clean search terms
    words = [
        w.strip(".,!?-'\"")
        for w in topic.lower().split()
        if len(w) > 2 and w not in stop_words
    ]

    if not words:
        # If the entire input was stop words (e.g. "this", "that"), don't search all questions
        cleaned = topic.strip().lower()
        if cleaned in stop_words or not cleaned:
            return []
        words = [cleaned]

    # Expand keywords to cover common academic variations
    def expand_term(w: str) -> list[str]:
        w_clean = w.lower().strip(".,!?:'\"")
        if not w_clean or len(w_clean) < 3:
            return [w_clean]

        domain_synonyms = {
            "sinusoid": ["sinusoid", "sine"],
            "sinusoids": ["sinusoid", "sine"],
            "sinusoidal": ["sinusoid", "sine"],
            "waveform": ["waveform", "wave"],
            "waveforms": ["waveform", "wave"],
            "phasor": ["phasor", "phase"],
            "phasors": ["phasor", "phase"],
            "phase": ["phasor", "phase"],
            "capacitor": ["capacit"],
            "capacitors": ["capacit"],
            "capacitance": ["capacit"],
            "capacitive": ["capacit"],
            "resistor": ["resist"],
            "resistors": ["resist"],
            "resistance": ["resist"],
            "resistive": ["resist"],
            "inductor": ["induct"],
            "inductors": ["induct"],
            "inductance": ["induct"],
            "inductive": ["induct"],
            "diode": ["diode", "zener"],
            "diodes": ["diode", "zener"],
            "transistor": ["transistor", "bjt", "fet", "mosfet"],
            "transistors": ["transistor", "bjt", "fet", "mosfet"],
            "opamp": ["opamp", "op-amp", "operational amplifier", "inverting amplifier"],
            "division": ["division", "divider", "divid"],
            "divider": ["division", "divider", "divid"],
            "clipper": ["clip", "clamp"],
            "clippers": ["clip", "clamp"],
            "clamper": ["clip", "clamp"],
            "clampers": ["clip", "clamp"],
            "rectifier": ["rectifi"],
            "rectifiers": ["rectifi"],
            "rectification": ["rectifi"],
            "nodal": ["node", "nodal"],
            "node": ["node", "nodal"],
            "nodes": ["node", "nodal"],
            "mesh": ["mesh", "loop"],
            "loop": ["mesh", "loop"],
            "loops": ["mesh", "loop"],
            "thevenin": ["thevenin"],
            "thevenins": ["thevenin"],
            "norton": ["norton"],
            "nortons": ["norton"],
            "superposition": ["superposition"],
            "binary": ["binary tree", "binary"],
            "tree": ["tree"],
            "trees": ["tree"],
            "turing": ["turing"],
            "automata": ["automata", "automaton", "pda", "dfa", "nfa"],
            "automaton": ["automata", "automaton", "pda", "dfa", "nfa"],
            "pda": ["pda", "pushdown"],
            "pushdown": ["pda", "pushdown"],
            "nfa": ["nfa", "nondeterministic", "thompson"],
            "thompson": ["thompson", "nfa"],
            "dfa": ["dfa", "deterministic"],
            "deterministic": ["dfa", "deterministic"],
            "grammar": ["grammar", "cfg"],
            "grammars": ["grammar", "cfg"],
            "cfg": ["grammar", "cfg"],
            "probability": ["probab"],
            "random": ["random"],
            "density": ["density"],
            "distribution": ["distribut"],
            "spectral": ["spectral", "psd"],
            "psd": ["spectral", "psd"],
            "wss": ["wss", "wide sense stationary", "stationary"],
            "stationary": ["wss", "stationary"],
            "distributed": ["distributed"],
            "lamport": ["lamport", "timestamp", "vector"],
            "timestamp": ["timestamp", "lamport", "vector"],
            "timestamps": ["timestamp", "lamport", "vector"],
            "election": ["election", "bully", "ring"],
            "bully": ["bully", "election"],
            "ring": ["ring", "election"],
            "commit": ["commit", "2pc", "two-phase"],
            "2pc": ["commit", "2pc", "two-phase"],
            "process": ["process"],
            "processes": ["process"],
            "scheduling": ["schedul"],
            "deadlock": ["deadlock"],
            "deadlocks": ["deadlock"],
            "semaphore": ["semaphore"],
            "synchronization": ["synchroniz"],
            "pipeline": ["pipeline"],
            "cache": ["cache"],
            "caches": ["cache"],
            "database": ["database", "dbms"],
            "databases": ["database", "dbms"],
            "transaction": ["transaction"],
            "transactions": ["transaction"],
            "sorting": ["sort"],
            "hashing": ["hash"],
        }
        if w_clean in domain_synonyms:
            return domain_synonyms[w_clean]

        terms = {w_clean}
        # Automatic morphological root extraction:
        # Plurals: 'circuits' -> 'circuit', 'voltages' -> 'voltage'
        if w_clean.endswith("ies") and len(w_clean) > 4:
            terms.add(w_clean[:-3] + "y")
        elif w_clean.endswith("es") and len(w_clean) > 4:
            terms.add(w_clean[:-2])
            terms.add(w_clean[:-1])
        elif w_clean.endswith("s") and len(w_clean) > 3:
            terms.add(w_clean[:-1])

        # Adjective / participle endings
        if w_clean.endswith("al") and len(w_clean) > 4:
            terms.add(w_clean[:-2])
        if w_clean.endswith("ive") and len(w_clean) > 5:
            terms.add(w_clean[:-3])
        if w_clean.endswith("ing") and len(w_clean) > 5:
            terms.add(w_clean[:-3])
        if w_clean.endswith("ed") and len(w_clean) > 4:
            terms.add(w_clean[:-2])

        return list(terms)

    word_groups = [expand_term(w) for w in words]
    if not word_groups:
        word_groups = [[topic.lower()]]

    with neo4j_driver.session() as session:
        # Unified cross-course & cross-topic retrieval:
        # 1. Matches course code (e.g. 23CSE303, 23MAT216, 23EEE104)
        # 2. Matches course name (e.g. 'Theory of Computation', 'Distributed Systems')
        # 3. Matches topic name attached to questions
        # 4. Matches expanded domain keyword groups in question text with ranked scoring
        result = session.run(
            """
            MATCH (q:Question)
            OPTIONAL MATCH (q)-[:BELONGS_TO]->(c:Course)
            OPTIONAL MATCH (c_t:Course)-[:TESTED_BY]->(q)
            OPTIONAL MATCH (q)-[:TESTS|TESTS_TOPIC]->(t:Topic)
            WITH q, c, c_t, t,
                 coalesce(c.code, c_t.code, q.course_code, '') AS code,
                 coalesce(c.name, c_t.name, '') AS cname,
                 coalesce(t.name, '') AS tname,
                 replace(toLower(q.text), "'", "") AS qtext
            WITH q, code, cname, tname, qtext,
                 size([group IN $word_groups WHERE ANY(term IN group WHERE qtext CONTAINS term)]) AS match_count,
                 (CASE WHEN toLower(code) = toLower($topic) THEN 10
                       WHEN size($topic) > 3 AND toLower(cname) CONTAINS toLower($topic) THEN 8
                       WHEN size($topic) > 3 AND toLower($topic) CONTAINS toLower(code) THEN 8
                       WHEN size($topic) > 3 AND toLower(tname) CONTAINS toLower($topic) THEN 6
                       ELSE 0 END) AS code_score
            WHERE match_count > 0 OR code_score > 0 OR qtext CONTAINS toLower($topic)
            RETURN DISTINCT q.id AS id, 
                   q.question_number AS question_number, 
                   q.text AS text, 
                   q.marks AS marks,
                   q.btl AS btl,
                   q.has_figure AS has_figure,
                   q.image_url AS image_url, 
                   code AS course_code,
                   match_count + code_score AS score
            ORDER BY score DESC, (CASE WHEN q.has_figure THEN 1 ELSE 0 END) DESC, toInteger(q.question_number) ASC
        """,
            word_groups=word_groups,
            topic=topic,
        )
        data = result.data()
        if not data and topic:
            # Lucene fulltext search fallback for edge cases
            clean_ft = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in topic).strip()
            if clean_ft:
                try:
                    ft_res = session.run("""
                        CALL db.index.fulltext.queryNodes('question_text_ft', $ft_query)
                        YIELD node AS q, score
                        WHERE score > 1.2
                        OPTIONAL MATCH (q)-[:BELONGS_TO]->(c:Course)
                        OPTIONAL MATCH (c_t:Course)-[:TESTED_BY]->(q)
                        WITH q, coalesce(c.code, c_t.code, q.course_code, '') AS code, score
                        RETURN DISTINCT q.id AS id, 
                               q.question_number AS question_number, 
                               q.text AS text, 
                               q.marks AS marks,
                               q.btl AS btl,
                               q.has_figure AS has_figure,
                               q.image_url AS image_url, 
                               code AS course_code,
                               score
                        ORDER BY (CASE WHEN q.has_figure THEN 1 ELSE 0 END) DESC, score DESC
                        LIMIT 15
                    """, ft_query=clean_ft).data()
                    if ft_res:
                        return ft_res
                except Exception as _fte:
                    print(f"[Neo4j] Lucene fulltext fallback notice: {_fte}")
        return data


# =============================================================================
# Bidirectional graph visualisation query
# =============================================================================

def fetch_graph_for_course(neo4j_driver, course_code: str, limit: int = 200) -> dict:
    """
    Returns all nodes and relationships needed to visualise the full course graph.
    Includes BOTH outgoing (syllabus structure) and incoming (questions) relationships.

    Returns {"nodes": [...], "edges": [...]}
    """
    with neo4j_driver.session() as session:
        result = session.run("""
            MATCH (c:Course {code: $code})
            OPTIONAL MATCH (c)-[:HAS_UNIT]->(u:Unit)
            OPTIONAL MATCH (u)-[:HAS_TOPIC]->(t:Topic)
            OPTIONAL MATCH (q:Question)-[:BELONGS_TO]->(c)
            OPTIONAL MATCH (q)-[:MAPPED_TO_CO]->(co:CourseOutcome)
            OPTIONAL MATCH (q)-[tr:TESTS_TOPIC]->(t2:Topic)
            WITH c, u, t, q, co, t2, tr
            LIMIT $limit
            RETURN c, u, t, q, co, t2,
                   tr.confidence AS topic_confidence,
                   tr.review_status AS topic_review_status
        """, code=course_code, limit=limit)

        nodes = {}
        edges = []

        def add_node(node_id, label, props):
            if node_id and node_id not in nodes:
                nodes[node_id] = {"id": node_id, "label": label, **props}

        for record in result:
            r = dict(record)
            c  = r.get("c")
            u  = r.get("u")
            t  = r.get("t")
            q  = r.get("q")
            co = r.get("co")
            t2 = r.get("t2")

            if c:
                add_node(c.get("code"), "Course",
                         {"name": c.get("name", ""), "code": c.get("code", "")})
            if u:
                uid = u.get("id") or u.get("title", "")
                add_node(uid, "Unit",
                         {"title": u.get("title", ""), "number": u.get("number", "")})
                if c:
                    edges.append({"from": c.get("code"), "to": uid, "type": "HAS_UNIT"})
            if t:
                add_node(t.get("id"), "Topic",
                         {"name": t.get("name", ""), "approved": t.get("approved", False)})
                if u:
                    uid = u.get("id") or u.get("title", "")
                    edges.append({"from": uid, "to": t.get("id"), "type": "HAS_TOPIC"})
            if q:
                qid = q.get("id", "")
                add_node(qid, "Question",
                         {"text": (q.get("text") or "")[:80],
                          "question_number": q.get("question_number", ""),
                          "marks": q.get("marks"),
                          "btl": q.get("btl", ""),
                          "image_url": q.get("image_url", "")})
                if c:
                    edges.append({"from": qid, "to": c.get("code"), "type": "BELONGS_TO"})
            if co and q:
                qid = q.get("id", "")
                add_node(co.get("id"), "CourseOutcome", {"id": co.get("id", "")})
                edges.append({"from": qid, "to": co.get("id"), "type": "MAPPED_TO_CO"})
            if t2 and q:
                qid = q.get("id", "")
                add_node(t2.get("id"), "Topic",
                         {"name": t2.get("name", ""), "approved": t2.get("approved", False)})
                edges.append({
                    "from":          qid,
                    "to":            t2.get("id"),
                    "type":          "TESTS_TOPIC",
                    "confidence":    r.get("topic_confidence"),
                    "review_status": r.get("topic_review_status"),
                })

        return {"nodes": list(nodes.values()), "edges": edges}
