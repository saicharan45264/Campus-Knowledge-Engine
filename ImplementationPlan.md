# M.A.C.H. AI — Finalized System Architecture & Implementation Plan
**Multimodal Academic Cognitive Hub — Amrita Vishwa Vidyapeetham Campus Knowledge Engine**
*On-Premise · Privacy-First · Local Small Models · macOS Apple Silicon*

---

## 0. What This System Does (Plain English)

A university-specific intelligent chat system with two user types:

**Admin** — dumps any number of PDFs (all 8 types listed below) in one drag-and-drop interface. The system automatically classifies each PDF, parses it, and routes it to the correct pipeline. No configuration needed.

**Student** — opens a chat interface and asks anything in natural language. The system understands what data is needed, retrieves it from the correct source, and responds with text, question cards with diagram images, prerequisite trees, timetables, or academic explanations.

---

## 1. The 8 Input Document Types (What Admin Uploads)

| # | Document Type | Sample Filename | Pages | What It Contains | System Use |
|---|---|---|---|---|---|
| 1 | **Curriculum & Syllabus** | `B.Tech CSE - 2023 Curriculum.pdf` | ~324 | All 150+ courses: Course codes, L-T-P-C credits, Semester placement, Units, Topics, Course Outcomes (CO1-CO6), Textbooks, References, Evaluation pattern, Elective baskets | Primary Knowledge Graph backbone |
| 2 | **Past Year Question Papers (PYQs)** | `23CSE104_I Sem Mid-Term_Odd 2023.pdf` | 3-20 | Exam questions with circuit diagrams, math equations, tables, sub-questions, BTL levels, CO tags, marks weightage | Question retrieval & image crop cards |
| 3 | **Degree Regulations** | `B.Tech ASE regulations 2023.pdf` | ~18 | Eligibility rules, grading system, CGPA policies, promotion criteria, re-exam rules, graduation requirements | Policy & regulation queries |
| 4 | **Academic Calendar** | `Academic-Calendar-AY-2025-2026.pdf` | ~14 | Day-by-day working/holiday schedule, semester start/end dates, exam periods, event days | Date & schedule queries |
| 5 | **Time Table (Dept-wise)** | `BTechCSEF6.pdf` | 1-2 | Slot-wise course schedule grid per department and section | Timetable queries |
| 6 | **Lab Time Table** | `AB2-GF-ITLAB1-EVEN-2025-26.pdf` | 1 | Lab slot schedule per batch | Lab schedule queries |
| 7 | **End-Sem / Assessment Schedules** | `BTech-End-Sem-2023.pdf` | 1-5 | Exam date sheets, room allocations, exam timing | Exam schedule queries |
| 8 | **Student Academic Records** | `BTech-2023-SA.pdf` | 1-5 | Student marks, CGPA, result summaries (admin-only) | CGPA & marks queries (admin) |

---

## 2. System Architecture Overview

```
ADMIN UPLOAD (Drag & Drop — Any Mix of 8 Document Types)
         |
         v
 +-----------------------------------------------------------+
 |          PDF Auto-Classifier & Smart Router               |
 |  (Filename pattern + first-page text fingerprint)         |
 |  Dispatches to correct ingestion pipeline in background   |
 +------+------------+-----------+----------+---------------+
        |            |           |          |
        v            v           v          v
   Curriculum     PYQ          Admin      Flat-text PDFs
   & Syllabus    Pipeline     Documents   (Regulations,
    Pipeline     (text +      Pipeline    Calendar, TT)
                 vision)
        |            |           |          |
        v            v           v          v
 Neo4j Knowledge Graph     PostgreSQL pgvector
 (Courses, Units, Topics,   (Chunked text embeddings
  Questions, Prerequisites,  for semantic search &
  Elective Baskets, COs)     hybrid BM25 + RRF)
        |                         |
        +-----------+-------------+
                    |
          STUDENT CHAT INTERFACE
                    |
                    v
          Intent Router (Tier-1 Keywords + Tier-2 Embedding)
                    |
   +----------------+------------------+--------------------+
   |                |                  |                    |
   v                v                  v                    v
Zero-LLM        Graph RAG          Hybrid RRF          AI Streaming
Fast-Path       Traversal          pg + Neo4j          Tutor Answer
(< 120ms)      (< 200ms)          (< 300ms)           (< 0.6s first token)
```

