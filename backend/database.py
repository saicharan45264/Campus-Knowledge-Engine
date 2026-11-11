import datetime
import os
import uuid

# python-dotenv is used to load configuration variables from a .env file
from dotenv import load_dotenv

# Neo4j is our Graph Database. We use it to store concepts and their relationships.
from neo4j import GraphDatabase

# pgvector is a PostgreSQL extension that allows us to store and search
# mathematical vectors (embeddings) directly in the database.
from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID

# SQLAlchemy is our Object-Relational Mapper (ORM). It lets us interact with
# PostgreSQL using Python classes instead of raw SQL queries.
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import declarative_base, sessionmaker

# -----------------------------------------------------------------------------
# Configuration Loading
# -----------------------------------------------------------------------------
# Load environment variables from the .env file located at the project root.
# os.path.dirname(__file__) gives us the 'backend/' folder.
# '..' moves us one level up to the root folder where '.env' is located.
load_dotenv(
    dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"), override=True
)


# 1. PostgreSQL Setup — Stores document metadata and vector embeddings

# Fetch database credentials from environment variables, providing safe defaults
POSTGRES_USER = os.getenv("POSTGRES_USER", "cluser")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "clpassword")
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT = os.getenv("POSTGRES_PORT", "5434")
POSTGRES_DB = os.getenv("POSTGRES_DB", "curriculumlens")

# Construct the connection string required by SQLAlchemy
DATABASE_URL = (
    f"postgresql+asyncpg://{POSTGRES_USER}:{POSTGRES_PASSWORD}"
    f"@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"
)

# The 'engine' is the core interface to the database
engine = create_async_engine(DATABASE_URL, echo=False)

# A 'session' is an ongoing transaction with the database. We use an async session.
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

# 'Base' is the parent class for all our database models
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
    # The type of document ("syllabus" or "pyq")
    doc_type = Column(String, nullable=False, default="pyq")
    # The department (for syllabus docs, e.g., "CSE")
    department = Column(String, nullable=True)
    # The year of the syllabus (for syllabus docs, e.g., "2023")
    year = Column(String, nullable=True)
    # The academic course code associated with this document (e.g., CSE101)
    course_code = Column(String, nullable=True)
    # Human-readable course name supplied by admins for PYQ documents
    course_name = Column(String, nullable=True)
    semester = Column(String, nullable=True, index=True)
    # Timestamp of when the document was uploaded
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class AcademicDocumentRecord(Base):
    """OCR/native-text records for exam schedules, seating plans, and lab timetables."""
    __tablename__ = "academic_document_records"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    record_type = Column(String, nullable=False, index=True)
    page_number = Column(Integer, nullable=False, default=1)
    line_number = Column(Integer, nullable=False, default=0)
    record_text = Column(Text, nullable=False)
    payload_json = Column(Text, nullable=True)


class StudentAccount(Base):
    """Registered student credentials and the academic scope used for retrieval."""
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

    # The vector embedding representing the semantic meaning of the text content.
    # The dimension is 768 to perfectly match the output of the 'nomic-embed-text' model.
    embedding = Column(Vector(768), nullable=True)


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


# -----------------------------------------------------------------------------
# Academic Calendar Models
# -----------------------------------------------------------------------------
class CalendarDay(Base):
    """
    Represents an individual date in the academic year.
    Stores holiday status, particulars, and any timetable-weekday override (e.g. 'Monday's Timetable').
    """
    __tablename__ = "calendar_days"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    calendar_date = Column(DateTime, nullable=False, index=True)
    day_of_week = Column(String, nullable=False)  # Mon, Tue, Wed, Thu, Fri, Sat, Sun
    status = Column(String, nullable=False, default="W")  # W, Holiday
    particulars = Column(Text, nullable=True)
    timetable_override = Column(String, nullable=True)  # Monday, Tuesday, etc.
    academic_year = Column(String, nullable=True, default="2026-2027")
    month_name = Column(String, nullable=True)  # e.g. Jun-26


