# CurriculumLens — Complete Technical Deep Dive

## 1. What the Project Is

CurriculumLens is a **Graph-RAG (Retrieval-Augmented Generation) system** built as a Final Year Project at Amrita Vishwa Vidyapeetham, Coimbatore. It turns raw academic PDFs (syllabuses, question papers) into an AI-powered campus knowledge engine that students can query in natural language.

The entire stack is **Vanilla** — Vanilla Python for the backend, Vanilla HTML/CSS/JS for the frontend. No FastAPI, no React, no SQLAlchemy, no LangChain.

---

## 2. Full Tech Stack

| Layer | Tool / Technology | Why |
|---|---|---|
| **Backend Web Server** | Python `http.server.BaseHTTPRequestHandler` | Custom, no framework |
| **Relational + Vector DB** | PostgreSQL 16 + `pgvector` extension + `tsvector` | Hybrid search (semantic + keyword) |
| **Graph Database** | Neo4j 5.x | Curriculum knowledge graph, PYQ→Topic mapping |
| **DB Driver** | `psycopg2` (raw SQL) + `neo4j` Python driver | **No ORM wrappers** |
| **LLM — Generation** | `gemma4:12b-it-qat` via Ollama | Multimodal, 12B params, runs locally |
| **LLM — Embeddings** | `nomic-embed-text` via Ollama | 768-dimensional vectors |
| **LLM — Vision** | Same `gemma4` model, multimodal mode | PYQ image parsing, student image uploads |
| **PDF Processing** | PyMuPDF (`fitz`) | Page-by-page text + image extraction |
| **Image Processing** | Pillow (`PIL`) | Crop individual questions from exam pages |
| **Auth** | `python-jose` (JWT HS256) + `passlib`/`bcrypt` | Stateless token auth |
| **HTTP Client** | `httpx` | Sync HTTP calls to Ollama REST API |
| **Frontend** | Vanilla HTML + CSS + JavaScript | No frameworks |
| **Infrastructure** | Docker Compose | Spins up PostgreSQL + Neo4j locally |
| **Cloud GPU** | Google Colab T4 + Ngrok tunnel | Runs the 12B Ollama model for free |

---

## 3. Tools & Techniques Explained

### 3.1 Raw SQL vs. ORM (Object-Relational Mapping)
Most modern applications use an ORM (like SQLAlchemy or Prisma) which hides the database behind Python code. For example, `session.query(User).filter_by(name="student")`. 
**What we did:** We explicitly rejected ORMs. We use `psycopg2` to write the **raw SQL queries ourselves** (e.g., `SELECT * FROM users WHERE username = %s`). 
**Why it matters:** It proves to the panel that you deeply understand SQL syntax, database connection pooling, cursor management, and how to prevent SQL injection attacks manually, rather than just relying on a magic library to do it for you.

### 3.2 Chunked HTTP Streaming
When an AI generates an answer, it can take 10 to 30 seconds. If we waited for the whole answer to finish before sending it to the frontend, the student would just see a loading spinner for 30 seconds.
**What we did:** We implemented **HTTP Chunked Transfer Encoding**. As the Ollama model generates a single word (token), our Python server immediately wraps it in an HTTP "chunk" and sends it across the network. 
**Why it matters:** The browser receives these chunks instantly via a `ReadableStream` and appends them to the chat UI in real-time. This creates the "typing" effect (like ChatGPT) and gives the user zero perceived latency.

### 3.3 pgvector — Semantic (Vector) Search
Every text chunk ingested from a PDF is converted into a **768-dimensional floating-point vector** (an embedding) by the `nomic-embed-text` model via Ollama. This embedding is stored in a `vector(768)` column in PostgreSQL using the `pgvector` extension.

When a student asks a question, that question is *also* embedded. PostgreSQL then uses the `<=>` operator (**cosine distance**) to find the stored chunks whose vectors are nearest to the question's vector — i.e., chunks that are *semantically similar*, even if they use different words.

### 3.4 tsvector — Lexical (Full-Text) Keyword Search
PostgreSQL has a built-in full-text search engine using `tsvector` (term vectors) and `tsquery`. Every chunk is indexed with `to_tsvector('english', content)`. When a query comes in, `plainto_tsquery('english', question)` matches chunks that contain the *exact words* or their stems (e.g., "circuit" matches "circuits").

This catches things vector search misses — like exact course codes (`23EEE104`) or specific technical terms.

### 3.5 Reciprocal Rank Fusion (RRF)
The key hybrid technique. Both the semantic search and the lexical search independently rank all chunks. RRF merges these two ranked lists using the formula:

```
RRF_score = 1/(k + rank_semantic) + 1/(k + rank_lexical)
```