---

## 3. Five-Layer Retrieval Safety Net (No Query Ever Fails)

For any question a student asks, 5 layers attempt to answer it in sequence:

```
Layer 1: Neo4j Graph RAG traversal via (q:Question)-[:TESTS_TOPIC]->(t:Topic)
         Finds questions by specific topic nodes linked via hybrid embedding mapping

Layer 2: Cypher multi-field keyword search
         Matches Course code, Course name, Topic name, Question text (with stems + synonyms)

Layer 3: Lucene fulltext BM25 (question_text_ft + topic_name_ft)
         Catches acronyms (PDA, NFA, PSD, 2PC) and morphological variants

Layer 4: PostgreSQL pgvector hybrid RRF
         Dense (nomic-embed 768d) + Sparse (tsvector BM25) fused at k=60

Layer 5: Curriculum Syllabus Guidance Fallback
         If no past exam questions exist, shows Units + Topics + prompt to upload PYQs
```

---

## 4. Document Ingestion Pipelines (Detailed)

### 4.1 Curriculum & Syllabus PDF Pipeline

**Trigger:** Admin uploads a PDF classified as `doc_type=syllabus`

**Process (fully automated, no LLM needed, ~8s for 324 pages):**

```
Phase 1: Page-type classification (regex fingerprint, < 1s)
   COVER | GENERAL_INFO | SEMESTER_TABLE | COURSE_DETAIL | ELECTIVE_BASKET | RULE | OTHER

Phase 2: Semester table extraction
   Extracts: Semester number, Course code, Course name, L-T-P-C credits, Category

Phase 3: Course detail extraction (per course page)
   Extracts:
     - Course code (e.g., 23CSE303)
     - Course name (e.g., Theory of Computation)
     - L-T-P-C structure
     - Units: Unit 1..5 with sub-topic names
     - Course Outcomes: CO1..CO6 with Bloom level
     - Textbooks and References
     - Evaluation pattern (CA:FE ratio)
     - Prerequisites

Phase 4: Elective basket extraction
   Maps elective course codes under their respective basket names
   e.g., "Electives in Artificial Intelligence" basket contains 23CSE401..23CSE412

Phase 5: Neo4j Graph MERGE writes (idempotent, collision-free)
   MERGE (c:Course) -[:IN_SEMESTER]-> (s:Semester)
   MERGE (c:Course) -[:HAS_UNIT]-> (u:Unit)
   MERGE (u:Unit) -[:HAS_TOPIC]-> (t:Topic)
   MERGE (c:Course) -[:HAS_CO]-> (co:CourseOutcome)
   MERGE (c:Course) -[:REQUIRES]-> (prereq:Course)
   MERGE (c:Course) -[:BELONGS_TO_BASKET]-> (b:ElectiveBasket)

Phase 6: PostgreSQL chunk embedding
   Stores syllabus text in DocumentChunk with 768d vector for hybrid search
```

**Neo4j Schema After Ingestion:**
```
(Dept)-[:OFFERS]->(Course)-[:IN_SEMESTER]->(Semester)
(Course)-[:HAS_UNIT]->(Unit)-[:HAS_TOPIC]->(Topic)
(Course)-[:HAS_CO]->(CourseOutcome)
(Course)-[:REQUIRES]->(Course)   <- Prerequisite edges
(Course)-[:BELONGS_TO_BASKET]->(ElectiveBasket)
(Course)-[:HAS_TEXTBOOK]->(Textbook)
```

---

### 4.2 Past Year Question Paper (PYQ) Pipeline

**Trigger:** Admin uploads a PDF classified as `doc_type=pyq`

