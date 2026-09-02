import os
import uuid
import base64
import shutil
from datetime import datetime, timedelta

# FastAPI is the core framework used to build our web API
from fastapi import FastAPI, UploadFile, File, Form, Depends, HTTPException, BackgroundTasks, Response
from fastapi.responses import StreamingResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from jose import jwt
from query_neo4j import fetch_all_problems_by_topic
from contextlib import asynccontextmanager
# CORSMiddleware allows our frontend (HTML files) to communicate with this backend
from fastapi.middleware.cors import CORSMiddleware
# SQLAlchemy components for interacting with our PostgreSQL database
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text
# Pydantic is used to define the structure of incoming data (like JSON requests)
from pydantic import BaseModel
import uvicorn
# StaticFiles already imported above

# Import our custom database configurations and models
from database import get_db, get_neo4j, Base, engine, Document, DocumentChunk, ProcessingReport
from utils import (
    process_pdf, process_pyq_visuals, describe_page_image, describe_uploaded_image,
    get_embedding, extract_knowledge_graph, save_to_neo4j, generate_answer, generate_answer_stream,
    extract_pyq_questions, map_questions_to_kg, clean_formula_text,
    extract_pyq_structured, add_prerequisite_edges, PREREQUISITE_MAP, map_pyq_structured_to_kg,
    hybrid_search_rrf, execute_neo4j_pyq_search, upload_question_image_to_cloudinary,
    delete_document_images_from_cloudinary, delete_all_images_from_cloudinary
)

# --- New focused modules (Graph RAG pipeline) ---
from curriculum_extractor import (
    extract_syllabus_structure, build_syllabus_kg
)
from pyq_processor import (
    PYQProcessingReport, SkipReason, save_questions_to_neo4j, build_initial_report
)
from topic_mapper import run_for_document as topic_mapper_run_for_document
from query_neo4j import (
    fetch_all_problems_by_topic,
    fetch_problems_by_topic_graph,
    fetch_graph_for_course,
)

from typing import List, Optional

# =============================================================================
# Application Setup
# =============================================================================

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
        await conn.execute(text("""
            ALTER TABLE document_chunks 
            ADD COLUMN IF NOT EXISTS tsv_content TSVECTOR 
            GENERATED ALWAYS AS (to_tsvector('english', content)) STORED
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_chunks_tsv ON document_chunks USING GIN(tsv_content)
        """))

        # Ensure documents table has processing_status column
        await conn.execute(text("""
            ALTER TABLE documents 
            ADD COLUMN IF NOT EXISTS processing_status VARCHAR DEFAULT 'pending'
        """))
        
    print("Database startup complete: Tables verified.")
    
    # Seed Neo4j prerequisite graph
    try:
        neo4j_driver = get_neo4j()
        add_prerequisite_edges(neo4j_driver, PREREQUISITE_MAP)
        print("Neo4j Prerequisite Graph seeded.")
    except Exception as e:
        print(f"Failed to seed prereqs: {e}")
        
    yield

# Initialize the FastAPI application
app = FastAPI(title="CurriculumLens Backend", lifespan=lifespan)

# Configure Cross-Origin Resource Sharing (CORS).
# This allows our local HTML files (opened directly in the browser) to send requests to this server.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],       # Allow requests from any origin
    allow_credentials=False,   # False because we use Bearer tokens, not cookies
    allow_methods=["*"],       # Allow all HTTP methods (GET, POST, DELETE, etc.)
    allow_headers=["*"],       # Allow all HTTP headers
)

# Mount the static directory so the frontend can retrieve images
os.makedirs("uploads/images", exist_ok=True)
app.mount("/static", StaticFiles(directory="uploads"), name="static")

# Serve the new M.A.C.H. frontend at /public/
# The frontend directory lives one level up from backend/
_frontend_dir = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.isdir(_frontend_dir):
    app.mount("/public", StaticFiles(directory=_frontend_dir, html=True), name="frontend")


# =============================================================================
# Route: GET / — Convenience redirect to the frontend login page
# =============================================================================

@app.get("/")
async def root_redirect():
    return RedirectResponse(url="/public/index.html")


# =============================================================================
# Auth Configuration (Option A — hardcoded demo credentials + JWT)
# =============================================================================

_JWT_SECRET    = os.getenv("JWT_SECRET",         "mach-curriculum-lens-secret-2026")
_JWT_ALGORITHM = os.getenv("JWT_ALGORITHM",      "HS256")
_JWT_EXPIRE    = int(os.getenv("JWT_EXPIRE_MINUTES", "480"))

# Demo credentials — matches the hints shown on the login page
_USERS = {
    "student": {"password": "student123", "role": "student"},
    "admin":   {"password": "admin123",   "role": "admin"},
}

def _create_token(username: str, role: str) -> str:
    payload = {
        "sub": username,
        "role": role,
        "exp": datetime.utcnow() + timedelta(minutes=_JWT_EXPIRE),
    }
    return jwt.encode(payload, _JWT_SECRET, algorithm=_JWT_ALGORITHM)


# =============================================================================
# Route: POST /login — JWT Authentication
# =============================================================================

from fastapi import Form as FastForm

@app.post("/login")
async def login(
    username: str = FastForm(...),
    password: str = FastForm(...)
):
    """
    Validates credentials against the hardcoded demo user dict.
    Returns a signed JWT access token on success.
    The new M.A.C.H. frontend decodes the JWT to extract the user's role.
    """
    user = _USERS.get(username.lower())
    if not user or user["password"] != password:
        raise HTTPException(status_code=401, detail="Incorrect username or password.")

    token = _create_token(username.lower(), user["role"])
    return {"access_token": token, "token_type": "bearer"}


# =============================================================================
# Route: POST /feedback/{message_id} — Student Thumbs Up/Down
# =============================================================================

class FeedbackRequest(BaseModel):
    score: int  # 1 = helpful, -1 = not helpful

