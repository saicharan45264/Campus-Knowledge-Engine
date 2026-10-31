import base64
import http
import os
import sys

# Store __pycache__ inside the cache/ folder to keep the root directory clean
sys.pycache_prefix = os.path.join(os.path.dirname(__file__), "cache", "__pycache__")

import shutil
import uuid
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import uvicorn
from collections import defaultdict
from typing import Optional
from intent_router import classify_query_intent

# FastAPI is the core framework used to build our web API
from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
)

# CORSMiddleware allows our frontend (HTML files) to communicate with this backend
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from jose import jwt

# Pydantic is used to define the structure of incoming data (like JSON requests)
from pydantic import BaseModel
from sqlalchemy import select, text

# SQLAlchemy components for interacting with our PostgreSQL database
from sqlalchemy.ext.asyncio import AsyncSession

# StaticFiles already imported above
# Import our custom database configurations and models
from database import Base, Document, DocumentChunk, ProcessingReport, engine, get_db, get_neo4j
from query_neo4j import fetch_all_problems_by_topic, fetch_problems_by_topic_graph
from utils import (
    PREREQUISITE_MAP,
    add_prerequisite_edges,
    classify_pdf_type,
    delete_all_images_from_cloudinary,
    delete_document_images_from_cloudinary,
    describe_uploaded_image,
    execute_neo4j_pyq_search,
    extract_pyq_questions,
    extract_pyq_structured,
    generate_answer,
    generate_answer_stream,
    get_embedding,
    hybrid_search_rrf,
    map_pyq_structured_to_kg,
    map_questions_to_kg,
    process_pyq_visuals,
    upload_question_image_to_cloudinary,
)

# Application Setup


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    This function runs once when the server starts.
    It connects to PostgreSQL and ensures all necessary tables exist.
    """
    async with engine.begin() as conn:
        # We must enable the 'vector' extension in PostgreSQL before creating tables
        # that use the Vector data type (like our DocumentChunk table).
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        # Create all tables defined in our SQLAlchemy models
        await conn.run_sync(Base.metadata.create_all)

        # Add generated column and GIN index for TSVector full text search
        await conn.execute(
            text("""
            ALTER TABLE document_chunks 
            ADD COLUMN IF NOT EXISTS tsv_content TSVECTOR 
            GENERATED ALWAYS AS (to_tsvector('english', content)) STORED
        """)
        )
        await conn.execute(
            text("""
            CREATE INDEX IF NOT EXISTS idx_chunks_tsv ON document_chunks USING GIN(tsv_content)
        """)
        )
        try:
            await conn.execute(
                text("""
                CREATE INDEX IF NOT EXISTS idx_chunks_vec 
                ON document_chunks 
                USING ivfflat (embedding vector_cosine_ops) 
                WITH (lists = 100)
            """)
            )
        except Exception as e:
            print(f"pgvector IVFFlat index notice: {e}")

    print("Database startup complete: Tables verified.")

    # Create Neo4j indexes for the new GraphRAG schema
    try:
        neo4j_driver = get_neo4j()
        with neo4j_driver.session() as _ns:
            _ns.run(
                "CREATE INDEX course_idx IF NOT EXISTS FOR (c:Course) ON (c.code, c.dept, c.year)"
            )
            _ns.run(
                "CREATE INDEX co_idx IF NOT EXISTS FOR (co:CourseOutcome) ON (co.id, co.course_code)"
            )
            _ns.run(
                "CREATE INDEX sv_idx IF NOT EXISTS FOR (sv:SyllabusVersion) ON (sv.dept, sv.year)"
            )
            _ns.run(
                "CREATE INDEX page_idx IF NOT EXISTS FOR (p:Page) ON (p.page_num, p.syllabus_version_id)"
            )
            _ns.run(
                "CREATE INDEX dept_idx IF NOT EXISTS FOR (d:Department) ON (d.name)"
            )
            _ns.run(
                "CREATE INDEX question_id_idx IF NOT EXISTS FOR (q:Question) ON (q.id)"
            )
            _ns.run(
                "CREATE INDEX question_qid_idx IF NOT EXISTS FOR (q:Question) ON (q.q_id)"
            )
            _ns.run(
                "CREATE INDEX bl_idx IF NOT EXISTS FOR (bl:BloomLevel) ON (bl.level)"
            )
            _ns.run(
                "CREATE INDEX va_url_idx IF NOT EXISTS FOR (va:VisualArtefact) ON (va.url)"
            )
            _ns.run(
                "CREATE INDEX va_hash_idx IF NOT EXISTS FOR (va:VisualArtefact) ON (va.hash)"
            )
            _ns.run(
                "CREATE INDEX topic_id_idx IF NOT EXISTS FOR (t:Topic) ON (t.topic_id)"
            )
            _ns.run(
                "CREATE FULLTEXT INDEX course_name_ft IF NOT EXISTS FOR (n:Course) ON EACH [n.name, n.code, n.title]"
            )
        print("Neo4j indexes created/verified.")
    except Exception as e:
        print(f"Neo4j index setup warning: {e}")

    # Seed Neo4j prerequisite graph (legacy PYQ graph)
    try:
        add_prerequisite_edges(neo4j_driver, PREREQUISITE_MAP)
        print("Neo4j Prerequisite Graph seeded.")
    except Exception as e:
        print(f"Failed to seed prereqs: {e}")

    # Warm up semantic intent router embeddings
    try:
        from intent_router import intent_router as _intent_router
        await _intent_router.initialize()
        print("Intent router warmed up.")
    except Exception as e:
        print(f"Intent router warmup warning: {e}")

    yield


app = FastAPI(
    title="M.A.C.H. - Multimodel Academic Cognitive Hub",
    description="A Fine-tuned Graph-RAG framework for Campus Knowledge Systems (MACH AI)",
    lifespan=lifespan,
)

from fastapi.middleware.gzip import GZipMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from config import settings

# Initialize Rate Limiter
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Add GZip compression for large JSON payloads
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Configure Cross-Origin Resource Sharing (CORS).
# This allows our local HTML files (opened directly in the browser) to send requests to this server.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow requests from any origin
    allow_credentials=False,  # False because we use Bearer tokens, not cookies
    allow_methods=["*"],  # Allow all HTTP methods (GET, POST, DELETE, etc.)
    allow_headers=["*"],  # Allow all HTTP headers
    expose_headers=["X-Response-Type", "X-Message-ID"],  # Allow frontend to read custom headers
)


@app.middleware("http")
async def custom_logging(request: Request, call_next):
    response = await call_next(request)
    if response.status_code != 304:
        # Replicate Uvicorn's default access log format, but ignore 304 Not Modified
        try:
            phrase = http.HTTPStatus(response.status_code).phrase
        except ValueError:
            phrase = ""
        client_host = request.client.host if request.client else "127.0.0.1"
        client_port = request.client.port if request.client else 0
        http_version = request.scope.get("http_version", "1.1")
        print(
            f'INFO:     {client_host}:{client_port} - "{request.method} {request.url.path} HTTP/{http_version}" {response.status_code} {phrase}'
        )
    return response


# Mount the static directory so the frontend can retrieve images
os.makedirs("uploads/images", exist_ok=True)
app.mount("/static", StaticFiles(directory="uploads"), name="static")

# Serve the frontend at /public/
# The frontend directory lives one level up from backend/
_frontend_dir = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.isdir(_frontend_dir):
    app.mount(
        "/public", StaticFiles(directory=_frontend_dir, html=True), name="frontend"
    )


# Route: GET / — Convenience redirect to the frontend login page


@app.get("/")
async def root_redirect():
    return RedirectResponse(url="/public/index.html")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    # Attempt to resolve the frontend favicon path
    favicon_path = os.path.join(
        os.path.dirname(__file__), "..", "frontend", "favicon.ico"
    )
    if os.path.exists(favicon_path):
        return FileResponse(favicon_path)
    return Response(status_code=204)


# Auth Configuration

_JWT_SECRET = settings.jwt_secret
_JWT_ALGORITHM = settings.jwt_algorithm
_JWT_EXPIRE = settings.jwt_expire_minutes

# Demo credentials — matches the hints shown on the login page
_USERS = {
    "student": {"password": "student123", "role": "student"},
    "admin": {"password": "admin123", "role": "admin"},
}


def _create_token(username: str, role: str) -> str:
    payload = {
        "sub": username,
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=_JWT_EXPIRE),
    }
    return jwt.encode(payload, _JWT_SECRET, algorithm=_JWT_ALGORITHM)


# Route: POST /login — JWT Authentication

from fastapi import Form as FastForm


@app.post("/login")
async def login(username: str = FastForm(...), password: str = FastForm(...)):
    """
    Validates credentials against the hardcoded demo user dict.
    Returns a signed JWT access token on success.
    The frontend decodes the JWT to extract the user's role.
    """
    user = _USERS.get(username.lower())
    if not user or user["password"] != password:
        raise HTTPException(status_code=401, detail="Incorrect username or password.")

    token = _create_token(username.lower(), user["role"])
    return {"access_token": token, "token_type": "bearer"}


# Route: POST /feedback/{message_id} — Student Thumbs Up/Down


class FeedbackRequest(BaseModel):
    score: int  # 1 = helpful, -1 = not helpful


@app.post("/feedback/{message_id}")
async def submit_feedback(message_id: str, body: FeedbackRequest):
    """
    Receives thumbs-up/down feedback from the student chat UI.
    Logs it for now; can be persisted to a DB table in future.
    """
    print(f"[Feedback] message_id={message_id}  score={body.score}")
    return {
        "message": "Feedback recorded",
        "message_id": message_id,
        "score": body.score,
    }


# Route: /chat — Handles Student Questions

import httpx

from utils import OLLAMA_BASE_URL, OLLAMA_MODEL





def execute_graph_prereq_query(neo4j_driver, question: str) -> list:
    """Extracts course name/code from question and finds multi-hop prerequisites via graph traversal."""
    import re
    q_lower = question.lower()
    code_match = re.search(r'\b\d{0,2}[a-z]{3}\d{3}\b', q_lower)
    course_code = code_match.group(0).upper() if code_match else None

    STOP = {
        "what", "all", "are", "the", "prerequisite", "prerequisites", "prereq", "prereqs",
        "for", "course", "courses", "subject", "subjects", "should", "i", "have", "done",
        "before", "choosing", "taking", "know", "needed", "to", "study", "enroll", "in",
        "is", "a", "an", "tell", "me", "which", "do", "get", "show", "list", "require", "requires",
        "pre-requisite", "pre-requisites"
    }
    clean_words = [
        w.strip(".,!?'\"") for w in q_lower.split()
        if w.strip(".,!?'\"") and w.strip(".,!?'\"") not in STOP
    ]
    phrase = " ".join(clean_words).strip()

    with neo4j_driver.session() as session:
        records = session.run(
            """
            MATCH (target:Course)
            WHERE ($code IS NOT NULL AND toUpper(target.code) CONTAINS $code)
               OR ($phrase <> '' AND toLower(coalesce(target.name, target.title, '')) CONTAINS toLower($phrase))
               OR ($phrase <> '' AND toLower(target.code) CONTAINS toLower($phrase))
               OR (size($words) > 0 AND all(w IN $words WHERE toLower(coalesce(target.name, target.title, '')) CONTAINS w OR toLower(target.code) CONTAINS w))
               OR (size($words) > 0 AND any(w IN $words WHERE toLower(coalesce(target.name, target.title, '')) CONTAINS w))
            MATCH path = (target)-[:REQUIRES|REQUIRES_PREREQUISITE*1..5]->(prereq:Course)
            WHERE target <> prereq
            RETURN coalesce(target.name, target.title, target.code) AS course,
                   target.code AS course_code,
                   collect(DISTINCT coalesce(prereq.name, prereq.title, prereq.code)) AS all_prerequisites
            LIMIT 10
            """,
            code=course_code,
            phrase=phrase,
            words=clean_words,
        ).data()

    # Deterministic fallback from official PREREQUISITE_MAP & COURSE_METADATA if graph is empty
    if not records:
        from utils import COURSE_METADATA, PREREQUISITE_MAP
        target_code = course_code
        if not target_code:
            for c_code, c_name in COURSE_METADATA.items():
                if phrase and phrase in c_name.lower():
                    target_code = c_code
                    break
                if clean_words and all(w in c_name.lower() for w in clean_words):
                    target_code = c_code
                    break
        if target_code:
            seen = set()
            def get_prereqs(code):
                deps = PREREQUISITE_MAP.get(code, [])
                for d in deps:
                    if d not in seen:
                        seen.add(d)
                        get_prereqs(d)
            get_prereqs(target_code)
            prereq_names = [COURSE_METADATA.get(c, c) for c in seen]
            target_name = COURSE_METADATA.get(target_code, target_code)
            records = [{
                "course": target_name,
                "course_code": target_code,
                "all_prerequisites": prereq_names,
            }]

    return records


def execute_graph_co_query(neo4j_driver, question: str) -> list:
    """Extracts Course Outcome (CO) or Bloom's Taxonomy Level (BTL) and queries Neo4j."""
    import re
    q_lower = question.lower()
    co_match = re.search(r"co\d+", q_lower)
    co_id = co_match.group(0).upper() if co_match else None

    btl_match = re.search(r"\b(l[1-6]|btl[1-6]|bt\s*[-–]?[1-6]|level\s*[1-6]|k[1-6])\b", q_lower)
    btl_digit = None
    if btl_match:
        m = re.search(r"[1-6]", btl_match.group(0))
        if m:
            btl_digit = m.group(0)

    # Extract optional course filter (e.g. "of data structures", "from os")
    course_keywords = []
    course_match = re.search(r"(?:of|from|in|for)\s+([a-zA-Z0-9\s]+?)(?:\s+course|\s+subject|\?|$)", q_lower)
    if course_match:
        c_phrase = course_match.group(1).strip()
        stop_c = {"the", "a", "an", "all", "me", "get", "show", "questions", "problems", "pyqs"}
        course_keywords = [w for w in c_phrase.split() if w not in stop_c and len(w) > 1]

    with neo4j_driver.session() as session:
        records = session.run(
            """
            MATCH (q:Question)
            OPTIONAL MATCH (q)-[:BELONGS_TO]->(c:Course)
            OPTIONAL MATCH (c_t:Course)-[:TESTED_BY]->(q)
            WITH q, coalesce(c, c_t) AS c
            WHERE ($co_id IS NULL OR EXISTS {
                MATCH (q)-[:MAPPED_TO_CO]->(co:CourseOutcome)
                WHERE toUpper(co.id) = $co_id
            })
            AND ($btl_digit IS NULL OR (
                toUpper(coalesce(q.btl, '')) CONTAINS $btl_digit
                OR EXISTS { MATCH (q)-[:HAS_BTL]->(bl:BloomLevel) WHERE toUpper(bl.level) CONTAINS $btl_digit }
            ))
            OPTIONAL MATCH (q)-[:MAPPED_TO_CO]->(co:CourseOutcome)
            WITH q, c, co,
                 CASE WHEN size($course_words) > 0 AND any(w IN $course_words WHERE toLower(coalesce(c.name, c.title, c.code, '')) CONTAINS w) THEN 1 ELSE 0 END AS course_match
            RETURN q.id AS id, coalesce(q.q_id, q.id) AS q_id, q.text AS question, q.btl AS btl, q.marks AS marks, 
                   q.has_figure AS has_figure, q.image_url AS image_url,
                   coalesce(c.code, q.course_code, co.course_code, '') AS course_code,
                   coalesce(co.id, '') AS co_id
            ORDER BY course_match DESC
            LIMIT 25
            """,
            co_id=co_id,
            btl_digit=btl_digit,
            course_words=course_keywords,
        ).data()

    return records


