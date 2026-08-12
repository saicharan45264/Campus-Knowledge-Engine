"""
Vanilla Python Database Connection Layer
-----------------------------------------
Instead of using SQLAlchemy (which abstracts away the actual database logic),
we use pure psycopg2. This proves our ability to manually write SQL queries,
handle connection pooling, and securely parameterize inputs to prevent SQL injection.
"""
import os
import psycopg2
from psycopg2.pool import SimpleConnectionPool
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '..', '.env'))

POSTGRES_USER     = os.getenv("POSTGRES_USER",     "cluser")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "clpassword")
POSTGRES_HOST     = os.getenv("POSTGRES_HOST",     "localhost")
POSTGRES_PORT     = os.getenv("POSTGRES_PORT",     "5434")
POSTGRES_DB       = os.getenv("POSTGRES_DB",       "curriculumlens")

try:
    pg_pool = SimpleConnectionPool(
        1, 10,
        user=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
        host=POSTGRES_HOST,
        port=POSTGRES_PORT,
        database=POSTGRES_DB
    )
except Exception as e:
    print(f"Failed to connect to Postgres: {e}")
    pg_pool = None

def get_db_connection():
    if not pg_pool:
        raise Exception("Database connection pool is not initialized")
    return pg_pool.getconn()

def release_db_connection(conn):
    if pg_pool and conn:
        pg_pool.putconn(conn)

def init_db():
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    username VARCHAR(255) UNIQUE NOT NULL,
                    hashed_password VARCHAR(255) NOT NULL,
                    role VARCHAR(50) NOT NULL
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS documents (
                    id SERIAL PRIMARY KEY,
                    filename VARCHAR(255) NOT NULL,
                    doc_type VARCHAR(50) NOT NULL,
                    course_code VARCHAR(50),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS chunks (
                    id SERIAL PRIMARY KEY,
                    document_id INTEGER REFERENCES documents(id) ON DELETE CASCADE,
                    content TEXT NOT NULL,
                    embedding vector(768)
                );
            """)
            # --- Performance indexes ---
            # Speed up the JOIN in hybrid_retrieve
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_chunks_doc
                ON chunks(document_id);
            """)
            # GIN index for fast tsvector full-text search (lexical arm of hybrid)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_chunks_fts
                ON chunks USING GIN(to_tsvector('english', content));
            """)
            conn.commit()

            # ivfflat vector index (requires >100 rows; safe to skip on fresh DB)
            try:
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_chunks_vec
                    ON chunks USING ivfflat(embedding vector_cosine_ops)
                    WITH (lists = 100);
                """)
                conn.commit()
            except Exception:
                conn.rollback()   # index creation failed (too few rows) — that is fine
    finally:
        release_db_connection(conn)

# Neo4j setup
NEO4J_URI      = os.getenv("NEO4J_URI",      "bolt://localhost:7687")
NEO4J_USER     = os.getenv("NEO4J_USER",     "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "clpassword")

neo4j_driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

def _prime_neo4j_schema():
    stubs = [
        "CREATE (n:SubTopic)-[:HAS_SUBTOPIC]->(n) DETACH DELETE n",
        "CREATE (n:Question)-[:HAS_QUESTION]->(n) DETACH DELETE n",
        "CREATE (a:Course)-[:HAS_PREREQUISITE]->(b:Course) DETACH DELETE a,b",
        "CREATE (co:CourseOutcome)<-[:HAS_OUTCOME]-(c:Course) DETACH DELETE co,c",
        "CREATE (ob:Objective)<-[:HAS_OBJECTIVE]-(c:Course) DETACH DELETE ob,c",
        "CREATE (tb:Textbook)<-[:HAS_TEXTBOOK]-(c:Course) DETACH DELETE tb,c",
        "CREATE (r:Reference)<-[:HAS_REFERENCE]-(c:Course) DETACH DELETE r,c",
        "CREATE (s:Semester)-[:INCLUDES]->(c:Course) DETACH DELETE s,c",
    ]
    try:
        with neo4j_driver.session() as s:
            for q in stubs:
                s.run(q)
    except Exception as e:
        print(f"neo4j schema prime skipped: {e}")

_prime_neo4j_schema()

def get_neo4j():
    return neo4j_driver