where `k = 60` (from the original 2009 paper). Chunks that rank high in *both* lists get a very high combined score. This is better than a raw score combination because it doesn't require calibrating the magnitudes of similarity scores vs text-match scores.

**This entire query is written as a single raw SQL CTE** (`WITH semantic AS (...), lexical AS (...), fused AS (...)`) directly in `retrieval.py`.

### 3.6 Neo4j Knowledge Graph
When a syllabus PDF is uploaded, we don't just store the text — we build a **structured property graph** in Neo4j. The node types and relationships are:

```
(:Department)-[:OFFERS]->(:Course)
(:Semester)-[:INCLUDES]->(:Course)
(:Course)-[:HAS_UNIT]->(:Unit)
(:Unit)-[:HAS_TOPIC]->(:Topic)
(:Topic)-[:HAS_SUBTOPIC]->(:SubTopic)
(:Topic)-[:HAS_QUESTION]->(:Question)
(:Course)-[:HAS_PREREQUISITE]->(:Course)
(:Course)-[:HAS_OUTCOME]->(:CourseOutcome)
(:Course)-[:HAS_TEXTBOOK]->(:Textbook)
```

This lets you answer structural questions with a graph traversal (e.g., *"What topics are covered in Unit 3 of 23CSE201?"* or *"Which PYQ questions are linked to Kirchhoff's Laws?"*) — something impossible to do efficiently with pure vector search.

### 3.7 Zero-Shot LLM Router
Before doing any retrieval, the student's question is classified into one of five categories: `Timetable`, `Curriculum`, `Academic Calendar`, `Regulations`, `Evaluation`.

This is done by calling `gemma4` via Ollama's `/api/generate` endpoint with a structured prompt that forces the model to reply with **only valid JSON**:
```json
{"primary_label": "Curriculum", "secondary_label": null, "confidence": 0.91}
```

The router then pre-filters which PostgreSQL partitions (`doc_type` column) to search — so a question about exams never hits syllabus chunks, keeping retrieval precision high.

### 3.8 PYQ Vision Pipeline (Multimodal)
This is one of the most complex parts of the system. When a PYQ (Previous Year Question paper) PDF is uploaded:
1. **PyMuPDF** opens the PDF and renders each page as a `2x zoom` bitmap.
2. Each page image is **base64-encoded** and sent to `gemma4` (the multimodal model) with a prompt asking it to extract every question, map it to syllabus topics, and report the "first 5-7 words" of each question.
3. The `first_few_words` is used by `page.search_for()` (PyMuPDF) to find the **pixel Y-coordinate** of each question on the page.
4. Using these pixel coordinates, **Pillow (PIL)** crops a tight bounding box image around each individual question.
5. That cropped image is saved to disk (`uploads/images/`).
6. `map_questions_to_kg()` creates a `(:Question)` node in Neo4j linked to the matching `(:Topic)` node by traversing `first_few_words` → topic name.

When a student later asks about a past exam question, the answer includes the **image path** of the exact cropped question from the original paper.

### 3.9 Regex Syllabus Parser
Rather than using an LLM to parse the Amrita syllabus PDF format (which would be slow and non-deterministic), we use a custom **regex parser** in `ingestion.py`. It's specifically tuned to the Amrita syllabus template:
- Detects course codes with the pattern `\b[0-9]{2}[a-zA-Z]{3}[0-9]{3}\b` (e.g., `23CSE201`)
- Identifies `L-T-P-C` credit patterns
- Extracts `CO1:`, `CO2:` course outcome definitions
- Parses `SyllabusUnit 1`, `Unit 2`, etc. with topic lists
- Builds prerequisite edges by matching course names against the prereq string

### 3.10 JWT Authentication (Stateless)
- Passwords are hashed with **bcrypt** (via `passlib`).
- On login, the server creates a **JWT** (JSON Web Token) signed with `HS256` algorithm and a `SECRET_KEY`. The token payload contains `sub` (username) and `role` (`student`/`admin`). Token TTL is 7 days.
- On every subsequent request, the frontend sends `Authorization: Bearer <token>` in the HTTP header.
- The server decodes and validates the JWT in `get_current_user()`, then pulls the user record from PostgreSQL to confirm existence.
- **No session state is stored server-side.** The JWT itself carries all the info.

---

## 4. Step-by-Step: How It Runs