**Stage 1: Deterministic Text Extractor (Primary, < 5s per paper)**
```
2-pass bounding-box algorithm using PyMuPDF
Pass 1: Extract question headers - detect Q1., Q2., Q10., (i), (a)
Pass 2: Crop exact pixel region per question, stitch cross-page questions

For each extracted question:
  - question_text: full text with sub-parts
  - btl_tag: Bloom's level [BTL1..BTL6] from [BTL X] annotation
  - co_tag: Course outcome [CO1..CO6] from [CO X] annotation
  - marks: from [X Marks] or [X M] annotation
  - has_figure: True if Fig., figure, diagram detected
  - image_url: Cloudinary/local URL of cropped question image
  - question_number: Q1, Q2, etc.
```

**Stage 2: Vision Fallback (for scanned/image-only PDFs)**
```
If Stage 1 extracts 0 questions (scanned PDF):
Render pages at 2x resolution -> send image chunks to vision model
Extract questions from image via gemma4:12b vision
think: False, format: json -> ensures < 4s per page chunk
```

**Stage 3: Topic Mapping (Hybrid Embedding, < 2s per paper)**
```
For each extracted question:
1. Expand acronyms: PDA -> pushdown automata, NFA -> nondeterministic finite automata
2. Embed expanded text with nomic-embed-text (768d)
3. Score against all topic nodes of this course using:
   hybrid_score = 0.5 x cosine_similarity(q_emb, t_emb) + 0.5 x keyword_overlap(q, t)
4. Attach to top 1-2 scoring topic nodes in Neo4j
```

**Stage 4: Dual Storage**
```
Neo4j: Question node + BELONGS_TO Course + TESTS Topic edges
PostgreSQL: DocumentChunk with labeled content + embedding for hybrid search
```

---

### 4.3 Admin / Flat-text Document Pipelines

| Document Type | Classifier Signal | Storage Strategy | Student Query |
|---|---|---|---|
| **Regulations** | `regulations`/`ASE`/`ordinance` in filename/text | PostgreSQL chunks + Neo4j Regulation nodes | "What is the CGPA for distinction?" |
| **Academic Calendar** | `calendar`/`academic` in filename/text | PostgreSQL chunks + structured date parsing | "When does semester start?" |
| **Time Table** | `slot 1`, `8 am`, `dept` in text | Table parser + PostgreSQL + Neo4j TimeSlot nodes | "When is my OS class?" |
| **Lab Timetable** | `lab`, `itlab`, `batch` in text | Same as timetable | "When is the programming lab?" |
| **Exam Schedule** | `end-sem` in filename | Date-structured parsing | "When is the Network exam?" |
| **Student Records** | `SA`/`marks`/`result` (admin-only) | Encrypted PostgreSQL (JWT gated) | Admin queries only |

---

## 5. PDF Auto-Classifier (Smart Router for Admin Upload)

```python
def classify_pdf_type(filename: str, first_page_text: str) -> str:
    """
    Returns: syllabus | pyq | regulations | calendar | timetable | lab_timetable | exam_schedule | records
    """
    fname = filename.lower()
    text = first_page_text[:500].lower()

    if any(k in text for k in ["curriculum and syllabi", "l-t-p-c", "course outcomes", "unit i", "unit 1"]):
        return "syllabus"
    if any(k in fname for k in ["curriculum", "syllabus"]):
        return "syllabus"

    if any(k in text for k in ["roll no", "marks]", "[co", "[btl", "answer all", "part a", "part b"]):
        return "pyq"
    if any(k in fname for k in ["mid-term", "end-sem", "pyq", "question paper", "exam"]):
        return "pyq"

    if any(k in text for k in ["regulations", "ordinances", "cgpa", "promotion criteria"]):
        return "regulations"

    if any(k in text for k in ["academic calendar", "working day", "holiday", "semester"]):
        return "calendar"

    if any(k in text for k in ["slot 1", "slot 2", "8 am", "8.00 am"]) and "dept" in text:
        return "timetable"

    if any(k in text for k in ["lab", "itlab", "batch"]):
        return "lab_timetable"

    if "end sem" in fname or "exam schedule" in text:
        return "exam_schedule"

    if any(k in text for k in ["cgpa", "sgpa", "student academic", "result", "marks"]):
        return "records"

    return "general"
```

