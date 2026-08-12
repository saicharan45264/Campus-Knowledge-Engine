from neo4j import Driver


def build_syllabus_kg(driver: Driver, dept: str, year: str, courses: list[dict], doc_id: int):
    if not courses:
        return

    # build name→code lookup so we can wire prereq edges in one pass
    name_to_code = {c["name"].upper(): c["code"] for c in courses if c.get("code")}

    with driver.session() as s:
        s.run("MERGE (d:Department {name: $dept})", dept=dept)

        for c in courses:
            code = c.get("code")
            if not code:
                continue

            s.run("""
                MERGE (d:Department {name: $dept})
                MERGE (c:Course {code: $code})
                ON CREATE SET c.name = $name, c.year = $year, c.dept = $dept,
                              c.credits = $credits, c.evaluation_pattern = $eval,
                              c.prerequisites = $prereqs, c.doc_id = $doc_id
                ON MATCH  SET c.name = $name, c.credits = $credits,
                              c.evaluation_pattern = $eval, c.prerequisites = $prereqs,
                              c.doc_id = $doc_id
                MERGE (d)-[:OFFERS]->(c)
            """, dept=dept, code=code, name=c.get("name"), year=year,
                 credits=c.get("credits"), eval=c.get("evaluation_pattern"),
                 prereqs=c.get("prerequisites"), doc_id=doc_id)

            if c.get("semester"):
                s.run("""
                    MATCH (c:Course {code: $code})
                    MERGE (sem:Semester {name: $sem})
                    MERGE (sem)-[:INCLUDES]->(c)
                """, code=code, sem=c["semester"])

            for obj in c.get("objectives", []):
                s.run("MATCH (c:Course {code:$code}) MERGE (o:Objective {text:$t}) MERGE (c)-[:HAS_OBJECTIVE]->(o)",
                      code=code, t=obj)

            for out in c.get("outcomes", []):
                s.run("MATCH (c:Course {code:$code}) MERGE (co:CourseOutcome {code:$co, text:$t}) MERGE (c)-[:HAS_OUTCOME]->(co)",
                      code=code, co=out["code"], t=out["text"])

            for tb in c.get("textbooks", []):
                s.run("MATCH (c:Course {code:$code}) MERGE (tb:Textbook {text:$t}) MERGE (c)-[:HAS_TEXTBOOK]->(tb)",
                      code=code, t=tb)

            for ref in c.get("references", []):
                s.run("MATCH (c:Course {code:$code}) MERGE (r:Reference {text:$t}) MERGE (c)-[:HAS_REFERENCE]->(r)",
                      code=code, t=ref)

            # wire prerequisite edges based on name substring matching
            prereq_str = (c.get("prerequisites") or "").upper()
            for known_name, known_code in name_to_code.items():
                if known_code != code and known_name in prereq_str:
                    s.run("""
                        MATCH (c1:Course {code:$c1})
                        MERGE (c2:Course {code:$c2})
                        MERGE (c1)-[:HAS_PREREQUISITE]->(c2)
                    """, c1=code, c2=known_code)

            for unit in c.get("units", []):
                u_num   = str(unit.get("number", ""))
                u_title = unit.get("title", "")
                if not u_title:
                    continue

                s.run("""
                    MATCH (c:Course {code:$code})
                    MERGE (u:Unit {number:$num, title:$title, course_code:$code})
                    MERGE (c)-[:HAS_UNIT]->(u)
                """, code=code, num=u_num, title=u_title)

                for topic in unit.get("topics", []):
                    t_name = topic.get("name")
                    if not t_name:
                        continue
                    s.run("""
                        MATCH (u:Unit {number:$num, title:$title, course_code:$code})
                        MERGE (t:Topic {name:$t, course_code:$code})
                        MERGE (u)-[:HAS_TOPIC]->(t)
                    """, num=u_num, title=u_title, code=code, t=t_name)

                    for st in topic.get("subtopics", []):
                        if not st:
                            continue
                        s.run("""
                            MATCH (t:Topic {name:$t, course_code:$code})
                            MERGE (st:SubTopic {name:$st, course_code:$code})
                            MERGE (t)-[:HAS_SUBTOPIC]->(st)
                        """, t=t_name, code=code, st=st)