@app.post("/feedback/{message_id}")
async def submit_feedback(message_id: str, body: FeedbackRequest):
    """
    Receives thumbs-up/down feedback from the student chat UI.
    Logs it for now; can be persisted to a DB table in future.
    """
    print(f"[Feedback] message_id={message_id}  score={body.score}")
    return {"message": "Feedback recorded", "message_id": message_id, "score": body.score}


# =============================================================================
# Route: GET /evaluate — RAGAS Evaluation Metrics
# =============================================================================

@app.get("/evaluate")
async def get_evaluation():
    """
    Returns RAGAS pipeline evaluation metrics for the Admin dashboard chart.
    Returns placeholder scores; replace with real RAGAS evaluation when available.
    """
    metrics = {
        "context_precision": 0.82,
        "faithfulness":      0.91,
        "answer_relevance":  0.78,
        "context_recall":    0.88,
        "answer_correctness": 0.85,
    }
    mean = round(sum(metrics.values()) / len(metrics), 2)
    return {
        "metrics":     metrics,
        "num_samples": 5,
        "model":       os.getenv("OLLAMA_MODEL", "local"),
        "mean":        mean,
    }


# =============================================================================
# Route: /chat — Handles Student Questions
# =============================================================================

import httpx
from utils import OLLAMA_BASE_URL, OLLAMA_MODEL

async def classify_query_intent(question: str) -> str:
    """
    Tiered classification: Fast keyword pre-filter first. 
    If ambiguous, invoke the LLM classifier.
    """
    q_lower = question.lower()
    
    # Tier 1: Fast Keyword Routing
    # Check for problem list requests first
    if any(marker in q_lower for marker in ["problems on", "problem on", "questions on", "question on", "pyqs on", "pyq on", "problems about", "questions about"]):
        return "PROBLEM_LIST"
        
    if any(k in q_lower for k in ["prerequisite", "before taking", "should i know", "requires", "needed for"]):
        return "MULTI_HOP_PREREQ"
    elif any(k in q_lower for k in ["btl", "co1", "co2", "co3", "co4", "co5", "bloom", "mapped to", "course outcome questions"]):
        return "GRAPH_PYQ_MAPPING"
    elif any(k in q_lower for k in ["syllabus", "topics", "units", "course outcomes", "objectives"]):
        return "SIMPLE_CURRICULUM"
    elif any(k in q_lower for k in ["question", "questions", "pyq", "past year", "exam", "paper", "midterm", "mid-term", "endsem", "end-sem", "problem", "problems", "circuit"]):
        return "SIMPLE_PYQ"
        
    # Tier 2: LLM Fallback (Slow)
    prompt = f"""
You are a query classification engine for a university information system.
Classify the user's question into exactly one primary category.

Categories:
- "PROBLEM_LIST": Questions asking for problem sets, practice problems, or exam questions on a specific academic topic (e.g. "problems on superposition theorem").
- "SIMPLE_CURRICULUM": Questions about courses, syllabus content, units, topics, learning outcomes.
- "SIMPLE_PYQ": Questions asking for a specific, single past year question (e.g., "what is question 5 of course EEE104?").
- "MULTI_HOP_PREREQ": Questions asking about course prerequisites or what to know before taking a course.
- "GRAPH_PYQ_MAPPING": Questions asking for questions mapped to specific Course Outcomes (COs) or Bloom's Taxonomy Levels (BTLs).

Return ONLY the category name. No explanations.
Question: {question}
"""
    try:
        async with httpx.AsyncClient(headers={"ngrok-skip-browser-warning": "true"}) as client:
            response = await client.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
                timeout=25.0
            )
            ans = response.json().get("response", "").strip().upper()
            if ans in ["SIMPLE_CURRICULUM", "SIMPLE_PYQ", "MULTI_HOP_PREREQ", "GRAPH_PYQ_MAPPING", "PROBLEM_LIST"]:
                return ans
    except Exception as e:
        print(f"LLM classification error: {type(e).__name__}: {e}")
        
    return "SIMPLE_CURRICULUM" # Default fallback


def execute_graph_prereq_query(neo4j_driver, question: str) -> list:
    """Extracts course name/code from question and finds prereqs."""
    # Rough extraction for demonstration (assumes course is in the query)
    words = question.lower().split()
    course_hint = next((w for w in words if len(w) > 3 and w not in ["what", "are", "the", "prerequisites", "for"]), "")
    
    with neo4j_driver.session() as session:
        records = session.run("""
            MATCH (target:Course)
            WHERE toLower(target.name) CONTAINS toLower($course_name) 
               OR toLower(target.code) CONTAINS toLower($course_name)
            MATCH path = (target)-[:REQUIRES*1..5]->(prereq:Course)
            RETURN target.name AS course, collect(DISTINCT prereq.name) AS all_prerequisites
            LIMIT 10
        """, course_name=course_hint).data()
    return records


def execute_graph_co_query(neo4j_driver, question: str) -> list:
    """Extracts CO id and queries the graph."""
    import re
    co_match = re.search(r'co\d+', question.lower())
    co_id = co_match.group(0).upper() if co_match else "CO1"
    
    with neo4j_driver.session() as session:
        records = session.run("""
            MATCH (q:Question)-[:MAPPED_TO_CO]->(co:CourseOutcome {id: $co_id})
                  -[:BELONGS_TO]->(c:Course)
            RETURN q.text AS question, q.btl AS btl, q.marks AS marks, q.has_figure AS has_figure, c.code AS course_code
            LIMIT 20
        """, co_id=co_id).data()
    return records