class CalendarDayCohort(Base):
    """
    Cohort-specific mapping for an academic date (e.g. IV yr Class Day 12).
    """
    __tablename__ = "calendar_day_cohorts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    calendar_day_id = Column(UUID(as_uuid=True), ForeignKey("calendar_days.id", ondelete="CASCADE"), nullable=False, index=True)
    cohort = Column(String, nullable=False, index=True)  # e.g. "IV yr", "III Yr", "II year", "I Year"
    class_day_number = Column(Integer, nullable=True)  # e.g. 1..90
    is_instruction_day = Column(Boolean, default=True)


class AcademicEvent(Base):
    """
    High-level academic milestones, holidays, and examination intervals.
    """
    __tablename__ = "academic_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String, nullable=False)
    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime, nullable=True)
    event_category = Column(String, nullable=False, default="milestone")  # holiday, exam, milestone, celebration
    cohort_applicability = Column(String, nullable=True, default="ALL")


# -----------------------------------------------------------------------------
# Timetable Models
# -----------------------------------------------------------------------------
class TimetableVersion(Base):
    """
    Metadata for a class weekly timetable (e.g. CSE Semester VII Section F).
    """
    __tablename__ = "timetable_versions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    department = Column(String, nullable=False, default="CSE")
    semester = Column(String, nullable=False, default="VII")
    section = Column(String, nullable=False, default="F")
    program = Column(String, nullable=True, default="B.Tech CSE")
    building = Column(String, nullable=True, default="AB III")
    academic_year = Column(String, nullable=True, default="2026-2027")
    class_advisors = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class WeeklySession(Base):
    """
    An individual scheduled class or activity period in the weekly grid.
    """
    __tablename__ = "weekly_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    timetable_id = Column(UUID(as_uuid=True), ForeignKey("timetable_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    day_of_week = Column(String, nullable=False, index=True)  # Monday, Tuesday, etc.
    slot_numbers = Column(String, nullable=False)  # "1", "4,5,6", "8,9,10"
    start_time = Column(String, nullable=False)  # "08.00 am"
    end_time = Column(String, nullable=False)    # "08.50 am"
    activity_label = Column(String, nullable=False)  # e.g. "PE - IV", "23CSE401 - Lab", "CIR"
    course_code = Column(String, nullable=True)
    elective_bucket = Column(String, nullable=True)  # "PE - IV", "PE - V", "PE - VI", "Free Elective - 2"
    group_label = Column(String, nullable=True)  # "G1", "G2"
    faculty = Column(String, nullable=True)
    venue = Column(String, nullable=True)


class ElectiveOption(Base):
    """
    Directory entry mapping an elective bucket to eligible course offerings, faculty, and room allocations.
    """
    __tablename__ = "elective_options"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    timetable_id = Column(UUID(as_uuid=True), ForeignKey("timetable_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    elective_bucket = Column(String, nullable=False, index=True)  # "PE - IV", "PE - V", "PE - VI", "Free Elective - 2"
    course_code = Column(String, nullable=False)
    course_name = Column(String, nullable=False)
    faculty = Column(String, nullable=True)
    venue = Column(String, nullable=True)
    group_label = Column(String, nullable=True)
    slot_info = Column(String, nullable=True)


# -----------------------------------------------------------------------------
# Regulations Models
# -----------------------------------------------------------------------------
class RegulationClause(Base):
    """
    Structured clause or ordinance from academic regulations (e.g. R.12.1, R.16.1, O.1).
    """
    __tablename__ = "regulation_clauses"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    clause_id = Column(String, nullable=False, index=True)  # e.g. "R.12.1", "O.1"
    parent_clause_id = Column(String, nullable=True, index=True)  # e.g. "R.12"
    title = Column(String, nullable=True)  # e.g. "Attendance"
    clause_text = Column(Text, nullable=False)
    page_number = Column(Integer, nullable=True)
    table_data_json = Column(Text, nullable=True)


class ClauseReference(Base):
    """
    Cross-reference between regulation clauses (e.g. R.16.2 referencing R.15.2).
    """
    __tablename__ = "clause_references"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    from_clause_id = Column(String, nullable=False, index=True)
    to_clause_id = Column(String, nullable=False, index=True)
    reference_context = Column(String, nullable=True)


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

# Fetch Neo4j connection details from environment variables
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "clpassword")

# Create a single, shared driver instance to communicate with the Neo4j database
neo4j_driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))


def get_neo4j():
    """
    Returns the shared Neo4j driver instance.
    """
    return neo4j_driver
