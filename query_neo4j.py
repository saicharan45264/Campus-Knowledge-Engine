from neo4j import GraphDatabase
driver = GraphDatabase.driver("bolt://localhost:7687", auth=("neo4j", "password"))
with driver.session() as session:
    result = session.run("MATCH (c:Course) RETURN c.code LIMIT 10")
    for r in result:
        print(r['c.code'])