def execute_graph_syllabus_query(neo4j_driver, question: str) -> list:
    """Queries Neo4j for course syllabus structure (Units & Topics)."""
    words = [w.strip(".,!?-'\"") for w in question.lower().split() if len(w) > 2 and w not in ["get", "me", "the", "syllabus", "for", "course", "topics", "units", "what", "is"]]
    if not words:
        words = [question.lower()]
        
    with neo4j_driver.session() as session:
        # First try matching course name or course code directly
        records = session.run("""
            MATCH (c:Course)
            WHERE all(word IN $words WHERE toLower(c.name) CONTAINS word OR toLower(c.code) CONTAINS word)
            OPTIONAL MATCH (c)-[:HAS_UNIT]->(u:Unit)-[:HAS_TOPIC]->(t:Topic)
            RETURN c.code as course_code, c.name as course_name, u.title as unit_title, collect(DISTINCT t.name) as topics
            ORDER BY size(c.name) ASC
            LIMIT 15
        """, words=words).data()
        
        # If no course name matches all words, fallback to topic-level search
        if not records:
            records = session.run("""
                MATCH (c:Course)-[:HAS_UNIT]->(u:Unit)-[:HAS_TOPIC]->(t:Topic)
                WHERE all(word IN $words WHERE toLower(t.name) CONTAINS word)
                RETURN c.code as course_code, c.name as course_name, u.title as unit_title, collect(DISTINCT t.name) as topics
                LIMIT 15
            """, words=words).data()
            
    return records


class ChatRequest(BaseModel):
    """Defines the expected JSON structure when a student asks a question."""
    message: str
    session_id: Optional[str] = None  # Client-side session tracking; unused server-side for now

import asyncio
from functools import lru_cache
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


def extract_topic_from_query(question: str) -> str:
    """Extract the topic from the user query."""
    q_lower = question.lower()
    
    # Try common markers
    markers = [
        "problems on", "problem on", "questions on", "question on", 
        "pyqs on", "pyq on", "problems about", "questions about", 
        "questions for", "problems for", "find questions on", "find problems on"
    ]
    for marker in markers:
        if marker in q_lower:
            idx = q_lower.find(marker)
            extracted = question[idx + len(marker):].strip("? .!").strip()
            if extracted:
                return extracted.title()
                
    # Fallback: remove stop words and return the remaining words capitalized
    stop_words_for_topic = {
        "get", "me", "a", "the", "all", "questions", "question", "problems", "problem",
        "pyqs", "pyq", "on", "about", "for", "find", "show", "list", "give", "related",
        "are", "there", "any", "is", "what", "how", "why", "who", "where", "can", "you",
        "tell", "explain", "describe", "provide", "please", "past", "year"
    }
    words = question.split()
    clean_words = []
    for w in words:
        w_clean = w.strip(".,!?-'\"")
        if w_clean.lower() not in stop_words_for_topic:
            clean_words.append(w)
            
    if clean_words:
        return " ".join(clean_words).title()
        
    return question.strip("? .!").title()


@app.post("/chat")
async def chat_endpoint(request: ChatRequest, db: AsyncSession = Depends(get_db)):
    """
    Takes a student's question, uses tiered intent routing, searches 
    PostgreSQL/Neo4j for relevant context, and streams the AI response back.
    """
    question = request.message
    cache_key = question.strip().lower()

    # --- Check cache first ---
    cached = _get_cached_response(cache_key)
    if cached:
        async def yield_cached():
            yield cached
        return StreamingResponse(yield_cached(), media_type="text/plain")

    context_parts = []
    
    intent = await classify_query_intent(question)
    print(f"[ROUTER] Intent classified as: {intent}")
    
    neo4j_driver = get_neo4j()
    
    if intent == "PROBLEM_LIST":
        topic_name = extract_topic_from_query(question)
        t_intent_start = time.time()

        # ── Graph RAG path: TESTS_TOPIC traversal first ─────────────────────
        problems = fetch_problems_by_topic_graph(neo4j_driver, topic_name)
        used_graph = bool(problems)
        print(f"[ROUTER] PROBLEM_LIST graph path: {len(problems)} results for '{topic_name}'")

        # ── Keyword fallback: only if graph returned nothing ─────────────────
        if not problems:
            print(f"[ROUTER] PROBLEM_LIST graph empty — falling back to keyword search.")
            problems = fetch_all_problems_by_topic(neo4j_driver, topic_name)

        return JSONResponse(
            content={
                "type":       "problem_list",
                "topic":      topic_name,
                "problems":   problems,
                "source":     "graph" if used_graph else "keyword_fallback",
            },
            headers={"X-Response-Type": "problem_list"},
        )
        
    elif intent == "MULTI_HOP_PREREQ":
        records = execute_graph_prereq_query(neo4j_driver, question)
        if records:
            context_parts.append("--- PREREQUISITE GRAPH KNOWLEDGE ---")
            for r in records:
                context_parts.append(f"Course: {r['course']} requires prerequisites: {', '.join(r['all_prerequisites'])}")
    
    elif intent == "GRAPH_PYQ_MAPPING":
        records = execute_graph_co_query(neo4j_driver, question)
        if records:
            context_parts.append("--- MAPPED QUESTIONS GRAPH KNOWLEDGE ---")
            for r in records:
                context_parts.append(f"Course {r['course_code']} Question (BTL: {r['btl']}, Marks: {r['marks']}): {r['question']}")
                
    elif intent == "SIMPLE_CURRICULUM":
        t_graph_start = time.time()
        records = execute_graph_syllabus_query(neo4j_driver, question)
        t_graph_ms = round((time.time() - t_graph_start) * 1000)

        if records:
            context_parts.append("--- SYLLABUS & CURRICULUM KNOWLEDGE GRAPH ---")
            syllabus_dict = {}
            for r in records:
                c_code = r['course_code']
                if c_code not in syllabus_dict:
                    syllabus_dict[c_code] = {"name": r['course_name'], "units": {}}
                if r.get('unit_title') and r.get('topics'):
                    syllabus_dict[c_code]["units"][r['unit_title']] = r['topics']

            for c_code, data in syllabus_dict.items():
                context_parts.append(f"Course: {c_code} - {data['name']}")
                if data["units"]:
                    for unit_title in sorted(data["units"].keys()):
                        topics = data["units"][unit_title]
                        topics_str = ", ".join(topics) if isinstance(topics, list) else str(topics)
                        context_parts.append(f"  {unit_title}: {topics_str}")
                else:
                    context_parts.append("  (No specific units or topics recorded for this course)")
        else:
            # ── Fallback: only if Neo4j returned nothing ─────────────────────
            # (e.g., course not yet uploaded, or question is asking for an explanation)
            question_embedding = await get_embedding(question)
            if question_embedding:
                try:
                    chunks = await hybrid_search_rrf(db, question, question_embedding, k=5)
                    if chunks:
                        context_parts.append("--- ADDITIONAL TEXT CHUNKS ---")
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
                img_url = record['image_url'].replace(' ', '%20') if record.get('image_url') else None
                img_markdown = f"\n![Diagram for Q{record['q_num']}]({img_url})" if img_url and img_url != "None" else ""
                context_parts.append(f"[Course: {record['course_code']} - Q: {record['q_num']}]\n{record['q_text']}{img_markdown}\nMarks: {record['marks']}\nBTL: {record['btl']}")
        else:
            question_embedding = await get_embedding(question)
            if question_embedding:
                try:
                    chunks = await hybrid_search_rrf(db, question, question_embedding, k=5)
                    if chunks:
                        context_parts.append("--- HYBRID SEARCH RESULTS (TEXT + SEMANTIC) ---")
                        for chunk in chunks:
                            content = chunk.get("content", "")
                            if content:
                                context_parts.append(content)
                except Exception as e:
                    print(f"Hybrid search error in /chat: {e}")

    # Generate final answer
    final_context = "\n".join(context_parts)
    if len(final_context) > 8000:
        final_context = final_context[:8000] + "\n...[Context Truncated]..."
        
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
        images = re.findall(r'!\[.*?\]\(.*?\)', final_context)
        if images:
            combined_response_str = "".join(full_response)
            unique_images = []
            for img in images:
                # Extract URL from the markdown tag e.g. ![...](url)
                url_match = re.search(r'\((https?://.*?)\)', img)
                if url_match:
                    url = url_match.group(1)
                    if url not in combined_response_str:
                        unique_images.append(img)
                else:
                    if img not in combined_response_str:
                        unique_images.append(img)

            if unique_images:
                image_block = "\n\n### Diagrams from Questions:\n" + "\n\n".join(unique_images)
                full_response.append(image_block)
                yield image_block
            
        _set_cache(cache_key, "".join(full_response))

    return StreamingResponse(stream_and_cache(), media_type="text/plain")


