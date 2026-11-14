"""backend/services/graph_service.py — Neo4j Knowledge Graph & GraphRAG Service.

Encapsulates all Neo4j Cypher traversals, CRAG corrective subgraph expansion,
prerequisite DAG construction, Bloom's Taxonomy / CO mappings, and syllabus queries.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import logging
import re

from backend.core.intent_router import normalize_query_typos
from backend.services.embedding_service import (
    cosine_similarity,
    embed_text,
    embed_texts,
    format_doc_embedding_text,
    format_query_embedding_text,
)
from backend.services.topic_mapper import (
    _expand_topic_acronyms,
    fetch_course_topics,
    resolve_course_and_topics,
    semantic_map_questions_to_topics,
)

logger = logging.getLogger("mach_ai.graph")

# -----------------------------------------------------------------------------
# Curated Course Metadata & Prerequisite DAG
# -----------------------------------------------------------------------------
COURSE_METADATA = {
    # Mathematics & Foundational Sciences
    "23MAT101": "Calculus",
    "19MAT101": "Calculus",
    "23MAT116": "Discrete Mathematics",
    "19MAT111": "Discrete Mathematics",
    "23MAT117": "Linear Algebra",
    "19MAT112": "Linear Algebra",
    "23MAT216": "Probability and Random Processes",
    "19MAT205": "Probability and Random Processes",
    "23CHY101": "Engineering Chemistry",
    "23PHY101": "Engineering Physics",
    "23EEE104": "Electric Circuits",

    # Core Computer Science (2023 & 2019 Regulations)
    "23CSE101": "Computational Problem Solving",
    "19CSE101": "Computer Programming",
    "23CSE102": "Computer Hardware Essentials",
    "23CSE111": "Object Oriented Programming",
    "23CSE201": "Procedural Programming Using C",
    "19CSE201": "Object Oriented Programming",
    "23CSE202": "Database Management Systems",
    "19CSE202": "Database Management Systems",
    "23CSE203": "Data Structures and Algorithms",
    "19CSE203": "Data Structures and Algorithms",
    "23CSE204": "Operating Systems",
    "23CSE214": "Operating Systems",
    "19CSE204": "Operating Systems",
    "23CSE205": "Digital Electronics",
    "23CSE211": "Design and Analysis of Algorithms",
    "19CSE211": "Design and Analysis of Algorithms",
    "23CSE212": "Principles of Functional Languages",
    "23CSE213": "Computer Organization and Architecture",
    "19CSE213": "Computer Organization and Architecture",
    "23CSE301": "Machine Learning",
    "19CSE301": "Machine Learning",
    "23CSE302": "Computer Networks",
    "23CSE304": "Embedded Systems",
    "19CSE304": "Computer Networks",
    "23CSE303": "Theory of Computation",
    "19CSE303": "Theory of Computation",
    "23CSE311": "Software Engineering",
    "19CSE311": "Software Engineering",
    "23CSE312": "Distributed Systems",
    "19CSE312": "Distributed Systems",
    "23CSE313": "Foundations of Cyber Security",
    "19CSE313": "Cryptography and Network Security",
    "23CSE314": "Compiler Design",
    "19CSE314": "Compiler Design",

    # Professional Electives & Advanced Streams
    "23CSE341": "Wireless Sensor Networks",
    "23CSE342": "Advanced Computer Networks",
    "23CSE343": "Wireless and Mobile Networks",
    "23CSE344": "Modern Cellular Wireless Networks",
    "23CSE345": "Software Defined Networks",
    "23CSE355": "Mining of Massive Datasets",
    "23CSE361": "Real Time Operating Systems",
    "23CSE363": "Cloud Computing",
    "23CSE364": "Cyber-Physical Systems",
    "23CSE365": "Internet of Things",
    "23CSE440": "Advanced Algorithms and Analysis",
    "23CSE441": "Introduction to Machine Learning",
    "23CSE442": "Distributed Systems",
    "23CSE453": "Competitive Programming",
    "23CSE458": "Embedded Programming",
    "23CSE466": "Parallel Programming",
    "23CSE473": "Deep Learning",
    "23CSE477": "Reinforcement Learning",
    "23AIE201": "Artificial Intelligence",
    "23AIE301": "Natural Language Processing",
}

PREREQUISITE_MAP = {
    "23CSE203": ["23MAT116", "23CSE101"],
    "19CSE203": ["19MAT111", "19CSE101"],
    "23CSE211": ["23CSE203", "23MAT116"],
    "19CSE211": ["19CSE203", "19MAT111"],
    "23CSE202": ["23CSE203"],
    "19CSE202": ["19CSE203"],
    "23CSE204": ["23CSE203", "23CSE213"],
    "23CSE214": ["23CSE203", "23CSE213"],
    "19CSE204": ["19CSE203", "19CSE213"],
    "23CSE301": ["23MAT117", "23MAT216", "23CSE203"],
    "19CSE301": ["19MAT112", "19MAT205", "19CSE203"],
    "23CSE303": ["23MAT116"],
    "19CSE303": ["19MAT111"],
    "23CSE302": ["23CSE204"],
    "19CSE304": ["19CSE204"],
    "23CSE304": ["23CSE213"],
    "23CSE312": ["23CSE204", "23CSE302"],
    "19CSE312": ["19CSE204", "19CSE304"],
    "23CSE442": ["23CSE204", "23CSE302"],
    "23CSE313": ["23MAT116", "23CSE302"],
    "19CSE313": ["19MAT111", "19CSE304"],
    "23CSE314": ["23CSE303", "23CSE203"],
    "19CSE314": ["19CSE303", "19CSE203"],
    "23CSE341": ["23CSE302"],
    "23CSE342": ["23CSE302"],
    "23CSE343": ["23CSE302"],
    "23CSE344": ["23CSE302"],
    "23CSE345": ["23CSE302"],
    "23CSE361": ["23CSE204", "23CSE304"],
    "23CSE363": ["23CSE302"],
    "23CSE364": ["23MAT116"],
    "23CSE365": ["23CSE304", "23CSE302"],
    "23CSE458": ["23CSE213"],
    "23CSE466": ["23CSE213"],
    "23CSE355": ["23CSE301"],
    "23CSE440": ["23CSE203"],
    "23CSE441": ["23MAT117", "23MAT216"],
    "23CSE473": ["23CSE301"],
    "23CSE477": ["23CSE301"],
    "23AIE201": ["23CSE203", "23MAT116"],
    "23AIE301": ["23CSE301"],
}


def add_prerequisite_edges(neo4j_driver, prerequisite_map: dict | None = None) -> None:
    """Creates REQUIRES and REQUIRES_PREREQUISITE edges between courses in Neo4j."""
    p_map = prerequisite_map or PREREQUISITE_MAP
    if not p_map:
        return

    with neo4j_driver.session() as session:
        for target_code, prereqs in p_map.items():
            t_name = COURSE_METADATA.get(target_code, target_code)
            session.run(
                """
                MERGE (c1:Course {code: $target_code})
                SET c1.name = coalesce(c1.name, $t_name),
                    c1.title = coalesce(c1.title, $t_name)
                """,
                target_code=target_code,
                t_name=t_name,
            )
            for prereq_code in prereqs:
                p_name = COURSE_METADATA.get(prereq_code, prereq_code)
                session.run(
                    """
                    MERGE (c1:Course {code: $target_code})
                    MERGE (c2:Course {code: $prereq_code})
                    SET c1.name = coalesce(c1.name, $t_name),
                        c1.title = coalesce(c1.title, $t_name),
                        c2.name = coalesce(c2.name, $p_name),
                        c2.title = coalesce(c2.title, $p_name)
                    MERGE (c1)-[:REQUIRES]->(c2)
                    MERGE (c1)-[:REQUIRES_PREREQUISITE]->(c2)
                    """,
                    target_code=target_code,
                    prereq_code=prereq_code,
                    t_name=t_name,
                    p_name=p_name,
                )


def build_syllabus_kg(neo4j_driver, dept: str, year: str, courses: list) -> None:
    """Builds the complete hierarchical syllabus Knowledge Graph in Neo4j."""
    if not courses:
        return
    with neo4j_driver.session() as session:
        session.run("MERGE (p:Program {name: $dept})", dept=dept)
        for course in courses:
            c_code = course.get("code")
            c_name = course.get("name")
            if not c_code:
                continue

            session.run(
                """
                MERGE (p:Program {name: $dept})
                MERGE (sv:SyllabusVersion {dept: $dept, year: $year})
                MERGE (p)-[:HAS_SYLLABUS_VERSION]->(sv)
                MERGE (c:Course {code: $c_code})
                ON CREATE SET c.name = $c_name, c.year = $year, c.dept = $dept
                """,
                dept=dept,
                c_code=c_code,
                c_name=c_name,
                year=year,
            )

            for unit in course.get("units", []):
                u_num = str(unit.get("number", ""))
                u_title = unit.get("title", "")
                if not u_title:
                    continue

                session.run(
                    """
                    MATCH (c:Course {code: $c_code})
                    MERGE (u:Unit {number: $u_num, title: $u_title, course_code: $c_code})
                    MERGE (c)-[:HAS_UNIT]->(u)
                    """,
                    c_code=c_code,
                    u_num=u_num,
                    u_title=u_title,
                )

                for topic in unit.get("topics", []):
                    t_name = topic.get("name")
                    if not t_name:
                        continue

                    session.run(
                        """
                        MATCH (u:Unit {number: $u_num, title: $u_title, course_code: $c_code})
                        MERGE (t:Topic {name: $t_name, course_code: $c_code})
                        MERGE (u)-[:HAS_TOPIC]->(t)
                        """,
                        u_num=u_num,
                        u_title=u_title,
                        c_code=c_code,
                        t_name=t_name,
                    )


def save_to_neo4j(neo4j_driver, course_code: str, triplets: list[dict]) -> None:
    """Writes extracted Subject-Predicate-Object triplets into Neo4j."""
    if not triplets:
        return

    with neo4j_driver.session() as session:
        session.run("MERGE (c:Course {code: $code})", code=course_code)
        for triplet in triplets:
            subject = triplet.get("subject")
            raw_predicate = (
                triplet.get("predicate", "RELATED_TO").upper().replace(" ", "_")
            )
            predicate = re.sub(r"[^A-Z0-9_]", "", raw_predicate) or "RELATED_TO"
            obj = triplet.get("object")

            if not subject or not obj or not isinstance(subject, str) or not isinstance(obj, str):
                continue

            clean_pred = re.sub(r"[^A-Za-z0-9_]", "_", str(predicate).strip()).upper() or "RELATED_TO"
            query = f"""
                MERGE (c:Course   {{code: $course_code}})
                MERGE (s:Concept  {{name: $subject}})
                MERGE (o:Concept  {{name: $obj}})
                MERGE (s)-[:{clean_pred}]->(o)
                MERGE (c)-[:HAS_CONCEPT]->(s)
            """
            try:
                session.run(query, course_code=course_code, subject=subject, obj=obj)
            except Exception as e:
                logger.warning("Failed to write triplet %s: %s", triplet, e)


async def map_pyq_structured_to_kg(
    neo4j_driver, structured_questions: list[dict], document_id: str | None = None
) -> None:
    """Connects parsed questions to Course, BloomLevel, VisualArtefact, and resolved Topic nodes."""
    if not structured_questions:
        return

    c_code_overall = structured_questions[0]["course_code"]
    course_title = structured_questions[0].get("course_title", "")
    resolved_code, _ = resolve_course_and_topics(neo4j_driver, c_code_overall, course_title)
    syllabus_context = fetch_course_topics(neo4j_driver, resolved_code, course_title)
    mapping = await semantic_map_questions_to_topics(
        resolved_code, syllabus_context, structured_questions
    )

    with neo4j_driver.session() as session:
        if resolved_code and resolved_code != c_code_overall.upper():
            session.run(
                """
                MERGE (c_pyq:Course {code: $pyq_code})
                SET c_pyq.name = coalesce(nullif(c_pyq.name, ''), $c_title),
                    c_pyq.title = coalesce(nullif(c_pyq.title, ''), $c_title)
                WITH c_pyq
                MATCH (c_syl:Course {code: $syl_code})
                MERGE (c_pyq)-[:ALIAS_OF]->(c_syl)
                """,
                pyq_code=c_code_overall.upper(),
                c_title=course_title or c_code_overall.upper(),
                syl_code=resolved_code,
            )

        for i, q in enumerate(structured_questions):
            q_id = q["id"]
            q_text = q["question_text"]
            btl = q["btl_tag"]
            marks = q["marks"]
            has_fig = q["has_figure"]
            co_tag = q["co_tag"]
            c_code = q["course_code"].upper()
            image_url = q.get("image_url", "")
            q_num = q.get("question_number", "")
            diag_summary = q.get("diagram_summary", "")
            diag_type = q.get("diagram_type", "")

            query = """
                MERGE (q:Question {id: $q_id})
                SET q.q_id = $q_id,
                    q.text = $q_text,
                    q.btl = $btl,
                    q.marks = $marks,
                    q.has_figure = $has_fig,
                    q.image_url = $image_url,
                    q.diagram_summary = $diag_summary,
                    q.diagram_type = $diag_type,
                    q.document_id = $doc_id,
                    q.course_code = $c_code,
                    q.resolved_course_code = $resolved_code,
                    q.question_number = $q_num
                MERGE (c:Course {code: $c_code})
                ON CREATE SET c.name = coalesce(nullif($c_title, ''), $c_code),
                              c.title = coalesce(nullif($c_title, ''), $c_code)
                MERGE (q)-[:BELONGS_TO]->(c)
                MERGE (c)-[:TESTED_BY]->(q)
            """
            session.run(
                query,
                q_id=q_id,
                q_text=q_text,
                btl=btl,
                marks=marks,
                has_fig=has_fig,
                c_code=c_code,
                c_title=course_title,
                resolved_code=resolved_code,
                image_url=image_url,
                diag_summary=diag_summary,
                diag_type=diag_type,
                doc_id=str(document_id) if document_id else "",
                q_num=q_num,
            )

            if btl:
                session.run(
                    """
                    MATCH (q:Question {id: $q_id})
                    MERGE (bl:BloomLevel {level: $btl})
                    MERGE (q)-[:HAS_BTL]->(bl)
                    """,
                    q_id=q_id,
                    btl=str(btl).upper(),
                )

            if image_url and image_url != "None":
                img_hash = q.get("image_hash") or hashlib.md5(image_url.encode()).hexdigest()
                session.run(
                    """
                    MATCH (q:Question {id: $q_id})
                    MERGE (va:VisualArtefact {url: $image_url})
                    SET va.question_id = $q_id,
                        va.hash = $hash,
                        va.description = $diag_summary,
                        va.diagram_type = $diag_type
                    MERGE (q)-[:BINDS_DIAGRAM]->(va)
                    """,
                    q_id=q_id,
                    image_url=image_url,
                    hash=img_hash,
                    diag_summary=diag_summary,
                    diag_type=diag_type,
                )

            topic_ids = mapping.get(i, [])
            for t_id in topic_ids:
                session.run(
                    """
                    MATCH (q:Question {id: $q_id})
                    MATCH (t:Topic {topic_id: $t_id})
                    MERGE (q)-[:TESTS]->(t)
                    MERGE (q)-[:TESTS_TOPIC]->(t)
                    """,
                    q_id=q_id,
                    t_id=t_id,
                )

            if co_tag:
                session.run(
                    """
                    MATCH (q:Question {id: $q_id})
                    MATCH (c:Course {code: $c_code})
                    MERGE (co:CourseOutcome {id: $co_id, course_code: $c_code})
                    MERGE (q)-[:MAPPED_TO_CO]->(co)
                    MERGE (co)-[:BELONGS_TO]->(c)
                    """,
                    q_id=q_id,
                    c_code=c_code,
                    co_id=co_tag,
                )


def expand_subgraph_context(neo4j_driver, query_term: str) -> dict:
    """CRAG Corrective Subgraph Expansion: dynamically expands Unit, CO, and Prerequisite nodes."""
    if not query_term or not query_term.strip():
        return {}

    clean_term = query_term.strip()
    with neo4j_driver.session() as session:
        record = session.run(
            """
            MATCH (c:Course)
            WHERE toLower(c.code) CONTAINS toLower($term)
               OR toLower(coalesce(c.name, c.title, '')) CONTAINS toLower($term)
               OR EXISTS {
                   MATCH (c)-[:HAS_UNIT]->(:Unit)-[:HAS_TOPIC]->(t:Topic)
                   WHERE toLower(t.name) CONTAINS toLower($term)
               }
            OPTIONAL MATCH (c)-[:HAS_UNIT]->(u:Unit)
            OPTIONAL MATCH (u)-[:HAS_TOPIC]->(t:Topic)
            OPTIONAL MATCH (c)-[:REQUIRES]->(p:Course)
            OPTIONAL MATCH (c)-[:HAS_OUTCOME]->(co:CourseOutcome)
            RETURN c.code AS code, coalesce(c.name, c.title, c.code) AS name,
                   c.credits AS credits,
                   collect(DISTINCT coalesce(u.title, 'Unit ' + toString(u.number)))[..5] AS units,
                   collect(DISTINCT t.name)[..12] AS sample_topics,
                   collect(DISTINCT coalesce(p.code, p.name))[..5] AS prerequisites,
                   collect(DISTINCT coalesce(co.id, '') + ': ' + coalesce(co.description, ''))[..6] AS course_outcomes
            LIMIT 1
            """,
            term=clean_term,
        ).data()

        if record:
            row = record[0]
            return {
                "course_code": row.get("code"),
                "course_name": row.get("name"),
                "credits": row.get("credits"),
                "units": [u for u in row.get("units", []) if u],
                "topics": [t for t in row.get("sample_topics", []) if t],
                "prerequisites": [p for p in row.get("prerequisites", []) if p],
                "course_outcomes": [co for co in row.get("course_outcomes", []) if co and co.strip() != "()"],
            }
    return {}


def filter_contrasting_subtopics(questions: list[dict], query_topic: str) -> list[dict]:
    """
    Prevents composite topics (e.g., 'Mesh and Node methods', 'Inductance and Capacitance',
    'Series parallel combination of R', 'Voltage Divider and Current Divider Rules',
    'Clippers and Clampers') from leaking contrasting questions into specific subtopic queries.
    """
    if not questions or not query_topic:
        return questions
    q_low = query_topic.lower()

    contrast_pairs = [
        # Mesh vs Nodal methods
        (r"\bmesh\b", r"\b(?:node|nodal)\b"),
        (r"\b(?:node|nodal)\b", r"\bmesh\b"),
        # R vs C vs L electrical components
        (r"\bcapacit", r"\b(?:resistor|resistance|resistors|inductor|inductance|inductors|leqv)\b"),
        (r"\bresist", r"\b(?:capacitor|capacitance|capacitors|inductor|inductance|inductors|leqv)\b"),
        (r"\binduct", r"\b(?:resistor|resistance|resistors|capacitor|capacitance|capacitors)\b"),
        # Voltage divider vs Current divider
        (r"\bvoltage\s+divid", r"\bcurrent\s+divid"),
        (r"\bcurrent\s+divid", r"\bvoltage\s+divid"),
        # Clippers vs Clampers
        (r"\bclipper\b", r"\bclamper\b"),
        (r"\bclamper\b", r"\bclipper\b"),
        # Half wave vs Full wave
        (r"\bhalf\s*wave\b", r"\bfull\s*wave\b"),
        (r"\bfull\s*wave\b", r"\bhalf\s*wave\b"),
        # Inverting vs Non-inverting amplifier
        (r"\binverting\b(?!\s*amplifier)", r"\bnon[- ]inverting\b"),
        (r"\bnon[- ]inverting\b", r"\binverting\b"),
    ]

    active_filters = []
    for req_pat, contr_pat in contrast_pairs:
        if re.search(req_pat, q_low) and not re.search(contr_pat, q_low):
            active_filters.append((req_pat, contr_pat))

    if not active_filters:
        return questions

    filtered = []
    for q in questions:
        q_text = (q.get("text") or "").lower()
        q_topic = (q.get("topic_name") or "").lower()
        q_full = f"{q_text} {q_topic}"

        drop = False
        for req_pat, contr_pat in active_filters:
            # 1. Must match what was requested
            if not re.search(req_pat, q_full):
                drop = True
                break
            # 2. Must not be purely contrasting
            if re.search(contr_pat, q_full) and not re.search(req_pat, q_text):
                drop = True
                break
        if not drop:
            filtered.append(q)
    return filtered


async def fetch_problems_by_topic_graph(
    neo4j_driver, topic: str, course_code: str | None = None
) -> list[dict]:
    """Strict specific-topic GraphRAG retrieval: matches specific Topic nodes & Question semantics."""
    if not topic or not topic.strip():
        return []

    raw_topic = topic.strip()
    clean_topic = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in raw_topic).strip()
    topic_words = [
        w.lower()
        for w in clean_topic.split()
        if len(w) > 2
        and w.lower() not in {
            "the", "and", "for", "with", "using", "only", "about",
            "analysis", "method", "methods", "law", "laws", "rule", "rules", "theorem",
        }
    ] or [w.lower() for w in clean_topic.split() if len(w) > 2]
    c_filter = course_code.upper().strip() if course_code else None

    with neo4j_driver.session() as session:
        # 1. Exact or all-token match on Topic nodes connected to Questions
        direct_rows = session.run(
            """
            MATCH (q:Question)-[:TESTS|TESTS_TOPIC]->(t:Topic)
            WHERE ($c_filter IS NULL OR toUpper(coalesce(q.course_code, '')) = $c_filter OR toUpper(coalesce(q.resolved_course_code, '')) = $c_filter)
              AND (
                  toLower(t.name) CONTAINS toLower($topic)
                  OR (size($topic) > 4 AND toLower($topic) CONTAINS toLower(t.name))
                  OR (size($words) > 0 AND all(w IN $words WHERE toLower(t.name) CONTAINS w))
              )
            RETURN DISTINCT q.id AS id, q.question_number AS question_number,
                   q.text AS text, q.marks AS marks, q.btl AS btl,
                   q.has_figure AS has_figure,
                   q.image_url AS image_url, q.course_code AS course_code,
                   t.name AS topic_name, 1.0 AS score
            ORDER BY (CASE WHEN q.has_figure THEN 1 ELSE 0 END) DESC, toInteger(question_number) ASC
            """,
            topic=raw_topic,
            words=topic_words,
            c_filter=c_filter,
        ).data()
        if direct_rows:
            direct_filtered = filter_contrasting_subtopics(direct_rows, raw_topic)
            if direct_filtered:
                return direct_filtered

        # 2. Semantic embedding similarity over Topic nodes that have linked Questions (catches phrasing variants)
        tested_topics = session.run(
            """
            MATCH (q:Question)-[:TESTS|TESTS_TOPIC]->(t:Topic)
            WHERE ($c_filter IS NULL OR toUpper(coalesce(q.course_code, '')) = $c_filter OR toUpper(coalesce(q.resolved_course_code, '')) = $c_filter)
            RETURN DISTINCT t.topic_id AS topic_id, t.name AS name
            """,
            c_filter=c_filter,
        ).data()

        if tested_topics:
            q_emb = await embed_text(format_query_embedding_text(_expand_topic_acronyms(raw_topic)))
            t_vecs = await embed_texts(
                [format_doc_embedding_text(t["name"], title="Topic") for t in tested_topics],
                batch_size=24,
            )
            matched_tids: list[str] = []
            for t_row, t_vec in zip(tested_topics, t_vecs):
                sim = cosine_similarity(q_emb, t_vec) if q_emb and t_vec else 0.0
                if sim >= 0.70:
                    matched_tids.append(t_row["topic_id"])

            if matched_tids:
                sem_rows = session.run(
                    """
                    MATCH (q:Question)-[:TESTS|TESTS_TOPIC]->(t:Topic)
                    WHERE t.topic_id IN $tids
                      AND ($c_filter IS NULL OR toUpper(coalesce(q.course_code, '')) = $c_filter OR toUpper(coalesce(q.resolved_course_code, '')) = $c_filter)
                    RETURN DISTINCT q.id AS id, q.question_number AS question_number,
                           q.text AS text, q.marks AS marks, q.btl AS btl,
                           q.has_figure AS has_figure,
                           q.image_url AS image_url, q.course_code AS course_code,
                           t.name AS topic_name, 0.9 AS score
                    ORDER BY (CASE WHEN q.has_figure THEN 1 ELSE 0 END) DESC, toInteger(question_number) ASC
                    """,
                    tids=matched_tids,
                    c_filter=c_filter,
                ).data()
                if sem_rows:
                    sem_filtered = filter_contrasting_subtopics(sem_rows, raw_topic)
                    if sem_filtered:
                        return sem_filtered

        # 3. Guarded Neo4j fulltext search on Topic names
        if clean_topic:
            try:
                idx_rec = session.run(
                    "SHOW FULLTEXT INDEXES YIELD name WHERE name = 'topic_name_ft' RETURN count(*) AS cnt"
                ).single()
                if idx_rec and idx_rec["cnt"] > 0:
                    ft_result = session.run(
                        """
                        CALL db.index.fulltext.queryNodes('topic_name_ft', $clean_topic)
                        YIELD node AS t, score
                        WHERE score > 1.5
                        MATCH (q:Question)-[r:TESTS|TESTS_TOPIC]->(t)
                        WHERE ($c_filter IS NULL OR toUpper(coalesce(q.course_code, '')) = $c_filter OR toUpper(coalesce(q.resolved_course_code, '')) = $c_filter)
                        RETURN DISTINCT q.id AS id, q.question_number AS question_number,
                               q.text AS text, q.marks AS marks, q.btl AS btl,
                               q.has_figure AS has_figure,
                               q.image_url AS image_url, q.course_code AS course_code,
                               t.name AS topic_name, score
                        ORDER BY (CASE WHEN q.has_figure THEN 1 ELSE 0 END) DESC, score DESC
                        LIMIT 20
                        """,
                        clean_topic=clean_topic,
                        c_filter=c_filter,
                    )
                    ft_data = ft_result.data()
                    if ft_data:
                        ft_filtered = filter_contrasting_subtopics(ft_data, raw_topic)
                        if ft_filtered:
                            return ft_filtered
            except Exception:
                pass

        return []


def fetch_all_problems_by_topic(
    neo4j_driver, topic: str, course_code: str | None = None
) -> list[dict]:
    """Retrieve questions strictly matching the requested topic tokens in text or mapped Topic."""
    stop_words = {
        "get", "me", "a", "the", "all", "questions", "question", "problems",
        "problem", "solve", "solved", "solutions", "solution", "calculate",
        "write", "analyse", "analysis", "determine", "derive", "sketch",
        "draw", "obtain", "evaluate", "on", "about", "find", "show", "list",
        "give", "related", "are", "there", "any", "is", "what", "how", "why",
        "who", "where", "can", "you", "tell", "explain", "describe", "provide",
        "in", "of", "to", "for", "with", "and", "or", "not", "this", "that", "only",
    }
    raw_tokens = [
        w.strip(".,!?-'\"")
        for w in topic.lower().split()
        if len(w.strip(".,!?-'\"")) > 2 and w.strip(".,!?-'\"") not in stop_words
    ]
    if not raw_tokens:
        cleaned = topic.strip().lower()
        if cleaned in stop_words or not cleaned:
            return []
        raw_tokens = [cleaned]

    def _stem_term(w: str) -> str:
        for suffix in ("ance", "ors", "or", "ive", "ing", "ion", "s"):
            if w.endswith(suffix) and len(w) - len(suffix) >= 4:
                return w[:-len(suffix)]
        return w

    circuit_synonyms = {
        "combination": ["combinat", "combination", "equivalent"],
        "combinat": ["combinat", "combination", "equivalent"],
        "combinations": ["combinat", "combination", "equivalent"],
        "equivalent": ["combinat", "combination", "equivalent"],
    }

    token_groups: list[list[str]] = []
    for tok in raw_tokens:
        stem = _stem_term(tok)
        if stem in circuit_synonyms:
            token_groups.append(circuit_synonyms[stem])
        elif tok in circuit_synonyms:
            token_groups.append(circuit_synonyms[tok])
        else:
            token_groups.append(list(set([stem, tok])))

    c_filter = course_code.upper().strip() if course_code else None

    with neo4j_driver.session() as session:
        result = session.run(
            """
            MATCH (q:Question)
            WHERE ($c_filter IS NULL OR toUpper(coalesce(q.course_code, '')) = $c_filter OR toUpper(coalesce(q.resolved_course_code, '')) = $c_filter)
            OPTIONAL MATCH (q)-[:TESTS|TESTS_TOPIC]->(t:Topic)
            WITH q,
                 coalesce(q.course_code, '') AS code,
                 collect(DISTINCT coalesce(t.name, '')) AS tnames,
                 toLower(coalesce(q.text, '')) AS qtext
            WITH q, code, tnames, qtext,
                 [tn IN tnames | toLower(tn)] AS tnames_lower
            WITH q, code, tnames, qtext, tnames_lower,
                 (CASE WHEN toLower(code) = toLower($topic) THEN 10
                       WHEN qtext CONTAINS toLower($topic) THEN 8
                       WHEN any(tn IN tnames_lower WHERE tn CONTAINS toLower($topic)) THEN 7
                       WHEN size($token_groups) > 0 AND all(group IN $token_groups WHERE any(alt IN group WHERE qtext CONTAINS alt OR any(tn IN tnames_lower WHERE tn CONTAINS alt))) THEN 6
                       ELSE 0 END) AS score
            WHERE score > 0
            RETURN DISTINCT q.id AS id,
                   q.question_number AS question_number,
                   q.question_number AS q_num,
                   q.text AS text,
                   q.text AS q_text,
                   q.marks AS marks,
                   q.btl AS btl,
                   q.has_figure AS has_figure,
                   q.image_url AS image_url,
                   q.diagram_summary AS diagram_summary,
                   code AS course_code,
                   head([tn IN tnames WHERE tn <> '']) AS topic_name,
                   score
            ORDER BY score DESC, (CASE WHEN q.has_figure THEN 1 ELSE 0 END) DESC
            LIMIT 20
            """,
            topic=" ".join(raw_tokens),
            token_groups=token_groups,
            c_filter=c_filter,
        )
        return filter_contrasting_subtopics(result.data(), topic)


def _resolve_course_fuzzy(session, phrase: str, words: list[str], limit: int = 10) -> list[dict]:
    """Resolves a course by fuzzy title similarity when exact substring matching finds nothing."""
    if not phrase and not words:
        return []
    all_courses = session.run("""
        MATCH (c:Course)
        RETURN c.code AS code,
               coalesce(nullif(c.name, ''), nullif(c.title, ''), c.code) AS name
    """).data()
    query_str = phrase or " ".join(words)
    scored: list[tuple[float, dict]] = []
    for row in all_courses:
        cname = (row.get("name") or "").lower()
        if not cname:
            continue
        seq_ratio = difflib.SequenceMatcher(None, query_str, cname).ratio()
        token_scores = [
            max((difflib.SequenceMatcher(None, w, cw).ratio() for cw in cname.split()), default=0.0)
            for w in words
        ]
        token_avg = sum(token_scores) / len(token_scores) if token_scores else 0.0
        best = max(seq_ratio, token_avg)
        if best >= 0.72:
            scored.append((best, row))
    scored.sort(key=lambda item: (-item[0], len(item[1].get("name") or "")))
    return [row for _, row in scored[:limit]]


def _extract_requested_aspects(q_lower: str) -> set[str]:
    """Identifies which specific course_detail fields the student asked for."""
    if "course details" in q_lower:
        return set()
    aspects: set[str] = set()
    if any(k in q_lower for k in ["course code", "subject code", "code of", "code for"]):
        aspects.add("course_code")
    if any(k in q_lower for k in ["which semester", "what semester", "which sem", "what sem", "taught in"]):
        aspects.add("semester")

    has_internal = any(k in q_lower for k in ["internal marks", "internal component", "continuous assessment", "internal assessment", "ca marks", "internals", "internal"])
    has_external = any(k in q_lower for k in ["end semester marks", "end sem marks", "endsem marks", "external marks", "external component", "external eval", "externals", "external", "end sem", "end-sem", "endsem", "end semester"])
    has_general_eval = any(k in q_lower for k in ["evaluation pattern", "evaluation", "assessment pattern", "assessment", "marks split", "pass mark", "pass marks"])

    if has_internal and has_external:
        aspects.add("internal_eval")
        aspects.add("endsem_eval")
    elif has_internal:
        aspects.add("internal_eval")
    elif has_external:
        aspects.add("endsem_eval")
    elif has_general_eval or "marks" in q_lower:
        aspects.add("evaluation")

    if any(k in q_lower for k in ["textbook", "textbooks", "texbook", "texbooks"]):
        aspects.add("textbooks")
    if any(k in q_lower for k in ["reference", "references", "ref", "refs"]):
        aspects.add("references")
    if any(k in q_lower for k in ["credit", "credits", "ltpc", "l-t-p-c", "l-t-p", "ltp", "contact hours", "lecture hours"]):
        aspects.add("credits")
    if any(k in q_lower for k in ["lab experiment", "lab experiments", "list of experiments", "lab"]):
        aspects.add("lab_experiments")
    if any(k in q_lower for k in ["course outcome", "course outcomes", "outcomes", "outcome"]) or re.search(r"\bcos?\b", q_lower):
        aspects.add("outcomes")
    if any(k in q_lower for k in ["objective", "objectives"]):
        aspects.add("objectives")
    if any(k in q_lower for k in ["prerequisite", "prerequisites", "prereq", "prereqs", "prerequite"]):
        aspects.add("prerequisites")
    return aspects


def _filter_course_detail_aspects(detail_rows: list[dict], aspects: set[str]) -> list[dict]:
    """Keeps only the requested aspects in course_detail rows when specific fields were asked."""
    if not aspects:
        return detail_rows
    filtered = []
    for row in detail_rows:
        comps = row.get("assessment_components") or []
        eval_pat = row.get("evaluation_pattern")
        internal_comps = [c for c in comps if not any(x in c.lower() for x in ["end semester", "end-sem", "end sem"])]
        endsem_comps = [c for c in comps if any(x in c.lower() for x in ["end semester", "end-sem", "end sem"])]

        show_eval = "evaluation" in aspects
        show_internal = "internal_eval" in aspects
        show_endsem = "endsem_eval" in aspects

        selected_comps = []
        if show_internal and show_endsem:
            parts = []
            if row.get("eval_internal"):
                parts.append(f"Internal (CA): {row.get('eval_internal')}% ({', '.join(internal_comps)})" if internal_comps else f"Internal (CA): {row.get('eval_internal')}%")
            if row.get("eval_endsem"):
                parts.append(f"End-Semester: {row.get('eval_endsem')}% ({', '.join(endsem_comps)})" if endsem_comps else f"End-Semester: {row.get('eval_endsem')}%")
            eval_display = " | ".join(parts) if parts else eval_pat
            selected_comps = comps
        elif show_internal:
            selected_comps = internal_comps
            eval_display = f"Internal (CA): {row.get('eval_internal')}% ({', '.join(internal_comps)})" if internal_comps else (f"Internal (CA): {row.get('eval_internal')}%" if row.get('eval_internal') else None)
        elif show_endsem:
            selected_comps = endsem_comps
            eval_display = f"End-Semester: {row.get('eval_endsem')}% ({', '.join(endsem_comps)})" if endsem_comps else (f"End-Semester: {row.get('eval_endsem')}%" if row.get('eval_endsem') else None)
        elif show_eval:
            selected_comps = comps
            eval_display = eval_pat
        else:
            eval_display = None

        out = {
            "course_code": row.get("course_code"),
            "course_name": row.get("course_name"),
            "semester": row.get("semester") if "semester" in aspects else None,
            "credits": row.get("credits") if "credits" in aspects else None,
            "L": row.get("L") if "credits" in aspects else None,
            "T": row.get("T") if "credits" in aspects else None,
            "P": row.get("P") if "credits" in aspects else None,
            "evaluation_pattern": eval_display,
            "eval_internal": row.get("eval_internal") if (show_eval or show_internal) else None,
            "eval_endsem": row.get("eval_endsem") if (show_eval or show_endsem) else None,
            "assessment_components": selected_comps,
            "lab_experiments": row.get("lab_experiments") if "lab_experiments" in aspects else [],
            "has_no_lab_experiments": bool("lab_experiments" in aspects and not (row.get("lab_experiments") or [])),
            "textbooks": row.get("textbooks") if "textbooks" in aspects else [],
            "references": row.get("references") if "references" in aspects else [],
            "outcomes": row.get("outcomes") if "outcomes" in aspects else [],
            "objectives": row.get("objectives") if "objectives" in aspects else [],
            "prerequisites": row.get("prerequisites") if "prerequisites" in aspects else [],
        }
        filtered.append(out)
    return filtered


def execute_graph_prereq_query(neo4j_driver, question: str) -> list:
    """Extracts course code/phrase and traverses multi-hop prerequisites in Neo4j."""
    q_lower = normalize_query_typos(question.lower())
    code_match = re.search(r'\b\d{0,2}[a-z]{3}\d{3}\b', q_lower)
    course_code = code_match.group(0).upper() if code_match else None

    STOP = {
        "what", "all", "are", "the", "prerequisite", "prerequisites", "prereq", "prereqs",
        "for", "of", "course", "courses", "subject", "subjects", "should", "i", "have", "done",
        "before", "choosing", "taking", "know", "needed", "need", "to", "study", "enroll", "in",
        "is", "a", "an", "tell", "me", "which", "do", "get", "show", "list", "require", "requires",
        "pre-requisite", "pre-requisites", "take", "required",
    }
    clean_words = [
        w.strip(".,!?'\"") for w in q_lower.split()
        if w.strip(".,!?'\"") and w.strip(".,!?'\"") not in STOP
    ]
    phrase = " ".join(clean_words).strip()

    def _run_prereq_cypher(session, code_val: str | None, phrase_val: str, words_val: list[str]) -> list:
        return session.run(
            """
            MATCH (target:Course)
            WHERE ($code IS NOT NULL AND toUpper(target.code) CONTAINS $code)
               OR ($phrase <> '' AND toLower(coalesce(target.name, target.title, '')) CONTAINS toLower($phrase))
               OR ($phrase <> '' AND toLower(target.code) CONTAINS toLower($phrase))
               OR (size($words) > 0 AND all(w IN $words WHERE toLower(coalesce(target.name, target.title, '')) CONTAINS w OR toLower(target.code) CONTAINS w))
            OPTIONAL MATCH path = (target)-[:REQUIRES|REQUIRES_PREREQUISITE*1..5]->(prereq:Course)
            WHERE prereq IS NULL OR target <> prereq
            WITH target,
                 [x IN collect(DISTINCT CASE
                     WHEN prereq IS NOT NULL AND nullif(prereq.name, '') IS NOT NULL THEN prereq.code + ' - ' + prereq.name
                     WHEN prereq IS NOT NULL AND nullif(prereq.title, '') IS NOT NULL THEN prereq.code + ' - ' + prereq.title
                     WHEN prereq IS NOT NULL THEN prereq.code
                     ELSE NULL
                 END) WHERE x IS NOT NULL] AS course_prereqs,
                 coalesce(target.prerequisite_notes, []) AS know_prereqs
            WITH target, course_prereqs + know_prereqs AS all_prerequisites
            WHERE size(all_prerequisites) > 0
            RETURN coalesce(nullif(target.name, ''), nullif(target.title, ''), target.code) AS course,
                   target.code AS course_code,
                   all_prerequisites
            LIMIT 10
            """,
            code=code_val,
            phrase=phrase_val,
            words=words_val,
        ).data()

    with neo4j_driver.session() as session:
        records = _run_prereq_cypher(session, course_code, phrase, clean_words)
        if not records and not course_code and (phrase or clean_words):
            fuzzy = _resolve_course_fuzzy(session, phrase, clean_words, limit=3)
            if fuzzy:
                records = _run_prereq_cypher(session, fuzzy[0]["code"], "", [])

    return records


def execute_graph_co_query(neo4j_driver, question: str) -> list:
    """Queries questions mapped to Course Outcome (CO) or Bloom's Taxonomy Level (BTL)."""
    q_lower = question.lower()
    co_match = re.search(r"co\d+", q_lower)
    co_id = co_match.group(0).upper() if co_match else None

    btl_match = re.search(r"\b(l[1-6]|btl[1-6]|bt\s*[-–]?[1-6]|level\s*[1-6]|k[1-6])\b", q_lower)
    btl_digit = None
    if btl_match:
        m = re.search(r"[1-6]", btl_match.group(0))
        if m:
            btl_digit = m.group(0)

    course_keywords = []
    course_match = re.search(r"(?:of|from|in|for)\s+([a-zA-Z0-9\s]+?)(?:\s+course|\s+subject|\?|$)", q_lower)
    if course_match:
        c_phrase = course_match.group(1).strip()
        stop_c = {"the", "a", "an", "all", "me", "get", "show", "questions", "problems", "pyqs"}
        course_keywords = [w for w in c_phrase.split() if w not in stop_c and len(w) > 1]

    with neo4j_driver.session() as session:
        records = session.run(
            """
            MATCH (q:Question)
            OPTIONAL MATCH (q)-[:BELONGS_TO]->(c:Course)
            OPTIONAL MATCH (c_t:Course)-[:TESTED_BY]->(q)
            WITH q, coalesce(c, c_t) AS c
            WHERE ($co_id IS NULL OR EXISTS {
                MATCH (q)-[:MAPPED_TO_CO]->(co:CourseOutcome)
                WHERE toUpper(co.id) = $co_id
            })
            AND ($btl_digit IS NULL OR (
                toUpper(coalesce(q.btl, '')) CONTAINS $btl_digit
                OR EXISTS { MATCH (q)-[:HAS_BTL]->(bl:BloomLevel) WHERE toUpper(bl.level) CONTAINS $btl_digit }
            ))
            OPTIONAL MATCH (q)-[:MAPPED_TO_CO]->(co:CourseOutcome)
            WITH q, c, co,
                 CASE WHEN size($course_words) > 0 AND any(w IN $course_words WHERE toLower(coalesce(c.name, c.title, c.code, '')) CONTAINS w) THEN 1 ELSE 0 END AS course_match
            RETURN q.id AS id, coalesce(q.q_id, q.id) AS q_id, q.text AS question, q.btl AS btl, q.marks AS marks, 
                   q.has_figure AS has_figure, q.image_url AS image_url,
                   coalesce(c.code, q.course_code, co.course_code, '') AS course_code,
                   coalesce(co.id, '') AS co_id
            ORDER BY course_match DESC
            LIMIT 25
            """,
            co_id=co_id,
            btl_digit=btl_digit,
            course_words=course_keywords,
        ).data()

    return records


