# CurriculumLens

Curriculum-Grounded Knowledge Retrieval and Academic Assistance System

A Final Year Project that turns uploaded academic syllabi into a searchable Knowledge Graph, enabling students to ask natural language questions grounded in official course materials.

---

## Project Structure

```
CurriculumLens/
|
|-- backend/                  # Python source files only
|   |-- app.py                # FastAPI server (all API routes)
|   |-- database.py           # PostgreSQL and Neo4j connection setup
|   |-- utils.py              # Core logic: PYQ chunking, embeddings, Syllabus regex, Vision AI
|   |-- uploads/              # Local storage for PDFs and dynamically cropped PYQ images
|
|-- frontend/                 # HTML/CSS/JS source files only
|   |-- login.html            # Entry point for the application
|   |-- login.js              # Handles role-based routing
|   |-- admin.html            # Admin dashboard: upload and manage documents
|   |-- admin.js              # Handles PDF uploads, deletions, and system reset
|   |-- student.html          # Student portal: AI-powered chat interface
|   |-- student.js            # Handles the AI chat and markdown parsing
|   |-- style.css             # Shared stylesheet for all pages
|
|-- architecture.md           # Beginner-friendly explanation of the tech stack
|-- api_docs.md               # Explanation of all backend endpoints
|-- colab_setup.md            # Google Colab cloud GPU setup guide
|-- troubleshooting.md        # Live-demo crash fixing guide
|-- .env                      # Environment variables (not committed to git)
|-- .env.example              # Template for setting up .env
|-- .gitignore
|-- .dockerignore
|-- docker-compose.yml        # Spins up PostgreSQL and Neo4j in Docker
|-- Dockerfile                # For containerising the backend (optional)
|-- requirements.txt          # Python dependencies
|-- readme.md
```

---

## How It Works

**For Syllabus Uploads:**
1. The backend parses the syllabus text.
2. A highly precise, deterministic Regex engine maps out every Unit and Topic into a structured format.
3. The structured topics are written directly to the Neo4j Knowledge Graph.

**For Past Year Questions (PYQs):**
1. `PyMuPDF` scans the invisible text layout to find exact Y-coordinates of question headers.
2. `Pillow` dynamically slices the high-res page image into individual question chunks.
3. The cropped chunks are sent to a Multimodal Vision AI (`gemma4`) which accurately transcribes the question text and extracts implicit formulas.
4. The question text is embedded as vectors in PostgreSQL, while relationships and models are mapped into Neo4j. The original cropped circuit diagram is also saved.

**For Answering Questions:**
1. A student asks a question in the chat portal.
2. The system searches PostgreSQL for similar text (vector search) and Neo4j for related concepts (graph traversal).
3. The retrieved context (which includes Markdown links to the cropped PYQ circuit diagrams) is passed to the LLM.
4. The LLM generates a grounded answer, displaying the perfectly cropped diagrams natively in the chat!

---

## Setup

### Requirements

- Docker Desktop (for PostgreSQL and Neo4j)
- Python 3.11
- Ollama running locally or tunnelled via Ngrok (e.g. from Google Colab)

### Step 1 — Configure environment

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

Edit `.env`:

```env
OLLAMA_BASE_URL=https://<your-ngrok-url>.ngrok-free.dev
OLLAMA_MODEL=gemma4:12b-it-qat
OLLAMA_EMBED_MODEL=nomic-embed-text

POSTGRES_USER=cluser
POSTGRES_PASSWORD=clpassword
POSTGRES_HOST=localhost
POSTGRES_PORT=5434
POSTGRES_DB=curriculumlens

NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=clpassword
```

### Step 2 — Start databases

```bash
docker compose up -d
```

### Step 3 — Set up Python environment

```bash
python3.11 -m venv backend/venv
source backend/venv/bin/activate
pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet
```

### Step 4 — Start the backend

```bash
cd backend
source venv/bin/activate
python app.py
```

The server runs at: http://localhost:8000

### Step 5 — Open the frontend

Open the following file directly in your browser (no web server needed):

```
frontend/login.html
```

- Type `admin` to access the Admin Dashboard
- Type `student` to access the Student Chat Portal

---

## API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | /upload | Upload a PDF and trigger background processing |
| GET | /documents | List all uploaded documents |
| DELETE | /documents/{id} | Delete a document and its data |
| POST | /chat | Submit a question and receive a grounded answer |
| POST | /reset | Wipe all data (PostgreSQL, Neo4j, uploaded files) |

---

## LLM Configuration

The system uses two separate Ollama models:

| Model | Purpose | Parameters |
|-------|---------|------------|
| `nomic-embed-text` | Converts text chunks into 768-dimensional vectors for semantic search | 137M |
| `gemma4:12b-it-qat` | Generates answers from retrieved context | 12B |

Before running the system, pull both models on your Ollama server:

```bash
ollama pull nomic-embed-text
ollama pull gemma4:12b-it-qat
```

- **Google Colab + Ngrok** — Run Ollama on a free T4 GPU, pull both models, and tunnel to your local machine.
- **Local Ollama** — Run `ollama serve` and pull both models on your laptop.