# =============================================================================
# Route: /debug-context — Returns the exact context built for a question
# =============================================================================

@app.post("/debug-context")
async def debug_context_endpoint(request: ChatRequest, db: AsyncSession = Depends(get_db)):
    """Debug endpoint: Returns the raw context that would be sent to the LLM."""
    question = request.message
    context_parts = []
    debug_info = {}

    try:
        neo4j_driver = get_neo4j()
        raw_words = question.lower().split()
        stop_words = {
            "get", "give", "show", "list", "tell", "what", "about", "from", "have",
            "me", "the", "for", "and", "with", "this", "that", "course", "syllabus",
            "please", "can", "could", "would", "want", "need", "find", "look"
        }
        words = [w for w in raw_words if len(w) > 3 and w not in stop_words]
        if not words:
            words = [w for w in raw_words if len(w) > 2]

        debug_info["extracted_words"] = words
        phrase = " ".join(words)
        debug_info["phrase"] = phrase

        with neo4j_driver.session() as session:
            course_records = list(session.run(
                """
                MATCH (c:Course)
                WHERE toLower(c.name) CONTAINS $phrase
                   OR any(word IN $words WHERE toLower(c.code) CONTAINS word)
                WITH c LIMIT 3
                OPTIONAL MATCH (c)-[:HAS_UNIT]->(u:Unit)-[:HAS_TOPIC]->(t:Topic)
                RETURN c.code as course_code, c.name as course_name, u.title as unit_title, t.name as topic_name
                ORDER BY course_code, unit_title, topic_name
                """,
                words=words,
                phrase=phrase
            ))
            debug_info["course_records_count"] = len(course_records)
            debug_info["sample_course_records"] = [dict(r) for r in course_records[:5]]

    except Exception as e:
        debug_info["error"] = str(e)

    return debug_info


# =============================================================================
# Route: /upload — Handles Admin PDF Uploads
# =============================================================================

@app.post("/upload")
async def upload_document(
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(...),
    doc_type: str = Form(...),
    department: Optional[str] = Form(None),
    dept: Optional[str] = Form(None),          # new frontend sends 'dept', old sends 'department'
    year: Optional[str] = Form(None),
    course_code: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db)
):
    """
    Accepts single or multiple PDF documents uploaded by the admin.
    Depending on `doc_type` ("syllabus" or "pyq"), dispatches to the correct pipeline.
    """
    os.makedirs("uploads", exist_ok=True)
    # Normalise: accept 'dept' (new frontend) OR 'department' (old frontend / direct API calls)
    department = department or dept

    uploaded_docs = []

    # Save the files and dispatch tasks
    for file in files:
        file_path = f"uploads/{uuid.uuid4()}_{file.filename}"
        with open(file_path, "wb") as f:
            content = await file.read()
            f.write(content)

        new_doc = Document(
            filename=file.filename,
            doc_type=doc_type,
            department=department,
            year=year,
            course_code=course_code,
            processing_status="pending",
        )
        db.add(new_doc)
        await db.commit()
        await db.refresh(new_doc)

        uploaded_docs.append({"id": str(new_doc.id), "filename": file.filename})

        if doc_type == "syllabus":
            background_tasks.add_task(
                process_syllabus_background, file_path, department, year, new_doc.id
            )
        elif doc_type == "pyq":
            background_tasks.add_task(
                process_pyq_background, file_path, course_code, new_doc.id
            )

    return {
        "message": f"{len(files)} file(s) queued for processing. "
                   f"Check /admin/pyq-processing/<document_id> for status.",
        "documents": uploaded_docs,
    }


# =============================================================================
# Route: /documents — Lists All Uploaded Documents
# =============================================================================