def execute_graph_syllabus_query(neo4j_driver, question: str) -> dict:
    """
    Returns structured syllabus query result:
      {"match_type": "exact",            "records": [...]}
      {"match_type": "similar_list",     "courses": [...]}
      {"match_type": "semester_courses", "semester": n, "courses": [...]}
      {"match_type": "elective_courses", "stream": s,  "courses": [...]}
      {"match_type": "course_detail",    "courses": [...]}
      {"match_type": "none",             "records": []}
    """
    import re
    q_lower = question.lower()

    # 1. Check for Semester queries e.g. "what courses in 3rd semester", "sem 4 courses"
    sem_match = re.search(r'\b(?:sem(?:ester)?\s*(\d+)|(\d+)(?:st|nd|rd|th)?\s*sem(?:ester)?)\b', q_lower)
    if sem_match:
        sem_num_str = sem_match.group(1) or sem_match.group(2)
        try:
            sem_num = int(sem_num_str)
            with neo4j_driver.session() as session:
                sem_res = session.run("""
                    MATCH (c:Course)
                    WHERE c.semester = $sem_num
                       OR EXISTS { MATCH (sem:Semester {number: $sem_num})-[:OFFERS]->(c) }
                    RETURN c.code AS course_code, coalesce(c.name, c.title, c.code) AS course_name,
                           c.credits AS credits, c.category AS category, c.L AS L, c.T AS T, c.P AS P
                    ORDER BY c.code ASC
                """, sem_num=sem_num).data()

                # Curated Amrita CSE B.Tech semester courses fallback
                if not sem_res:
                    sem_course_map = {
                        1: [("23MAT101", "Calculus"), ("23CSE101", "Computational Problem Solving"), ("23PHY101", "Engineering Physics")],
                        2: [("23MAT117", "Linear Algebra"), ("23CSE111", "Object Oriented Programming"), ("23EEE104", "Electric Circuits")],
                        3: [("23MAT116", "Discrete Mathematics"), ("23CSE201", "Procedural Programming Using C"), ("23CSE202", "Database Management Systems"), ("23CSE203", "Data Structures and Algorithms"), ("23CSE205", "Digital Electronics")],
                        4: [("23MAT216", "Probability and Random Processes"), ("23CSE211", "Design and Analysis of Algorithms"), ("23CSE212", "Principles of Functional Languages"), ("23CSE213", "Computer Organization and Architecture"), ("23CSE214", "Operating Systems")],
                        5: [("23CSE301", "Machine Learning"), ("23CSE302", "Computer Networks"), ("23CSE303", "Theory of Computation"), ("23CSE304", "Embedded Systems")],
                        6: [("23CSE311", "Software Engineering"), ("23CSE312", "Distributed Systems"), ("23CSE313", "Foundations of Cyber Security"), ("23CSE314", "Compiler Design")],
                    }
                    if sem_num in sem_course_map:
                        sem_res = [{"course_code": code, "course_name": name, "credits": 4, "category": "Core", "L": 3, "T": 0, "P": 2} for code, name in sem_course_map[sem_num]]

                if sem_res:
                    return {"match_type": "semester_courses", "semester": sem_num, "courses": sem_res}
        except Exception as e:
            print(f"Semester query error: {e}")

    # 2. Check for Elective / Basket queries
    if any(k in q_lower for k in ["elective", "electives", "basket", "baskets", "stream"]):
        stream_words = [
            w.strip(".,!?'\"")
            for w in q_lower.split()
            if w.strip(".,!?'\"") not in [
                "what", "all", "electives", "elective", "do", "i", "have", "under",
                "stream", "courses", "subjects", "in", "are", "the", "me", "show",
                "list", "tell", "give", "for", "of", "basket", "baskets"
            ]
        ]
        stream_hint = " ".join([w for w in stream_words if w]).strip()
        if stream_hint.lower() in ["ai", "a.i.", "ai stream", "ai basket"]:
            stream_hint = "artificial intelligence"
        with neo4j_driver.session() as session:
            eb_res = session.run("""
                MATCH (eb:ElectiveBasket)-[:CONTAINS]->(c:Course)
                WHERE $hint = ''
                   OR toLower(eb.name) CONTAINS toLower($hint)
                   OR toLower(coalesce(c.name, c.title, '')) CONTAINS toLower($hint)
                RETURN eb.name AS basket_name,
                       c.code AS course_code,
                       coalesce(nullif(c.title, ''), nullif(c.name, ''), c.code) AS course_name,
                       c.credits AS credits
                ORDER BY basket_name, course_code
                LIMIT 50
            """, hint=stream_hint).data()

            if not eb_res:
                eb_res = session.run("""
                    MATCH (c:Course)
                    WHERE c.elective_basket IS NOT NULL
                      AND ($hint = '' OR toLower(c.elective_basket) CONTAINS toLower($hint)
                           OR toLower(coalesce(c.name, c.title, '')) CONTAINS toLower($hint))
                    RETURN c.elective_basket AS basket_name,
                           c.code AS course_code,
                           coalesce(nullif(c.title, ''), nullif(c.name, ''), c.code) AS course_name,
                           c.credits AS credits
                    ORDER BY basket_name, course_code
                    LIMIT 50
                """, hint=stream_hint).data()

            # Deterministic elective mapping fallback
            if not eb_res:
                from utils import COURSE_METADATA
                filtered = []
                for code, name in COURSE_METADATA.items():
                    if any(tag in code for tag in ["CSE34", "CSE35", "CSE36", "CSE4", "AIE"]):
                        if not stream_hint or stream_hint in name.lower() or ("ai" in stream_hint and any(a in name.lower() for a in ["learning", "intelligence", "data", "mining", "nlp"])):
                            filtered.append({
                                "basket_name": f"{stream_hint.upper() if stream_hint else 'Elective'} Stream",
                                "course_code": code,
                                "course_name": name,
                                "credits": 3,
                            })
                if filtered:
                    return {"match_type": "elective_courses", "stream": stream_hint or "Electives", "courses": filtered[:12]}

            if eb_res:
                return {"match_type": "elective_courses", "stream": stream_hint or "All Electives", "courses": eb_res}

    # 3. Check for specific Course Attribute queries (evaluation pattern, textbooks, references, course code, credits)
    is_attr_query = any(k in q_lower for k in [
        "evaluation pattern", "assessment pattern", "textbook", "textbooks", "reference", "references", "reference book", "course code", "credits"
    ])

    STOP = {
        "get", "me", "the", "syllabus", "for", "course", "topics", "units", "unit",
        "what", "is", "give", "show", "list", "provide", "all", "of", "about",
        "in", "please", "and", "a", "an", "will", "be", "covered", "pattern", "evaluation",
        "textbook", "textbooks", "references", "reference", "code", "credits", "assessment"
    }
    words = [w.strip(".,!?'\"") for w in q_lower.split()
             if len(w) > 1 and w.lower() not in STOP]
    phrase = " ".join(words).strip()

    with neo4j_driver.session() as session:
        # Check course detail (evaluation, textbooks, code, credits)
        if is_attr_query and (phrase or words):
            detail_res = session.run("""
                MATCH (c:Course)
                WHERE ($phrase <> '' AND (toLower(coalesce(c.name, c.title, '')) CONTAINS toLower($phrase) OR toLower(c.code) CONTAINS toLower($phrase)))
                   OR (size($words) > 0 AND all(w IN $words WHERE toLower(coalesce(c.name, c.title, '')) CONTAINS w OR toLower(c.code) CONTAINS w))
                OPTIONAL MATCH (c)-[:HAS_EVALUATION_PATTERN]->(ep:EvaluationPattern)
                OPTIONAL MATCH (c)-[:HAS_TEXTBOOK|USES_TEXTBOOK]->(tb:Textbook)
                OPTIONAL MATCH (c)-[:HAS_REFERENCE|USES_REFERENCE]->(ref:Reference)
                RETURN c.code AS course_code, coalesce(c.name, c.title, c.code) AS course_name,
                       coalesce(c.evaluation_pattern_ratio, ep.ratio, '50:50') AS evaluation_pattern,
                       c.credits AS credits, c.L AS L, c.T AS T, c.P AS P,
                       collect(DISTINCT coalesce(tb.title, tb.text)) AS textbooks,
                       collect(DISTINCT coalesce(ref.title, ref.text)) AS references
                LIMIT 5
            """, phrase=phrase, words=words).data()

            if not detail_res:
                from utils import COURSE_METADATA
                for code, name in COURSE_METADATA.items():
                    if (phrase and phrase in name.lower()) or (words and all(w in name.lower() for w in words)):
                        detail_res = [{
                            "course_code": code,
                            "course_name": name,
                            "evaluation_pattern": "50:50 (Continuous Internal: 50, End-Semester: 50)",
                            "credits": "4",
                            "L": 3, "T": 0, "P": 2,
                            "textbooks": ["Standard University Textbooks and Reference Materials"],
                            "references": ["Department Library Reference Copies"]
                        }]
                        break

            if detail_res and any(r.get("course_code") for r in detail_res):
                for r in detail_res:
                    r["textbooks"] = [t for t in r.get("textbooks", []) if t]
                    r["references"] = [rf for rf in r.get("references", []) if rf]
                return {"match_type": "course_detail", "courses": detail_res}

        # 4. Exact match on code or full name for units and topics
        if phrase:
            exact = session.run("""
                MATCH (c:Course)
                WHERE toLower(c.code) = toLower($sp) OR toLower(coalesce(c.name, c.title, '')) CONTAINS toLower($sp)
                MATCH (c)-[:HAS_UNIT]->(u:Unit)
                OPTIONAL MATCH (u)-[:HAS_TOPIC]->(t:Topic)
                WITH c, u, t ORDER BY coalesce(t.order, toInteger(last(split(t.topic_id, '_t'))), 0) ASC
                WITH c, u.number AS unit_num,
                     coalesce(u.title, 'Unit ' + toString(u.number)) AS unit_title,
                     [x IN collect(DISTINCT t.name) WHERE x IS NOT NULL] AS topics
                RETURN c.code AS course_code, coalesce(c.name, c.title, c.code) AS course_name,
                       toInteger(unit_num) AS unit_number, unit_title, topics
                ORDER BY unit_number ASC
            """, sp=phrase).data()
            if exact:
                return {"match_type": "exact", "records": exact}

        # 5. Keyword match across courses
        candidates = []
        if words:
            candidates = session.run("""
                MATCH (c:Course)
                WHERE all(word IN $words WHERE toLower(coalesce(c.name, c.title, '')) CONTAINS word
                          OR toLower(c.code) CONTAINS word)
                RETURN c.code AS course_code, coalesce(c.name, c.title, c.code) AS course_name
                ORDER BY size(coalesce(c.name, c.title, '')) ASC LIMIT 10
            """, words=words).data()

        if len(candidates) == 1:
            single = session.run("""
                MATCH (c:Course {code: $c_code})-[:HAS_UNIT]->(u:Unit)
                OPTIONAL MATCH (u)-[:HAS_TOPIC]->(t:Topic)
                WITH c, u, t ORDER BY coalesce(t.order, toInteger(last(split(t.topic_id, '_t'))), 0) ASC
                WITH c, u.number AS unit_num,
                     coalesce(u.title, 'Unit ' + toString(u.number)) AS unit_title,
                     [x IN collect(DISTINCT t.name) WHERE x IS NOT NULL] AS topics
                RETURN c.code AS course_code, coalesce(c.name, c.title, c.code) AS course_name,
                       toInteger(unit_num) AS unit_number, unit_title, topics
                ORDER BY unit_number ASC
            """, c_code=candidates[0]["course_code"]).data()
            if single:
                return {"match_type": "exact", "records": single}

        if len(candidates) > 1:
            return {"match_type": "similar_list", "courses": candidates}

        # 6. Topic-level fallback
        if words:
            topic_records = session.run("""
                MATCH (c:Course)-[:HAS_UNIT]->(u:Unit)-[:HAS_TOPIC]->(t:Topic)
                WHERE all(word IN $words WHERE toLower(t.name) CONTAINS word)
                WITH c, u.number AS unit_num,
                     coalesce(u.title, 'Unit ' + toString(u.number)) AS unit_title,
                     [x IN collect(DISTINCT t.name) WHERE x IS NOT NULL] AS topics
                RETURN c.code AS course_code, coalesce(c.name, c.title, c.code) AS course_name,
                       toInteger(unit_num) AS unit_number, unit_title, topics
                ORDER BY c.code, unit_num ASC LIMIT 15
            """, words=words).data()
            if topic_records:
                return {"match_type": "exact", "records": topic_records}

    return {"match_type": "none", "records": []}


