import os
import sqlite3
import psycopg2
from psycopg2.pool import SimpleConnectionPool
from dotenv import load_dotenv

load_dotenv(override=True)

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL not found in environment")

# PostgreSQL Pool
# Use a global pool object
pg_pool = None

def init_postgres_pool():
    global pg_pool
    if pg_pool is None:
        pg_pool = SimpleConnectionPool(1, 20, DATABASE_URL)
    return pg_pool

def get_pg_connection():
    if pg_pool is None:
        init_postgres_pool()
    return pg_pool.getconn()

def release_pg_connection(conn):
    if pg_pool is not None:
        pg_pool.putconn(conn)

# SQLite FTS
SQLITE_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "fts.sqlite")

def get_sqlite_connection():
    conn = sqlite3.connect(SQLITE_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_dbs():
    # Run pg schema
    pg_conn = get_pg_connection()
    try:
        with pg_conn.cursor() as cur:
            schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
            with open(schema_path, "r") as f:
                cur.execute(f.read())
            # Seed default university for Phase 1 testing
            cur.execute("""
                INSERT INTO universities (id, name, short_code)
                VALUES ('00000000-0000-0000-0000-000000000000', 'Amrita Vishwa Vidyapeetham', 'AMRITA_CB')
                ON CONFLICT (id) DO NOTHING;
            """)
        pg_conn.commit()
    finally:
        release_pg_connection(pg_conn)
    
    # Run sqlite schema
    sqlite_conn = get_sqlite_connection()
    try:
        schema_path = os.path.join(os.path.dirname(__file__), "fts_schema.sql")
        with open(schema_path, "r") as f:
            sqlite_conn.executescript(f.read())
        sqlite_conn.commit()
    finally:
        sqlite_conn.close()

if __name__ == "__main__":
    print("Initializing databases...")
    init_dbs()
    print("Databases initialized successfully.")