@app.get("/documents")
async def list_documents(db: AsyncSession = Depends(get_db)):
    """
    Fetches and returns a list of all documents from the PostgreSQL database,
    ordered by the most recently uploaded first.
    """
    try:
        # Query the Document table, ordering by creation date descending
        result = await db.execute(select(Document).order_by(Document.created_at.desc()))
        docs = result.scalars().all()

        return [
            {
                "id":               str(doc.id),
                "filename":         doc.filename,
                "doc_type":         doc.doc_type,
                "course_code":      doc.course_code,
                "created_at":       doc.created_at.isoformat(),
                "processing_status": doc.processing_status,
            }
            for doc in docs
        ]
    except Exception as e:
        print(f"Error fetching document list: {e}")
        return []


# =============================================================================
# Route: /documents/{id} — Deletes a Specific Document
# =============================================================================

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
                        session.run("""
                            MATCH (q:Question)
                            WHERE q.document_id = $doc_id
                            DETACH DELETE q
                        """, doc_id=str(doc_id))
                        
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
                            session.run("""
                                MATCH (c:Course {code: $c_code})
                                OPTIONAL MATCH (c)-[:HAS_UNIT]->(u:Unit)
                                OPTIONAL MATCH (u)-[:HAS_TOPIC]->(t:Topic)
                                DETACH DELETE t, u
                            """, c_code=c_code)
                            session.run("""
                                MATCH (c:Course {code: $c_code})
                                WHERE NOT (c)-[:HAS_UNIT]->() AND NOT (c)-[:HAS_QUESTION_MODEL]->()
                                DETACH DELETE c
                            """, c_code=c_code)
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
        await db.execute(text("DELETE FROM document_chunks WHERE document_id = :id"), {"id": doc_id})
        await db.execute(text("DELETE FROM documents WHERE id = :id"), {"id": doc_id})
        
        await db.commit()
        return {"message": "Document and its associated graph nodes deleted successfully."}
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# Route: /reset — Hard Resets the Entire System
# =============================================================================

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


# =============================================================================
# Route: /image-query — Student Uploads an Image for Reverse Lookup
# =============================================================================

@app.post("/image-query")
async def image_query_endpoint(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db)
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
            query = select(DocumentChunk).order_by(
                DocumentChunk.embedding.cosine_distance(query_embedding)
            ).limit(10)
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
                words=words
            )
            
            graph_facts = []
            for r in records:
                labels = r['labels']
                if 'Question' in labels:
                    fact = f"[Course {r['course_code']} - PYQ] {r['text']}"
                    if r['formulas']:
                        fact += f" (Formulas: {r['formulas']})"
                    graph_facts.append(fact)
                else:
                    graph_facts.append(f"[Course {r['course_code']} - {labels[0]}] {r['name']}")
                    
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

    return {
        "description": image_description,
        "response": ai_response
    }


# =============================================================================
# Background Task Logic
# =============================================================================

async def process_syllabus_background(
    file_path: str, department: str, year: str, document_id: uuid.UUID
):
    """
    Extracts curriculum structure from a PDF using the new curriculum_extractor module.
    Preserves page boundaries. Writes Document, Course, Unit, and Topic nodes to Neo4j.
    Updates Document.processing_status in PostgreSQL when done.
    """
    doc_id_str = str(document_id)
    print(f"[Syllabus] Starting processing for {department} {year} (doc={doc_id_str})...")

    # Mark as processing
    async for db in get_db():
        await db.execute(
            text("UPDATE documents SET processing_status = 'processing' WHERE id = :id"),
            {"id": doc_id_str}
        )
        await db.commit()
        break

    neo4j_driver = get_neo4j()
    status = "failed"
    try:
        # extract_syllabus_structure now receives the FILE PATH, not joined text.
        # It opens the PDF page-by-page internally to preserve line structure.
        structure = await extract_syllabus_structure(
            file_path=file_path,
            dept=department,
            year=year,
            document_id=doc_id_str
        )
        courses = structure.get("courses", [])

        if courses:
            build_syllabus_kg(neo4j_driver, department, year, courses, document_id=doc_id_str)
            status = "completed"
        else:
            print(f"[Syllabus] No courses extracted from {file_path}.")
            status = "failed"

    except Exception as e:
        import traceback
        print(f"[Syllabus] Fatal error: {e}")
        traceback.print_exc()
        status = "failed"

    # Update processing status in PostgreSQL
    async for db in get_db():
        await db.execute(
            text("UPDATE documents SET processing_status = :status WHERE id = :id"),
            {"status": status, "id": doc_id_str}
        )
        await db.commit()
        break

    print(f"[Syllabus] Finished processing {department} {year} — status: {status}.")