class ChatRequest(BaseModel):
    """Defines the expected JSON structure when a student asks a question."""

    message: str
    session_id: Optional[str] = None
    image_b64: Optional[str] = None


import time

# Simple in-memory cache for recent chat responses
_chat_cache = {}
_CACHE_TTL = 300  # 5 minutes


def _get_cached_response(key):
    if key in _chat_cache:
        val, ts = _chat_cache[key]
        if time.time() - ts < _CACHE_TTL:
            return val
        del _chat_cache[key]
    return None


def _set_cache(key, val):
    _chat_cache[key] = (val, time.time())
    # Evict old entries if cache grows too large
    if len(_chat_cache) > 100:
        oldest = min(_chat_cache, key=lambda k: _chat_cache[k][1])
        del _chat_cache[oldest]


COURSE_ALIASES = {
    "daa": "Design and Analysis of Algorithms",
    "os": "Operating Systems",
    "dsa": "Data Structures and Algorithms",
    "dbms": "Database Management Systems",
    "cn": "Computer Networks",
    "toc": "Theory of Computation",
    "cd": "Compiler Design",
    "ds": "Distributed Systems",
    "dist systems": "Distributed Systems",
    "maths 1": "Calculus",
    "maths 2": "Linear Algebra",
    "maths 3": "Discrete Mathematics",
    "maths 4": "Probability and Random Processes",
    "prp": "Probability and Random Processes",
    "prob": "Probability and Random Processes",
    "probability": "Probability and Random Processes",
    "ml": "Machine Learning",
    "ai": "Artificial Intelligence",
    "crypto": "Foundations of Cyber Security",
    "bee": "Basic Electrical and Electronics Engineering",
    "beee": "Basic Electrical and Electronics Engineering",
    "eee": "Introduction to Electrical and Electronics Engineering",
    "ieee": "Introduction to Electrical and Electronics Engineering",
    "coa": "Computer Organization and Architecture",
    "ca": "Computer Architecture",
    "se": "Software Engineering",
    "oop": "Object Oriented Programming",
}

