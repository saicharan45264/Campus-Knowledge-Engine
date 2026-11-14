"""backend/database.py — PostgreSQL (pgvector HALFVEC) models and Neo4j driver helpers."""
from __future__ import annotations

import datetime
import logging
import uuid

from neo4j import GraphDatabase
from pgvector.sqlalchemy import HALFVEC
from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from backend.config import settings

logger = logging.getLogger("mach_ai.db")

# 1. PostgreSQL 16 + pgvector Setup
DATABASE_URL = (
    f"postgresql+asyncpg://{settings.postgres_user}:{settings.postgres_password}"
    f"@{settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db}"
)

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
Base = declarative_base()


# -----------------------------------------------------------------------------
# Database Models (Tables)
# -----------------------------------------------------------------------------


class Document(Base):
    """
    Represents an uploaded PDF document.
    Table name: documents
    """

    __tablename__ = "documents"

    # Primary key: A unique identifier for every document
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # The original name of the uploaded file
    filename = Column(String, nullable=False)
    # The document category selected by the administrator.
    doc_type = Column(String, nullable=False, default="pyq")
    # The department (for syllabus docs, e.g., "CSE")
    department = Column(String, nullable=True)
    # The year of the syllabus (for syllabus docs, e.g., "2023")
    year = Column(String, nullable=True)
    # The academic course code associated with this document (e.g., CSE101)
    course_code = Column(String, nullable=True)
    # Additional admin-selected scope. Admin metadata remains authoritative.
    course_name = Column(String, nullable=True)
    semester = Column(String, nullable=True, index=True)
    section = Column(String, nullable=True)
    # Timestamp of when the document was uploaded
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class StudentAccount(Base):
    """Student identity and the authoritative academic scope for personalized retrieval."""

    __tablename__ = "student_accounts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    full_name = Column(String, nullable=False)
    age = Column(Integer, nullable=False)
    gender = Column(String, nullable=False)
    roll_number = Column(String, nullable=False, unique=True, index=True)
    college_email = Column(String, nullable=False, unique=True, index=True)
    password_hash = Column(String, nullable=False)
    department = Column(String, nullable=False, index=True)
    study_year = Column(String, nullable=False)
    academic_year = Column(String, nullable=False)
    semester = Column(String, nullable=False)
    section = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class AcademicDocumentRecord(Base):
    """Page/line level extracted records for exam, seating, and lab PDFs."""

    __tablename__ = "academic_document_records"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    record_type = Column(String, nullable=False, index=True)
    page_number = Column(Integer, nullable=False, default=1)
    line_number = Column(Integer, nullable=False, default=0)
    record_text = Column(Text, nullable=False)
    payload_json = Column(Text, nullable=True)


class CalendarDay(Base):
    """One day in an official academic calendar, including overrides and holidays."""

    __tablename__ = "calendar_days"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    calendar_date = Column(DateTime, nullable=False, index=True)
    day_of_week = Column(String, nullable=False)
    status = Column(String, nullable=False, default="W")
    particulars = Column(Text, nullable=True)
    timetable_override = Column(String, nullable=True)
    academic_year = Column(String, nullable=True)
    month_name = Column(String, nullable=True)


class CalendarDayCohort(Base):
    """Cohort class-day counter associated with a calendar date."""

    __tablename__ = "calendar_day_cohorts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    calendar_day_id = Column(UUID(as_uuid=True), ForeignKey("calendar_days.id", ondelete="CASCADE"), nullable=False, index=True)
    cohort = Column(String, nullable=False, index=True)
    class_day_number = Column(Integer, nullable=True)
    is_instruction_day = Column(Boolean, default=True)


class AcademicEvent(Base):
    """A named academic milestone, holiday, or examination period."""

    __tablename__ = "academic_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String, nullable=False)
    start_date = Column(DateTime, nullable=False, index=True)
    end_date = Column(DateTime, nullable=True)
    event_category = Column(String, nullable=False, default="milestone")
    cohort_applicability = Column(String, nullable=True, default="ALL")


