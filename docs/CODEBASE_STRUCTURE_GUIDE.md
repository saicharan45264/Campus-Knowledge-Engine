# Comprehensive Architecture Restructuring & Engineering Directive for M.A.C.H. AI

### Objective & Architectural Mandate
We are standardizing the entire M.A.C.H. AI codebase to eliminate technical debt, monolithic files (no 1800+ line single files), circular imports, and inconsistencies. The codebase must reflect senior-level software engineering standards: strict separation of concerns, clean modular design, defensive programming, comprehensive async/await patterns, and zero agentic/boilerplate fluff.

Every feature, button, route, and pipeline must be 100% functional, properly wired between frontend, backend, databases (PostgreSQL + Neo4j), and testing suites. Remove all outdated code, legacy mock functions, and unused dependencies. All code comments and documentation must be written in clear, natural, professional Indian English. No emojis.

---

### Complete Repository Structure

```
MACH_AI/
├── Dockerfile                          # Multi-stage production Docker container definition
├── docker-compose.yml                  # Polyglot container stack (FastAPI, pgvector, Neo4j 5.x)
├── requirements.txt                    # Pinned Python package dependencies
├── pytest.ini                          # Pytest configuration and asyncio loop scopes
├── ruff.toml                           # Linter and formatter rule configuration
├── README.md                           # Comprehensive production setup and API guide
├── ImplementationPlan.md               # Master engineering specification & milestone tracker
│
├── backend/
│   ├── __init__.py
│   ├── academic_tool_calling.py        # Fine-tuned or bounded lexical tool selection for academic documents
│   ├── main.py                         # FastAPI application entrypoint, lifespan hooks, CORS, routes
│   ├── config.py                       # Pydantic Settings management (.env parsing, model parameters)
│   ├── database.py                     # Polyglot connections: Async PostgreSQL + Neo4j Bolt driver
│   │
│   ├── api/                            # HTTP Domain Endpoints
│   │   ├── __init__.py
│   │   ├── auth.py                     # JWT token issuance, Bcrypt verification, user sessions
│   │   ├── chat.py                     # Core conversational tutoring endpoint (/chat) with SSE streaming
│   │   ├── ingestion.py                # PDF upload, document management, processing status polling
│   │   └── evaluation.py               # System health (/health), telemetry, benchmark runs (/eval/run)
│   │
│   ├── core/                           # System Infrastructure & Core Logic
│   │   ├── __init__.py
│   │   ├── intent_router.py            # Dual-layer deterministic & semantic query intent classifier
│   │   ├── guardrails.py               # Tier-0 input sanitization, injection filters, safety advisory
│   │   ├── semantic_cache.py           # Tier-0.5 semantic vector cache using pgvector cosine similarity
│   │   ├── http_client.py              # Shared persistent httpx.AsyncClient connection pool for Ollama
│   │   └── telemetry.py                # Query latency tracking and retrieval instrumentation
│   │
│   ├── services/                       # Business Logic & Data Orchestration
│   │   ├── __init__.py
│   │   ├── academic_document_qa_service.py # Scoped retrieval for exam schedules, seating plans, and lab timetables
│   │   ├── academic_qa_service.py      # Unified QA service across academic administrative documents
│   │   ├── calendar_qa_service.py      # Institutional academic calendar QA service (milestones, class days, holidays)
│   │   ├── date_resolution.py          # Calendar date parsing and relative date resolution
│   │   ├── document_ingestion.py       # Asynchronous ingestion pipeline coordinator for all document types
│   │   ├── embedding_service.py        # Local Ollama embedding interface (embeddinggemma-2)
│   │   ├── graph_service.py            # Neo4j Cypher traversals (curriculum, prerequisites, PYQ mappings)
│   │   ├── rag_orchestrator.py         # Multi-turn conversational memory & hybrid RAG prompt assembly
│   │   ├── regulation_service.py       # Amrita B.Tech Academic Regulations QA service (clauses, citations)
│   │   ├── schedule_service.py         # Examination schedule and slot lookup service
│   │   ├── storage_service.py          # File system management for uploaded PDFs and image crops
│   │   ├── timetable_directory.py      # Course faculty and room directory from class timetables
│   │   └── topic_mapper.py             # Semantic mapping between exam questions and syllabus topics
│   │
│   └── parsers/                        # Document Extraction & Transformation Pipelines
│       ├── __init__.py
│       ├── academic_documents_parser.py # Seating arrangements, end-sem/mid-term exam schedules, lab timetables
│       ├── calendar_parser.py          # Institutional academic calendar table parser
│       ├── pdf_classifier.py           # Discriminator identifying Syllabus vs Question Paper (PYQ)
│       ├── pyq_parser.py               # Exam paper parser (Questions, Marks, BTL levels, CO tags)
│       ├── regulations_parser.py       # B.Tech regulations parser (clauses, conditions, cross-references)
│       ├── syllabus_parser.py          # Hierarchical syllabus parser (Units, Topics, Outcomes, Books)
│       ├── timetable_parser.py         # Class timetable grid parser (slots, batches, faculty, venues)
│       └── visual_cropper.py           # Multimodal diagram extractor and visual bounding box cropper
│
├── frontend/
│   ├── index.html                      # Sign-in portal for Student and Admin roles
│   ├── student.html                    # Student conversational workspace & curriculum dashboard
│   ├── admin.html                      # Administrative document ingestion & telemetry portal
│   ├── manifest.json                   # Web application manifest configuration
│   ├── service-worker.js               # Service worker for offline asset caching
│   ├── favicon.ico                     # Multi-resolution favicon resource (16x16 to 256x256)
│   │
│   ├── css/                            # Modular Stylesheets
│   │   ├── variables.css               # Centralized CSS design tokens (Amrita maroon, dark theme)
│   │   ├── base.css                    # Global typography, resets, font family declarations
│   │   ├── layout.css                  # Core application shell layout and responsive navigation
│   │   ├── components.css              # Buttons, cards, modals, chips, and animated badges
│   │   ├── page_login.css              # Authentication portal styles and split-panel animation
│   │   ├── page_student.css            # Chat stream, verified syllabus cards, voice/attach controls
│   │   ├── page_admin.css              # Document dropzone, telemetry gauges, data tables
│   │   └── main.css                    # Unified stylesheet entrypoint
│   │
│   ├── js/                             # Modular JavaScript Architecture (ES Modules)
│   │   ├── api.js                      # Centralized fetch wrapper with JWT bearer injection & error alerts
│   │   ├── auth.js                     # Authentication token storage, role guards, route protection
│   │   ├── utils.js                    # HTML escaping, timestamp formatters, UI utilities
│   │   ├── page_login.js               # Login form controller & dynamic Knowledge Graph canvas
│   │   ├── page_student.js             # Chat controller, SSE stream reader, verified cards, speech input
│   │   └── page_admin.js               # PDF upload controller, processing status poller, system reset
│   │
│   └── icons/                          # High-DPI Brand Artwork & Icons
│       ├── mach-logo-high-res.jpg      # Master high-resolution brand artwork
│       ├── icon-192.png                # PWA application icon (192x192)
│       ├── icon-512.png                # PWA application icon (512x512)
│       ├── apple-touch-icon.png        # iOS Safari home screen icon (180x180)
│       ├── favicon-32x32.png           # Desktop browser tab icon
│       └── favicon-16x16.png           # Compact browser tab icon
│
├── tests/                              # Comprehensive Test Suite (110 Tests)
│   ├── __init__.py
│   ├── test_academic_document_qa.py    # Exam seating plans, hall allocations, and schedule filter tests
│   ├── test_academic_tool_calling.py   # Fine-tuned & lexical tool dispatcher tests
│   ├── test_calendar_restoration.py    # Academic calendar milestones, cohort class days, and holiday tests
│   ├── test_embedder.py                # Embedding vector dimensionality, task prefix, and format tests
│   ├── test_integration_neo4j.py       # Live Neo4j property graph traversal and merge tests
│   ├── test_intent_router.py           # Intent classification, typo handling, aspect extraction tests
│   ├── test_lab_timetable_queries.py   # IT Lab and laboratory timetable slot allocation tests
│   ├── test_parsers_and_security.py    # PDF parsing integrity, regex extraction, guardrail injection tests
│   ├── test_regulation_grounding.py    # B.Tech Academic Regulations citation grounding and traversal tests
│   ├── test_security_auth.py           # Bcrypt hashing, JWT token expiry, role-based endpoint security
│   ├── test_timetable_question_service.py # Class timetable directory, faculty venue, and free period tests
│   └── test_topic_mapper.py            # Question-to-syllabus semantic mapping and status calibration tests
│
├── deploy/                             # Production Infrastructure Configurations
│   ├── nginx/
│   │   └── nginx.conf                  # Reverse proxy configuration with SSL termination & SSE streaming
│   └── systemd/
│       └── ollama.service              # Systemd unit definition for local Ollama LLM service
│
├── docs/                               # Research Papers & Academic Documentation
│   ├── MACH_IEEE_Paper.tex             # Complete IEEE conference research paper
│   ├── generated_tables.tex            # Evaluation latency and benchmark comparison tables
│   ├── ref.bib                         # Academic bibliography and literature citations
│   └── CODEBASE_STRUCTURE_GUIDE.md     # This comprehensive architecture & code standardization guide
│
└── scripts/                            # Operational & Maintenance Utilities
    ├── __init__.py
    ├── backup_databases.sh             # Automated backup utility for PostgreSQL and Neo4j
    ├── eval_benchmark.py               # Automated RAG evaluation (Hit Rate, MRR, Context Recall)
    ├── reembed_chunks.py               # Vector re-embedding migration utility
    └── sync_paper_metrics.py           # Synchronization utility between telemetry and LaTeX tables
```

