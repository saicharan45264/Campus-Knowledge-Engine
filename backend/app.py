import os
import uuid
import base64
import shutil

# FastAPI is the core framework used to build our web API
from fastapi import FastAPI, UploadFile, File, Form, Depends, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse
from contextlib import asynccontextmanager
# CORSMiddleware allows our frontend (HTML files) to communicate with this backend
from fastapi.middleware.cors import CORSMiddleware
# SQLAlchemy components for interacting with our PostgreSQL database
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text
# Pydantic is used to define the structure of incoming data (like JSON requests)
from pydantic import BaseModel
import uvicorn
from fastapi.staticfiles import StaticFiles

# Import our custom database configurations and models
from database import get_db, get_neo4j, Base, engine, Document, DocumentChunk
from utils import (
    process_pdf, process_pyq_visuals, describe_page_image, describe_uploaded_image,
    get_embedding, extract_knowledge_graph, save_to_neo4j, generate_answer, generate_answer_stream,
    extract_syllabus_structure, build_syllabus_kg, extract_pyq_questions, map_questions_to_kg, clean_formula_text,
    extract_pyq_structured, add_prerequisite_edges, PREREQUISITE_MAP, map_pyq_structured_to_kg, hybrid_search_rrf, execute_neo4j_pyq_search
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
    allow_credentials=True,
    allow_methods=["*"],       # Allow all HTTP methods (GET, POST, DELETE, etc.)
    allow_headers=["*"],       # Allow all HTTP headers
)