def extract_topic_from_query(question: str) -> str:
    """
    Extract the topic from the user query.
    Intelligently handles deictic pronouns ('this', 'that', 'it') by resolving
    them against uploaded image context if present.
    """
    import re

    # 1. Inspect image context if available
    img_context_match = re.search(r'\[Uploaded Image Context:\s*(.*?)\]', question, flags=re.DOTALL | re.IGNORECASE)
    img_content_match = re.search(r'^Image Content:\s*(.*)', question, flags=re.DOTALL | re.IGNORECASE)
    image_context_raw = ""
    if img_context_match:
        image_context_raw = img_context_match.group(1).strip()
    elif img_content_match:
        image_context_raw = img_content_match.group(1).strip()

    # Isolate user's conversational text without wrappers
    user_text = re.sub(r'\[Uploaded Image Context:.*?\]', '', question, flags=re.DOTALL | re.IGNORECASE)
    user_text = re.sub(r'^Image Content:.*', '', user_text, flags=re.DOTALL | re.IGNORECASE).strip()
    q_lower = user_text.lower()

    deictic_words = {
        "this", "that", "it", "these", "those", "here", "figure", "fig",
        "diagram", "circuit", "image", "pic", "picture", "schematic", "attachment"
    }

    extracted = ""
    markers = [
        "problems on",
        "problem on",
        "questions on",
        "question on",
        "pyqs on",
        "pyq on",
        "problems about",
        "questions about",
        "questions for",
        "problems for",
        "find questions on",
        "find problems on",
        "pyqs for",
    ]
    for marker in markers:
        if marker in q_lower:
            idx = q_lower.find(marker)
            extracted = user_text[idx + len(marker) :].strip("? .!").strip()
            break

    # If no marker hit, remove stop words
    if not extracted:
        stop_words_for_topic = {
            "get", "me", "a", "the", "all", "questions", "question", "problems",
            "problem", "pyqs", "pyq", "on", "about", "for", "find", "show",
            "list", "give", "related", "are", "there", "any", "is", "what",
            "how", "why", "who", "where", "can", "you", "tell", "explain",
            "describe", "provide", "please", "past", "year", "solve", "practice",
            "paper", "papers", "exam",
        }
        words = user_text.split()
        clean_words = [w for w in words if w.strip(".,!?-'\"").lower() not in stop_words_for_topic]
        if clean_words:
            extracted = " ".join(clean_words).strip("? .!")

    # Check for course aliases (e.g. "DAA" -> "Design and Analysis of Algorithms")
    if extracted.lower() in COURSE_ALIASES:
        return COURSE_ALIASES[extracted.lower()]

    # Check if the extracted topic is deictic (refers to the visual diagram) or empty
    extracted_tokens = set(re.findall(r'\b\w+\b', extracted.lower()))
    is_deictic = not extracted or bool(extracted_tokens & deictic_words)

    if is_deictic:
        if image_context_raw and not image_context_raw.startswith("Error analyzing image:"):
            # Check for JSON "topic" field
            topic_json_m = re.search(r'"topic":\s*"([^"]+)"', image_context_raw, re.IGNORECASE)
            if topic_json_m:
                cand = topic_json_m.group(1).strip()
                if cand and len(cand) > 2:
                    return cand.title()

            # Check for keyword / component / topic pattern
            kw_match = re.search(r'(?:keywords?|components?|concept|topic):\s*(.*)', image_context_raw, re.IGNORECASE)
            if kw_match:
                candidate = kw_match.group(1).split(",")[0].split("\n")[0].strip()
                if candidate and len(candidate) > 2:
                    return candidate.title()

            # Otherwise clean generic thinking/preamble and take the primary term
            clean_img = re.sub(r'(?i)^(thinking process:?|the image shows|this is a|circuit diagram of|core concept:)\s*', '', image_context_raw)
            clean_img = re.sub(r'^\d+[\.\)]\s*\*+[^*]+\*+:?\s*', '', clean_img)
            # Find first meaningful word or phrase
            for part in clean_img.replace("\n", ",").split(","):
                part_clean = re.sub(r'^\d+[\.\)]\s*', '', part.strip(" *#-\t.!"))
                if part_clean and len(part_clean) > 2 and not part_clean.lower().startswith("thinking") and not part_clean.lower().startswith("analyze"):
                    return part_clean.title()

        # If no valid image context and query was literally deictic, return empty to avoid full-database scan
        return ""

    return extracted.title()


# ── Conversational Memory ─────────────────────────────────────────
_session_history: dict = defaultdict(list)
_session_last_active: dict = {}
MAX_HISTORY_TURNS = 6  # last 3 user + 3 assistant turns