---

### Detailed File Responsibilities

#### 1. Backend Layer (`backend/`)
- **`backend/main.py`**: Initializes the FastAPI app instance using `lifespan` context manager. Handles startup tasks (database schema creation, intent router warm-up) and graceful connection pool teardown. Configures CORS, GZip compression, and static asset routes (`/public`, `/icons`, `/static`, `/favicon.ico`).
- **`backend/config.py`**: Reads `.env` via `pydantic-settings`. Defines database credentials, host configurations, JWT secrets, and model parameters (`OLLAMA_MODEL="gemma4:12b-it-qat"`, `OLLAMA_EMBED_MODEL="embeddinggemma-2:latest"`).
- **`backend/database.py`**: Manages polyglot persistence. Sets up async SQLAlchemy session maker with PostgreSQL (`pgvector`) for vector halfvec(768) embeddings and full-text search indexes. Manages the official Neo4j Bolt driver connection pool. Seeds default student and admin users with Bcrypt passwords.

#### 2. API Endpoints (`backend/api/`)
- **`backend/api/auth.py`**: Provides `/login` supporting form-data and JSON payloads with Bcrypt hash verification. Issues JWT tokens containing user role (`student` or `admin`). Exports `get_current_user` dependency.
- **`backend/api/chat.py`**: Handles conversational tutoring queries (`POST /chat`). Executes Tier-0 guardrails, checks Tier-0.5 semantic cache, routes deterministic Zero-LLM intents directly to Neo4j graph services (returning structured JSON cards), or falls back to Hybrid RRF RAG with real-time SSE token streaming from Ollama.
- **`backend/api/ingestion.py`**: Manages document ingestion (`POST /upload`). Automatically classifies documents as Syllabus or PYQ, routes parsing pipelines asynchronously, stores chunks into PostgreSQL, and constructs Knowledge Graph nodes and edges in Neo4j.
- **`backend/api/evaluation.py`**: System health check (`GET /health`), automated benchmark evaluation runner (`POST /eval/run`), and telemetry metrics retriever.