def map_questions_to_kg(driver: Driver, course_code: str, questions: list, image_path: str = None, doc_id: int = None):
    if not questions:
        return

    with driver.session() as s:
        for q in questions:
            if isinstance(q, str):
                q = {"text": q, "question_number": "Unknown",
                     "matched_topics": [], "implicit_formulas": []}

            q_text   = str(q.get("text", ""))
            formulas = [str(f) for f in q.get("implicit_formulas", []) if f]
            topics   = [str(t) for t in q.get("matched_topics",   []) if t]
            q_num    = str(q.get("question_number", ""))
            marks    = str(q.get("marks", ""))
            co       = str(q.get("co", "")) if q.get("co") else None
            btl      = str(q.get("btl", "")) if q.get("btl") else None
            parent_num  = str(q.get("parent_number", ""))
            parent_text = str(q.get("parent_text", ""))

            # if no topics matched we still store the question on the course node
            # so it isnt lost — we can fix the mapping later
            s.run("""
                MERGE (c:Course {code: $code})
                
                // Idempotent question creation
                MERGE (q:Question {
                    course_code:       $code,
                    question_number:   $num,
                    text:              $text
                })
                ON CREATE SET
                    q.marks = $marks,
                    q.co = $co,
                    q.btl = $btl,
                    q.implicit_formulas = $formulas,
                    q.image_path = $img,
                    q.doc_id = $doc_id
                ON MATCH SET
                    q.marks = CASE WHEN $marks <> "" THEN $marks ELSE q.marks END,
                    q.co = CASE WHEN $co IS NOT NULL THEN $co ELSE q.co END,
                    q.btl = CASE WHEN $btl IS NOT NULL THEN $btl ELSE q.btl END,
                    q.image_path = CASE WHEN $img IS NOT NULL THEN $img ELSE q.image_path END,
                    q.doc_id = $doc_id
                
                WITH q, c
                
                // Link sub-parts to their parent question
                FOREACH (_ IN CASE WHEN $parent_num <> "" AND $parent_text <> "" THEN [1] ELSE [] END |
                    MERGE (parent:Question {
                        course_code: $code, 
                        question_number: $parent_num, 
                        text: $parent_text
                    })
                    ON CREATE SET parent.doc_id = $doc_id
                    ON MATCH SET parent.doc_id = $doc_id
                    MERGE (parent)-[:HAS_SUBQUESTION]->(q)
                )
                
                WITH q, c
                UNWIND CASE WHEN size($topics) = 0 THEN ['__UNMATCHED__'] ELSE $topics END AS topic_name
                OPTIONAL MATCH (t:Topic    {name: topic_name, course_code: $code})
                OPTIONAL MATCH (st:SubTopic{name: topic_name, course_code: $code})
                WITH q, c, t, st
                FOREACH (_ IN CASE WHEN t  IS NOT NULL THEN [1] ELSE [] END | MERGE (t)-[:HAS_QUESTION]->(q))
                FOREACH (_ IN CASE WHEN st IS NOT NULL THEN [1] ELSE [] END | MERGE (st)-[:HAS_QUESTION]->(q))
                FOREACH (_ IN CASE WHEN t IS NULL AND st IS NULL THEN [1] ELSE [] END |
                    MERGE (c)-[:HAS_UNMATCHED_QUESTION]->(q))
            """, code=course_code, text=q_text, num=q_num, marks=marks,
                 co=co, btl=btl, formulas=formulas, topics=topics, img=image_path,
                 parent_num=parent_num, parent_text=parent_text, doc_id=doc_id)