class TimetableVersion(Base):
    """Admin-scoped version of a weekly classroom timetable."""

    __tablename__ = "timetable_versions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    department = Column(String, nullable=False)
    semester = Column(String, nullable=False)
    section = Column(String, nullable=False)
    program = Column(String, nullable=True)
    building = Column(String, nullable=True)
    academic_year = Column(String, nullable=True)
    class_advisors = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class WeeklySession(Base):
    """Single scheduled teaching/activity period in a timetable."""

    __tablename__ = "weekly_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    timetable_id = Column(UUID(as_uuid=True), ForeignKey("timetable_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    day_of_week = Column(String, nullable=False, index=True)
    slot_numbers = Column(String, nullable=False)
    start_time = Column(String, nullable=False)
    end_time = Column(String, nullable=False)
    activity_label = Column(String, nullable=False)
    course_code = Column(String, nullable=True)
    elective_bucket = Column(String, nullable=True)
    group_label = Column(String, nullable=True)
    faculty = Column(String, nullable=True)
    venue = Column(String, nullable=True)


class ElectiveOption(Base):
    """Course/faculty/venue option attached to a timetable elective bucket."""

    __tablename__ = "elective_options"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    timetable_id = Column(UUID(as_uuid=True), ForeignKey("timetable_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    elective_bucket = Column(String, nullable=False, index=True)
    course_code = Column(String, nullable=False)
    course_name = Column(String, nullable=False)
    faculty = Column(String, nullable=True)
    venue = Column(String, nullable=True)
    group_label = Column(String, nullable=True)
    slot_info = Column(String, nullable=True)


class RegulationClause(Base):
    """Structured regulation clause preserving clause ID, hierarchy and source page."""

    __tablename__ = "regulation_clauses"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    clause_id = Column(String, nullable=False, index=True)
    parent_clause_id = Column(String, nullable=True, index=True)
    title = Column(String, nullable=True)
    clause_text = Column(Text, nullable=False)
    page_number = Column(Integer, nullable=True)
    table_data_json = Column(Text, nullable=True)


class ClauseReference(Base):
    """Cross-reference between two clauses in a regulations PDF."""

    __tablename__ = "clause_references"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=True, index=True)
    from_clause_id = Column(String, nullable=False, index=True)
    to_clause_id = Column(String, nullable=False, index=True)
    reference_context = Column(String, nullable=True)


class DocumentChunk(Base):
    """
    Represents a specific paragraph or section of text extracted from a document.
    Table name: document_chunks
    """

    __tablename__ = "document_chunks"

    # Primary key: A unique identifier for this specific chunk
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Foreign key-like relationship to the parent Document
    document_id = Column(UUID(as_uuid=True), nullable=False)
    # The actual text content of this chunk
    content = Column(Text, nullable=False)
    # The course code, duplicated here for easier querying
    course_code = Column(String, nullable=True)

    # Marks whether this chunk was extracted as plain text or described from a visual
    # element (equation, diagram, circuit) by the vision model. Values: "text" or "visual".
    content_type = Column(String, default="text")

    # 768-dim half-precision vector matching embeddinggemma-2:latest (50% smaller HNSW footprint)
    embedding = Column(HALFVEC(768), nullable=True)


class ProcessingReport(Base):
    """
    Stores per-document PYQ extraction and mapping audit report as a JSON payload.
    Table name: processing_reports
    """
    __tablename__ = "processing_reports"

    id          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    course_code = Column(String, nullable=True)
    report_json = Column(Text, nullable=False, default="{}")
    created_at  = Column(DateTime, default=datetime.datetime.utcnow)


class QueryLog(Base):
    """
    Telemetry and observability log table tracking latency, routing, and token metrics.
    Table name: query_logs
    """
    __tablename__ = "query_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(String(100), nullable=True)
    user_query = Column(Text, nullable=False)
    classified_intent = Column(String(50), nullable=False)
    retrieval_layer = Column(String(50), nullable=False)  # 'fast_path', 'graph_rag', 'lucene_fallback', 'rrf', 'syllabus_fallback', 'semantic_cache'
    router_ms = Column(Float, nullable=False, default=0.0)
    retrieval_ms = Column(Float, nullable=False, default=0.0)
    ttft_ms = Column(Float, nullable=True)
    total_ms = Column(Float, nullable=False, default=0.0)
    prompt_tokens = Column(Integer, default=0)
    completion_tokens = Column(Integer, default=0)
    hit_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class SemanticCache(Base):
    """
    Semantic vector cache table for instant sub-3ms answer retrieval.
    Table name: semantic_cache
    """
    __tablename__ = "semantic_cache"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    query_text = Column(Text, nullable=False)
    query_embedding = Column(HALFVEC(768), nullable=False)
    cached_intent = Column(String(50), nullable=False)
    response_payload = Column(JSONB, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class User(Base):
    """
    Represents an authorized campus user account with role-based access.
    Table name: users
    """
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(20), nullable=False, default="student")  # 'student' | 'admin'
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


async def init_auth_schema():
    """Create the login table independently from document indexing."""
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: User.__table__.create(sync_conn, checkfirst=True))

    # Seed initial user accounts if users table is empty
    try:
        import bcrypt
        from sqlalchemy import func, select

        async with AsyncSessionLocal() as session:
            count = await session.scalar(select(func.count(User.id)))
            if count == 0:
                student_hash = bcrypt.hashpw(b"student123", bcrypt.gensalt(12)).decode("utf-8")
                admin_hash = bcrypt.hashpw(b"admin123", bcrypt.gensalt(12)).decode("utf-8")
                demo_student = User(
                    username="student",
                    password_hash=student_hash,
                    role="student",
                )
                demo_admin = User(
                    username="admin",
                    password_hash=admin_hash,
                    role="admin",
                )
                session.add_all([demo_student, demo_admin])
                await session.commit()
                logger.info("Initialized default student and admin user accounts.")
    except Exception as e:
        logger.warning("Notice initializing default user accounts: %s", e)



