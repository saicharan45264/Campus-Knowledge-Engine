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
|                    |  - Query Safety Filter             |                                          |
|                    |  - Semantic Similarity Cache        |                                          |
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
- **Query Safety Filter & Semantic Cache:** Rejects prompt injections and off-topic queries upfront, and serves semantically similar repeated queries directly from `semantic_cache` (`halfvec(768)` HNSW).
- **Student & Admin Web Portals:** Student portal supports real-time SSE streaming with collapsible `<think>` reasoning, zoomable PYQ cards, mark/topic filtering, image paste/upload/drop queries, and feedback; Admin portal supports bulk PDF upload, background ingestion polling, evaluation radar charts, and system reset.

---

## Repository Structure

```
MACH_AI/
├── README.md                      # Project documentation and setup guide
├── docker-compose.yml             # Full-stack Docker setup (PostgreSQL 16 + Neo4j 5.24 + FastAPI)
├── requirements.txt               # Python package dependencies
├── ruff.toml                      # Ruff linter rules
├── .gitignore                     # Git exclusion rules
├── .env                           # Local environment configuration
│
├── backend/                       # FastAPI application package
│   ├── main.py                    # ASGI entrypoint (CORS, GZip, security headers, static mounts)
│   ├── config.py                  # Pydantic BaseSettings & environment config
│   ├── database.py                # SQLAlchemy ORM models, halfvec HNSW + GIN indexes, Neo4j schema
│   ├── ImplementationPlan.md      # Backend implementation checklist and roadmap
│   ├── scripts/                   # Backend maintenance utilities (backup_databases.sh, reembed_chunks.py)
│   ├── api/
│   │   ├── auth.py                # JWT auth, Bcrypt hashing, RBAC guards (/login)
│   │   ├── chat.py                # /chat (SSE streaming & fast-path), /feedback, /image-query
│   │   ├── ingestion.py           # /upload, /documents, /processing-status, DELETE, /reset
│   │   └── evaluation.py          # /health, /eval/run, /eval/metrics
│   ├── core/
│   │   ├── query_safety_filter.py # Input safety filter & prompt injection sanitizer
│   │   ├── intent_router.py       # Regex + embedding cosine intent classifier
│   │   ├── semantic_cache.py      # PostgreSQL halfvec(768) cache + in-memory cache
│   │   ├── query_performance_logger.py # Query latency tracking and query_logs database logger
│   │   └── http_client.py         # Shared httpx client & GPU concurrency semaphore
│   ├── services/
│   │   ├── rag_orchestrator.py    # Multi-turn history, hybrid RRF retrieval, Gemma 4 streaming
│   │   ├── graph_service.py       # Neo4j Cypher queries, contrasting subtopic isolation, topic/course lookup
│   │   ├── academic_document_qa_service.py # Scoped retrieval for exam schedules, seating, and lab timetables
│   │   ├── calendar_qa_service.py # Institutional academic calendar QA (milestones, class days, holidays)
│   │   ├── regulation_service.py  # B.Tech Academic Regulations 2023 QA with citation verification
│   │   ├── timetable_directory.py # Course faculty and venue directory from class timetables
│   │   ├── embedding_service.py   # Batched text & multimodal embeddinggemma-2 caller + LRU cache
│   │   ├── topic_mapper.py        # Title-alias resolution, calibrated question-topic mapper
│   │   ├── evaluation_benchmark.py # 20-sample ground-truth benchmark suite
│   │   └── storage_service.py     # Cloudinary upload with local /static/images fallback
│   └── parsers/
│       ├── pdf_classifier.py      # Auto-detects Syllabus, PYQ, Calendar, Regulations, Schedules
│       ├── pyq_parser.py          # 2-pass bounding-box PYQ extractor (marks, BTL, CO, crops)
│       ├── visual_cropper.py      # Selective high-res cropper, SSRF-safe loader & Gemma 4 VLM
│       ├── syllabus_parser.py     # 4-phase deterministic curriculum parser + <=1600-char chunks
│       ├── academic_documents_parser.py # Seating charts, mid-term/end-sem exam schedules, lab grids
│       ├── calendar_parser.py     # Academic calendar tables into daily event records
│       ├── regulations_parser.py  # Regulation clauses, section hierarchies, and cross-references
│       └── timetable_parser.py    # Class timetable grids into slots, faculty, and room venues
│
├── frontend/                      # Web UI portals served at /public
│   ├── index.html                 # Unified login page
│   ├── student.html               # Student chat copilot & multimodal PYQ cards
│   ├── admin.html                 # Admin upload queue, evaluation radar & settings
│   ├── manifest.json & service-worker.js
│   ├── css/                       # Modular CSS stylesheets
│   └── js/                        # ES module UI controllers (api, auth, utils, login, student, admin)
│
├── deploy/                        # Production server deployment & container configs
│   ├── Dockerfile                 # Production multi-stage container image
│   ├── .dockerignore              # Docker build exclusions
│   ├── .env.example               # Reference environment variables template
│   ├── nginx/nginx.conf           # HTTPS reverse proxy & unbuffered SSE streaming for /chat
│   └── systemd/ollama.service     # Managed Ollama systemd unit
│
└── tests/                         # Automated test suite (110+ tests)
    ├── pytest.ini                 # Pytest runner configuration
    ├── cache/                     # Centralized test and linter caches
    ├── test_data/                 # Sample evaluation test fixtures
    ├── test_academic_document_qa.py
    ├── test_academic_tool_calling.py
    ├── test_calendar_restoration.py
    ├── test_embedder.py
    ├── test_integration_neo4j.py
    ├── test_intent_router.py
    ├── test_lab_timetable_queries.py
    ├── test_parsers_and_security.py
    ├── test_regulation_grounding.py
    ├── test_security_auth.py
    ├── test_timetable_question_service.py
    └── test_topic_mapper.py
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

## Quickstart Setup & Execution Runbook

### Prerequisites
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (PostgreSQL 16 `pgvector`, Neo4j 5.24 Community, FastAPI)
- Python 3.11+ (for local host execution, scripts, and tests)
- [Ollama](https://ollama.com/) (running locally on port `11434` or remotely on Google Colab GPU via Ngrok)

---

### 1. Configure Environment
Copy the deployment template into your local `.env`:
```bash
cp deploy/.env.example .env
```
Key defaults configured in `.env`:
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

---

### 2. LLM Serving Options

#### Option A: Local Ollama Execution
```bash
ollama serve
ollama pull embeddinggemma-2:latest
ollama pull gemma4:12b-it-qat
```

#### Option B: Free Google Colab GPU Tunnel (via Ngrok)
Run this block in a free Google Colab GPU Notebook (T4 / A100):
```python
# 1. Install Ollama & Dependencies
!apt-get update -qq && apt-get install -y -qq zstd pciutils > /dev/null 2>&1
!curl -fsSL https://ollama.com/install.sh | sh > /dev/null 2>&1
!pip install pyngrok -q > /dev/null 2>&1

