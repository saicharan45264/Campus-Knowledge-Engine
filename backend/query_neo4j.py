import json
import re
from embedder import embed_text, cosine_similarity

def get_course_codes_for_topic(neo4j_driver, topic: str, original_query: str = "") -> list[str]:
    """Resolve a PYQ topic to its owning syllabus course(s)."""
    explicit_codes = list(dict.fromkeys(
        code.upper() for code in re.findall(r"\b\d{2}[A-Z]{2,4}\d{3,4}\b", (original_query or "").upper())
    ))
    with neo4j_driver.session() as session:
        if explicit_codes:
            existing = session.run(
                "MATCH (c:Course) WHERE toUpper(c.code) IN $codes "
                "RETURN DISTINCT toUpper(c.code) AS course_code",
                codes=explicit_codes,
            ).data()
            return [row["course_code"] for row in existing if row.get("course_code")] or explicit_codes

        if not topic or not topic.strip():
            return []

        matched_topics = session.run(
            """
            MATCH (c:Course)-[:HAS_UNIT]->(:Unit)-[:HAS_TOPIC]->(t:Topic)
            WHERE toLower(t.name) = toLower($topic)
               OR toLower(t.name) CONTAINS toLower($topic)
               OR (size($topic) > 3 AND toLower($topic) CONTAINS toLower(t.name))
            RETURN DISTINCT toUpper(c.code) AS course_code
            """,
            topic=topic.strip(),
        ).data()
        codes = [row["course_code"] for row in matched_topics if row.get("course_code")]
        if codes:
            return codes

        matched_courses = session.run(
            """
            MATCH (c:Course)
            WHERE toLower(coalesce(c.name, c.title, '')) CONTAINS toLower($topic)
               OR (size($topic) > 3 AND toLower($topic) CONTAINS toLower(coalesce(c.name, c.title, '')))
            RETURN DISTINCT toUpper(c.code) AS course_code
            """,
            topic=topic.strip(),
        ).data()
        return [row["course_code"] for row in matched_courses if row.get("course_code")]


async def fetch_problems_by_topic_graph(
    neo4j_driver, topic: str, course_codes: list[str] | None = None
) -> list[dict]:
    """
    Return only questions explicitly linked to the matching syllabus topic.
    Confirmed and provisional edges are preserved as separate result rows.
    Matches topic name via direct substring and Lucene fulltext index on Topic(name).
    """
    if not topic or not topic.strip():
        return []

    clean_topic = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in topic).strip()

    with neo4j_driver.session() as session:
        # 1. Direct name match traversal
        result = session.run(
            """
            MATCH (c:Course)-[:HAS_UNIT]->(:Unit)-[:HAS_TOPIC]->(t:Topic)
            WHERE toLower(t.name) CONTAINS toLower($topic)
               OR (size($topic) > 3 AND toLower($topic) CONTAINS toLower(t.name))
            MATCH (q:Question)-[r:TESTS_TOPIC|SUGGESTED_TOPIC]->(t)
            WHERE coalesce(q.course_code, c.code) = c.code
              AND (size($course_codes) = 0 OR toUpper(c.code) IN $course_codes)
            RETURN DISTINCT q.id AS id, q.question_number AS question_number,
                   q.text AS text, q.marks AS marks, q.btl AS btl,
                   q.has_figure AS has_figure,
                   q.image_url AS image_url, c.code AS course_code,
                   t.name AS topic_name, type(r) AS topic_link_type,
                   r.confidence AS mapping_score, r.mapping_status AS mapping_status
            ORDER BY CASE WHEN topic_link_type = 'TESTS_TOPIC' THEN 0 ELSE 1 END,
                     has_figure DESC, question_number ASC
            """,
            topic=topic.strip(), course_codes=[c.upper() for c in (course_codes or [])],
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
                    MATCH (c:Course)-[:HAS_UNIT]->(:Unit)-[:HAS_TOPIC]->(t)
                    MATCH (q:Question)-[r:TESTS_TOPIC|SUGGESTED_TOPIC]->(t)
                    WHERE coalesce(q.course_code, c.code) = c.code
                      AND (size($course_codes) = 0 OR toUpper(c.code) IN $course_codes)
                    RETURN DISTINCT q.id AS id, q.question_number AS question_number,
                           q.text AS text, q.marks AS marks, q.btl AS btl,
                           q.has_figure AS has_figure,
                           q.image_url AS image_url, c.code AS course_code,
                           t.name AS topic_name, type(r) AS topic_link_type,
                           r.confidence AS mapping_score, r.mapping_status AS mapping_status, score
                    ORDER BY CASE WHEN topic_link_type = 'TESTS_TOPIC' THEN 0 ELSE 1 END,
                             has_figure DESC, score DESC
                    LIMIT 20
                    """,
                    clean_topic=clean_topic,
                    course_codes=[c.upper() for c in (course_codes or [])],
                )
                ft_data = ft_result.data()
                if ft_data:
                    return ft_data
            except Exception as _fte:
                print(f"[Neo4j] topic_name_ft traversal notice: {_fte}")

        return []



def fetch_all_problems_by_topic(
    neo4j_driver, topic: str, course_codes: list[str] | None = None
) -> list[dict]:
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
            WHERE (match_count > 0 OR code_score > 0 OR qtext CONTAINS toLower($topic))
              AND (size($course_codes) = 0 OR toUpper(code) IN $course_codes)
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
            course_codes=[c.upper() for c in (course_codes or [])],
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
                        WHERE size($course_codes) = 0 OR toUpper(code) IN $course_codes
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
                    """, ft_query=clean_ft,
                         course_codes=[c.upper() for c in (course_codes or [])]).data()
                    if ft_res:
                        return ft_res
                except Exception as _fte:
                    print(f"[Neo4j] Lucene fulltext fallback notice: {_fte}")
        return data