---

## 6. Intent Classification (What Kind of Question Is Being Asked)

| Intent | Example Queries | Resolution |
|---|---|---|
| `PROBLEM_LIST` | "Questions on mesh analysis", "Give 10-mark PYQs on Turing machines" | Graph RAG + Topic traversal + Question cards |
| `SIMPLE_CURRICULUM` | "Courses in 3rd semester", "Syllabus of ML", "Electives in AI" | Zero-LLM Neo4j traversal |
| `MULTI_HOP_PREREQ` | "Prerequisites for DAA", "What should I study before TOC" | Multi-hop Cypher `[:REQUIRES*1..5]` |
| `GRAPH_PYQ_MAPPING` | "CO1 questions in OS", "BTL3 questions on cryptography" | Neo4j CourseOutcome -> Question |
| `GENERAL_QUERY` | "Explain Bully algorithm", "Derive PSD formula", "Difference NFA vs DFA" | pgvector hybrid + streaming LLM |
| `CALENDAR_QUERY` | "When does semester start?", "Is 5 June a holiday?" | Calendar document chunks |
| `TIMETABLE_QUERY` | "When is my OS lecture?", "Lab slot for batch A" | Timetable document chunks |
| `REGULATION_QUERY` | "Minimum CGPA for distinction", "Backlog policy" | Regulation document chunks |

---

## 7. Admin Upload UI Design

Single drag-and-drop interface — no categorization needed by admin:

```
+----------------------------------------------------------+
|  M.A.C.H. Admin Upload Portal                            |
+----------------------------------------------------------+
|   +----------------------------------------------+       |
|   |                                              |       |
|   |   Drag & Drop ANY University PDFs Here       |       |
|   |   (Syllabus, PYQs, Timetables, Calendars,   |       |
|   |    Regulations, Lab Sheets, Mark Lists)      |       |
|   |                                              |       |
|   |   Or Click to Browse Files (500+ at once)    |       |
|   +----------------------------------------------+       |
|                                                          |
|   [✓] 3 Syllabus PDFs detected and queued               |
|   [✓] 12 PYQ Papers detected and queued                  |
|   [✓] 2 Timetables detected and queued                   |
|   [⚙] Processing in background...                        |
|                                                          |
|   [Document Library]  [Processing Status]  [Logs]       |
+----------------------------------------------------------+
```

Key design rules:
- Admin does NOT categorize anything manually
- All PDFs process in the background — admin can leave the page
- Live processing status panel shows progress per document
- Failed PDFs are flagged with error message and retry button

---

## 8. Student Chat UI Design

```
+----------------------------------------------------------+
|  M.A.C.H. Campus Knowledge Engine                        |
+---------------------------+------------------------------+
|  Suggested Queries:       |  Chat History                |
|  - 3rd Semester Courses   |                              |
|  - Prerequisites for DAA  |  Student: show questions     |
|  - Upload Diagram         |          on mesh analysis    |
|  [Upload Image/Circuit]   |                              |
|                           |  MACH AI:                    |
|                           |  +----------------------+   |
|                           |  | Q6: [23EEE104]        |   |
|                           |  | Using Mesh analysis,  |   |
|                           |  | find I for circuit... |   |
|                           |  | [Marks: 10] [BTL: 3]  |   |
|                           |  | [Circuit diagram img] |   |
|                           |  +----------------------+   |
|  +--------------------+   |                              |
|  | Type your question |   |                              |
|  |            [Send]  |   |                              |
|  +--------------------+   |                              |
+---------------------------+------------------------------+
```

---

## 9. Technology Stack

