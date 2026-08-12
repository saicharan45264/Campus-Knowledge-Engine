"""
CurriculumLens Custom Web Server
---------------------------------
We intentionally avoided using frameworks like FastAPI or Django here.
By building our own HTTP request handler using Python's built-in `http.server`,
we demonstrate a fundamental understanding of how web servers, routing,
and WSGI/HTTP protocols work under the hood. This also keeps the project
lightweight and strictly \"Vanilla Python\".

Performance improvements in this version:
- ThreadingMixIn: each request is handled in its own thread, so long-running
  uploads/ingestion no longer block the chat endpoint.
- Async ingestion: /upload returns immediately; a background thread does the
  heavy PDF → KG work and updates an in-memory status dict.
- KG retrieval: /chat now queries Neo4j for topic/question matches in addition
  to the PostgreSQL hybrid search, surfacing the built knowledge graph.
- Keyword classifier: query classification is now instant (regex), not an LLM call.
"""
import os
import json
import warnings
with warnings.catch_warnings():
    warnings.filterwarnings("ignore", category=DeprecationWarning)
    import cgi

import re
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs

# We import the core and services
from core.database import init_db, get_db_connection, release_db_connection, get_neo4j
from core.security import verify_password, create_access_token, get_current_user, AuthError
from services.llm import get_embedding, classify_query, generate_answer
from services.retrieval import hybrid_retrieve, graph_retrieve, build_context
from services.ingestion import process_pdf, extract_syllabus_structure, process_and_map_pyq_document
from services.graph import build_syllabus_kg

# We need a fallback dict for mime types
import mimetypes
mimetypes.init()

# ── Async ingestion status registry ───────────────────────────────────────────
# Maps doc_id (int) -> {"status": "processing"|"done"|"error", "message": str}
_ingestion_status: dict[int, dict] = {}
_ingestion_lock = threading.Lock()


def _update_status(doc_id: int, status: str, message: str = ""):
    with _ingestion_lock:
        _ingestion_status[doc_id] = {"status": status, "message": message}


def _get_status(doc_id: int) -> dict:
    with _ingestion_lock:
        return _ingestion_status.get(doc_id, {"status": "unknown", "message": ""})


# ── Background ingestion worker ───────────────────────────────────────────────
def _run_ingestion(doc_id: int, file_path: str, doc_type: str,
                   course_code: str, dept: str, year: str):
    """
    Runs in a daemon thread after /upload returns 200 to the client.
    All heavy PDF → embedding → KG work happens here.
    """
    def progress(msg: str):
        print(f"[ingestion doc={doc_id}] {msg}")
        _update_status(doc_id, "processing", msg)

    try:
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                if doc_type == "syllabus":
                    progress("Extracting text from PDF…")
                    chunks   = process_pdf(file_path)
                    full_text = "\n".join(chunks)

                    progress("Parsing syllabus structure…")
                    struct = extract_syllabus_structure(full_text, dept, year)

                    progress(f"Building KG for {len(struct.get('courses', []))} courses…")
                    for c in struct.get("courses", []):
                        build_syllabus_kg(get_neo4j(), dept, year, [c], doc_id=doc_id)

                    progress(f"Embedding {len(chunks)} chunks…")
                    for i, chunk in enumerate(chunks):
                        emb = get_embedding(chunk)
                        cur.execute(
                            "INSERT INTO chunks (document_id, content, embedding) "
                            "VALUES (%s, %s, CAST(%s AS vector))",
                            (doc_id, chunk, str(emb))
                        )
                        if (i + 1) % 10 == 0:
                            progress(f"Embedded {i + 1}/{len(chunks)} chunks…")

                elif doc_type == "pyq":
                    progress("Fetching syllabus topics from KG…")
                    with get_neo4j().session() as s:
                        res = s.run(
                            "MATCH (t:Topic {course_code: $code}) RETURN t.name AS name "
                            "UNION MATCH (st:SubTopic {course_code: $code}) RETURN st.name AS name",
                            code=course_code
                        )
                        topics = [r["name"] for r in res]

                    progress(f"Processing PYQ PDF ({len(topics)} topics available)…")
                    pyq_results = process_and_map_pyq_document(
                        file_path, course_code, topics, get_neo4j(),
                        progress_callback=progress,
                        doc_id=doc_id
                    )

                    progress(f"Embedding {len(pyq_results)} PYQ chunks…")
                    for i, (q, fname) in enumerate(pyq_results):
                        text_content = (
                            f"Course {course_code} PYQ Question: {q['text']} "
                            f"(Marks: {q.get('marks', 'N/A')}, "
                            f"CO: {q.get('co', 'N/A')}, "
                            f"BTL: {q.get('btl', 'N/A')})"
                        )
                        emb = get_embedding(text_content)
                        cur.execute(
                            "INSERT INTO chunks (document_id, content, embedding) "
                            "VALUES (%s, %s, CAST(%s AS vector))",
                            (doc_id, text_content, str(emb))
                        )
                        if (i + 1) % 5 == 0:
                            progress(f"Embedded {i + 1}/{len(pyq_results)} PYQ chunks…")

                conn.commit()
                _update_status(doc_id, "done", "Ingestion complete")
                print(f"[ingestion doc={doc_id}] ✓ done")

        finally:
            release_db_connection(conn)

    except Exception as e:
        print(f"[ingestion doc={doc_id}] ERROR: {e}")
        _update_status(doc_id, "error", str(e))