def get_session_history(session_id: Optional[str]) -> list:
    if not session_id:
        return []
    now = time.time()
    if session_id in _session_last_active:
        if now - _session_last_active[session_id] > 7200:  # 2-hour expiry
            _session_history[session_id] = []
    _session_last_active[session_id] = now
    return list(_session_history[session_id][-MAX_HISTORY_TURNS:])

def append_session_message(session_id: Optional[str], role: str, content: str):
    if not session_id or not content.strip():
        return
    _session_history[session_id].append({"role": role, "content": content.strip()})
    _session_last_active[session_id] = time.time()
    if len(_session_history[session_id]) > MAX_HISTORY_TURNS:
        _session_history[session_id] = _session_history[session_id][-MAX_HISTORY_TURNS:]


@app.post("/chat")
@limiter.limit("60/minute")
async def chat_endpoint(
    request: Request, payload: ChatRequest, db: AsyncSession = Depends(get_db)
):
    """
    Takes a student's question, uses tiered intent routing, searches
    PostgreSQL/Neo4j for relevant context, and streams the AI response back.
    """
    question = payload.message
    session_id = payload.session_id or "default_session"

    if payload.image_b64:
        print("[CHAT] Image uploaded, analyzing with vision model...")
        b64_data = payload.image_b64
        if "," in b64_data:
            b64_data = b64_data.split(",")[1]
        try:
            image_description = await describe_uploaded_image(b64_data)
            print(f"[CHAT] Image Description: {image_description}")
            if image_description and not image_description.startswith("Error analyzing image:"):
                if not question.strip():
                    question = f"Image Content: {image_description}"
                else:
                    question = f"{question}\n\n[Uploaded Image Context: {image_description}]"
            else:
                print(f"[CHAT] Vision did not produce valid keywords: {image_description}")
        except Exception as e:
            print(f"[CHAT] Failed to analyze image: {e}")

    append_session_message(session_id, "user", question)
    
    history = get_session_history(session_id)
    cache_key = f"{session_id}:{question.strip().lower()}" if history else question.strip().lower()

    # --- Check cache first ---
    cached = _get_cached_response(cache_key)
    if cached:

        async def yield_cached():
            yield cached

        return StreamingResponse(yield_cached(), media_type="text/plain")

    context_parts = []
    search_query = question

    # First, classify intent immediately using fast tier-1 / embedding router (<1ms)
    intent = await classify_query_intent(question)
    print(f"[ROUTER] Intent classified as: {intent}")

    # Zero-LLM Fast-Path: Deterministic graph queries bypass LLM rewriting completely (<=120ms)
    is_deterministic_intent = intent in ("PROBLEM_LIST", "MULTI_HOP_PREREQ", "GRAPH_PYQ_MAPPING", "SIMPLE_CURRICULUM")

    # LLM query rewriting: only consider for open-ended queries with conversation history and vague pronouns
    if not is_deterministic_intent and history and len(history) > 2:
        vague_markers = ["that", "this", "it", "same", "tell me more", "explain more", "continue", "previous"]
        if any(vm in question.lower() for vm in vague_markers) or len(question.split()) <= 3:
            try:
                hist_str = "\n".join(f"{m['role'].upper()}: {m['content']}" for m in history[-4:])
                rewrite_prompt = (
                    f"Conversation so far:\n{hist_str}\n\n"
                    f"New user message: {question}\n\n"
                    "If this message is a vague follow-up that refers to something from the conversation "
                    "(e.g. 'that topic', 'unit 2 only', 'same course'), rewrite it as a fully self-contained "
                    "academic query. Otherwise return it unchanged. Return ONLY the rewritten query."
                )
                import httpx as _httpx
                async with _httpx.AsyncClient(headers={"ngrok-skip-browser-warning": "true"}) as _c:
                    _r = await _c.post(
                        f"{OLLAMA_BASE_URL}/api/generate",
                        json={"model": OLLAMA_MODEL, "prompt": rewrite_prompt,
                              "stream": False, "options": {"temperature": 0.0}},
                        timeout=5.0,
                    )
                    rewritten = _r.json().get("response", "").strip()
                    if rewritten and rewritten.lower() != question.lower():
                        print(f"[ROUTER] Query rewritten: '{question}' -> '{rewritten}'")
                        search_query = rewritten
                        intent = await classify_query_intent(search_query)
            except Exception as _e:
                print(f"[ROUTER] Rewrite skipped: {_e}")

    neo4j_driver = get_neo4j()

    if intent == "PROBLEM_LIST":
        topic_name = extract_topic_from_query(question)
        import re as _re
        mark_filter = None
        mark_match = _re.search(r'\b(\d+)\s*(?:-| )?marks?\b', question.lower())
        if mark_match:
            try:
                mark_filter = int(mark_match.group(1))
            except Exception:
                mark_filter = None

        graph_problems = await fetch_problems_by_topic_graph(neo4j_driver, topic_name) if topic_name else []
        keyword_problems = fetch_all_problems_by_topic(neo4j_driver, topic_name) if topic_name else []
        print(f"[ROUTER] Graph RAG: {len(graph_problems)}, Keyword: {len(keyword_problems)} for '{topic_name}' (mark_filter={mark_filter})")
        seen_texts, seen_ids, problems = set(), set(), []
        for p in graph_problems + keyword_problems:
            p_id = p.get("id")
            norm = _re.sub(r'\s+', ' ', (p.get("text") or "").strip().lower())
            if norm and norm not in seen_texts and p_id not in seen_ids:
                seen_texts.add(norm)
                if p_id: seen_ids.add(p_id)
                if mark_filter is not None:
                    p_marks = p.get("marks")
                    try:
                        if p_marks is not None and int(p_marks) != mark_filter:
                            continue
                    except Exception:
                        pass
                problems.append(p)

        # Fallback to all problems for this topic if mark filter was too restrictive
        if not problems and mark_filter is not None:
            for p in graph_problems + keyword_problems:
                p_id = p.get("id")
                norm = _re.sub(r'\s+', ' ', (p.get("text") or "").strip().lower())
                if norm and p_id not in seen_ids:
                    seen_ids.add(p_id)
                    problems.append(p)

        # Prioritize questions with figures if an image was uploaded by student
        if payload.image_b64 and problems:
            problems.sort(key=lambda p: (1 if p.get("has_figure") else 0), reverse=True)

        # If no questions found, check if topic/course exists in curriculum graph for guidance
        syllabus_guidance = None
        if not problems and topic_name:
            try:
                with neo4j_driver.session() as s_sess:
                    c_match = s_sess.run("""
                        MATCH (c:Course)
                        WHERE toLower(c.code) = toLower($t)
                           OR toLower(c.name) CONTAINS toLower($t)
                           OR (size($t) > 3 AND toLower($t) CONTAINS toLower(c.code))
                        OPTIONAL MATCH (c)-[:HAS_UNIT]->(u:Unit)
                        OPTIONAL MATCH (u)-[:HAS_TOPIC]->(t:Topic)
                        RETURN c.code AS code, c.name AS name,
                               u.number AS unit_num, u.title AS unit_title,
                               collect(DISTINCT t.name) AS topics
                        ORDER BY u.number
                    """, t=topic_name).data()
                    if c_match:
                        c_code = c_match[0]["code"]
                        c_name = c_match[0]["name"]
                        units_data = []
                        for r in c_match:
                            if r.get("unit_title"):
                                units_data.append({
                                    "unit": r.get("unit_title") or f"Unit {r.get('unit_num')}",
                                    "topics": [t for t in r.get("topics", []) if t][:8]
                                })
                        syllabus_guidance = {
                            "course_code": c_code,
                            "course_name": c_name,
                            "units": units_data
                        }
            except Exception as _e:
                print(f"[PROBLEM_LIST] Syllabus guidance fallback error: {_e}")

        # Add X-Response-Type header so the frontend can detect this is a structured
        # JSON payload (problem card layout) rather than a streaming text response.
        return JSONResponse(
            content={
                "type": "problem_list",
                "topic": topic_name or "Practice Problems",
                "problems": problems,
                "syllabus_guidance": syllabus_guidance,
            },
            headers={"X-Response-Type": "problem_list"},
        )

    elif intent == "MULTI_HOP_PREREQ":
        records = execute_graph_prereq_query(neo4j_driver, question)
        return JSONResponse(
            content={
                "type": "prereq_result",
                "results": records,
                "query": question,
            },
            headers={"X-Response-Type": "prereq_result"},
        )

    elif intent == "GRAPH_PYQ_MAPPING":
        records = execute_graph_co_query(neo4j_driver, question)
        return JSONResponse(
            content={
                "type": "co_mapping",
                "results": records,
                "query": question,
            },
            headers={"X-Response-Type": "co_mapping"},
        )

    elif intent == "SIMPLE_CURRICULUM":
        syllabus_res = execute_graph_syllabus_query(neo4j_driver, search_query)
        match_type = syllabus_res.get("match_type")

        # Zero-LLM Fast-Path: structured Neo4j graph result returned immediately (<120ms)
        if match_type in ("exact", "similar_list", "semester_courses", "elective_courses", "course_detail"):
            return JSONResponse(
                content={
                    "type": "curriculum_result",
                    "data": syllabus_res,
                    "query": question,
                },
                headers={"X-Response-Type": "curriculum_result"},
            )
        else:
            # Fallback to hybrid PostgreSQL vector search + LLM
            question_embedding = await get_embedding(search_query)
            if question_embedding:
                try:
                    chunks = await hybrid_search_rrf(db, search_query, question_embedding, k=5)
                    if chunks:
                        context_parts.append("--- ADDITIONAL TEXT CHUNKS ---")
                        for chunk in chunks:
                            if chunk.get("content"):
                                context_parts.append(chunk["content"])
                except Exception as e:
                    print(f"Hybrid search error: {e}")

    elif intent == "GENERAL_QUERY":
        # Search PostgreSQL via RRF for extra prose chunks to explain the concept
        question_embedding = await get_embedding(question)
        if question_embedding:
            try:
                chunks = await hybrid_search_rrf(db, question, question_embedding, k=5)
                if chunks:
                    context_parts.append("--- REFERENCE MATERIAL ---")
                    for chunk in chunks:
                        content = chunk.get("content", "")
                        if content:
                            context_parts.append(content)
            except Exception as e:
                print(f"Hybrid search error in /chat: {e}")

    else:
        # SIMPLE_PYQ
        neo4j_driver = get_neo4j()
        neo4j_results = execute_neo4j_pyq_search(neo4j_driver, question)

        if neo4j_results:
            context_parts.append("--- NEO4J PYQ SEARCH RESULTS ---")
            for record in neo4j_results:
                img_url = (
                    record["image_url"].replace(" ", "%20")
                    if record.get("image_url")
                    else None
                )
                img_markdown = (
                    f"\n![Diagram for Q{record['q_num']}]({img_url})"
                    if img_url and img_url != "None"
                    else ""
                )
                context_parts.append(
                    f"[Course: {record['course_code']} - Q: {record['q_num']}]\n{record['q_text']}{img_markdown}\nMarks: {record['marks']}\nBTL: {record['btl']}"
                )
        else:
            question_embedding = await get_embedding(question)
            if question_embedding:
                try:
                    chunks = await hybrid_search_rrf(
                        db, question, question_embedding, k=5
                    )
                    if chunks:
                        context_parts.append(
                            "--- HYBRID SEARCH RESULTS (TEXT + SEMANTIC) ---"
                        )
                        for chunk in chunks:
                            content = chunk.get("content", "")
                            if content:
                                context_parts.append(content)
                except Exception as e:
                    print(f"Hybrid search error in /chat: {e}")

    # Generate final answer (pruned context budget cap: ~1200 tokens ≈ 4800 chars for edge GPU limits)
    final_context = "\n".join(context_parts)
    if len(final_context) > 4800:
        final_context = final_context[:4800] + "\n...[Context Truncated]..."

    print("DEBUG FINAL CONTEXT:")
    print(final_context)

    # Stream response and cache it
    async def stream_and_cache():
        full_response = []
        async for chunk in generate_answer_stream(question, final_context):
            full_response.append(chunk)
            yield chunk

        # Append images at the end to guarantee they render, since the LLM often drops them
        import re

        images = re.findall(r"!\[.*?\]\(.*?\)", final_context)
        if images:
            combined_response_str = "".join(full_response)
            unique_images = []
            for img in images:
                # Extract URL from the markdown tag e.g. ![...](url)
                url_match = re.search(r"\((https?://.*?)\)", img)
                if url_match:
                    url = url_match.group(1)
                    if url not in combined_response_str:
                        unique_images.append(img)
                else:
                    if img not in combined_response_str:
                        unique_images.append(img)

            if unique_images:
                image_block = "\n\n### Diagrams from Questions:\n" + "\n\n".join(
                    unique_images
                )
                full_response.append(image_block)
                yield image_block

        response_str = "".join(full_response)
        _set_cache(cache_key, response_str)
        append_session_message(session_id, "assistant", response_str)

    return StreamingResponse(stream_and_cache(), media_type="text/plain")