| Layer | Tool | Why |
|---|---|---|
| **Backend API** | FastAPI + Uvicorn | Async, streaming SSE, lightweight |
| **Knowledge Graph** | Neo4j 5.x Community | Course -> Unit -> Topic -> Question traversal |
| **Vector DB** | PostgreSQL 16 + pgvector | Hybrid BM25 + dense RRF search |
| **Embedding Model** | `nomic-embed-text` via Ollama | 768d, runs locally on Mac Metal |
| **Generative LLM** | `gemma4:12b-it-qat` via Ollama | Multimodal text + vision, local Metal GPU |
| **PDF Parser** | PyMuPDF (fitz) + Pillow | Bounding-box extraction, 2x resolution cropping |
| **Vision Pipeline** | gemma4 vision via Ollama | Circuit/diagram analysis, think:False for speed |
| **Frontend** | Vanilla HTML + CSS + JavaScript | No build toolchain, instant load |
| **Document Storage** | Cloudinary / Local filesystem | Question image crop hosting |
| **Caching** | In-memory LRU dict -> Redis upgrade | < 5ms repeat queries |
| **Fulltext Search** | Neo4j Lucene indexes | BM25 over graph nodes |

---

## 10. Implemented & Working (Current State)

### Syllabus Ingestion
- 324-page curriculum PDF fully ingested in < 8 seconds
- 184 courses, 4,523 topic nodes, 8 semester mappings in Neo4j
- All elective baskets with their course codes correctly mapped

### PYQ Ingestion
- 90 questions across 4 courses (23EEE104, 23CSE303, 23MAT216, 23CSE312)
- Each question attached to specific topic node via hybrid embedding mapper
- Diagram images cropped and stored per question

### Query Retrieval (verified)
- "show questions on resistance" -> 14 EEE104 questions from Resistance topic
- "questions for 23MAT216" -> all 16 probability questions
- "pushdown automata questions" -> exact PDA topic traversal
- "sinusoidal waveform", "bully algorithm", "lamport timestamp" all working

### Conceptual Academic Answers
- "Explain NFA vs DFA" -> Full comparison with state minimization, equivalence proof
- Time to first streaming token: < 0.6 seconds

### Prerequisite Chains
- "Prerequisites for 23CSE303" -> ordered prerequisite list from Neo4j

### Curriculum Navigation
- "Electives in AI basket" -> all AI elective course codes and names
- "Units of Machine Learning" -> full unit breakdown with topic names

### Image Upload Query
- Student uploads circuit/diagram image -> vision identifies topic in 2-3s
- System retrieves matching past exam questions with figures

### Syllabus Guidance Fallback
- If no PYQs uploaded for a course -> shows curriculum units and topics + upload prompt

---

## 11. What Needs to Be Built Next (Pending)

### Phase 1: Remaining 6 Document Types (High Priority)

**11.1 Regulation Documents** — `backend/regulation_parser.py`
- Extract key policies as Neo4j Regulation nodes
- Supports: "What CGPA do I need?", "How many backlogs allowed?"

**11.2 Academic Calendar** — `backend/calendar_parser.py`
- Parse date-wise rows: Date | Day | Status | Particulars
- Store in PostgreSQL + Neo4j CalendarEvent nodes

**11.3 Timetable Parser** — `backend/timetable_parser.py`
- Parse slot grid: Slot1 = 8am, Course X in Room Y for Dept CSE F6
- Store as: (Course)-[:SCHEDULED_AT]->(TimeSlot {day, time, room})

**11.4 Exam Schedule** — `backend/exam_parser.py`
- Extract: Course name | Date | Time | Room

### Phase 2: Smart Admin Bulk Upload UI
- Single drag-and-drop page replacing per-tab form UI
- `POST /upload-bulk` with auto-classification
- `GET /processing-status` for real-time progress display

### Phase 3: Performance Upgrades
- FastEmbed in-process embeddings: 25ms -> 3ms (8x speedup)
- Redis caching: cross-worker shared, < 1ms hits
- Neo4j `asyncio.to_thread`: non-blocking concurrent queries
- HTTP connection pooling: shared Ollama client, -20ms per turn

### Phase 4: Fine-Tuning (Later)
- Generate 9,000-sample dataset from Neo4j + PostgreSQL
- Train on Google Colab (free T4 GPU) with QLoRA on Gemma base
- Export GGUF -> import as `mach-campus` model in local Ollama

