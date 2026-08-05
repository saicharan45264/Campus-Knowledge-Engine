import os
import uuid
import base64
import shutil

# FastAPI is the core framework used to build our web API
from fastapi import FastAPI, UploadFile, File, Form, Depends, HTTPException, BackgroundTasks
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
    get_embedding, extract_knowledge_graph, save_to_neo4j, generate_answer,
    extract_syllabus_structure, build_syllabus_kg, extract_pyq_questions, map_questions_to_kg, clean_formula_text
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
    print("Database startup complete: Tables verified.")
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

class ChatRequest(BaseModel):
    """Defines the expected JSON structure when a student asks a question."""
    message: str

@app.post("/chat")
async def chat_endpoint(request: ChatRequest, db: AsyncSession = Depends(get_db)):
    """
    Takes a student's question, searches both PostgreSQL and Neo4j for relevant
    context, and generates an AI response grounded in that context.
    """
    question = request.message
    context_parts = []

    # --- Step 1: Graph Search in Neo4j (Structured Knowledge First) ---
    try:
        neo4j_driver = get_neo4j()
        # Extract keywords from the question, ignoring short stop words like "the", "for", "me"
        raw_words = question.lower().split()
        stop_words = {"get", "me", "the", "for", "and", "with", "this", "that", "course", "syllabus"}
        words = [w for w in raw_words if len(w) > 3 and w not in stop_words]
        
        # If no meaningful words remain, fallback to raw words
        if not words:
            words = raw_words

        with neo4j_driver.session() as session:
            # Query 1: Find any courses or topics matching the question.
            records = session.run(
                """
                MATCH (n)
                WHERE (n:Course OR n:Topic OR n:SubTopic)
                AND all(word IN $words WHERE toLower(n.name) CONTAINS word OR toLower(n.code) CONTAINS word)
                
                // If the matched node is a Topic/Subtopic, find its parent Course
                OPTIONAL MATCH (c:Course)-[:HAS_UNIT]->(:Unit)-[:HAS_TOPIC]->(t:Topic)
                WHERE n = t OR (t)-[:HAS_SUBTOPIC]->(n)
                
                // Identify the main course to pull the syllabus for
                WITH coalesce(c, n) as target_course, n as matched_node
                LIMIT 3
                
                // Now pull the full syllabus for the target_course
                OPTIONAL MATCH (target_course:Course)-[:HAS_UNIT]->(u:Unit)-[:HAS_TOPIC]->(t:Topic)
                
                RETURN 
                    target_course.code as course_code,
                    target_course.name as course_name,
                    labels(matched_node) as match_type,
                    matched_node.name as matched_node_name,
                    u.title as unit_title,
                    t.name as topic_name
                """,
                words=words
            )
            
            graph_facts = []
            syllabus_dict = {}
            
            for r in records:
                c_code = r['course_code']
                if not c_code: continue
                
                if r['unit_title'] and r['topic_name']:
                    if c_code not in syllabus_dict:
                        syllabus_dict[c_code] = {"name": r['course_name'], "units": {}}
                    if r['unit_title'] not in syllabus_dict[c_code]["units"]:
                        syllabus_dict[c_code]["units"][r['unit_title']] = []
                    
                    # Avoid duplicates
                    if r['topic_name'] not in syllabus_dict[c_code]["units"][r['unit_title']]:
                        syllabus_dict[c_code]["units"][r['unit_title']].append(r['topic_name'])
                else:
                    m_type = r['match_type'][0] if r['match_type'] else "Unknown"
                    graph_facts.append(f"[Course {c_code} - {m_type}] {r['matched_node_name']}")

            for c_code, data in syllabus_dict.items():
                fact = f"Course Syllabus for [{c_code}] {data['name']}:\n"
                for unit, topics in data['units'].items():
                    fact += f"  - {unit}: " + ", ".join(topics) + "\n"
                graph_facts.append(fact)

            # Query 2: Search PYQ Question & QuestionModel nodes in Neo4j (fetch across all uploaded documents)
            pyq_records = session.run(
                """
                MATCH (c:Course)-[:HAS_QUESTION_MODEL]->(qm:QuestionModel)-[:HAS_QUESTION]->(q:Question)
                WHERE any(word IN $words WHERE toLower(q.text) CONTAINS word OR toLower(qm.name) CONTAINS word OR (q.implicit_formulas IS NOT NULL AND any(f IN q.implicit_formulas WHERE toLower(f) CONTAINS word)))
                RETURN c.code as course_code, qm.name as model_name, q.question_number as q_num, q.text as q_text, q.implicit_formulas as formulas, q.image_url as image_url
                LIMIT 25
                """,
                words=words
            )
            for pr in pyq_records:
                q_text = pr['q_text']
                if q_text:
                    q_fact = f"[PYQ - Course {pr['course_code']} - {pr['q_num']}] Question: {q_text}"
                    if pr['formulas']:
                        cleaned_f = [clean_formula_text(f) for f in pr['formulas'] if clean_formula_text(f)]
                        if cleaned_f:
                            q_fact += f" (Formulas: {', '.join(cleaned_f)})"
                    if pr['image_url']:
                        q_fact += f"\n![Diagram for {pr['q_num']}]({pr['image_url']})"
                    graph_facts.append(q_fact)

            if graph_facts:
                context_parts.append("--- KNOWLEDGE GRAPH FACTS ---")
                context_parts.extend(graph_facts)
            else:
                # Fallback: If no specific course matched, list all available courses
                fallback_records = session.run("MATCH (c:Course) RETURN c.code as code, c.name as name")
                all_courses = [f"[{r['code']}] {r['name']}" for r in fallback_records]
                if all_courses:
                    context_parts.append("--- AVAILABLE COURSES IN THE DATABASE ---")
                    context_parts.append("\n".join(all_courses))
    except Exception as e:
        print(f"Neo4j graph search error: {e}")

    # --- Step 2: Vector Search in PostgreSQL ---
    question_embedding = await get_embedding(question)

    if question_embedding:
        try:
            query = select(DocumentChunk).order_by(
                DocumentChunk.embedding.cosine_distance(question_embedding)
            ).limit(10)
            
            result = await db.execute(query)
            similar_chunks = result.scalars().all()

            if similar_chunks:
                context_parts.append("--- RELEVANT TEXT FROM DOCUMENTS ---")
                for chunk in similar_chunks:
                    context_parts.append(chunk.content)
        except Exception as e:
            print(f"Postgres vector search error: {e}")

    # --- Step 3: Generate the Final AI Answer ---
    final_context = "\n".join(context_parts)
    
    # Truncate context to prevent LLM context-window overflow or timeouts
    if len(final_context) > 3000:
        final_context = final_context[:3000] + "\n...[Context Truncated for Length]..."
        
    print("DEBUG FINAL CONTEXT:")
    print(final_context)
    
    # Ask the AI model to answer the question using ONLY the provided context
    ai_response = await generate_answer(question, final_context)
    
    return {"response": ai_response}


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
