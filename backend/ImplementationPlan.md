# M.A.C.H. AI — Pending Tasks & Deployment Checklist
**Multimodal Academic Cognitive Hub — Amrita Vishwa Vidyapeetham**  
*Actionable pending tasks for server deployment, host configuration, and institutional data ingestion.*

---

## 1. Pending Tasks Summary

| Category | Task | Target Environment | Status |
| :--- | :--- | :--- | :--- |
| **Server Operations** | Deploy Nginx HTTPS reverse proxy with institutional SSL certificates | Campus GPU Server / Linux VM | Pending Server Setup |
| **Server Operations** | Enable Ollama systemd background daemon (`deploy/systemd/ollama.service`) | Campus GPU Server / Linux VM | Pending Server Setup |
| **Server Operations** | Configure nightly cron job for automated database dumps (`backup_databases.sh`) | Campus Host Server | Pending Server Setup |
| **Production Hardening** | Generate production `JWT_SECRET` and set restrictive `CORS_ORIGINS` | Production `.env` on Host | Pending Launch |
| **Data Ingestion** | Upload remaining B.Tech engineering stream curricula (ECE, EEE, MECH, AIE) | Admin Portal (`/public/admin.html`) | In Progress |
| **Data Ingestion** | Ingest complete historical PYQ sets and model answers | Admin Portal (`/public/admin.html`) | In Progress |
| **Data Ingestion** | Ingest active semester timetables and exam seating allotment PDFs | Admin Portal (`/public/admin.html`) | In Progress |
| **Data Ingestion** | Execute batch chunk re-embedding script (`reembed_chunks.py`) | Production Backend Container | Post-Ingestion |

---

## 2. Server Operations & Deployment

### Step 1: HTTPS Reverse Proxy & SSL Setup
Modern web browsers block Web Speech API microphone dictation over insecure HTTP connections on remote hosts. An SSL reverse proxy is required.

- **Config Path:** [`deploy/nginx/nginx.conf`](file:///Users/Vamsi/Desktop/FYP/MACH_AI/deploy/nginx/nginx.conf)
- **Actions Required:**
  1. Install Nginx on the campus host: `sudo apt update && sudo apt install -y nginx`
  2. Copy configuration: `sudo cp deploy/nginx/nginx.conf /etc/nginx/sites-available/mach.conf`
  3. Install institutional SSL certificate and private key (e.g. from Let's Encrypt or Amrita IT Services):
     ```nginx
     ssl_certificate /etc/ssl/certs/mach_amrita_edu.crt;
     ssl_certificate_key /etc/ssl/private/mach_amrita_edu.key;
     ```
  4. Enable the site and restart Nginx:
     ```bash
     sudo ln -s /etc/nginx/sites-available/mach.conf /etc/nginx/sites-enabled/
     sudo nginx -t && sudo systemctl restart nginx
     ```
  5. Verify that `proxy_buffering off;` remains active on the `/chat` route for Server-Sent Events (SSE) token streaming.

### Step 2: Systemd Managed Ollama Service
Ensure model inference runs automatically on boot with GPU acceleration.

- **Service Unit Path:** [`deploy/systemd/ollama.service`](file:///Users/Vamsi/Desktop/FYP/MACH_AI/deploy/systemd/ollama.service)
- **Actions Required:**
  1. Copy the systemd service file:
     ```bash
     sudo cp deploy/systemd/ollama.service /etc/systemd/system/ollama.service
     ```
  2. Reload systemd and enable service:
     ```bash
     sudo systemctl daemon-reload
     sudo systemctl enable --now ollama
     ```
  3. Pull required model weights onto the host:
     ```bash
     ollama pull embeddinggemma-2:latest
     ollama pull gemma4:12b-it-qat
     ```
  4. Verify service status:
     ```bash
     curl http://localhost:11434/api/tags
     ```

### Step 3: Nightly Automated Database Backups
Automate snapshots for both PostgreSQL and Neo4j with a 14-day retention cycle.

- **Script Path:** [`backend/scripts/backup_databases.sh`](file:///Users/Vamsi/Desktop/FYP/MACH_AI/backend/scripts/backup_databases.sh)
- **Actions Required:**
  1. Ensure execution permissions on host:
     ```bash
     chmod +x backend/scripts/backup_databases.sh
     ```
  2. Add entry to host cron table (`crontab -e`):
     ```cron
     0 2 * * * /bin/bash /absolute/path/to/MACH_AI/backend/scripts/backup_databases.sh >> /var/log/mach_backup.log 2>&1
     ```
  3. Verify that the backup directory `/var/backups/mach_ai` is created and writable.

---

## 3. Production Configuration & Security Hardening

- **Production `.env` Parameters:**
  1. Generate a strong cryptographic secret for JWT token issuance:
     ```bash
     openssl rand -hex 32
     ```
  2. Update production `.env`:
     ```env
     APP_ENV=production
     JWT_SECRET="<generated-random-32-byte-hex>"
     CORS_ORIGINS="https://mach.amrita.edu"
     OLLAMA_BASE_URL="http://host.docker.internal:11434"
     ```
  3. Ensure `.env` file permissions are restricted to the server administrator:
     ```bash
     chmod 600 .env
     ```

---

## 4. Institutional Data Ingestion & Indexing

All ingestion components and parsers are verified. The operational task is populating the remaining institutional PDF documents.

### Step 1: Upload Remaining Curricula & Handbooks
1. Log in to the Admin Dashboard at `https://mach.amrita.edu/public/admin.html` using administrative credentials.
2. Under the **Document Ingestion** tab, upload official curriculum handbooks for:
   - B.Tech Artificial Intelligence & Data Science (AIE)
   - B.Tech Electronics & Communication Engineering (ECE)
   - B.Tech Electrical & Electronics Engineering (EEE)
   - B.Tech Mechanical Engineering (MECH)
3. Ensure the ingestion pipeline creates corresponding `Program`, `Course`, and `Topic` nodes in Neo4j.

### Step 2: Ingest Semester Timetables & Seating Plans
1. Upload active semester schedule PDFs (`timetable_parser.py` maps room slots, faculty codes, and course periods).
2. Upload mid-term and end-semester seating layout PDFs (`academic_documents_parser.py` maps roll number ranges to hall allocations).

### Step 3: Batch Re-embedding (Post-Ingestion)
After bulk ingestion of multi-branch PDFs, execute the re-embedding utility inside the container to ensure all vector chunks in PostgreSQL `halfvec(768)` are indexed:
```bash
docker exec -it cl-backend python backend/scripts/reembed_chunks.py
```

---

## 5. Post-Deployment Verification Checklist

Once the server deployment steps and document ingestion are completed, perform the following end-to-end checks:

- [ ] **Health Endpoint:** Confirm `GET /health` returns `{"status": "ok", "postgres": true, "neo4j": true, "ollama": true}`.
- [ ] **Authentication Flow:** Verify student and admin logins via `/public/index.html`.
- [ ] **Zero-LLM Fast-Path:** Query `"What are the prerequisites for 23CSE301?"` and verify sub-30ms response time.
- [ ] **SSE Token Streaming:** Verify live answer streaming on the student chat interface without buffering delays.
- [ ] **Microphone Input:** Confirm Web Speech voice-to-text dictation works over HTTPS.
- [ ] **Quality Benchmark:** Click **Run Quality Check** on the Admin tab to run `POST /eval/run` and verify that the retrieval accuracy radar chart renders with passing metrics.
