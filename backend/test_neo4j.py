from neo4j import GraphDatabase

uri = "bolt://localhost:7687"
username = "neo4j"
password = "clpassword"

driver = GraphDatabase.driver(uri, auth=(username, password))

with driver.session() as session:
    words = ['machine', 'learning', 'syllabus']
    records = session.run(
        """
        MATCH (n)
        WHERE (n:Course OR n:Topic OR n:SubTopic)
        AND any(word IN $words WHERE toLower(n.name) CONTAINS word OR toLower(n.code) CONTAINS word)
        
        OPTIONAL MATCH (n:Course)-[:HAS_UNIT]->(u:Unit)-[:HAS_TOPIC]->(t:Topic)
        
        OPTIONAL MATCH (c:Course)-[:HAS_UNIT]->(u2:Unit)-[:HAS_TOPIC]->(t2:Topic)
        WHERE n = t2 OR (t2)-[:HAS_SUBTOPIC]->(n)
        
        RETURN 
            coalesce(n.code, c.code) as course_code,
            coalesce(n.name, c.name) as course_name,
            labels(n) as match_type,
            n.name as matched_node_name,
            u.title as unit_title,
            t.name as topic_name
        LIMIT 50
        """,
        words=words
    )
    
    for r in records:
        print(dict(r))
