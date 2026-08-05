import asyncio
from app import get_neo4j

async def test():
    neo4j_driver = get_neo4j()
    words = ['machine', 'learning']
    with neo4j_driver.session() as session:
        records = session.run(
            """
            MATCH (n)
            WHERE (n:Course OR n:Topic OR n:SubTopic)
            AND any(word IN $words WHERE toLower(n.name) CONTAINS word OR toLower(n.code) CONTAINS word)
            
            OPTIONAL MATCH (c:Course)-[:HAS_UNIT]->(:Unit)-[:HAS_TOPIC]->(t:Topic)
            WHERE n = t OR (t)-[:HAS_SUBTOPIC]->(n)
            
            WITH coalesce(c, n) as target_course, n as matched_node
            LIMIT 3
            
            OPTIONAL MATCH (target_course:Course)-[:HAS_UNIT]->(u:Unit)-[:HAS_TOPIC]->(t:Topic)
            
            RETURN 
                target_course.code as course_code,
                target_course.name as course_name,
                labels(matched_node) as match_type,
                matched_node.name as matched_node_name,
                u.title as unit_title,
                t.name as topic_name
            """,
            words=words
        )
        graph_facts = []
        syllabus_dict = {}
        for r in records:
            c_code = r['course_code']
            if not c_code: continue
            if r['unit_title'] and r['topic_name']:
                if c_code not in syllabus_dict:
                    syllabus_dict[c_code] = {"name": r['course_name'], "units": {}}
                if r['unit_title'] not in syllabus_dict[c_code]["units"]:
                    syllabus_dict[c_code]["units"][r['unit_title']] = []
                if r['topic_name'] not in syllabus_dict[c_code]["units"][r['unit_title']]:
                    syllabus_dict[c_code]["units"][r['unit_title']].append(r['topic_name'])

        for c_code, data in syllabus_dict.items():
            fact = f"Course Syllabus for [{c_code}] {data['name']}:\n"
            for unit, topics in data['units'].items():
                fact += f"  - {unit}: " + ", ".join(topics) + "\n"
            graph_facts.append(fact)
        print("--- GRAPH FACTS ---")
        for f in graph_facts:
            print(f)

asyncio.run(test())