import os, threading, time
from pyngrok import ngrok

# 2. Start Ollama Server in Background
threading.Thread(target=lambda: os.system("OLLAMA_HOST=0.0.0.0 ollama serve"), daemon=True).start()
print("Starting Ollama server...")
time.sleep(3)

# 3. Pull Production Models
print("\nPulling embeddinggemma-2:latest (768d embeddings)...")
!ollama pull embeddinggemma-2:latest
print("\nPulling gemma4:12b-it-qat (Multimodal Vision + Reasoning)...")
!ollama pull gemma4:12b-it-qat

# 4. Open Ngrok Tunnel (replace with your Ngrok token)
ngrok.set_auth_token("YOUR_NGROK_AUTH_TOKEN")
public_url = ngrok.connect(11434).public_url
print("\n" + "="*60)
print(f"OLLAMA CLOUD URL: {public_url}")
print("="*60 + "\n")
print(f'Set in your local .env: OLLAMA_BASE_URL="{public_url}"')
```

---

### 3. Docker Orchestration

#### Clean Reset (Rebuild from Scratch)
```bash
docker compose down -v
docker compose up -d --build
```

#### Normal Start / Stop
```bash
# Start all containers in background
docker compose up -d

# Stop all containers
docker compose down
```

#### Run Backend Locally on Host (Keeping DBs in Docker)
```bash
# 1. Start database containers only
docker compose up -d postgres neo4j
docker stop cl-backend 2>/dev/null || true

# 2. Activate virtualenv and run FastAPI backend
source venv/bin/activate
pip install -r requirements.txt
python backend/main.py
```

---

### 4. Default Access & Credentials

| Service | Interface URL | Default Credentials | Purpose |
|:---|:---|:---|:---|
| **Student Portal** | [`http://localhost:8000`](http://localhost:8000) | `student` / `student123` | Conversational query interface & PYQ search |
| **Admin Portal** | [`http://localhost:8000/admin.html`](http://localhost:8000/admin.html) | `admin` / `admin123` | Document ingestion queue & evaluation radar |
| **PostgreSQL (pgvector)** | `localhost:5434` | `mach` / `ai` (`db: mach_ai`) | Relational documents, 768d vector store & audit logs |
| **Neo4j Browser** | [`http://localhost:7474`](http://localhost:7474) | `neo4j` / `mach_ai_cke` | Visual Graph Explorer (`bolt://localhost:7687`) |

---

### 5. Database Inspection & Verification Queries

Inspect PostgreSQL inside the running container:
```bash
docker exec -it cl-postgres psql -U mach -d mach_ai
```

Common verification queries:
```sql
-- 1. Check indexed documents
SELECT doc_type, department, filename, created_at FROM documents;

-- 2. Check total indexed vector chunks
SELECT COUNT(*) AS total_chunks FROM document_chunks;

-- 3. Inspect recent query routing & latency logs
SELECT intent, execution_latency_ms, timestamp FROM query_logs ORDER BY timestamp DESC LIMIT 10;
```

---

### 6. Automated Testing & Code Quality

Run tests and linters from the project root:
```bash
# Linter check
./venv/bin/ruff check .

# Run full automated regression test suite (110+ tests across 12 suites)
./venv/bin/pytest tests/ -v
```

---

### 7. Maintenance & Backups

```bash
# Run automated database backup snapshot (PostgreSQL dump + Neo4j archive)
./backend/scripts/backup_databases.sh

# Batch re-embed document chunks into PostgreSQL pgvector
python backend/scripts/reembed_chunks.py
```

---

## Current & Upcoming Roadmap

- **Completed:** Strict curriculum graph hierarchy (`Program` / `SyllabusVersion` / `electiveCourse` / shared `EvaluationPattern`), `HALFVEC(768)` + `embeddinggemma-2:latest` multimodal pipeline, 1024px `gemma4:12b-it-qat` VLIL diagram analysis, cross-listed course code resolution, topic-strict PYQ retrieval, full security hardening, and Docker Compose deployment.
- **Upcoming:** Expanding ingestion across institutional academic calendars, regulations handbooks, lab timetables, and multi-department PYQ archives, plus campus HTTPS edge proxy deployment via Nginx (`deploy/nginx/nginx.conf`) and automated nightly database backups (`scripts/backup_databases.sh`).

---

*Amrita Vishwa Vidyapeetham · Department of Computer Science & Engineering · Final Year Project 2025–26*
