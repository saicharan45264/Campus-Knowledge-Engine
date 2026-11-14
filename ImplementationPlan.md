# M.A.C.H. AI — Production Deployment & Operations Plan
**Multimodal Academic Cognitive Hub — Amrita Vishwa Vidyapeetham**  
*VLIL Final Year Project — System Status, Deployment Steps & Upcoming Work*

---

## Status Overview

| Area | Item | Priority | Status |
|---|---|:---:|:---:|
| **Graph Schema** | Hierarchical Neo4j model (`Program`, `SyllabusVersion`, `Semester`, `electiveCourse`, `ElectiveBasket`, shared `EvaluationPattern`, `AssessmentComponent`, `Topic`, `Question`, `ExamPaper`) | High | Completed |
| **Vector Store** | PostgreSQL `pgvector` upgraded to `HALFVEC(768)` HNSW (`halfvec_cosine_ops`) + GIN full-text search + re-embedding utility | High | Completed |
| **VLIL Multimodal** | `embeddinggemma-2:latest` unified text+image embeddings & `gemma4:12b-it-qat` 1024px diagram analysis | High | Completed |
| **PYQ & Topic Mapping** | 2-pass PyMuPDF cropper, course-title alias resolver (e.g. `23CSE104` -> `23EEE104`), topic-strict retrieval & multi-turn carryover | High | Completed |
| **Academic Document QA** | Scoped lookups for exam seating by roll number, mid-term/end-sem dates, timetable faculty directory, academic calendar milestones, and B.Tech regulations | High | Completed |
| **Security & Linking** | Bcrypt auth, RBAC guards, SSRF/path-traversal checks, XSS escaping, image payload validation, frontend-backend parity & 110 tests | Critical | Completed |
| **Containerization** | Full-stack Docker Compose (`postgres`, `neo4j`, `backend`) with clean `.dockerignore` & non-baked `.env` | High | Completed |
| **Reverse Proxy (HTTPS)** | Campus edge HTTPS via `deploy/nginx/nginx.conf` for Web Speech microphone & unbuffered SSE | Critical | Ready for Server Deploy |
| **Ollama Systemd Daemon** | Managed `deploy/systemd/ollama.service` for 24x7 local model serving on GPU server | High | Ready for Server Deploy |
| **Nightly Backups** | Automated PostgreSQL & Neo4j dumps using `scripts/backup_databases.sh` with 14-day retention | Medium | Ready for Server Deploy |
| **Campus Data Expansion** | Ingest remaining B.Tech stream syllabi, academic calendars, regulations, lab allocations, and PYQ sets | Medium | In Progress |

---

## Phase 1: Core Security & Pre-Launch Checks (Completed)

1. **JWT & Credentials:** Set a strong random `JWT_SECRET` in `.env` before starting production containers (`backend/main.py` logs a warning if default or short secrets are detected under `APP_ENV=production`).
2. **CORS Origins:** Set `CORS_ORIGINS=https://mach.amrita.edu` in production `.env`. When wildcard `*` is used locally, `allow_credentials` is disabled automatically.
3. **Upload & Image Guards:**
   - PDF uploads (`/upload`) enforce `%PDF-` magic bytes, 100 MB streaming bounds, and sanitized filenames under `settings.uploads_dir`.
   - Chat and `/image-query` attachments validate base64 integrity, image magic headers (`PNG`, `JPEG`, `WebP`, `GIF`), and a 20 MB limit.
   - `visual_cropper.load_image_as_b64` blocks private/loopback/metadata SSRF targets and restricts local disk reads to `settings.uploads_dir`.

---

## Phase 2: Campus Server Deployment Steps

### 1. HTTPS / SSL Reverse Proxy (Required for Microphone Input)
Modern browsers disable Web Speech dictation on remote `http://` hosts. Deploy Nginx in front of FastAPI (`backend:8000`) using [`deploy/nginx/nginx.conf`](file:///Users/Vamsi/Desktop/FYP/MACH_AI/deploy/nginx/nginx.conf):
- Redirect port 80 traffic to HTTPS (port 443) with institutional TLS certificates.
- Keep `proxy_buffering off;` and `proxy_cache off;` on `/chat` so SSE reasoning & answer tokens stream smoothly in real time.
- Forward `Host`, `X-Real-IP`, `X-Forwarded-For`, and `X-Forwarded-Proto` headers on both `/` and `/chat`.

### 2. Ollama Host Service
Install and start Ollama as a managed daemon on Linux using [`deploy/systemd/ollama.service`](file:///Users/Vamsi/Desktop/FYP/MACH_AI/deploy/systemd/ollama.service):
```bash
sudo cp deploy/systemd/ollama.service /etc/systemd/system/ollama.service
sudo systemctl daemon-reload
sudo systemctl enable --now ollama
ollama pull embeddinggemma-2:latest
ollama pull gemma4:12b-it-qat
```
Verify both models are served: `curl http://localhost:11434/api/tags`.

### 3. Docker Compose Production Launch
```bash
cp .env.example .env
# Edit .env: set strong JWT_SECRET, CORS_ORIGINS, and DB credentials
docker compose up -d --build
```
Verify service health:
```bash
curl http://localhost:8000/health
```
Expected status is `"status": "ok"` with `postgres: true`, `neo4j: true`, and `ollama: true`.

---

## Phase 3: Data Ingestion, Evaluation & Backups

### 1. Ingest Institutional Documents (Current & Upcoming)
- Upload core curriculum handbooks (`docs/model input docs/B.Tech CSE - 2023 Curriculum.pdf`) and PYQ archives via the Admin Upload tab (`/public/admin.html`) using Auto-Detect mode.
- **Upcoming ingestion scope:** Extend parser profiles and structured ingestion for academic calendars (`Academic-Calendar-AY-2025-2026.pdf`), B.Tech regulations (`B.Tech ASE regulations 2023.pdf`), lab allocations, and multi-semester exam papers across departments.

### 2. Automated Verification & Benchmarking
Run linting, unit/integration tests, and the 20-case benchmark after updates:
```bash
./venv/bin/ruff check .
./venv/bin/pytest -v
./venv/bin/python scripts/eval_benchmark.py
./venv/bin/python scripts/sync_paper_metrics.py
```
Admins can also click **Run Evaluation Pipeline** inside `/public/admin.html`, which triggers `POST /eval/run`, saves `scripts/benchmark_results.json`, and updates the radar chart.

### 3. Automated Database Backups
Schedule [`scripts/backup_databases.sh`](file:///Users/Vamsi/Desktop/FYP/MACH_AI/scripts/backup_databases.sh) in `crontab -e` on the host:
```cron
0 2 * * * /bin/bash /path/to/MACH_AI/scripts/backup_databases.sh >> /var/log/mach_backup.log 2>&1
```
This archives both PostgreSQL (`pg_dump`) and Neo4j (`neo4j-admin database dump`) snapshots daily and prunes files older than 14 days.
