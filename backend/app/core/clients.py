"""
CurriculumLens Backend — Service Clients

Connection factories for Neo4j.
"""

from neo4j import AsyncGraphDatabase
from app.core.config import get_settings

settings = get_settings()

# ── Neo4j ──

def get_neo4j_driver():
    """Create an async Neo4j driver."""
    return AsyncGraphDatabase.driver(
        settings.NEO4J_URI,
        auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
    )


async def init_neo4j_constraints(driver) -> None:
    """Create uniqueness constraints and indexes for the CKG schema."""
    constraints = [
        "CREATE CONSTRAINT IF NOT EXISTS FOR (c:Course) REQUIRE c.code IS UNIQUE",
        "CREATE CONSTRAINT IF NOT EXISTS FOR (u:Unit) REQUIRE u.uid IS UNIQUE",
        "CREATE CONSTRAINT IF NOT EXISTS FOR (t:Topic) REQUIRE t.tid IS UNIQUE",
        "CREATE CONSTRAINT IF NOT EXISTS FOR (co:Concept) REQUIRE co.cid IS UNIQUE",
        "CREATE CONSTRAINT IF NOT EXISTS FOR (f:Formula) REQUIRE f.fid IS UNIQUE",
        "CREATE CONSTRAINT IF NOT EXISTS FOR (q:PYQQuestion) REQUIRE q.qid IS UNIQUE",
        "CREATE INDEX IF NOT EXISTS FOR (co:Concept) ON (co.name)",
        "CREATE INDEX IF NOT EXISTS FOR (t:Topic) ON (t.name)",
    ]
    async with driver.session() as session:
        for constraint in constraints:
            await session.run(constraint)