#### 3. Core Engine (`backend/core/`)
- **`backend/core/intent_router.py`**: Classifies queries into `SIMPLE_CURRICULUM`, `PROBLEM_LIST`, `MULTI_HOP_PREREQ`, `GRAPH_PYQ_MAPPING`, or `GENERAL_QUERY` using deterministic regex rules and vector semantic similarity.
- **`backend/core/guardrails.py`**: Tier-0 safety shield. Detects prompt injections, jailbreak attempts, and off-topic non-academic queries using regex patterns.
- **`backend/core/semantic_cache.py`**: Caches answered queries in PostgreSQL using vector cosine similarity. Serves repeated questions in sub-10ms.
- **`backend/core/http_client.py`**: Provides a shared, persistent `httpx.AsyncClient` with connection pooling to eliminate connection overhead during streaming generation.
- **`backend/core/telemetry.py`**: `QueryLatencyTracker` measuring routing, retrieval, and generation latencies for every incoming student request.

#### 4. Business Services (`backend/services/`)
- **`backend/services/graph_service.py`**: Executes deterministic Cypher queries on Neo4j for course syllabus units, textbooks, references, evaluation pattern ratios, and multi-hop prerequisite chains. Features contrasting subtopic isolation (Mesh vs Nodal, $R$ vs $C$ vs $L$ circuits).
- **`backend/services/rag_orchestrator.py`**: Resolves bare follow-up queries using conversation history, executes hybrid RRF search (dense pgvector halfvec + sparse tsquery), and streams model tokens.
- **`backend/services/academic_document_qa_service.py`**: Scoped retrieval for examination schedules (mid-term, end-semester), seating allocations by roll number/hall, and laboratory batch schedules.
- **`backend/services/calendar_qa_service.py`**: Authoritative Institutional Academic Calendar queries covering cohort class-day counts, holidays, timetable day substitutions, and milestones.
- **`backend/services/regulation_service.py`**: B.Tech Academic Regulations 2023 QA service with clause hierarchy traversal, condition preservation, and citation verification.
- **`backend/services/timetable_directory.py`**: Course faculty and venue directory extracted from weekly class timetables.
- **`backend/services/embedding_service.py`**: Interacts with Ollama embedding API (`embeddinggemma-2`). Handles batch embeddings, task prefixes (`task: search query | query: ...`), and dimension validation.
- **`backend/services/storage_service.py`**: Manages file storage for uploaded PDFs and extracted visual diagram crops.
- **`backend/services/topic_mapper.py`**: Maps past exam questions to syllabus topics with calibrated statuses (`mapped`, `provisional`, `unmapped`).