# Route: /upload — Handles Admin PDF Uploads (Single or Bulk, with Auto-Classification)


@app.post("/upload")
async def upload_document(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    doc_type: str | None = Form(None),
    department: str | None = Form(None),
    dept: str | None = Form(None),
    year: str | None = Form(None),
    course_code: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
):
    """
    Accepts single or multiple PDF documents uploaded by the admin.
    If `doc_type` is omitted or set to 'auto', uses the intelligent classifier
    to automatically determine whether each document is a Syllabus or PYQ,
    extracting the department/year or course code automatically.
    """
    os.makedirs("uploads", exist_ok=True)
    department = department or dept

    uploaded_items = []

    for file in files:
        file_path = f"uploads/{uuid.uuid4()}_{file.filename}"
        with open(file_path, "wb") as f:
            content = await file.read()
            f.write(content)

        # Run auto-classification if doc_type is auto/not provided or to fill missing fields
        meta = classify_pdf_type(file_path)
        
        # Determine effective document type
        effective_doc_type = doc_type if doc_type in ("syllabus", "pyq") else meta.get("doc_type", "pyq")
        
        # Determine effective fields
        if effective_doc_type == "syllabus":
            eff_dept = department or meta.get("department", "CSE")
            eff_year = year or meta.get("year", "2023")
            eff_code = None
        else:
            eff_code = course_code or meta.get("course_code", "UNKNOWN")
            eff_dept = None
            eff_year = None

        new_doc = Document(
            filename=file.filename,
            doc_type=effective_doc_type,
            department=eff_dept,
            year=eff_year,
            course_code=eff_code,
        )
        db.add(new_doc)
        await db.commit()
        await db.refresh(new_doc)

        # Dispatch background task
        if effective_doc_type == "syllabus":
            background_tasks.add_task(
                process_syllabus_background, file_path, eff_dept, eff_year, new_doc.id
            )
        elif effective_doc_type == "pyq":
            background_tasks.add_task(
                process_pyq_background, file_path, eff_code, new_doc.id
            )

        uploaded_items.append({
            "id": str(new_doc.id),
            "filename": file.filename,
            "doc_type": effective_doc_type,
            "course_code": eff_code or f"{eff_dept} ({eff_year})",
            "status": "processing"
        })

    return {
        "message": f"{len(files)} file(s) uploaded successfully! Ingestion underway.",
        "items": uploaded_items
    }


# Route: /documents — Lists All Uploaded Documents with Status Reports


@app.get("/documents")
async def list_documents(db: AsyncSession = Depends(get_db)):
    """
    Fetches and returns a list of all documents from the PostgreSQL database,
    along with their background processing reports and status.
    """
    try:
        result = await db.execute(select(Document).order_by(Document.created_at.desc()))
        docs = result.scalars().all()

        # Fetch processing reports for status indicators
        rep_result = await db.execute(select(ProcessingReport))
        reports_by_doc = {}
        for r in rep_result.scalars().all():
            try:
                reports_by_doc[str(r.document_id)] = json.loads(r.report_json)
            except Exception:
                reports_by_doc[str(r.document_id)] = {"status": "completed"}

        formatted = []
        for doc in docs:
            d_id = str(doc.id)
            rep = reports_by_doc.get(d_id)
            status_text = "completed" if rep else "processing"
            details = None
            if rep:
                st = rep.get("status", "")
                if "error" in st:
                    status_text = "error"
                elif "success" in st:
                    status_text = "completed"
                num_q = rep.get("structured", 0) or rep.get("vision", 0)
                if num_q:
                    details = f"{num_q} questions mapped"
                elif rep.get("summary"):
                    details = "Graph indexed"

            formatted.append({
                "id": d_id,
                "filename": doc.filename,
                "doc_type": doc.doc_type,
                "course_code": doc.course_code or doc.department or "—",
                "created_at": doc.created_at.isoformat() if doc.created_at else "",
                "status": status_text,
                "details": details or status_text
            })

        return formatted
    except Exception as e:
        print(f"Error fetching document list: {e}")
        return []


# Route: /processing-status — Live Status for Active Processing Jobs