---

## 12. File & Module Map

```
MACH_AI/
├── backend/
│   ├── main.py                  # FastAPI app, routes, intent dispatch
│   ├── intent_router.py         # Tier-1 keyword + Tier-2 embedding classifier
│   ├── query_neo4j.py           # All Neo4j Cypher queries (topic, prereq, curriculum)
│   ├── utils.py                 # PDF extraction, vision, streaming LLM, topic mapper
│   ├── syllabus_parser.py       # 324-page curriculum PDF ingestion (zero-LLM, ~8s)
│   ├── embedder.py              # nomic-embed-text embedding via Ollama
│   ├── database.py              # SQLAlchemy models, Neo4j + PostgreSQL connection
│   ├── Modelfile                # Ollama mach-campus model manifest (future)
│   ├── regulation_parser.py     # [PENDING] B.Tech regulations PDF
│   ├── calendar_parser.py       # [PENDING] Academic calendar PDF
│   └── timetable_parser.py      # [PENDING] Timetable PDF
├── frontend/
│   ├── index.html               # Login page
│   ├── page_student.html        # Student chat UI
│   ├── page_admin.html          # Admin panel (needs bulk upload upgrade)
│   └── js/
│       ├── page_student.js      # Chat rendering, image upload, problem cards
│       └── page_admin.js        # Admin upload, document list
├── docs/
│   ├── model input docs/        # 8 sample PDFs (all document types)
│   └── test_docs/               # Test PYQ PDFs already ingested
└── scripts/
    ├── ingest_test_data.py      # Bulk test ingestion scripts
    ├── train_qlora.py           # [FUTURE] Fine-tuning script for Colab
    └── eval_ragas.py            # [FUTURE] RAGAS benchmarking
```

---

## 13. API Routes Map

| Route | Method | Purpose |
|---|---|---|
| `POST /chat` | POST | Main student query endpoint (streaming SSE) |
| `POST /upload` | POST | Upload PDFs with doc_type |
| `POST /upload-bulk` | POST | [PENDING] Auto-classify and bulk upload |
| `GET /documents` | GET | List all uploaded documents |
| `DELETE /documents/{id}` | DELETE | Remove document + graph nodes |
| `POST /image-query` | POST | Student uploads image for diagram lookup |
| `GET /processing-status` | GET | [PENDING] Real-time ingestion progress |
| `GET /health` | GET | System health check |
| `POST /reset` | POST | Admin full system reset |

---

## 14. Local Performance Benchmarks (Verified on Mac Apple Silicon)

| Query Type | Example | Latency |
|---|---|---|
| Cached repeat | "prerequisites of DAA" | < 5 ms |
| Curriculum navigation | "courses in semester 3" | 80-120 ms |
| Topic-based PYQ retrieval | "questions on resistance" | 90-180 ms |
| Cross-topic keyword search | "questions on Bully algorithm" | 100-200 ms |
| Syllabus guidance fallback | "questions for 23CSE201" | 80-100 ms |
| Streaming academic answer | "explain NFA vs DFA" | 0.54s first token |
| Image vision analysis | student uploads circuit diagram | 2.1-3.5s |
| PYQ ingestion per paper | 20-question paper | 4-8s |
| Syllabus ingestion | 324-page curriculum PDF | ~8s |

---

## 15. Implementation Constraints

| Constraint | Decision |
|---|---|
| Runs fully offline | Ollama local, Neo4j local, PostgreSQL local |
| No CUDA required | All models run on Apple Metal via Ollama |
| Small model compatible | Works with gemma4:12b-it-qat, can scale down to 3B |
| No external API keys | Only Cloudinary for image hosting (has local fallback) |
| Idempotent ingestion | Cypher MERGE, MD5 question identity hashing |
| No hallucination for structured queries | Zero-LLM fast-path for curriculum/prereq/PYQ cards |
| Privacy-preserving | Student records stored encrypted, JWT-gated |

---

*Master plan — Amrita Vishwa Vidyapeetham · M.A.C.H. AI · FYP 2025-26*