async def process_pyq_background(
    file_path: str, course_code: str, document_id: uuid.UUID
):
    """
    Extracts structured questions from a PYQ PDF, writes them to Neo4j using the
    canonical Question → BELONGS_TO → Course path, embeds them in PostgreSQL,
    collects a full ProcessingReport, and kicks off async topic mapping.

    Updates Document.processing_status in PostgreSQL when done.
    """
    import time
    doc_id_str = str(document_id)
    print(f"[PYQ] Starting processing for {course_code} (doc={doc_id_str})...")

    neo4j_driver = get_neo4j()

    # Get filename from PostgreSQL for the report
    filename = file_path.split("/")[-1]
    async for db in get_db():
        result = await db.execute(
            text("SELECT filename FROM documents WHERE id = :id"), {"id": doc_id_str}
        )
        row = result.first()
        if row:
            filename = row[0]
        await db.execute(
            text("UPDATE documents SET processing_status = 'processing' WHERE id = :id"),
            {"id": doc_id_str}
        )
        await db.commit()
        break

    # ── Stage 1: Structured text extractor (fast, CO/BTL-aware) ────────────
    t_extract_start = time.time()
    structured_qs = extract_pyq_structured(file_path, course_code, doc_id_str)
    t_extract_s = round(time.time() - t_extract_start, 2)

    # Detect total pages
    total_pages = 0
    try:
        import fitz as _fitz
        _doc = _fitz.open(file_path)
        total_pages = len(_doc)
        _doc.close()
    except Exception:
        pass

    # Build initial report from structured extraction results
    skip_reasons_list = []
    report = PYQProcessingReport(
        document_id=doc_id_str,
        filename=filename,
        course_code=course_code,
        total_pages=total_pages,
        candidates_detected=len(structured_qs),
        accepted=len(structured_qs),
        skipped=0,
        skip_reasons=skip_reasons_list,
        status="processing",
        timings={"extraction_s": t_extract_s},
    )

    final_status = "failed"

    if structured_qs:
        print(f"[PYQ] Text extractor found {len(structured_qs)} questions. Writing to Neo4j...")

        # ── Stage 2: Write to Neo4j (canonical path) ────────────────────────
        save_questions_to_neo4j(neo4j_driver, structured_qs, doc_id_str, report)

        # ── Stage 3: Embed and store in PostgreSQL ──────────────────────────
        t_embed_start = time.time()
        embed_count = 0
        async for db in get_db():
            for q in structured_qs:
                labeled_content = (
                    f"[PYQ - {course_code} - {q['question_number']}]\n{q['question_text']}"
                )
                embedding = await get_embedding(labeled_content)
                if embedding:
                    text_chunk = DocumentChunk(
                        document_id=document_id,
                        content=labeled_content,
                        course_code=course_code,
                        content_type="text",
                        embedding=embedding
                    )
                    db.add(text_chunk)
                    embed_count += 1
            await db.commit()
            break
        report.embedded_postgres = embed_count
        report.timings["embed_s"] = round(time.time() - t_embed_start, 2)

        # ── Stage 4: Topic mapping (async, best-effort) ─────────────────────
        try:
            t_map_start = time.time()
            map_result = await topic_mapper_run_for_document(
                neo4j_driver, doc_id_str, report
            )
            report.mapped_to_topic = map_result.get("mapped", 0)
            report.timings["topic_map_s"] = round(time.time() - t_map_start, 2)
        except Exception as map_err:
            print(f"[PYQ] Topic mapping error (non-fatal): {map_err}")

        report.finalize()
        final_status = report.status
        print(f"[PYQ] Finished processing structured text for {course_code}!")

    else:
        # ── Stage 1b: Vision pipeline fallback (scanned PDFs) ──────────────
        print(f"[PYQ] Text extractor found 0 questions. Falling back to Vision AI...")
        page_chunks = process_pyq_visuals(file_path)
        print(f"[PYQ] Rendered {len(page_chunks)} image chunks.")

        async for db in get_db():
            for i, chunk_data in enumerate(page_chunks):
                print(f"[PYQ] Extracting from chunk {i+1}/{len(page_chunks)} via Vision AI...")
                try:
                    questions = await extract_pyq_questions(chunk_data["base64"])
                    if not questions:
                        continue

                    image_bytes = base64.b64decode(chunk_data["base64"])
                    page_num_lbl = chunk_data['page'] + 1
                    chunk_index = chunk_data['chunk_index']
                    public_id = f"{document_id}_page_{page_num_lbl}_chunk_{chunk_index}"
                    image_url = upload_question_image_to_cloudinary(image_bytes, public_id)

                    # Legacy write path preserved for vision-extracted questions
                    map_questions_to_kg(neo4j_driver, course_code, questions, document_id, image_url)

                    for q in questions:
                        if isinstance(q, str):
                            q = {"text": q, "question_number": "Unknown",
                                 "likely_topic": "General", "implicit_formulas": []}
                        q_text = q.get("text", "")
                        if len(q_text) < 20 or q_text.startswith('{') or '{"' in q_text:
                            continue
                        if any(junk in q_text[:50].lower() for junk in
                               ['answer all', 'part a', 'part b', 'co |', 'course outcomes']):
                            continue

                        markdown_image = f"![PYQ Page - {course_code}]({image_url})"
                        labeled_content = (
                            f"[PYQ - {course_code} - {q.get('question_number')}]\n"
                            f"{q_text}\nImplicit Formulas: "
                            f"{', '.join(q.get('implicit_formulas', []))}\n\n{markdown_image}"
                        )
                        embedding = await get_embedding(labeled_content)
                        if embedding:
                            visual_chunk = DocumentChunk(
                                document_id=document_id,
                                content=labeled_content,
                                course_code=course_code,
                                content_type="visual",
                                embedding=embedding
                            )
                            db.add(visual_chunk)
                            report.saved_neo4j += 1
                            report.embedded_postgres += 1
                            await db.commit()

                except Exception as e:
                    import traceback
                    print(f"[PYQ] Error on chunk {chunk_data['chunk_index']} p{chunk_data['page']+1}: {e}")
                    traceback.print_exc()
                    await db.rollback()
            break

        # ── Stage 4: Topic mapping (async, best-effort) for Vision Fallback ──
        try:
            t_map_start = time.time()
            map_result = await topic_mapper_run_for_document(
                neo4j_driver, doc_id_str, report
            )
            report.mapped_to_topic = map_result.get("mapped", 0)
            report.timings["topic_map_s"] = round(time.time() - t_map_start, 2)
        except Exception as map_err:
            print(f"[PYQ] Topic mapping error (non-fatal): {map_err}")

        report.finalize()
        final_status = report.status
        print(f"[PYQ] Finished vision fallback for {course_code}!")

    # ── Persist ProcessingReport to PostgreSQL ──────────────────────────────
    try:
        async for db in get_db():
            pr = ProcessingReport(
                document_id=document_id,
                course_code=course_code,
                report_json=report.to_json(),
            )
            db.add(pr)
            await db.execute(
                text("UPDATE documents SET processing_status = :status WHERE id = :id"),
                {"status": final_status, "id": doc_id_str}
            )
            await db.commit()
            break
    except Exception as save_err:
        print(f"[PYQ] Failed to save ProcessingReport: {save_err}")
        async for db in get_db():
            await db.execute(
                text("UPDATE documents SET processing_status = :status WHERE id = :id"),
                {"status": final_status, "id": doc_id_str}
            )
            await db.commit()
            break

    print(f"[PYQ] Processing complete for {course_code} — status: {final_status}.")


# =============================================================================
# Admin Routes — Extraction Review
# =============================================================================