@app.get("/processing-status")
async def get_processing_status(db: AsyncSession = Depends(get_db)):
    """
    Returns real-time processing status for the most recent 20 documents.
    Used by the admin UI to poll progress during bulk uploads.
    """
    try:
        result = await db.execute(select(Document).order_by(Document.created_at.desc()).limit(20))
        docs = result.scalars().all()

        rep_result = await db.execute(select(ProcessingReport))
        reports_by_doc = {}
        for r in rep_result.scalars().all():
            try:
                reports_by_doc[str(r.document_id)] = json.loads(r.report_json)
            except Exception:
                reports_by_doc[str(r.document_id)] = {"status": "completed"}

        items = []
        for doc in docs:
            d_id = str(doc.id)
            rep = reports_by_doc.get(d_id)
            items.append({
                "id": d_id,
                "filename": doc.filename,
                "doc_type": doc.doc_type,
                "course_code": doc.course_code or doc.department or "—",
                "status": rep.get("status", "processing") if rep else "processing",
                "structured": rep.get("structured", 0) if rep else 0,
                "created_at": doc.created_at.isoformat() if doc.created_at else ""
            })
        return items
    except Exception as e:
        print(f"Error fetching processing status: {e}")
        return []


# Route: /documents/{id} — Deletes a Specific Document


@app.delete("/documents/{doc_id}")
async def delete_document(doc_id: str, db: AsyncSession = Depends(get_db)):
    """
    Permanently deletes a document and all of its associated text chunks
    from PostgreSQL, Neo4j, and the local file system.
    """
    try:
        # 1. Fetch document metadata first
        doc_uuid = uuid.UUID(doc_id) if isinstance(doc_id, str) else doc_id
        result = await db.execute(select(Document).where(Document.id == doc_uuid))
        doc = result.scalar_one_or_none()

        if doc:
            c_code = doc.course_code.upper() if doc.course_code else None

            # 2. Delete from Neo4j Graph Database
            try:
                neo4j_driver = get_neo4j()
                with neo4j_driver.session() as session:
                    if doc.doc_type == "pyq":
                        # Delete Question nodes tagged with this document_id
                        session.run(
                            """
                            MATCH (q:Question)
                            WHERE q.document_id = $doc_id
                            DETACH DELETE q
                        """,
                            doc_id=str(doc_id),
                        )

                        # Delete orphan QuestionModel nodes
                        session.run("""
                            MATCH (qm:QuestionModel)
                            WHERE NOT (qm)-[:HAS_QUESTION]->()
                            DETACH DELETE qm
                        """)

                        # Delete orphan Course nodes if they have no units and no question models
                        session.run("""
                            MATCH (c:Course)
                            WHERE NOT (c)-[:HAS_UNIT]->() AND NOT (c)-[:HAS_QUESTION_MODEL]->()
                            DETACH DELETE c
                        """)

                    elif doc.doc_type == "syllabus":
                        if c_code:
                            session.run(
                                """
                                MATCH (c:Course {code: $c_code})
                                OPTIONAL MATCH (c)-[:HAS_UNIT]->(u:Unit)
                                OPTIONAL MATCH (u)-[:HAS_TOPIC]->(t:Topic)
                                DETACH DELETE t, u
                            """,
                                c_code=c_code,
                            )
                            session.run(
                                """
                                MATCH (c:Course {code: $c_code})
                                WHERE NOT (c)-[:HAS_UNIT]->() AND NOT (c)-[:HAS_QUESTION_MODEL]->()
                                DETACH DELETE c
                            """,
                                c_code=c_code,
                            )
            except Exception as graph_err:
                print(f"Error cleaning up Neo4j for document {doc_id}: {graph_err}")

            # 3. Clean up physical image files from disk and Cloudinary
            try:
                # Cloudinary delete
                delete_document_images_from_cloudinary(str(doc_id))

                images_dir = "uploads/images"
                if os.path.exists(images_dir):
                    for fname in os.listdir(images_dir):
                        if str(doc_id) in fname:
                            os.remove(os.path.join(images_dir, fname))
            except Exception as fs_err:
                print(f"Error deleting physical files for document {doc_id}: {fs_err}")

        # 4. Delete child chunks and parent document from PostgreSQL
        await db.execute(
            text("DELETE FROM document_chunks WHERE document_id = :id"), {"id": doc_id}
        )
        await db.execute(text("DELETE FROM documents WHERE id = :id"), {"id": doc_id})

        await db.commit()
        return {
            "message": "Document and its associated graph nodes deleted successfully."
        }
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


# Route: /reset — Hard Resets the Entire System


@app.post("/reset")
async def reset_system(db: AsyncSession = Depends(get_db)):
    """
    DANGER ZONE: This endpoint completely wipes all data from PostgreSQL, Neo4j,
    and deletes all uploaded files from the disk. Used to start totally fresh.
    """
    errors = []

    # 1. Wipe PostgreSQL (Delete all chunks and documents)
    try:
        await db.execute(text("DELETE FROM document_chunks"))
        await db.execute(text("DELETE FROM documents"))
        await db.commit()
        print("System Reset: PostgreSQL wiped.")
    except Exception as e:
        await db.rollback()
        errors.append(f"PostgreSQL error: {e}")

    # 2. Wipe Neo4j (Delete all nodes and relationships in the graph)
    try:
        neo4j_driver = get_neo4j()
        with neo4j_driver.session() as session:
            # Cypher command to find all nodes and detach/delete them
            session.run("MATCH (n) DETACH DELETE n")
        print("System Reset: Neo4j wiped.")
    except Exception as e:
        errors.append(f"Neo4j error: {e}")

    # 3. Wipe Disk and Cloudinary (Delete local uploads and wipe Cloudinary bucket)
    try:
        delete_all_images_from_cloudinary()

        uploads_dir = "uploads"
        if os.path.exists(uploads_dir):
            shutil.rmtree(uploads_dir)
            os.makedirs(uploads_dir)
        print("System Reset: Uploads folder cleared.")
    except Exception as e:
        errors.append(f"File system / Cloudinary error: {e}")

    # If any step failed, return a 500 error outlining what went wrong
    if errors:
        raise HTTPException(status_code=500, detail="; ".join(errors))

    return {"message": "System reset complete. All data has been wiped."}


# Route: /image-query — Student Uploads an Image for Reverse Lookup


@app.post("/image-query")
async def image_query_endpoint(
    file: UploadFile = File(...), db: AsyncSession = Depends(get_db)
):
    """
    Accepts an image (photo of an equation, formula, circuit diagram, etc.) from
    a student. The vision model describes the image, and we use that description
    to search both PostgreSQL and Neo4j for related curriculum content.
    """
    # Read the uploaded image and convert it to base64
    image_bytes = await file.read()
    b64_image = base64.b64encode(image_bytes).decode("utf-8")

    # Step 1: Ask the vision model to describe what is in the image
    image_description = await describe_uploaded_image(b64_image)

    # Step 2: Use the description as a search query (same hybrid search as /chat)
    context_parts = []

    # --- Vector Search in PostgreSQL ---
    query_embedding = await get_embedding(image_description)
    if query_embedding:
        try:
            query = (
                select(DocumentChunk)
                .order_by(DocumentChunk.embedding.cosine_distance(query_embedding))
                .limit(10)
            )
            result = await db.execute(query)
            similar_chunks = result.scalars().all()

            if similar_chunks:
                context_parts.append("--- RELEVANT TEXT FROM DOCUMENTS ---")
                for chunk in similar_chunks:
                    context_parts.append(chunk.content)
        except Exception as e:
            print(f"Image query vector search error: {e}")

    # --- Graph Search in Neo4j ---
    try:
        neo4j_driver = get_neo4j()
        words = image_description.lower().split()

        with neo4j_driver.session() as session:
            # Query the graph: Find any nodes where the name or text matches the student's question words.
            # We search across Topics, SubTopics, QuestionModels, and Questions.
            records = session.run(
                """
                MATCH (n)
                WHERE (n:Topic OR n:SubTopic OR n:QuestionModel OR n:Question)
                AND any(word IN $words WHERE toLower(n.name) CONTAINS word OR toLower(n.text) CONTAINS word OR toLower(n.implicit_formulas) CONTAINS word)
                
                // If it's a question, find its model and course
                OPTIONAL MATCH (c:Course)-[:HAS_QUESTION_MODEL]->(qm:QuestionModel)-[:HAS_QUESTION]->(n:Question)
                
                // If it's a topic, find its course
                OPTIONAL MATCH (c2:Course)-[:HAS_UNIT]->(:Unit)-[:HAS_TOPIC]->(n:Topic)
                
                RETURN labels(n) as labels, n.name as name, n.text as text, n.implicit_formulas as formulas,
                       coalesce(c.code, c2.code) as course_code
                LIMIT 5
                """,
                words=words,
            )

            graph_facts = []
            for r in records:
                labels = r["labels"]
                if "Question" in labels:
                    fact = f"[Course {r['course_code']} - PYQ] {r['text']}"
                    if r["formulas"]:
                        fact += f" (Formulas: {r['formulas']})"
                    graph_facts.append(fact)
                else:
                    graph_facts.append(
                        f"[Course {r['course_code']} - {labels[0]}] {r['name']}"
                    )

            if graph_facts:
                context_parts.append("--- KNOWLEDGE GRAPH FACTS ---")
                context_parts.extend(graph_facts)
    except Exception as e:
        print(f"Image query graph search error: {e}")

    # Step 3: Generate the final answer using all the context
    final_context = "\n".join(context_parts)

    prompt_question = (
        f"A student uploaded an image. Here is what the image contains:\n\n"
        f"{image_description}\n\n"
        f"Based on the curriculum context provided, explain where this concept appears "
        f"in the course materials, what topics it relates to, and what kinds of questions "
        f"or problems typically involve this formula, equation, or diagram."
    )

    ai_response = await generate_answer(prompt_question, final_context)

    return {"description": image_description, "response": ai_response}