# ── Threaded HTTP server ───────────────────────────────────────────────────────
class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """
    Mix in ThreadingMixIn so each request gets its own thread.
    Without this, a long-running upload blocks every other request
    (including /chat) for the entire duration of ingestion.
    """
    daemon_threads = True   # background threads die when main thread exits


class CurriculumLensHandler(BaseHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, DELETE, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Authorization, Content-Type')
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    def send_error_json(self, code, message):
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({"detail": message}).encode('utf-8'))

    def send_json(self, data):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode('utf-8'))

    def get_auth_user(self):
        auth_header = self.headers.get('Authorization')
        if not auth_header or not auth_header.startswith("Bearer "):
            raise AuthError("Unauthorized")
        token = auth_header.split(" ")[1]
        conn = get_db_connection()
        try:
            return get_current_user(token, conn)
        finally:
            release_db_connection(conn)

    def do_GET(self):
        """
        Handles all incoming GET requests.
        Acts as our custom router, directing requests to either the REST API
        or serving the static frontend files.
        """
        if self.path.startswith('/documents'):
            try:
                user = self.get_auth_user()
                conn = get_db_connection()
                try:
                    # Using RealDictCursor so our raw SQL returns dictionaries (like an ORM would)
                    from psycopg2.extras import RealDictCursor
                    with conn.cursor(cursor_factory=RealDictCursor) as cur:
                        cur.execute("SELECT * FROM documents ORDER BY created_at DESC")
                        docs = cur.fetchall()
                        # Fix datetime serialization
                        for d in docs:
                            if d['created_at']:
                                d['created_at'] = d['created_at'].isoformat()
                            # Attach ingestion status from the in-memory registry
                            d['ingestion_status'] = _get_status(d['id']).get('status', 'done')
                            d['ingestion_message'] = _get_status(d['id']).get('message', '')
                        self.send_json(docs)
                finally:
                    release_db_connection(conn)
            except AuthError:
                self.send_error_json(401, "Unauthorized")
            except Exception as e:
                self.send_error_json(500, str(e))
            return

        # ── Ingestion status endpoint ──────────────────────────────────────────
        # GET /upload/status/<doc_id>  → {"status": "processing"|"done"|"error", "message": "..."}
        m = re.match(r'^/upload/status/(\d+)$', self.path)
        if m:
            try:
                self.get_auth_user()
                doc_id = int(m.group(1))
                self.send_json(_get_status(doc_id))
            except AuthError:
                self.send_error_json(401, "Unauthorized")
            except Exception as e:
                self.send_error_json(500, str(e))
            return

        if self.path == '/evaluate':
            try:
                user = self.get_auth_user()
                # Return mock evaluation metrics for the admin dashboard
                self.send_json({
                    "metrics": {
                        "context_precision": 0.82,
                        "faithfulness": 0.91,
                        "answer_relevance": 0.78,
                        "context_recall": 0.88,
                        "answer_correctness": 0.85
                    },
                    "num_samples": 5,
                    "model": "gemma4:12b-it-qat"
                })
            except AuthError:
                self.send_error_json(401, "Unauthorized")
            except Exception as e:
                self.send_error_json(500, str(e))
            return

        # Serve static files (mocking a CDN for uploaded images)
        if self.path.startswith('/static/'):
            filename = os.path.join(os.path.dirname(__file__), 'uploads', self.path.replace('/static/', '', 1))
            self.serve_file(filename)
            return

        # Serve frontend files
        frontend_dir = os.path.join(os.path.dirname(__file__), '..', 'frontend')
        if self.path == '/' or self.path == '':
            self.serve_file(os.path.join(frontend_dir, 'public', 'login.html'))
            return
        elif self.path.startswith('/public/') or self.path.startswith('/src/'):
            safe_path = self.path.lstrip('/')
            self.serve_file(os.path.join(frontend_dir, safe_path))
            return

        self.send_error_json(404, "Not Found")

    def do_POST(self):
        """
        Handles all incoming POST requests (Form submissions, API calls).
        Manually parses headers, form data, and JSON bodies instead of relying on Pydantic.
        """
        if self.path == '/login':
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length).decode('utf-8')
            
            # Manually parse urlencoded form data
            form = parse_qs(post_data)
            username = form.get('username', [''])[0]
            password = form.get('password', [''])[0]

            conn = get_db_connection()
            try:
                from psycopg2.extras import RealDictCursor
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("SELECT * FROM users WHERE username = %s", (username,))
                    user = cur.fetchone()
                if not user or not verify_password(password, user['hashed_password']):
                    self.send_error_json(401, "Incorrect username or password")
                    return
                token = create_access_token(data={"sub": user["username"], "role": user["role"]})
                self.send_json({"access_token": token, "token_type": "bearer"})
            finally:
                release_db_connection(conn)
            return

        if self.path == '/chat':
            try:
                user = self.get_auth_user()
                content_length = int(self.headers.get('Content-Length', 0))
                post_data = json.loads(self.rfile.read(content_length).decode('utf-8'))
                message = post_data.get('message', '')

                # ── Step 1: classify (instant, keyword-based, no LLM) ─────────
                classification = classify_query(message)
                labels = [classification.get("primary_label")]
                if classification.get("secondary_label"):
                    labels.append(classification.get("secondary_label"))

                # ── Step 2: embed (one Ollama call) ───────────────────────────
                emb = get_embedding(message)

                # ── Step 3: hybrid PostgreSQL retrieval ───────────────────────
                conn = get_db_connection()
                try:
                    pg_results = hybrid_retrieve(message, emb, labels, conn, top_k=6)
                finally:
                    release_db_connection(conn)

                # ── Step 4: KG retrieval (NEW) — query Neo4j for topics & PYQs
                kg_results = graph_retrieve(message, get_neo4j(), top_k=6)

                # ── Step 5: merge both sources into one context string ─────────
                context_str = build_context(pg_results, kg_results)

                # Setup streaming response
                self.send_response(200)
                self.send_header('Content-Type', 'text/plain')
                self.send_header('Transfer-Encoding', 'chunked')
                self.send_header('X-Message-ID', 'msg_fallback')
                self.end_headers()

                # Generate Answer (yields chunks)
                for chunk in generate_answer(message, context_str, ""):
                    if not chunk: continue
                    encoded = chunk.encode('utf-8')
                    self.wfile.write(f"{len(encoded):X}\r\n".encode('utf-8'))
                    self.wfile.write(encoded + b"\r\n")
                    self.wfile.flush()
                self.wfile.write(b"0\r\n\r\n")

            except AuthError:
                self.send_error_json(401, "Unauthorized")
            except Exception as e:
                self.send_error_json(500, str(e))
            return
        
        if self.path == '/upload':
            try:
                user = self.get_auth_user()
                # Use cgi.FieldStorage for multipart parsing
                form = cgi.FieldStorage(
                    fp=self.rfile,
                    headers=self.headers,
                    environ={'REQUEST_METHOD': 'POST'}
                )
                
                if 'file' not in form or 'doc_type' not in form:
                    self.send_error_json(400, "Missing fields")
                    return
                
                file_item = form['file']
                doc_type  = form.getvalue('doc_type')
                
                if doc_type == 'syllabus':
                    dept        = form.getvalue('dept') or 'CSE'
                    year        = form.getvalue('year') or '2023'
                    course_code = f"{dept}-{year}"
                else:
                    course_code = form.getvalue('course_code')
                    dept        = 'N/A'
                    year        = 'N/A'

                if not course_code:
                    self.send_error_json(400, "Missing course code or dept/year")
                    return

                # ── Save file to disk (fast, synchronous) ─────────────────────
                os.makedirs('uploads', exist_ok=True)
                file_path = os.path.join('uploads', os.path.basename(file_item.filename))
                with open(file_path, 'wb') as f:
                    f.write(file_item.file.read())

                # ── Insert document record in DB (fast, synchronous) ──────────
                conn = get_db_connection()
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            "INSERT INTO documents (filename, doc_type, course_code) "
                            "VALUES (%s, %s, %s) RETURNING id",
                            (file_item.filename, doc_type, course_code)
                        )
                        doc_id = cur.fetchone()[0]
                        conn.commit()
                finally:
                    release_db_connection(conn)

                # ── Kick off background ingestion thread ──────────────────────
                _update_status(doc_id, "processing", "Queued for ingestion…")
                t = threading.Thread(
                    target=_run_ingestion,
                    args=(doc_id, file_path, doc_type, course_code, dept, year),
                    daemon=True,
                    name=f"ingestion-{doc_id}",
                )
                t.start()

                # ── Return immediately — client polls /upload/status/<doc_id> ─
                self.send_json({
                    "status":  "processing",
                    "doc_id":  doc_id,
                    "message": "File received. Ingestion running in background.",
                })

            except AuthError:
                self.send_error_json(401, "Unauthorized")
            except Exception as e:
                self.send_error_json(500, str(e))
            return
            
        if self.path == '/reset':
            try:
                user = self.get_auth_user()
                if user['role'] != 'admin':
                    self.send_error_json(403, "Forbidden")
                    return
                conn = get_db_connection()
                try:
                    with conn.cursor() as cur:
                        cur.execute("TRUNCATE TABLE chunks, documents CASCADE")
                        conn.commit()
                    # Wipe Neo4j
                    with get_neo4j().session() as s:
                        s.run("MATCH (n) DETACH DELETE n")
                    # Wipe uploads directory
                    import shutil
                    if os.path.exists('uploads'):
                        shutil.rmtree('uploads')
                    os.makedirs('uploads', exist_ok=True)
                    # Clear in-memory status registry
                    with _ingestion_lock:
                        _ingestion_status.clear()
                    
                    self.send_json({"status": "success"})
                finally:
                    release_db_connection(conn)
            except AuthError:
                self.send_error_json(401, "Unauthorized")
            except Exception as e:
                self.send_error_json(500, str(e))
            return
            
    def do_DELETE(self):
        """
        Handles DELETE requests, specifically for deleting documents by ID.
        """
        if self.path.startswith('/documents/'):
            try:
                user = self.get_auth_user()
                if user['role'] != 'admin':
                    self.send_error_json(403, "Forbidden")
                    return
                
                doc_id = self.path.split('/')[-1]
                if not doc_id.isdigit():
                    self.send_error_json(400, "Invalid document ID")
                    return
                    
                conn = get_db_connection()
                try:
                    with conn.cursor() as cur:
                        cur.execute("DELETE FROM documents WHERE id = %s RETURNING filename, doc_type, course_code", (doc_id,))
                        deleted_doc = cur.fetchone()
                        if not deleted_doc:
                            self.send_error_json(404, "Document not found")
                            return
                            
                        filename, doc_type, course_code = deleted_doc
                        conn.commit()
                        
                        # Delete the physical PDF file
                        try:
                            file_path = os.path.join('uploads', filename)
                            if os.path.exists(file_path):
                                os.remove(file_path)
                        except Exception as e:
                            print(f"Warning: could not delete physical file {filename}: {e}")
                        
                        # Delete corresponding neo4j nodes based on doc_id
                        with get_neo4j().session() as s:
                            if doc_type == "syllabus":
                                s.run("""
                                    MATCH (c:Course {doc_id: $doc_id})
                                    OPTIONAL MATCH (c)-[*]->(n)
                                    WHERE NOT n:Department
                                    DETACH DELETE c, n
                                """, doc_id=int(doc_id))
                            elif doc_type == "pyq":
                                # Collect image paths BEFORE deleting nodes
                                img_result = s.run("""
                                    MATCH (q:Question {doc_id: $doc_id})
                                    RETURN COLLECT(DISTINCT q.image_path) AS paths
                                """, doc_id=int(doc_id))
                                record = img_result.single()
                                image_paths = record["paths"] if record else []

                                s.run("""
                                    MATCH (q:Question {doc_id: $doc_id})
                                    OPTIONAL MATCH (q)-[:HAS_SUBQUESTION]->(sub)
                                    DETACH DELETE q, sub
                                """, doc_id=int(doc_id))

                                # Delete image files from disk
                                for img_path in image_paths:
                                    if img_path:
                                        full = os.path.join("uploads", img_path)
                                        try:
                                            if os.path.exists(full):
                                                os.remove(full)
                                                print(f"Deleted image: {img_path}")
                                        except Exception as e:
                                            print(f"Warning: could not delete image {img_path}: {e}")
                                
                            # Clean up orphaned nodes (like textbooks, outcomes) that no longer have relationships
                            s.run("MATCH (n) WHERE NOT (n)--() AND NOT n:Department DETACH DELETE n")

                        # Remove from status registry
                        with _ingestion_lock:
                            _ingestion_status.pop(int(doc_id), None)
                            
                        self.send_json({"status": "success", "message": "Document deleted successfully"})
                finally:
                    release_db_connection(conn)
            except AuthError:
                self.send_error_json(401, "Unauthorized")
            except Exception as e:
                self.send_error_json(500, str(e))
            return
            
        self.send_error_json(404, "Not Found")

    def serve_file(self, path):
        if not os.path.exists(path):
            self.send_error_json(404, "File not found")
            return
        content_type, _ = mimetypes.guess_type(path)
        if not content_type:
            content_type = 'application/octet-stream'
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.end_headers()
        with open(path, 'rb') as f:
            self.wfile.write(f.read())

if __name__ == '__main__':
    # When the server starts, ensure our raw SQL tables exist.
    # This replaces the need for Alembic migrations or SQLAlchemy Base.metadata.create_all
    print("Initializing Database...")
    init_db()
    
    conn = get_db_connection()
    try:
        from core.security import get_password_hash
        with conn.cursor() as cur:
            # Seed the database with default users if empty
            cur.execute("SELECT COUNT(*) FROM users")
            if cur.fetchone()[0] == 0:
                cur.execute(
                    "INSERT INTO users (username, hashed_password, role) VALUES (%s, %s, %s)",
                    ("student", get_password_hash("student123"), "student")
                )
                cur.execute(
                    "INSERT INTO users (username, hashed_password, role) VALUES (%s, %s, %s)",
                    ("admin", get_password_hash("admin123"), "admin")
                )
                conn.commit()
                print("Created default users.")
    finally:
        release_db_connection(conn)

    print("Starting Threaded Vanilla Python Server on port 8000...")
    server = ThreadedHTTPServer(('0.0.0.0', 8000), CurriculumLensHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    server.server_close()