def _require_admin(authorization: str = None):
    """Verify the request carries a valid admin JWT. Returns the payload."""
    from fastapi import Header
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header.")
    token = authorization.split(" ", 1)[1]
    try:
        payload = jwt.decode(token, _JWT_SECRET, algorithms=[_JWT_ALGORITHM])
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired token.")
    if payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required.")
    return payload


@app.get("/admin/extraction-review/courses")
async def admin_list_courses(authorization: Optional[str] = None):
    """List all courses with their extraction status and topic counts."""
    _require_admin(authorization)
    neo4j_driver = get_neo4j()
    with neo4j_driver.session() as session:
        records = session.run("""
            MATCH (c:Course)-[:HAS_UNIT]->(u:Unit)-[:HAS_TOPIC]->(t:Topic)
            RETURN c.code AS code, c.name AS name,
                   count(DISTINCT u) AS unit_count,
                   count(DISTINCT t) AS topic_count,
                   count(DISTINCT CASE WHEN t.approved = true THEN t END) AS approved_count,
                   count(DISTINCT CASE WHEN t.approved = false THEN t END) AS pending_count
            ORDER BY c.code
        """).data()
    return {"courses": records}


@app.get("/admin/extraction-review/courses/{course_code}")
async def admin_course_review(course_code: str, authorization: Optional[str] = None):
    """Return units + topics for a course, including unit raw_text for admin review."""
    _require_admin(authorization)
    neo4j_driver = get_neo4j()
    with neo4j_driver.session() as session:
        records = session.run("""
            MATCH (c:Course {code: $code})-[:HAS_UNIT]->(u:Unit)
            OPTIONAL MATCH (u)-[:HAS_TOPIC]->(t:Topic)
            RETURN u.id AS unit_id, u.number AS unit_number, u.title AS unit_title,
                   u.raw_text AS unit_raw_text,
                   collect({
                       id: t.id, name: t.name, approved: t.approved,
                       extraction_method: t.extraction_method,
                       extraction_confidence: t.extraction_confidence
                   }) AS topics
            ORDER BY u.number
        """, code=course_code).data()
    return {"course_code": course_code, "units": records}


class TopicUpdateRequest(BaseModel):
    name: Optional[str] = None
    approved: Optional[bool] = None

@app.put("/admin/extraction-review/topics/{topic_id}")
async def admin_update_topic(
    topic_id: str, body: TopicUpdateRequest, authorization: Optional[str] = None
):
    """Edit or approve/reject a Topic node."""
    _require_admin(authorization)
    neo4j_driver = get_neo4j()
    updates = {}
    if body.name is not None:
        updates["name"] = body.name
        updates["normalized_name"] = body.name.lower().strip()
    if body.approved is not None:
        updates["approved"] = body.approved
    if not updates:
        raise HTTPException(status_code=400, detail="Nothing to update.")
    set_clause = ", ".join(f"t.{k} = ${k}" for k in updates)
    with neo4j_driver.session() as session:
        session.run(f"MATCH (t:Topic {{id: $id}}) SET {set_clause}",
                    id=topic_id, **updates)
    return {"status": "updated", "topic_id": topic_id}


class TopicMergeRequest(BaseModel):
    source_id: str
    target_id: str

@app.post("/admin/extraction-review/topics/merge")
async def admin_merge_topics(body: TopicMergeRequest, authorization: Optional[str] = None):
    """Merge source topic into target: move all relationships then delete source."""
    _require_admin(authorization)
    neo4j_driver = get_neo4j()
    with neo4j_driver.session() as session:
        # Move HAS_TOPIC relationships
        session.run("""
            MATCH (u:Unit)-[:HAS_TOPIC]->(src:Topic {id: $src_id})
            MATCH (tgt:Topic {id: $tgt_id})
            MERGE (u)-[:HAS_TOPIC]->(tgt)
        """, src_id=body.source_id, tgt_id=body.target_id)
        # Move TESTS_TOPIC relationships
        session.run("""
            MATCH (q:Question)-[r:TESTS_TOPIC]->(src:Topic {id: $src_id})
            MATCH (tgt:Topic {id: $tgt_id})
            MERGE (q)-[:TESTS_TOPIC {confidence: r.confidence, review_status: r.review_status,
                                     mapping_method: r.mapping_method, mapped_at: r.mapped_at}]->(tgt)
        """, src_id=body.source_id, tgt_id=body.target_id)
        # Delete source
        session.run("MATCH (t:Topic {id: $id}) DETACH DELETE t", id=body.source_id)
    return {"status": "merged", "source_id": body.source_id, "target_id": body.target_id}


@app.post("/admin/extraction-review/courses/{course_code}/approve")
async def admin_approve_course_topics(
    course_code: str, authorization: Optional[str] = None
):
    """Mark all Topics for this course as approved."""
    _require_admin(authorization)
    neo4j_driver = get_neo4j()
    with neo4j_driver.session() as session:
        result = session.run("""
            MATCH (c:Course {code: $code})-[:HAS_UNIT]->(u:Unit)-[:HAS_TOPIC]->(t:Topic)
            SET t.approved = true
            RETURN count(t) AS approved_count
        """, code=course_code).single()
    count = result["approved_count"] if result else 0
    return {"status": "approved", "course_code": course_code, "topics_approved": count}


# =============================================================================
# Admin Routes — PYQ Processing Status
# =============================================================================

@app.get("/admin/pyq-processing/{document_id}")
async def admin_pyq_processing_status(
    document_id: str, authorization: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
):
    """Return the full ProcessingReport for a PYQ document."""
    _require_admin(authorization)
    result = await db.execute(
        text("SELECT report_json FROM processing_reports WHERE document_id = :id ORDER BY created_at DESC LIMIT 1"),
        {"id": document_id}
    )
    row = result.first()
    if not row:
        # Try to get at least the document status
        doc_result = await db.execute(
            text("SELECT processing_status, filename, course_code FROM documents WHERE id = :id"),
            {"id": document_id}
        )
        doc = doc_result.first()
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found.")
        return {
            "document_id": document_id,
            "processing_status": doc[0],
            "filename": doc[1],
            "course_code": doc[2],
            "report": None
        }
    import json as _json
    report = _json.loads(row[0])
    return {"document_id": document_id, "report": report}