# Background Task Logic


async def process_syllabus_background(
    file_path: str, department: str, year: str, document_id: uuid.UUID
):
    import json
    report = {"type": "syllabus", "status": "started", "file": file_path}
    """
    Campus-wide GraphRAG syllabus ingestion using the new SyllabusParser.
    Builds a full dual-layer Neo4j graph (semantic + source/document).
    Supports any department (CSE, ECE, EEE, MECH, ...) and curriculum year.
    """
    from syllabus_parser import SyllabusParser
    from utils import PREREQUISITE_MAP, add_prerequisite_edges

    print(f"[Syllabus] Starting GraphRAG ingestion for {department} {year}...")
    neo4j_driver = get_neo4j()

    try:
        parser = SyllabusParser(neo4j_driver)
        # parse_and_ingest is synchronous (CPU-bound regex + Neo4j writes);
        # run it in a thread executor to avoid blocking the event loop.
        import asyncio

        loop = asyncio.get_event_loop()
        summary = await loop.run_in_executor(
            None, parser.parse_and_ingest, file_path, department, year
        )
        print(f"[Syllabus] Ingestion complete: {summary}")

        # Seed prerequisite DAG from official Amrita CSE curriculum map
        try:
            add_prerequisite_edges(neo4j_driver, PREREQUISITE_MAP)
            print("[Syllabus] Prerequisite graph reinforced.")
        except Exception as _pe:
            print(f"[Syllabus] Prereq seeding notice: {_pe}")

        # Store syllabus text chunks in PostgreSQL DocumentChunk for pgvector RRF search
        try:
            import pymupdf as _fitz
            _doc = _fitz.open(file_path)
            async for db in get_db():
                for p_idx in range(min(len(_doc), 80)):
                    txt = _doc[p_idx].get_text().strip()
                    if len(txt) > 60:
                        chunk_content = f"[Syllabus - {department} {year} - Page {p_idx+1}]\n{txt[:1500]}"
                        emb = await get_embedding(chunk_content[:1000])
                        if emb:
                            db.add(DocumentChunk(
                                document_id=document_id,
                                content=chunk_content,
                                course_code=f"{department}_{year}",
                                content_type="syllabus",
                                embedding=emb,
                            ))
                await db.commit()
                break
            _doc.close()
            print("[Syllabus] Stored text chunks in pgvector.")
        except Exception as _ce:
            print(f"[Syllabus] Document chunk storage notice: {_ce}")

        report["status"] = "success"
        report["summary"] = summary
        async for db in get_db():
            db.add(ProcessingReport(document_id=document_id, course_code="SYLLABUS", report_json=json.dumps(report)))
            await db.commit()
            break
    except Exception as e:
        import traceback

        print(f"[Syllabus] ERROR during ingestion of {department} {year}: {e}")
        report["status"] = "error"
        report["error"] = str(e)
        async for db in get_db():
            db.add(ProcessingReport(document_id=document_id, course_code="SYLLABUS", report_json=json.dumps(report)))
            await db.commit()
            break
        traceback.print_exc()


async def process_pyq_background(
    file_path: str, course_code: str, document_id: uuid.UUID
):
    import json
    report = {"type": "pyq", "course_code": course_code, "status": "started", "structured": 0, "vision": 0, "mapped": 0}
    print(f"[PYQ] Starting processing for {course_code}...")
    neo4j_driver = get_neo4j()

    # Stage 1: Structured Text Extractor (fast, CO/BTL-aware)
    structured_qs = extract_pyq_structured(file_path, course_code, str(document_id))

    if structured_qs:
        print(
            f"[PYQ] Text extractor found {len(structured_qs)} questions. Mapping to KG..."
        )
        await map_pyq_structured_to_kg(neo4j_driver, structured_qs, str(document_id))

        async for db in get_db():
            for q in structured_qs:
                labeled_content = f"[PYQ - {course_code} - {q['question_number']}]\n{q['question_text']}"
                img_url = q.get("image_url")
                if img_url:
                    labeled_content += f"\n\n![Diagram for Q{q['question_number']}]({img_url})"
                embedding = await get_embedding(labeled_content)
                if embedding:
                    text_chunk = DocumentChunk(
                        document_id=document_id,
                        content=labeled_content,
                        course_code=course_code,
                        content_type="text",
                        embedding=embedding,
                    )
                    db.add(text_chunk)
            await db.commit()
            break
        print(f"[PYQ] Finished processing structured text for {course_code}!")
        report["status"] = "success_structured"
        report["structured"] = len(structured_qs)
        async for db in get_db():
            db.add(ProcessingReport(document_id=document_id, course_code=course_code, report_json=json.dumps(report)))
            await db.commit()
            break
        return

    print("[PYQ] Text extractor found 0 questions. Falling back to Vision AI...")

    # Stage 2: Vision pipeline
    # Render pages as chunked images
    page_chunks = process_pyq_visuals(file_path)
    print(f"[PYQ] Rendered {len(page_chunks)} image chunks.")

    async for db in get_db():
        for i, chunk_data in enumerate(page_chunks):
            print(
                f"[PYQ] Extracting questions from chunk {i + 1}/{len(page_chunks)} via Vision AI..."
            )
            try:
                # Extract questions via Vision
                questions = await extract_pyq_questions(chunk_data["base64"])

                if not questions:
                    continue

                # Upload the page image chunk to Cloudinary (or local fallback)
                image_bytes = base64.b64decode(chunk_data["base64"])
                page_num_lbl = chunk_data["page"] + 1
                chunk_index = chunk_data["chunk_index"]
                public_id = f"{document_id}_page_{page_num_lbl}_chunk_{chunk_index}"
                image_url = upload_question_image_to_cloudinary(image_bytes, public_id)

                # Map extracted questions into the KG
                await map_questions_to_kg(
                    neo4j_driver, course_code, questions, document_id, image_url
                )

                for q in questions:
                    if isinstance(q, str):
                        q = {
                            "text": q,
                            "question_number": "Unknown",
                            "likely_topic": "General",
                            "implicit_formulas": [],
                        }

                    q_text = q.get("text", "")

                    # Validate q_text before storing to prevent junk/JSON
                    if len(q_text) < 20 or q_text.startswith("{") or '{"' in q_text:
                        continue
                    if any(
                        junk in q_text[:50].lower()
                        for junk in [
                            "answer all",
                            "part a",
                            "part b",
                            "co |",
                            "course outcomes",
                        ]
                    ):
                        continue

                    # Append the markdown image link so the LLM includes it in the chat
                    markdown_image = f"![PYQ Page - {course_code}]({image_url})"
                    labeled_content = f"[PYQ - {course_code} - {q.get('question_number')}]\n{q_text}\nImplicit Formulas: {', '.join(q.get('implicit_formulas', []))}\n\n{markdown_image}"

                    embedding = await get_embedding(labeled_content)
                    if embedding:
                        visual_chunk = DocumentChunk(
                            document_id=document_id,
                            content=labeled_content,
                            course_code=course_code,
                            content_type="visual",
                            embedding=embedding,
                        )
                        db.add(visual_chunk)
                        await db.commit()

            except Exception as e:
                import traceback

                print(
                    f"[PYQ] Error processing chunk {chunk_data['chunk_index']} on page {chunk_data['page'] + 1}: {e}"
                )
                traceback.print_exc()
                await db.rollback()
        break

    print(f"[PYQ] Finished processing all content for {course_code}!")
    report["status"] = "success_vision"
    async for db in get_db():
        db.add(ProcessingReport(document_id=document_id, course_code=course_code, report_json=json.dumps(report)))
        await db.commit()
        break


# Application Entry Point

# When you run `python app.py`, uvicorn starts the server on port 8000
if __name__ == "__main__":
    import webbrowser
    from threading import Timer

    def open_browser():
        webbrowser.open("http://localhost:8000")

    # Open the browser 1.5 seconds after starting the command,
    # giving Uvicorn enough time to bind to the port.
    Timer(1.5, open_browser).start()

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True, access_log=False)
