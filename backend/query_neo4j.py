from neo4j import GraphDatabase
driver = GraphDatabase.driver("bolt://localhost:7687", auth=("neo4j", "clpassword"))
with driver.session() as session:
    result = session.run("MATCH (c:Course) RETURN c.code as code")
    codes = [r['code'] for r in result]
    print([c for c in codes if "EEE" in c])