#### 5. Parsers (`backend/parsers/`)
- **`backend/parsers/pdf_classifier.py`**: Analyzes document contents to classify uploaded files as syllabus, PYQ, calendar, regulation, exam schedule, seating plan, or timetable.
- **`backend/parsers/syllabus_parser.py`**: Parses academic curricula extracting course codes, titles, L-T-P-C credits, units, topics, outcomes, evaluation patterns, and textbooks.
- **`backend/parsers/pyq_parser.py`**: Parses past examination papers extracting individual questions, sub-parts, maximum marks, Bloom's Taxonomy Levels (BTL 1–6), and Course Outcomes (CO1–CO6).
- **`backend/parsers/academic_documents_parser.py`**: Extracts tabular data from seating charts, mid-term/end-sem examination schedules, and laboratory schedules.
- **`backend/parsers/calendar_parser.py`**: Parses academic calendar tables into chronological calendar day records with status tags and timetable overrides.
- **`backend/parsers/regulations_parser.py`**: Parses academic regulation clauses, section hierarchies, conditions, and clause cross-references.
- **`backend/parsers/timetable_parser.py`**: Parses department class timetables into section grids, time slots, course assignments, and classroom venues.
- **`backend/parsers/visual_cropper.py`**: Identifies diagrams, circuits, and figures in exam papers using PyMuPDF vector drawing detection, crops them as PNG images, and binds them to Neo4j Question nodes.

#### 6. Frontend Layer (`frontend/`)
- **`frontend/index.html`**: Dual-portal authentication view with animated knowledge graph canvas.
- **`frontend/student.html`**: Conversational interface with verified syllabus/curriculum cards, diagram lightbox, voice recognition, and image attachment preview.
- **`frontend/admin.html`**: Upload dashboard with drag-and-drop dropzone, live ingestion progress, and database reset controls.
- **`frontend/js/page_student.js`**: Renders structured Zero-LLM cards for units, course details, prerequisites, and problems, and handles real-time SSE token streaming for open-ended queries.
- **`frontend/js/page_admin.js`**: Controls file uploads, status polling, and database actions.

---

### Codebase Guidelines & Development Standards

1. **No Monolithic Files**:
   - Every file must have a single, well-defined responsibility. Never combine API routing, database models, and parsing logic in one file.
2. **Deterministic Fast Path Priority**:
   - Factual syllabus, credit, semester, prerequisite, and textbook queries must resolve directly in Neo4j without invoking the LLM (Zero-LLM Fast Path, sub-20ms latency).
3. **Conversational Context Retention**:
   - When a student asks a bare follow-up (e.g. `unit 3 and references` or `what are the textbooks`), the system must carry over the previously resolved course code from session history.
4. **Input Sanitization & Error Handling**:
   - Use defensive validation (`try...except`) and log warnings using Python's standard `logging` module. Never expose raw internal exceptions or database credentials to API responses.
5. **Clean Indian English**:
   - All comments, docstrings, and frontend user notifications must be written in professional, natural Indian English. Do not include emojis in code or system responses.
6. **Strict Verification**:
   - Run the complete test suite before submitting any changes:
     ```bash
     pytest tests/ -v
     ```
   - Ensure all Docker containers build cleanly without warnings:
     ```bash
     docker compose up -d --build
     ```