# Mount the static directory so the frontend can retrieve images
os.makedirs("uploads/images", exist_ok=True)
app.mount("/static", StaticFiles(directory="uploads"), name="static")


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
    if any(k in q_lower for k in ["prerequisite", "before taking", "should i know", "requires", "needed for"]):
        return "MULTI_HOP_PREREQ"
    elif any(k in q_lower for k in ["btl", "co1", "co2", "co3", "co4", "co5", "bloom", "mapped to", "course outcome questions"]):
        return "GRAPH_PYQ_MAPPING"
    elif any(k in q_lower for k in ["syllabus", "topics", "units", "course outcomes", "objectives"]):
        return "SIMPLE_CURRICULUM"
    elif any(k in q_lower for k in ["question", "list questions", "pyq", "past year"]):
        return "SIMPLE_PYQ"
        
    # Tier 2: LLM Fallback (Slow)
    prompt = f"""
You are a query classification engine for a university information system.
Classify the user's question into exactly one primary category.

Categories:
- "SIMPLE_CURRICULUM": Questions about courses, syllabus content, units, topics, learning outcomes.
- "SIMPLE_PYQ": Questions asking for past year questions on a topic.
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
                timeout=30.0
            )
            ans = response.json().get("response", "").strip().upper()
            if ans in ["SIMPLE_CURRICULUM", "SIMPLE_PYQ", "MULTI_HOP_PREREQ", "GRAPH_PYQ_MAPPING"]:
                return ans
    except Exception as e:
        print(f"LLM classification error: {type(e).__name__}: {e}")
        
    return "SIMPLE_PYQ" # Default fallback for general questions


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
    
    if intent == "MULTI_HOP_PREREQ":
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
        # Search Neo4j Graph for Syllabus Units & Topics
        records = execute_graph_syllabus_query(neo4j_driver, question)
        if records:
            syllabus_dict = {}
            for r in records:
                c_code = r['course_code']
                if c_code not in syllabus_dict:
                    syllabus_dict[c_code] = {"name": r['course_name'], "units": {}}
                if r['unit_title'] and r['topics']:
                    syllabus_dict[c_code]["units"][r['unit_title']] = r['topics']
            
            for c_code, data in syllabus_dict.items():
                fact = f"Course Syllabus for [{c_code}] {data['name']}:\n"
                for unit, topics in data['units'].items():
                    fact += f"  - {unit}: " + ", ".join(topics) + "\n"
                context_parts.append(fact)

        # Also search PostgreSQL via RRF for extra prose chunks
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
        import re as _re
        def _dedup_key(text: str) -> str:
            """Strip [PYQ - ...] / [Course: ...] prefix and normalize whitespace for deduplication."""
            text = text.strip()
            text = _re.sub(r'^\[(?:PYQ|Course)[^\]]+\]\s*', '', text)
            return ' '.join(text.split())[:120]

        seen_texts = set()  # Track seen question texts to avoid duplicates
        neo4j_driver = get_neo4j()
        neo4j_results = execute_neo4j_pyq_search(neo4j_driver, question)
        
        if neo4j_results:
            context_parts.append("--- NEO4J PYQ SEARCH RESULTS ---")
            for record in neo4j_results:
                q_text = record.get('q_text', '')
                # Strip CO/BTL distribution tables that bloat context but add no value
                q_text = _re.split(r'(?:Course Outcome|CO\s*\n|\*{3,})', q_text)[0].strip()
                key = _dedup_key(q_text)
                if key in seen_texts:
                    continue
                seen_texts.add(key)
                img_url = record['image_url'].replace(' ', '%20') if record.get('image_url') else None
                img_markdown = f"\n![Diagram for Q{record['q_num']}]({img_url})" if img_url and img_url != "None" else ""
                context_parts.append(f"[Course: {record['course_code']} - Q: {record['q_num']}]\n{q_text}{img_markdown}\nMarks: {record['marks']}\nBTL: {record['btl']}")
        
        # Always supplement with hybrid search to catch anything Neo4j missed
        question_embedding = await get_embedding(question)
        if question_embedding:
            try:
                chunks = await hybrid_search_rrf(db, question, question_embedding, k=5)
                if chunks:
                    extra_parts = []
                    for chunk in chunks:
                        content = chunk.get("content", "")
                        key = _dedup_key(content)
                        if content and key not in seen_texts:
                            seen_texts.add(key)
                            extra_parts.append(content)
                    if extra_parts:
                        context_parts.append("--- ADDITIONAL TEXT CHUNKS ---")
                        context_parts.extend(extra_parts)
            except Exception as e:
                print(f"Hybrid search error in /chat: {e}")
        
        # ALWAYS run PostgreSQL full-text search as a guaranteed fallback
        # This works even when the embedding model is unavailable (GPU busy)
        try:
            stop_words_pg = {'get', 'me', 'a', 'the', 'all', 'questions', 'question',
                             'on', 'about', 'give', 'find', 'show', 'list', 'what', 'how'}
            kws = [w.strip(".,!?-'\"") for w in question.lower().split() 
                   if len(w) > 3 and w not in stop_words_pg]
            if kws:
                pg_query = " & ".join(kws)  # AND search across all keywords to prevent false positives
                from sqlalchemy import text as sql_text
                pg_result = await db.execute(sql_text("""
                    SELECT content FROM document_chunks
                    WHERE tsv_content @@ to_tsquery('english', :q)
                    ORDER BY ts_rank(tsv_content, to_tsquery('english', :q)) DESC
                    LIMIT 10
                """), {"q": pg_query})
                pg_rows = pg_result.fetchall()
                pg_extra = []
                for row in pg_rows:
                    content = row[0] or ""
                    key = _dedup_key(content)
                    if content and key not in seen_texts:
                        seen_texts.add(key)
                        pg_extra.append(content)
                if pg_extra:
                    if "--- ADDITIONAL TEXT CHUNKS ---" not in context_parts:
                        context_parts.append("--- ADDITIONAL TEXT CHUNKS ---")
                    context_parts.extend(pg_extra)
        except Exception as e:
            print(f"PostgreSQL text search error: {e}")

    # Generate final answer
    final_context = "\n".join(context_parts)
    if len(final_context) > 8000:
        final_context = final_context[:12000] + "\n...[Context Truncated]..."
        
    print("DEBUG FINAL CONTEXT:")
    print(final_context)
    
    # Stream response and cache it
    async def stream_and_cache():
        full_response = []
        async for chunk in generate_answer_stream(question, final_context):
            full_response.append(chunk)
            yield chunk
            
        # Only append images that the LLM dropped from its response
        import re
        ai_text = "".join(full_response)
        all_context_images = re.findall(r'!\[.*?\]\(.*?\)', final_context)
        missed_images = [img for img in all_context_images if re.search(r'\((.+?)\)', img).group(1) not in ai_text]
        if missed_images:
            image_block = "\n\n### Diagrams from Questions:\n" + "\n\n".join(missed_images)
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
    year: Optional[str] = Form(None),
    course_code: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db)
):
    """
    Accepts single or multiple PDF documents uploaded by the admin.
    Depending on `doc_type` ("syllabus" or "pyq"), dispatches to the correct pipeline.
    """
    os.makedirs("uploads", exist_ok=True)
    
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
            course_code=course_code
        )
        db.add(new_doc)
        await db.commit()
        await db.refresh(new_doc)

        if doc_type == "syllabus":
            background_tasks.add_task(process_syllabus_background, file_path, department, year, new_doc.id)
        elif doc_type == "pyq":
            background_tasks.add_task(process_pyq_background, file_path, course_code, new_doc.id)

    return {"message": f"{len(files)} file(s) uploaded successfully! Processing in the background."}


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
        
        # Format the results into a list of dictionaries
        return [
            {
                "id": str(doc.id),
                "filename": doc.filename,
                "course_code": doc.course_code,
                "created_at": doc.created_at.isoformat()
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
                        # Delete Question nodes tagged with this document_id or course_code
                        session.run("""
                            MATCH (q:Question)
                            WHERE q.document_id = $doc_id OR (q.course_code = $c_code AND $c_code IS NOT NULL)
                            DETACH DELETE q
                        """, doc_id=str(doc_id), c_code=c_code)
                        
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

            # 3. Clean up physical image files from disk
            try:
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

    # 3. Wipe Disk (Delete the entire uploads folder and recreate it empty)
    try:
        uploads_dir = "uploads"
        if os.path.exists(uploads_dir):
            shutil.rmtree(uploads_dir)
            os.makedirs(uploads_dir)
        print("System Reset: Uploads folder cleared.")
    except Exception as e:
        errors.append(f"File system error: {e}")

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

async def process_syllabus_background(file_path: str, department: str, year: str, document_id: uuid.UUID):
    print(f"[Syllabus] Starting processing for {department} {year}...")
    
    # Extract text from the PDF
    chunks = process_pdf(file_path)
    full_text = " ".join(chunks)

    # With the new deterministic regex parser, we don't need to chunk the text!
    # We can pass the entire massive syllabus string directly.
    neo4j_driver = get_neo4j()
    
    print(f"[Syllabus] Using deterministic regex parser on full text ({len(full_text)} chars)...")
    structure = await extract_syllabus_structure(full_text, department, year)
    courses = structure.get("courses", [])
    
    # Build Knowledge Graph
    if courses:
        build_syllabus_kg(neo4j_driver, department, year, courses)

    # (Removed redundant vector embedding loop for syllabus text)
    # The entire Syllabus structure is now perfectly captured in the Neo4j Knowledge Graph,
    # so we don't need to hammer the Ngrok tunnel with 4,000+ vector embedding requests.
    
    print(f"[Syllabus] Finished processing {department} {year}.")

async def process_pyq_background(file_path: str, course_code: str, document_id: uuid.UUID):
    print(f"[PYQ] Starting processing for {course_code}...")
    neo4j_driver = get_neo4j()

    # Stage 1: Structured Text Extractor (fast, CO/BTL-aware)
    structured_qs = extract_pyq_structured(file_path, course_code, str(document_id))
    
    if structured_qs:
        print(f"[PYQ] Text extractor found {len(structured_qs)} questions. Mapping to KG...")
        map_pyq_structured_to_kg(neo4j_driver, structured_qs)
        
        async for db in get_db():
            for q in structured_qs:
                labeled_content = f"[PYQ - {course_code} - {q['question_number']}]\n{q['question_text']}"
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
            await db.commit()
            break
        print(f"[PYQ] Finished processing structured text for {course_code}!")
        return
        
    print(f"[PYQ] Text extractor found 0 questions. Falling back to Vision AI...")

    # Stage 2: Vision pipeline
    # Render pages as chunked images
    page_chunks = process_pyq_visuals(file_path)
    print(f"[PYQ] Rendered {len(page_chunks)} image chunks.")

    async for db in get_db():
        for i, chunk_data in enumerate(page_chunks):
            print(f"[PYQ] Extracting questions from chunk {i+1}/{len(page_chunks)} via Vision AI...")
            try:
                # Extract questions via Vision
                questions = await extract_pyq_questions(chunk_data["base64"])
                
                if not questions:
                    continue

                # Save the physical image of the chunk to the disk so the frontend can display it
                image_bytes = base64.b64decode(chunk_data["base64"])
                page_num = chunk_data['page'] + 1
                chunk_index = chunk_data['chunk_index']
                image_filename = f"images/{document_id}_page_{page_num}_chunk_{chunk_index}.jpg"
                image_path = f"uploads/{image_filename}"
                image_url = f"http://localhost:8000/static/{image_filename}"
                
                os.makedirs("uploads/images", exist_ok=True)
                with open(image_path, "wb") as f:
                    f.write(image_bytes)

                # Map extracted questions into the KG
                map_questions_to_kg(neo4j_driver, course_code, questions, document_id, image_url)
                
                for q in questions:
                    if isinstance(q, str):
                        q = {"text": q, "question_number": "Unknown", "likely_topic": "General", "implicit_formulas": []}
                        
                    q_text = q.get("text", "")
                    
                    # Validate q_text before storing to prevent junk/JSON
                    if len(q_text) < 20 or q_text.startswith('{') or '{"' in q_text:
                        continue
                    if any(junk in q_text[:50].lower() for junk in ['answer all', 'part a', 'part b', 'co |', 'course outcomes']):
                        continue
                        
                    # Append the markdown image link so the LLM includes it in the chat
                    markdown_image = f"![PYQ Page - {course_code}](http://localhost:8000/static/{image_filename})"
                    
                    labeled_content = f"[PYQ - {course_code} - {q.get('question_number')}]\n{q_text}\nImplicit Formulas: {', '.join(q.get('implicit_formulas', []))}\n\n{markdown_image}"
                    
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
                        await db.commit()

            except Exception as e:
                import traceback
                print(f"[PYQ] Error processing chunk {chunk_data['chunk_index']} on page {chunk_data['page'] + 1}: {e}")
                traceback.print_exc()
                await db.rollback()
        break

    print(f"[PYQ] Finished processing all content for {course_code}!")


# =============================================================================
# Application Entry Point
# =============================================================================

# When you run `python app.py`, uvicorn starts the server on port 8000
if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