async def init_db_schema():
    """
    Initializes PostgreSQL tables, vector extensions, HNSW indexes,
    and default administrative/student user accounts.

    Neo4j constraints and indexes are created lazily during PDF ingestion
    so a fresh database starts with zero nodes and zero labels.
    """
    await init_auth_schema()

    # 1. PostgreSQL setup
    async with engine.begin() as conn:
        await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "uuid-ossp";'))
        await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "vector";'))
        await conn.run_sync(Base.metadata.create_all)

        # ``create_all`` creates new tables but does not add columns to an
        # existing installation. Keep this additive migration safe to rerun.
        for column_name, column_type in (
            ("course_name", "TEXT"),
            ("semester", "TEXT"),
            ("section", "TEXT"),
        ):
            await conn.execute(text(
                f"ALTER TABLE documents ADD COLUMN IF NOT EXISTS {column_name} {column_type}"
            ))
        # Older databases stored clause references without their source document.
        await conn.execute(text(
            "ALTER TABLE clause_references ADD COLUMN IF NOT EXISTS document_id UUID "
            "REFERENCES documents(id) ON DELETE CASCADE"
        ))
        
    # Keep optional index changes in a separate transaction so they cannot roll back required schema updates.
    try:
        async with engine.begin() as conn:
            col_types = await conn.execute(text("""
                SELECT attrelid::regclass::text AS table_name,
                       attname AS column_name,
                       format_type(atttypid, atttypmod) AS col_type
                FROM pg_attribute
                WHERE attrelid IN ('document_chunks'::regclass, 'semantic_cache'::regclass)
                  AND attname IN ('embedding', 'query_embedding')
                  AND NOT attisdropped;
            """))
            for row in col_types:
                if "halfvec" not in (row.col_type or "").lower():
                    idx_name = "idx_chunks_hnsw" if "document_chunks" in row.table_name else "idx_cache_hnsw"
                    tbl = "document_chunks" if "document_chunks" in row.table_name else "semantic_cache"
                    col = "embedding" if tbl == "document_chunks" else "query_embedding"
                    await conn.execute(text(f"DROP INDEX IF EXISTS {idx_name};"))
                    await conn.execute(text(
                        f"ALTER TABLE {tbl} ALTER COLUMN {col} "
                        f"TYPE halfvec(768) USING {col}::halfvec(768);"
                    ))

            await conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_chunks_hnsw 
                ON document_chunks USING hnsw (embedding halfvec_cosine_ops)
                WITH (m = 16, ef_construction = 64);
            """))
            await conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_cache_hnsw 
                ON semantic_cache USING hnsw (query_embedding halfvec_cosine_ops)
                WITH (m = 16, ef_construction = 64);
            """))
            await conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_chunks_course_code 
                ON document_chunks(course_code);
            """))
            # Ensure full-text search generated column and GIN index exist
            await conn.execute(text("""
                ALTER TABLE document_chunks 
                ADD COLUMN IF NOT EXISTS tsv_content tsvector 
                GENERATED ALWAYS AS (to_tsvector('english', content)) STORED;
            """))
            await conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_chunks_tsv 
                ON document_chunks USING gin(tsv_content);
            """))
    except Exception as e:
        logger.warning("Notice creating database vector and fulltext indexes: %s", e)