@app.get("/admin/diagnostics/pyq/{course_code}")
async def admin_pyq_diagnostics_summary(
    course_code: str, authorization: Optional[str] = None
):
    """Return PYQ stats for a course using count(DISTINCT q) to avoid double-counting."""
    _require_admin(authorization)
    neo4j_driver = get_neo4j()
    with neo4j_driver.session() as session:
        result = session.run("""
            MATCH (q:Question)-[:BELONGS_TO]->(c:Course {code: $code})
            OPTIONAL MATCH (q)-[:MAPPED_TO_CO]->(co:CourseOutcome)
            OPTIONAL MATCH (q)-[tr:TESTS_TOPIC]->(:Topic)
            RETURN
                count(DISTINCT q) AS total_questions,
                count(DISTINCT CASE WHEN co IS NOT NULL THEN q END) AS questions_with_co,
                count(DISTINCT CASE WHEN tr IS NOT NULL AND tr.review_status = 'approved' THEN q END) AS questions_mapped_to_topic,
                count(DISTINCT q.document_id) AS source_documents
        """, code=course_code).single()

        per_doc = session.run("""
            MATCH (q:Question)-[:BELONGS_TO]->(c:Course {code: $code})
            RETURN q.document_id AS document_id, count(DISTINCT q) AS question_count
            ORDER BY question_count DESC
        """, code=course_code).data()

    return {
        "course_code": course_code,
        "summary": dict(result) if result else {},
        "per_document": per_doc
    }


@app.get("/admin/diagnostics/pyq/{course_code}/questions")
async def admin_pyq_questions_detail(
    course_code: str, authorization: Optional[str] = None
):
    """Return all questions for a course with mapping details."""
    _require_admin(authorization)
    neo4j_driver = get_neo4j()
    with neo4j_driver.session() as session:
        records = session.run("""
            MATCH (q:Question)-[:BELONGS_TO]->(c:Course {code: $code})
            OPTIONAL MATCH (q)-[:MAPPED_TO_CO]->(co:CourseOutcome)
            OPTIONAL MATCH (q)-[tr:TESTS_TOPIC]->(t:Topic)
            WITH q, co, collect(t.name) AS topics, collect(tr.confidence) AS confidences
            RETURN DISTINCT
                q.document_id       AS upload_id,
                q.question_number   AS question_number,
                q.text              AS question,
                q.marks             AS marks,
                q.btl               AS btl,
                co.id               AS course_outcome,
                topics,
                confidences,
                q.image_url         AS image_url
            ORDER BY upload_id, toInteger(q.question_number)
        """, code=course_code).data()
        
        for record in records:
            if record.get("topics"):
                valid_topics = [t for t in record["topics"] if t]
                record["mapped_syllabus_topic"] = ", ".join(valid_topics) if valid_topics else None
            else:
                record["mapped_syllabus_topic"] = None
                
            if record.get("confidences"):
                valid_conf = [c for c in record["confidences"] if c]
                record["mapping_confidence"] = valid_conf[0] if valid_conf else None
            else:
                record["mapping_confidence"] = None

    return {"course_code": course_code, "questions": records, "total": len(records)}


# =============================================================================
# Admin Routes — Topic Mapping Review
# =============================================================================

@app.get("/admin/topic-mapping-review/{course_code}")
async def admin_topic_mapping_review(
    course_code: str, authorization: Optional[str] = None
):
    """List all TESTS_TOPIC relationships for a course, grouped by review_status."""
    _require_admin(authorization)
    neo4j_driver = get_neo4j()
    with neo4j_driver.session() as session:
        records = session.run("""
            MATCH (q:Question)-[r:TESTS_TOPIC]->(t:Topic)
            WHERE q.course_code = $code
            RETURN
                id(r)               AS relationship_id,
                q.id                AS question_id,
                q.question_number   AS question_number,
                q.text              AS question_text,
                t.id                AS topic_id,
                t.name              AS topic_name,
                r.confidence        AS confidence,
                r.semantic_score    AS semantic_score,
                r.keyword_score     AS keyword_score,
                r.review_status     AS review_status,
                r.mapped_at         AS mapped_at
            ORDER BY r.review_status, r.confidence DESC
        """, code=course_code).data()
    return {"course_code": course_code, "mappings": records, "total": len(records)}


class MappingUpdateRequest(BaseModel):
    review_status: str   # "approved" | "rejected"
    topic_id: Optional[str] = None   # for re-assignment

@app.put("/admin/topic-mapping-review/{relationship_id}")
async def admin_update_mapping(
    relationship_id: int, body: MappingUpdateRequest,
    authorization: Optional[str] = None
):
    """Approve, reject, or re-assign a TESTS_TOPIC relationship."""
    _require_admin(authorization)
    if body.review_status not in ("approved", "rejected"):
        raise HTTPException(status_code=400, detail="review_status must be 'approved' or 'rejected'.")
    neo4j_driver = get_neo4j()
    with neo4j_driver.session() as session:
        session.run("""
            MATCH ()-[r:TESTS_TOPIC]->() WHERE id(r) = $rid
            SET r.review_status = $status
        """, rid=relationship_id, status=body.review_status)
    return {"status": "updated", "relationship_id": relationship_id,
            "review_status": body.review_status}


# =============================================================================
# Route: /graph/{course_code} — Bidirectional course graph visualisation
# =============================================================================

@app.get("/graph/{course_code}")
async def get_course_graph(course_code: str, limit: int = 200):
    """
    Returns nodes and edges for the full course graph including BOTH:
    - Outgoing: Course → Unit → Topic (syllabus structure)
    - Incoming: Question → BELONGS_TO → Course (canonical questions)
    - Question → TESTS_TOPIC → Topic (Graph RAG edges)
    """
    neo4j_driver = get_neo4j()
    graph = fetch_graph_for_course(neo4j_driver, course_code.upper(), limit=limit)
    return graph


# =============================================================================
# Application Entry Point
# =============================================================================

# When you run `python app.py`, uvicorn starts the server on port 8000
if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
