# M.A.C.H. AI
**Multimodal Academic Cognitive Hub — A Fine-Tuned Graph-RAG Framework for Campus Knowledge Systems**  
*Amrita Vishwa Vidyapeetham · Final Year Project (VLIL — Vision-Language Intelligent Learning) · 2025–26*

[![FastAPI](https://img.shields.io/badge/Backend-FastAPI-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Neo4j](https://img.shields.io/badge/Graph_DB-Neo4j_5.24-008CC1?logo=neo4j&logoColor=white)](https://neo4j.com/)
[![PostgreSQL](https://img.shields.io/badge/Vector_DB-PostgreSQL_16_+_pgvector-336791?logo=postgresql&logoColor=white)](https://github.com/pgvector/pgvector)
[![Ollama](https://img.shields.io/badge/Local_Models-Gemma_4_12B_%2B_EmbeddingGemma_2-000000?logo=ollama&logoColor=white)](https://ollama.ai/)
[![Docker](https://img.shields.io/badge/Containers-Docker_Compose-2496ED?logo=docker&logoColor=white)](https://www.docker.com/)

---

## Overview

**M.A.C.H. AI** is an on-premise, privacy-preserving academic assistant built for university curriculum handbooks and Past Year Question papers (PYQs). It handles structured B.Tech regulations, semester course tables, prerequisite chains, evaluation patterns, and multimodal PYQ diagrams such as electrical circuits, waveforms, and automata.

The platform pairs a **Neo4j Property Graph** for exact curriculum and topic traversal with **PostgreSQL + `pgvector`** (`halfvec(768)` HNSW + GIN full-text search) for hybrid retrieval, powered locally via **Ollama** using **`gemma4:12b-it-qat`** (multimodal vision-language reasoning) and **`embeddinggemma-2:latest`** (unified text + image embeddings).

```
+----------------------------------------------------------------------------------------------------+
|                                    M.A.C.H. AI ARCHITECTURE                                        |
+----------------------------------------------------------------------------------------------------+
|                                                                                                    |
|      +------------------------+                           +-----------------------+                |
|      |  Curriculum Handbooks  |                           | Past Year Exam Papers |                |
|      |   (B.Tech 300+ Pages)  |                           |  (Mid / End-Sem PDF)  |                |
|      +-----------+------------+                           +-----------+-----------+                |
|                  |                                                    |                            |
|                  v                                                    v                            |
|      +------------------------+                           +-----------------------+                |
|      | 4-Phase Deterministic  |                           | 2-Pass PyMuPDF Cropper|                |
|      | Syllabus Parser        |                           | + Gemma 4 Vision VLM  |                |
|      +-----------+------------+                           +-----------+-----------+                |
|                  |                                                    |                            |
|                  +--------------------+-------------------------------+                            |
|                                       |                                                            |
|                                       v                                                            |
|                    +------------------------------------+                                          |
|                    |     Polyglot Persistence Layer     |                                          |
|                    +------------------+-----------------+                                          |
|                    |                  |                 |                                          |
|                    v                  v                 v                                          |
|              +-----------+      +-----------+     +------------+                                   |
|              |   Neo4j   |      |PostgreSQL |     | Cloudinary |                                   |
|              | Academic  |      | halfvec   |     | / Local    |                                   |
|              |   Graph   |      | HNSW+GIN  |     |  /static   |                                   |
|              +-----+-----+      +-----+-----+     +-----+------+                                   |
|                    |                  |                 |                                          |
|                    +------------------+-----------------+                                          |
|                                       |                                                            |
|                                       v                                                            |
|                    +------------------------------------+                                          |
|                    |  Tiered Query & Retrieval Engine   |                                          |
|                    |  - Tier-0 Guardrails               |                                          |
|                    |  - Tier-0.5 Semantic Cache         |                                          |
|                    |  - Zero-LLM Graph Fast-Path        |                                          |
|                    |  - Hybrid RRF + Topic Graph + VLM  |                                          |
|                    +------------------+-----------------+                                          |
|                                       |                                                            |
|                                       v                                                            |
|              +--------------------------------------------------+                                  |
|              | Local Streaming & Vision: gemma4:12b-it-qat      |                                  |
|              +--------------------------------------------------+                                  |
+----------------------------------------------------------------------------------------------------+
```

---

## Key Features

- **100% On-Premise & Private:** Runs locally on Apple Silicon or Linux GPU servers. No syllabus data, exam papers, or student queries leave the campus network.
- **Zero-LLM Graph Fast-Path:** Direct Neo4j Cypher queries answer curriculum structure, semester course tables, elective baskets, credit/LTPC specs, evaluation patterns, and prerequisite chains without calling the LLM.
- **Strict Hierarchical Academic Graph:** Models `Program -> SyllabusVersion -> Semester / ElectiveBasket / CurriculumRule -> Course -> Unit -> Topic`, shared `EvaluationPattern -> AssessmentComponent`, and `Question -> Topic / Course / ExamPaper` relationships.
- **VLIL Multimodal Diagram Pipeline:** Selectively crops PYQ pages containing circuits or figures at high resolution (1024px) using PyMuPDF, analyzes diagrams with `gemma4:12b-it-qat`, and computes joint text + diagram embeddings with `embeddinggemma-2:latest`.
- **Course-Code Alias & Topic Mapping:** Automatically resolves cross-listed codes (e.g., `23CSE104` to `23EEE104` via normalized course title) and maps PYQs accurately to specific topics using batched embeddings plus a selective Gemma 4 tie-breaker.
- **Tier-0 Guardrails & Tier-0.5 Semantic Cache:** Rejects prompt injections and off-topic queries upfront, and serves semantically similar repeated queries directly from `semantic_cache` (`halfvec(768)` HNSW).
- **Student & Admin Web Portals:** Student portal supports real-time SSE streaming with collapsible `<think>` reasoning, zoomable PYQ cards, mark/topic filtering, image paste/upload/drop queries, and feedback; Admin portal supports bulk PDF upload, background ingestion polling, evaluation radar charts, and system reset.

---

## Repository Structure

```
MACH_AI/
├── README.md                      # Project documentation and setup guide
├── ImplementationPlan.md          # Current & upcoming deployment roadmap
├── Dockerfile                     # Production container image for FastAPI server
├── docker-compose.yml             # Full-stack Docker setup (PostgreSQL 16 + Neo4j 5.24 + FastAPI)
├── requirements.txt               # Python package dependencies
├── pytest.ini                     # Pytest runner configuration
├── ruff.toml                      # Ruff linter rules
├── .env.example                   # Environment template (copy to .env before running)
│
├── backend/                       # FastAPI application package
│   ├── main.py                    # ASGI entrypoint (CORS, GZip, security headers, static mounts)
│   ├── config.py                  # Pydantic BaseSettings & environment config
│   ├── database.py                # SQLAlchemy ORM models, halfvec HNSW + GIN indexes, Neo4j schema
│   ├── api/
│   │   ├── auth.py                # JWT auth, Bcrypt hashing, RBAC guards (/login)
│   │   ├── chat.py                # /chat (SSE streaming & fast-path), /feedback, /image-query
│   │   ├── ingestion.py           # /upload, /documents, /processing-status, DELETE, /reset
│   │   └── evaluation.py          # /health, /eval/run, /eval/metrics
│   ├── core/
│   │   ├── guardrails.py          # Input sanitizer & retrieved chunk delimiter sanitizer
│   │   ├── intent_router.py       # Regex + embedding cosine intent classifier
│   │   ├── semantic_cache.py      # PostgreSQL halfvec(768) cache + in-memory cache
│   │   ├── telemetry.py           # Query latency tracking and query_logs aggregation
│   │   └── http_client.py         # Shared httpx client & GPU concurrency semaphore
│   ├── services/
│   │   ├── rag_orchestrator.py    # Multi-turn history, hybrid RRF retrieval, Gemma 4 streaming
│   │   ├── graph_service.py       # Neo4j Cypher queries, topic/course lookup, CRAG expansion
│   │   ├── embedding_service.py   # Batched text & multimodal embeddinggemma-2 caller + LRU cache
│   │   ├── topic_mapper.py        # Title-alias resolution, semantic + VLM topic mapper
│   │   └── storage_service.py     # Cloudinary upload with local /static/images fallback
│   └── parsers/
│       ├── pdf_classifier.py      # Auto-detects Syllabus vs PYQ PDFs + course title
│       ├── pyq_parser.py          # 2-pass bounding-box PYQ extractor (marks, BTL, CO, crops)
│       ├── visual_cropper.py      # Selective high-res cropper, SSRF-safe loader & Gemma 4 VLM
│       └── syllabus_parser.py     # 4-phase deterministic curriculum parser + <=1600-char chunks
│
├── frontend/                      # Web UI portals served at /public
│   ├── index.html                 # Unified login page
│   ├── student.html               # Student chat copilot & multimodal PYQ cards
│   ├── admin.html                 # Admin upload queue, evaluation radar & settings
│   ├── manifest.json & service-worker.js
│   ├── css/                       # Modular CSS stylesheets
│   └── js/                        # ES module UI controllers (api, auth, utils, login, student, admin)
│
├── scripts/                       # Evaluation & DevOps utilities
│   ├── eval_benchmark.py          # 20-query ground-truth retrieval benchmark
│   ├── sync_paper_metrics.py      # Exports benchmark & telemetry metrics to docs/generated_tables.tex
│   ├── reembed_chunks.py          # Batch migration script to re-embed chunks with embeddinggemma-2
│   └── backup_databases.sh        # Automated PostgreSQL & Neo4j backup script (14-day retention)
│
├── deploy/                        # Campus server deployment configs
│   ├── nginx/nginx.conf           # HTTPS reverse proxy & unbuffered SSE streaming for /chat
│   └── systemd/ollama.service     # Managed Ollama systemd unit
│
├── tests/                         # Automated test suite (35 tests)
│   ├── test_embedder.py
│   ├── test_intent_router.py
│   ├── test_parsers_and_security.py
│   ├── test_integration_neo4j.py
│   └── test_security_auth.py
│
└── docs/                          # Research manuscript, inputs & test PDFs
    ├── MACH_IEEE_Paper.tex
    ├── generated_tables.tex
    ├── ref.bib
    ├── model input docs/          # Official curriculum & exam paper PDFs
    └── test_docs/                 # Course-wise sample question papers
```

---

## Database Schema Overview

### 1. Neo4j Property Graph
- **Curriculum Hierarchy:** `Program` -> `SyllabusVersion` -> `Semester` / `ElectiveBasket` / `CurriculumRule`
- **Courses & Structure:** `Semester -[:OFFERS]-> Course`, `Semester -[:HAS_ELECTIVE_COURSE]-> electiveCourse`, `ElectiveBasket -[:CONTAINS]-> Course`
- **Assessment & Syllabus:** `Course -[:HAS_EVALUATION_PATTERN]-> EvaluationPattern -[:ASSESSED_BY]-> AssessmentComponent`, `Course -[:HAS_UNIT]-> Unit -[:HAS_TOPIC]-> Topic`, `Course -[:HAS_OBJECTIVE]-> CourseObjective`, `Course -[:HAS_OUTCOME]-> CourseOutcome`, `Course -[:USES_TEXTBOOK]-> Textbook`, `Course -[:USES_REFERENCE]-> ReferenceBook`
- **Prerequisites & PYQs:** `Course -[:REQUIRES]-> Course`, `Question -[:BELONGS_TO]-> Course`, `Question -[:FROM_PAPER]-> ExamPaper`, `Question -[:TESTS]-> Topic`, `Course -[:TESTED_BY]-> Question`, `TopicAlias`

### 2. PostgreSQL + `pgvector`
- `users`: Bcrypt-hashed credentials and user roles (`student`, `admin`).
- `documents`: Uploaded PDF metadata, classification (`syllabus` / `pyq`), and course/program details.
- `document_chunks`: Extracted syllabus/PYQ chunks with 768-dim `HALFVEC(768)` embeddings indexed via HNSW (`halfvec_cosine_ops`) and a stored `tsvector` GIN full-text index.
- `processing_reports`: Stage-by-stage ingestion summaries and status (`completed` / `error`).
- `query_logs`: Per-query latency breakdown (router, retrieval, TTFT, total ms) and token counts.
- `semantic_cache`: `HALFVEC(768)` prompt embeddings and cached structured/text responses.

---

## Quickstart Setup

### Prerequisites
- Docker Desktop (for `pgvector`, `Neo4j 5.24`, and containerized backend)
- Python 3.11+ (for local development, scripts, and tests)
- [Ollama](https://ollama.ai/) running locally on port `11434`

### 1. Configure Environment
```bash
cp .env.example .env
```
Update `JWT_SECRET` (mandatory in production) and verify Ollama/DB settings in `.env`:
```env
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=gemma4:12b-it-qat
OLLAMA_EMBED_MODEL=embeddinggemma-2:latest

POSTGRES_USER=mach
POSTGRES_PASSWORD=ai
POSTGRES_HOST=localhost
POSTGRES_PORT=5434
POSTGRES_DB=mach_ai

NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=mach_ai_cke
```

### 2. Pull Required Ollama Models
```bash
ollama serve
ollama pull embeddinggemma-2:latest
ollama pull gemma4:12b-it-qat
```

### 3. Start Stack with Docker Compose
```bash
docker compose up -d --build
```
- **Web Portal:** [`http://localhost:8000/public/index.html`](http://localhost:8000/public/index.html)
- **Health Check:** [`http://localhost:8000/health`](http://localhost:8000/health)
- **Neo4j Browser:** [`http://localhost:7474`](http://localhost:7474)
- **Demo Accounts:** `student` / `student123` · `admin` / `admin123`

For local host backend execution during development (keeping DBs in Docker):
```bash
docker compose up -d postgres neo4j
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

---

## REST API Reference

| Method | Endpoint | Description | Access |
|---|---|---|:---:|
| `POST` | `/login` | Authenticate credentials and issue JWT bearer token | Public |
| `POST` | `/chat` | Query endpoint (Fast-Path JSON, cached responses, SSE streaming, image attachments) | Student / Admin |
| `POST` | `/feedback/{message_id}` | Submit thumbs up/down feedback (`X-Message-ID`) | Student / Admin |
| `POST` | `/image-query` | Reverse-lookup academic concept from an uploaded image | Student / Admin |
| `POST` | `/upload` | Bulk PDF upload with auto-classification and background ingestion | Admin |
| `GET` | `/documents` | List indexed documents and ingestion status | Admin |
| `GET` | `/processing-status` | Poll background ingestion queue progress | Admin |
| `DELETE` | `/documents/{doc_id}` | Cascade delete document, chunks, crops, and Neo4j nodes | Admin |
| `POST` | `/eval/run` | Run and save the 20-query evaluation benchmark | Admin |
| `GET` | `/eval/metrics` | Fetch runtime telemetry and saved benchmark metrics | Admin |
| `POST` | `/reset` | Wipe all documents, chunks, images, and Neo4j graph data | Admin |
| `GET` | `/health` | Check PostgreSQL, Neo4j, and Ollama connectivity | Public |

---

## Testing, Linting & Evaluation

```bash
# Linter
./venv/bin/ruff check .

# Full unit + integration + security test suite (35 tests)
./venv/bin/pytest -v

# Ground-truth evaluation benchmark & LaTeX table export
./venv/bin/python scripts/eval_benchmark.py
./venv/bin/python scripts/sync_paper_metrics.py

# Re-embed existing chunks after changing embedding model
./venv/bin/python scripts/reembed_chunks.py
```

---

## Current & Upcoming Roadmap

- **Completed:** Strict curriculum graph hierarchy (`Program` / `SyllabusVersion` / `electiveCourse` / shared `EvaluationPattern`), `HALFVEC(768)` + `embeddinggemma-2:latest` multimodal pipeline, 1024px `gemma4:12b-it-qat` VLIL diagram analysis, cross-listed course code resolution, topic-strict PYQ retrieval, full security hardening, and Docker Compose deployment.
- **Upcoming:** Expanding ingestion across institutional academic calendars, regulations handbooks, lab timetables, and multi-department PYQ archives, plus campus HTTPS edge proxy deployment via Nginx (`deploy/nginx/nginx.conf`) and automated nightly database backups (`scripts/backup_databases.sh`).

---

*Amrita Vishwa Vidyapeetham · Department of Computer Science & Engineering · Final Year Project 2025–26*