def ensure_neo4j_schema(neo4j_driver=None, schema_type: str = "all") -> None:
    """Create Neo4j constraints and indexes on demand during PDF ingestion.

    Args:
        neo4j_driver: Optional Neo4j driver instance. Uses shared driver if omitted.
        schema_type: One of ``"syllabus"``, ``"pyq"``, or ``"all"``.
    """
    if schema_type not in ("syllabus", "pyq", "all"):
        raise ValueError(f"Unsupported Neo4j schema_type: {schema_type}")

    try:
        driver = neo4j_driver or get_neo4j()
        with driver.session() as session:
            # Both syllabus and PYQ ingestion attach nodes to Course
            session.run(
                "CREATE CONSTRAINT course_code_unique IF NOT EXISTS FOR (c:Course) REQUIRE c.code IS UNIQUE"
            )
            session.run("CREATE INDEX course_code_idx IF NOT EXISTS FOR (c:Course) ON (c.code)")

            if schema_type in ("syllabus", "all"):
                session.run("CREATE INDEX program_name_idx IF NOT EXISTS FOR (p:Program) ON (p.name)")
                session.run("CREATE INDEX topic_name_idx IF NOT EXISTS FOR (t:Topic) ON (t.name)")
                session.run("CREATE FULLTEXT INDEX topic_name_ft IF NOT EXISTS FOR (t:Topic) ON EACH [t.name]")
                session.run("CREATE INDEX eval_ratio_idx IF NOT EXISTS FOR (ep:EvaluationPattern) ON (ep.ratio)")
                session.run("CREATE INDEX assess_key_idx IF NOT EXISTS FOR (ac:AssessmentComponent) ON (ac.key)")

            if schema_type in ("pyq", "all"):
                try:
                    session.run(
                        "CREATE CONSTRAINT question_id_unique IF NOT EXISTS FOR (q:Question) REQUIRE q.id IS UNIQUE"
                    )
                except Exception as ce:
                    # If a non-unique index exists with the same target, drop it and retry constraint
                    if "IndexAlreadyExists" in str(ce):
                        session.run("DROP INDEX question_id_idx IF EXISTS")
                        session.run(
                            "CREATE CONSTRAINT question_id_unique IF NOT EXISTS FOR (q:Question) REQUIRE q.id IS UNIQUE"
                        )
                session.run("CREATE INDEX question_course_idx IF NOT EXISTS FOR (q:Question) ON (q.course_code)")
                session.run(
                    "CREATE FULLTEXT INDEX question_text_ft IF NOT EXISTS FOR (q:Question) ON EACH [q.text, q.diagram_summary]"
                )
        logger.info("Neo4j schema ensured for '%s'.", schema_type)
    except Exception as e:
        logger.warning("Notice ensuring Neo4j indexes (%s): %s", schema_type, e)


def drop_neo4j_schema(neo4j_driver=None) -> None:
    """Drop managed Neo4j constraints and indexes after a full graph reset."""
    statements = (
        "DROP CONSTRAINT course_code_unique IF EXISTS",
        "DROP CONSTRAINT question_id_unique IF EXISTS",
        "DROP INDEX course_code_idx IF EXISTS",
        "DROP INDEX program_name_idx IF EXISTS",
        "DROP INDEX topic_name_idx IF EXISTS",
        "DROP INDEX eval_ratio_idx IF EXISTS",
        "DROP INDEX assess_key_idx IF EXISTS",
        "DROP INDEX question_course_idx IF EXISTS",
        "DROP INDEX question_id_idx IF EXISTS",
        "DROP INDEX topic_name_ft IF EXISTS",
        "DROP INDEX question_text_ft IF EXISTS",
    )
    try:
        driver = neo4j_driver or get_neo4j()
        with driver.session() as session:
            for stmt in statements:
                session.run(stmt)
        logger.info("Neo4j constraints and indexes dropped.")
    except Exception as e:
        logger.warning("Notice dropping Neo4j indexes: %s", e)


# -----------------------------------------------------------------------------
# Database Dependency Injection
# -----------------------------------------------------------------------------
async def get_db():
    """
    A helper function that provides a database session to our FastAPI routes.
    It ensures the session is properly closed after the request finishes.
    """
    async with AsyncSessionLocal() as session:
        yield session


# 2. Neo4j Setup — Stores the Curriculum Knowledge Graph
neo4j_driver = GraphDatabase.driver(
    settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
)


def get_neo4j():
    """
    Returns the shared Neo4j driver instance.
    """
    return neo4j_driver