### Phase 1 — Admin Uploads a Syllabus PDF
1. Admin opens `http://localhost:8000/public/admin.html`, logs in.
2. Selects a PDF file, selects `doc_type = syllabus`, enters a course code, clicks Upload.
3. Frontend POSTs `multipart/form-data` with the file to `/upload`, attaching the JWT Bearer token.
4. Server receives the multipart data via `cgi.FieldStorage`, saves the file to `uploads/`.
5. Inserts a row into the `documents` table, gets back the `doc_id`.
6. Calls `process_pdf(file_path)` → PyMuPDF splits the PDF into text chunks (paragraphs > 20 chars).
7. Calls `extract_syllabus_structure(full_text, dept, year)` → the custom regex parser returns a dict with courses, units, topics, outcomes, textbooks, etc.
8. For each course: calls `build_syllabus_kg(neo4j_driver, ...)` → runs MERGE Cypher queries to build the full course graph.
9. For each text chunk: calls `get_embedding(chunk)` → Ollama returns a 768-dim vector. Stores `(doc_id, chunk_text, vector)` in the `chunks` table using an INSERT query.
10. Commits. Responds `{"status": "success"}`.

### Phase 2 — Student Asks a Question (The Core Flow)
1. Student types *"What are the topics covered in Unit 2 of 23EEE104?"* and hits Enter.
2. Frontend POSTs `{"message": "..."}` to `/chat` with the Bearer token.
3. **Step 1 — Classify:** `classify_query(message)` calls Ollama `/api/generate` with the router prompt at `temperature=0.1`. Returns `{"primary_label": "Curriculum", "secondary_label": null, "confidence": 0.93}`.
4. **Step 2 — Embed:** `get_embedding(message)` calls Ollama `/api/embeddings` → gets a 768-dim vector for the question.
5. **Step 3 — Filter:** The label `"Curriculum"` maps to `doc_types = ["curriculum", "syllabus"]`. Only chunks from documents of these types are searched.
6. **Step 4 — Hybrid RRF Retrieval:** `hybrid_retrieve(question, embedding, labels, conn, top_k=6)` executes the multi-CTE SQL query:
   - `semantic` CTE: ranks chunks by cosine distance `embedding <=> CAST(%s AS vector)` → top 20.
   - `lexical` CTE: ranks chunks by `ts_rank_cd(to_tsvector, plainto_tsquery)` → top 20.
   - `fused` CTE: FULL OUTER JOINs both results, computes `1/(60+rank_s) + 1/(60+rank_l)` for every chunk, orders by this score, returns top 6.
7. Returns the top 6 chunks with `[Score: 0.0312 | Course: 23EEE104]` tags prepended.
8. **Step 5 — Generate (Streaming):** `generate_answer(question, context_str)` calls Ollama `/api/generate` with `stream: True` and `num_ctx: 16384`. As each token arrives in the HTTP stream, the server immediately writes it as a chunked HTTP chunk. Frontend reads the `ReadableStream` and appends each token to the chat bubble in real-time.

### Phase 3 — Student Uploads an Image in Chat
1. Student clicks the image attach button and uploads a photo of a circuit diagram.
2. Frontend converts the image to base64, POSTs to `/chat` (or image endpoint).
3. `describe_uploaded_image(base64_image)` sends the image + prompt to `gemma4` (multimodal). Gets a textual description: *"This shows a RLC series circuit with a voltage source V, resistor R = 10Ω..."*
4. That description is used as the text query — the rest of the flow (embed → retrieve → generate) proceeds as normal.

---

## 5. Database Schema

### PostgreSQL
```sql
users(id, username, hashed_password, role)
documents(id, filename, doc_type, course_code, created_at)
chunks(id, document_id → documents.id, content TEXT, embedding vector(768))
```

### Neo4j Nodes
`Department`, `Semester`, `Course`, `Unit`, `Topic`, `SubTopic`, `CourseOutcome`, `Objective`, `Textbook`, `Reference`, `Question`

### Neo4j Key Relationships
`OFFERS`, `INCLUDES`, `HAS_UNIT`, `HAS_TOPIC`, `HAS_SUBTOPIC`, `HAS_QUESTION`, `HAS_PREREQUISITE`, `HAS_OUTCOME`

---

## 6. What Makes It Non-Trivial

1. **No framework dependencies** — The custom HTTP routing, multipart parsing, chunked transfer encoding, and connection pool management is all handwritten.
2. **Dual-database retrieval strategy** — Most RAG systems use a single vector store. We use RRF fusion across PostgreSQL vector search AND keyword search in a single raw SQL query.
3. **Knowledge Graph construction** — The Neo4j graph is not manually built — it is automatically inferred from unstructured PDFs using a regex parser specifically designed for the Amrita syllabus template.
4. **Vision-based PYQ ingestion** — The pipeline crops individual questions from exam paper images using pixel coordinates discovered by the multimodal LLM + PyMuPDF text search.
5. **Zero-Shot Router + Intent-Aware Pre-filtering** — The system classifies the user's intent before any retrieval, so searches hit only the relevant data partition. This dramatically improves precision.