def _roman_sem_to_int(token: str) -> int | None:
    token = token.strip().upper()
    if token.isdigit():
        return int(token)
    roman_map = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8}
    return roman_map.get(token)


def execute_graph_syllabus_query(neo4j_driver, question: str) -> dict:
    """Answers curriculum inquiries (PO/PEO/PSO, semesters, electives, course details, syllabus) deterministically."""
    q_lower = normalize_query_typos(question.lower())

    # 1. Program Outcomes, PEOs, PSOs, Framework, Abbreviations, and Academic Rules
    if any(k in q_lower for k in [
        "program outcome", "program outcomes", "program educational objective",
        "program specific outcome", "peo", "peos", "pso", "psos",
        "nptel", "live-in-lab", "live in lab", "credit limit", "exemption",
        "general framework", "credit distribution", "total credits", "aicte",
        "abbreviation", "abbreviations", "stand for", "full form",
    ]) and not re.search(r"\b\d{2}[a-z]{3}\d{3}\b", q_lower):
        with neo4j_driver.session() as session:
            # Check if this is a semester-specific total credits query (e.g. "total credits in sem 3")
            sem_q_match = re.search(
                r"\b(?:sem(?:ester)?\s*(viii|vii|vi|iv|v|iii|ii|i|\d+)|(\d+)(?:st|nd|rd|th)?\s*sem(?:ester)?)\b",
                q_lower,
            )
            if any(k in q_lower for k in ["credit", "credits"]) and sem_q_match:
                s_raw = sem_q_match.group(1) or sem_q_match.group(2)
                s_num = _roman_sem_to_int(s_raw)
                if s_num is not None:
                    sem_meta = session.run(
                        "MATCH (sem:Semester {number: $s_num}) RETURN sem.total_credits AS tc LIMIT 1",
                        s_num=s_num,
                    ).single()
                    tc_val = sem_meta["tc"] if sem_meta and sem_meta["tc"] is not None else "N/A"
                    return {
                        "match_type": "curriculum_info",
                        "title": f"Semester {s_num} Total Credits",
                        "items": [
                            {"id": f"Sem {s_num}", "description": f"Total credits offered in Semester {s_num}: {tc_val} credits."}
                        ],
                    }

            if any(k in q_lower for k in ["general framework", "credit distribution", "total credits", "aicte"]):
                gi = session.run("MATCH (g:GeneralInformation) RETURN g.framework AS fw LIMIT 1").single()
                if gi and gi["fw"]:
                    try:
                        fw_rows = json.loads(gi["fw"])
                        # If user specifically asked for "total credits" without "framework" or "distribution", return only the total row
                        if "total credits" in q_lower and not any(k in q_lower for k in ["framework", "distribution", "category"]):
                            tot_row = next((r for r in fw_rows if r.get("category") == "TOTAL"), None)
                            if tot_row:
                                return {
                                    "match_type": "curriculum_info",
                                    "title": "Total Program Credits",
                                    "items": [
                                        {
                                            "id": "TOTAL",
                                            "description": f"Total Program Credits: {tot_row.get('credits')} credits (AICTE Recommended: {tot_row.get('aicte_recommended')}).",
                                        }
                                    ],
                                }

                        items = [
                            {
                                "id": r["category"],
                                "description": f"{r['description']} — Courses: {r.get('courses') or '-'}, Credits: {r.get('credits')} (AICTE Rec: {r.get('aicte_recommended')})",
                            }
                            for r in fw_rows
                        ]
                        if items:
                            return {"match_type": "curriculum_info", "title": "Curriculum General Framework", "items": items}
                    except Exception:
                        pass
            elif any(k in q_lower for k in ["abbreviation", "abbreviations", "stand for", "full form"]):
                gi = session.run("MATCH (g:GeneralInformation) RETURN g.abbreviations AS abbr LIMIT 1").single()
                if gi and gi["abbr"]:
                    try:
                        abbr_map = json.loads(gi["abbr"])
                        items = [{"id": k, "description": v} for k, v in abbr_map.items()]
                        if items:
                            return {"match_type": "curriculum_info", "title": "Curriculum Abbreviations", "items": items}
                    except Exception:
                        pass
            if any(k in q_lower for k in ["nptel", "live-in", "live in lab", "credit limit", "exemption", "free elective"]):
                rules = session.run(
                    "MATCH (r:CurriculumRule) RETURN 'Rule' AS id, r.text AS description LIMIT 12"
                ).data()
                if rules:
                    if "nptel" in q_lower and "live" not in q_lower:
                        filtered = [r for r in rules if "nptel" in (r.get("description") or "").lower()]
                        if filtered:
                            return {"match_type": "curriculum_info", "title": "NPTEL Elective Rule", "items": filtered}
                    if "live-in" in q_lower or "live in lab" in q_lower:
                        dedicated = [
                            r for r in rules
                            if "live-in-labs" in (r.get("description") or "").lower()
                            and "nptel" not in (r.get("description") or "").lower()
                        ]
                        filtered = dedicated or [r for r in rules if "live-in" in (r.get("description") or "").lower()]
                        if filtered:
                            return {"match_type": "curriculum_info", "title": "Live-in-Labs Exemption Rule", "items": filtered}
                    if "free elective" in q_lower:
                        filtered = [r for r in rules if "free electives" in (r.get("description") or "").lower()]
                        if filtered:
                            return {"match_type": "curriculum_info", "title": "Free Electives Rule", "items": filtered}
                    return {"match_type": "curriculum_info", "title": "Academic & Curriculum Rules", "items": rules}
            elif any(k in q_lower for k in ["program specific outcome", "pso", "psos"]):
                psos = session.run(
                    "MATCH (p:ProgramSpecificOutcome) RETURN p.id AS id, p.description AS description ORDER BY p.id"
                ).data()
                if psos:
                    return {"match_type": "curriculum_info", "title": "Program Specific Outcomes (PSOs)", "items": psos}
            elif any(k in q_lower for k in ["program educational objective", "peo", "peos"]):
                peos = session.run(
                    "MATCH (p:ProgramEducationalObjective) RETURN p.id AS id, p.description AS description ORDER BY p.id"
                ).data()
                if peos:
                    return {"match_type": "curriculum_info", "title": "Program Educational Objectives (PEOs)", "items": peos}
            else:
                pos = session.run(
                    "MATCH (p:ProgramOutcome) RETURN p.id AS id, p.description AS description ORDER BY toInteger(replace(p.id, 'PO', ''))"
                ).data()
                if pos:
                    return {"match_type": "curriculum_info", "title": "Program Outcomes (POs)", "items": pos}

    # 2. Semester query (supports digits "4", "4th" and Roman numerals "IV")
    sem_match = re.search(
        r"\b(?:sem(?:ester)?\s*(viii|vii|vi|iv|v|iii|ii|i|\d+)|(\d+)(?:st|nd|rd|th)?\s*sem(?:ester)?)\b",
        q_lower,
    )
    if sem_match:
        raw_sem = sem_match.group(1) or sem_match.group(2)
        sem_num = _roman_sem_to_int(raw_sem)
        if sem_num is not None:
            try:
                with neo4j_driver.session() as session:
                    sem_meta = session.run(
                        "MATCH (sem:Semester {number: $sem_num}) RETURN sem.total_credits AS tc LIMIT 1",
                        sem_num=sem_num,
                    ).single()
                    sem_res = session.run("""
                        MATCH (c:Course)
                        WHERE c.semester = $sem_num
                           OR EXISTS { MATCH (sem:Semester {number: $sem_num})-[:OFFERS]->(c) }
                        RETURN c.code AS course_code,
                               coalesce(nullif(c.title, ''), nullif(c.name, ''), c.code) AS course_name,
                               c.credits AS credits, c.category AS category,
                               c.L AS L, c.T AS T, c.P AS P,
                               c.evaluation_pattern_ratio AS eval_pattern
                        ORDER BY c.code ASC
                    """, sem_num=sem_num).data()

                    if sem_res:
                        return {
                            "match_type": "semester_courses",
                            "semester": sem_num,
                            "total_credits": sem_meta["tc"] if sem_meta else None,
                            "courses": sem_res,
                        }
            except Exception as e:
                logger.warning("Semester query notice: %s", e)

    # 3. Electives query
    if any(k in q_lower for k in ["elective", "electives", "basket", "baskets", "stream"]):
        asks_baskets_only = any(k in q_lower for k in ["basket", "baskets", "headings", "streams"])
        is_professional_basket_q = asks_baskets_only and "professional" in q_lower
        is_free_basket_q = asks_baskets_only and "free" in q_lower

        stream_stop = {
            "what", "all", "are", "is", "the", "electives", "elective", "do", "i", "we",
            "have", "under", "stream", "streams", "courses", "subjects", "in", "for", "of",
            "me", "show", "list", "tell", "give", "basket", "baskets", "offered", "available",
            "there", "any", "please", "can", "you", "headings", "categories", "types",
            "professional", "free",
        }
        stream_words = [
            w.strip(".,!?'\"")
            for w in q_lower.split()
            if w.strip(".,!?'\"") and w.strip(".,!?'\"") not in stream_stop
        ]
        stream_hint = " ".join(stream_words).strip()
        alias_map = {
            "ai": "artificial intelligence",
            "a.i.": "artificial intelligence",
            "ml": "artificial intelligence",
            "ds": "data science",
            "cn": "computer networks",
            "cv": "computer vision",
            "cps": "cyber physical",
            "cyber": "cyber security",
            "cybersecurity": "cyber security",
        }
        stream_hint = alias_map.get(stream_hint.lower(), stream_hint)
        with neo4j_driver.session() as session:
            if not stream_hint or asks_baskets_only:
                baskets = session.run("""
                    MATCH (eb:ElectiveBasket)-[:CONTAINS]->(c:Course)
                    RETURN eb.name AS basket_name, count(DISTINCT c) AS course_count
                    ORDER BY basket_name ASC
                """).data()
                if baskets:
                    if is_free_basket_q:
                        baskets = [
                            b for b in baskets
                            if any(k in b["basket_name"].lower() for k in ["free electives", "amrita value"])
                        ]
                    elif is_professional_basket_q:
                        baskets = [
                            b for b in baskets
                            if not any(k in b["basket_name"].lower() for k in ["free electives", "amrita value"])
                        ]
                    title_name = "Professional Elective Baskets" if is_professional_basket_q else ("Free Elective Baskets" if is_free_basket_q else "Available Elective Streams")
                    return {"match_type": "elective_baskets", "title": title_name, "baskets": baskets}

            eb_res = session.run("""
                OPTIONAL MATCH (ec:electiveCourse)-[:HAS_BASKET]->(eb:ElectiveBasket)
                WITH eb, collect(DISTINCT ec.name) AS slot_names
                MATCH (eb)-[:CONTAINS]->(c:Course)
                WHERE $hint = ''
                   OR toLower(eb.name) CONTAINS toLower($hint)
                   OR any(sn IN slot_names WHERE toLower(sn) CONTAINS toLower($hint))
                   OR toLower(coalesce(c.name, c.title, '')) CONTAINS toLower($hint)
                RETURN eb.name AS basket_name,
                       c.code AS course_code,
                       coalesce(nullif(c.title, ''), nullif(c.name, ''), c.code) AS course_name,
                       c.credits AS credits
                ORDER BY basket_name, course_code
                LIMIT 60
            """, hint=stream_hint).data()

            if eb_res:
                return {"match_type": "elective_courses", "stream": stream_hint or "All Electives", "courses": eb_res}

    # 4. Identify target Course(s) cleanly by code or name/words
    code_match = re.search(r"\b(\d{2}[a-z]{3}\d{3})\b", q_lower)
    explicit_code = code_match.group(1).upper() if code_match else None

    unit_filter_m = re.search(r"\bunit\s*[-–:]?\s*(i{1,3}v?|v|\d+)\b", q_lower)
    target_unit_num = _roman_sem_to_int(unit_filter_m.group(1)) if unit_filter_m else None

    requested_aspects = _extract_requested_aspects(q_lower)
    asks_detail_aspect = bool(requested_aspects or "course details" in q_lower) and not (
        "syllabus" in q_lower or "topics" in q_lower or target_unit_num is not None
    )

    STOP = {
        "get", "me", "the", "syllabus", "curriculum", "for", "course", "subject",
        "topics", "topic", "units", "unit", "what", "is", "are", "does", "do",
        "have", "how", "many", "much", "give", "show", "list", "provide", "tell", "all",
        "of", "about", "in", "on", "to", "please", "and", "or", "a", "an", "will",
        "be", "covered", "pattern", "evaluation", "assessment", "marks", "split", "internal",
        "internals", "external", "externals", "end", "sem", "semester", "endsem", "end-sem",
        "textbook", "textbooks", "book", "books", "references", "reference", "code",
        "credit", "credits", "ltpc", "l-t-p-c", "l-t-p", "ltp", "contact", "hours",
        "lecture", "outcome", "outcomes", "cos", "co", "objective", "objectives",
        "details", "detail", "under", "from", "with", "its", "it", "which",
        "taught", "lab", "experiment", "experiments", "pass", "mark", "ca", "cat", "cal",
    }
    words = [
        w.strip(".,!?'\"()")
        for w in q_lower.split()
        if w.strip(".,!?'\"()")
        and w.strip(".,!?'\"()") not in STOP
        and not re.fullmatch(r"\d+", w.strip(".,!?'\"()"))
    ]
    phrase = " ".join(words).strip()

    with neo4j_driver.session() as session:
        matched_courses = []
        if explicit_code:
            matched_courses = session.run("""
                MATCH (c:Course {code: $code})
                RETURN c.code AS code, coalesce(nullif(c.name, ''), nullif(c.title, ''), c.code) AS name
                LIMIT 5
            """, code=explicit_code).data()
        elif phrase:
            matched_courses = session.run("""
                MATCH (c:Course)
                WITH c, toLower(coalesce(c.name, '') + ' ' + coalesce(c.title, '')) AS cname
                WHERE cname CONTAINS toLower($phrase)
                   OR (size($words) > 0 AND all(w IN $words WHERE cname CONTAINS w))
                RETURN c.code AS code,
                       coalesce(nullif(c.name, ''), nullif(c.title, ''), c.code) AS name,
                       size(coalesce(c.name, c.title, '')) AS nlen
                ORDER BY nlen ASC
                LIMIT 10
            """, phrase=phrase, words=words).data()
        if not matched_courses and not explicit_code and (phrase or words):
            matched_courses = _resolve_course_fuzzy(session, phrase, words, limit=5)

        if not matched_courses:
            return {"match_type": "none", "records": []}

        # If multiple ambiguous courses match by keyword (and user did not give a code),
        # check if one is an exact title match, else present selectable chips
        if len(matched_courses) > 1 and not explicit_code:
            def _norm_title(s: str) -> str:
                return re.sub(r"\s+", " ", re.sub(r"\b(?:and|&|for|in|of|the|to)\b", " ", s.lower())).strip()

            norm_phrase = _norm_title(phrase)
            exact_name_hit = [
                mc for mc in matched_courses if _norm_title(mc["name"]) == norm_phrase
            ]
            if exact_name_hit:
                matched_courses = sorted(
                    exact_name_hit, key=lambda x: (0 if x["code"].startswith("23") else 1, x["code"])
                )[:1]
            else:
                return {
                    "match_type": "similar_list",
                    "original_question": question,
                    "courses": [
                        {"course_code": mc["code"], "course_name": mc["name"]}
                        for mc in matched_courses
                    ],
                }

        target_code = matched_courses[0]["code"]

        if asks_detail_aspect:
            detail_rows = session.run("""
                MATCH (c:Course {code: $code})
                OPTIONAL MATCH (c)-[:HAS_TEXTBOOK|USES_TEXTBOOK]->(tb:Textbook)
                OPTIONAL MATCH (c)-[:HAS_REFERENCE|USES_REFERENCE]->(rf:Reference)
                OPTIONAL MATCH (c)-[:HAS_OUTCOME]->(co:CourseOutcome)
                OPTIONAL MATCH (c)-[:HAS_OBJECTIVE]->(obj:CourseObjective)
                OPTIONAL MATCH (c)-[:REQUIRES|REQUIRES_PREREQUISITE]->(pre:Course)
                OPTIONAL MATCH (c)-[ar:ASSESSED_BY]->(ac:AssessmentComponent)
                WITH c, tb, rf, co, obj, pre, ar, ac
                ORDER BY coalesce(tb.order, 0), coalesce(rf.order, 0), co.id, coalesce(obj.order, 0)
                WITH c.code AS course_code,
                     coalesce(nullif(c.name, ''), nullif(c.title, ''), c.code) AS course_name,
                     c.credits AS credits, c.L AS L, c.T AS T, c.P AS P,
                     c.semester AS semester,
                     c.evaluation_pattern_ratio AS evaluation_pattern,
                     c.eval_internal AS eval_internal,
                     c.eval_endsem AS eval_endsem,
                     coalesce(c.lab_experiments, []) AS lab_experiments,
                     coalesce(c.prerequisite_notes, []) AS pnotes,
                     [x IN collect(DISTINCT CASE WHEN ac IS NOT NULL THEN ac.name + ': ' + toString(ar.marks) ELSE NULL END) WHERE x IS NOT NULL] AS assessment_components,
                     [x IN collect(DISTINCT coalesce(tb.title, tb.text)) WHERE x IS NOT NULL] AS textbooks,
                     [x IN collect(DISTINCT coalesce(rf.title, rf.text)) WHERE x IS NOT NULL] AS references,
                     [x IN collect(DISTINCT CASE WHEN co IS NOT NULL THEN co.id + ': ' + co.description ELSE NULL END) WHERE x IS NOT NULL] AS outcomes,
                     [x IN collect(DISTINCT obj.description) WHERE x IS NOT NULL] AS objectives,
                     [x IN collect(DISTINCT coalesce(nullif(pre.name, ''), pre.code)) WHERE x IS NOT NULL] AS pre_codes
                RETURN course_code, course_name, credits, L, T, P, semester,
                       evaluation_pattern, eval_internal, eval_endsem,
                       lab_experiments, assessment_components, textbooks, references,
                       outcomes, objectives, pre_codes + pnotes AS prerequisites
            """, code=target_code).data()
            if detail_rows:
                for row in detail_rows:
                    comps = row.get("assessment_components") or []
                    if comps and row.get("evaluation_pattern"):
                        row["evaluation_pattern"] = f"{row['evaluation_pattern']} ({', '.join(comps)})"
                return {
                    "match_type": "course_detail",
                    "courses": _filter_course_detail_aspects(detail_rows, requested_aspects),
                }

        # Fetch Units & Topics (with optional unit number filter)
        exact = session.run("""
            MATCH (c:Course {code: $code})-[:HAS_UNIT]->(u:Unit)
            WHERE $unit_num IS NULL OR toInteger(u.number) = $unit_num
            OPTIONAL MATCH (u)-[:HAS_TOPIC]->(t:Topic)
            WITH c, u, t ORDER BY coalesce(t.order, toInteger(last(split(t.topic_id, '_t'))), 0) ASC
            WITH c, u.number AS unit_num,
                 coalesce(u.title, 'Unit ' + toString(u.number)) AS unit_title,
                 [x IN collect(DISTINCT t.name) WHERE x IS NOT NULL] AS topics
            RETURN c.code AS course_code,
                   coalesce(nullif(c.name, ''), nullif(c.title, ''), c.code) AS course_name,
                   c.credits AS credits, c.L AS L, c.T AS T, c.P AS P,
                   c.evaluation_pattern_ratio AS evaluation_pattern,
                   toInteger(unit_num) AS unit_number, unit_title, topics
            ORDER BY unit_number ASC
        """, code=target_code, unit_num=target_unit_num).data()
        if exact:
            extra_details = None
            if requested_aspects:
                detail_rows = session.run("""
                    MATCH (c:Course {code: $code})
                    OPTIONAL MATCH (c)-[:HAS_TEXTBOOK|USES_TEXTBOOK]->(tb:Textbook)
                    OPTIONAL MATCH (c)-[:HAS_REFERENCE|USES_REFERENCE]->(rf:Reference)
                    OPTIONAL MATCH (c)-[:HAS_OUTCOME]->(co:CourseOutcome)
                    OPTIONAL MATCH (c)-[:HAS_OBJECTIVE]->(obj:CourseObjective)
                    OPTIONAL MATCH (c)-[:REQUIRES|REQUIRES_PREREQUISITE]->(pre:Course)
                    OPTIONAL MATCH (c)-[ar:ASSESSED_BY]->(ac:AssessmentComponent)
                    WITH c, tb, rf, co, obj, pre, ar, ac
                    ORDER BY coalesce(tb.order, 0), coalesce(rf.order, 0), co.id, coalesce(obj.order, 0)
                    WITH c.code AS course_code,
                         coalesce(nullif(c.name, ''), nullif(c.title, ''), c.code) AS course_name,
                         c.credits AS credits, c.L AS L, c.T AS T, c.P AS P,
                         c.semester AS semester,
                         c.evaluation_pattern_ratio AS evaluation_pattern,
                         c.eval_internal AS eval_internal,
                         c.eval_endsem AS eval_endsem,
                         coalesce(c.lab_experiments, []) AS lab_experiments,
                         coalesce(c.prerequisite_notes, []) AS pnotes,
                         [x IN collect(DISTINCT CASE WHEN ac IS NOT NULL THEN ac.name + ': ' + toString(ar.marks) ELSE NULL END) WHERE x IS NOT NULL] AS assessment_components,
                         [x IN collect(DISTINCT coalesce(tb.title, tb.text)) WHERE x IS NOT NULL] AS textbooks,
                         [x IN collect(DISTINCT coalesce(rf.title, rf.text)) WHERE x IS NOT NULL] AS references,
                         [x IN collect(DISTINCT CASE WHEN co IS NOT NULL THEN co.id + ': ' + co.description ELSE NULL END) WHERE x IS NOT NULL] AS outcomes,
                         [x IN collect(DISTINCT obj.description) WHERE x IS NOT NULL] AS objectives,
                         [x IN collect(DISTINCT coalesce(nullif(pre.name, ''), pre.code)) WHERE x IS NOT NULL] AS pre_codes
                    RETURN course_code, course_name, credits, L, T, P, semester,
                           evaluation_pattern, eval_internal, eval_endsem,
                           lab_experiments, assessment_components, textbooks, references,
                           outcomes, objectives, pre_codes + pnotes AS prerequisites
                """, code=target_code).data()
                if detail_rows:
                    for row in detail_rows:
                        comps = row.get("assessment_components") or []
                        if comps and row.get("evaluation_pattern"):
                            row["evaluation_pattern"] = f"{row['evaluation_pattern']} ({', '.join(comps)})"
                    extra_details = _filter_course_detail_aspects(detail_rows, requested_aspects)

            res_exact = {"match_type": "exact", "records": exact}
            if extra_details:
                res_exact["extra_details"] = extra_details[0]
            return res_exact

        # Fallback for Lab / Project / AVP courses with 0 syllabus units -> return course_detail
        fallback_detail = session.run("""
            MATCH (c:Course {code: $code})
            OPTIONAL MATCH (c)-[:HAS_OUTCOME]->(co:CourseOutcome)
            OPTIONAL MATCH (c)-[:HAS_OBJECTIVE]->(obj:CourseObjective)
            OPTIONAL MATCH (c)-[:HAS_TEXTBOOK|USES_TEXTBOOK]->(tb:Textbook)
            OPTIONAL MATCH (c)-[:HAS_REFERENCE|USES_REFERENCE]->(rf:Reference)
            WITH c.code AS course_code,
                 coalesce(nullif(c.name, ''), nullif(c.title, ''), c.code) AS course_name,
                 c.credits AS credits, c.L AS L, c.T AS T, c.P AS P,
                 c.evaluation_pattern_ratio AS evaluation_pattern,
                 coalesce(c.description, '') AS description,
                 coalesce(c.lab_experiments, []) AS lab_experiments,
                 [x IN collect(DISTINCT coalesce(tb.title, tb.text)) WHERE x IS NOT NULL] AS textbooks,
                 [x IN collect(DISTINCT coalesce(rf.title, rf.text)) WHERE x IS NOT NULL] AS references,
                 [x IN collect(DISTINCT CASE WHEN co IS NOT NULL THEN co.id + ': ' + co.description ELSE NULL END) WHERE x IS NOT NULL] AS outcomes,
                 [x IN collect(DISTINCT obj.description) WHERE x IS NOT NULL] AS objectives
            RETURN course_code, course_name, credits, L, T, P, evaluation_pattern,
                   description, lab_experiments, textbooks, references, outcomes, objectives
        """, code=target_code).data()
        if fallback_detail:
            return {"match_type": "course_detail", "courses": fallback_detail}

    return {"match_type": "none", "records": []}


# Aliases and re-exports for backwards compatibility
execute_neo4j_pyq_search = fetch_all_problems_by_topic
