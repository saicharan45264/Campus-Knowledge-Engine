# CurriculumLens

**A Graph-RAG system that turns academic PDFs into a searchable, AI-powered campus knowledge engine.**

Built as a Final Year Project at **Amrita Vishwa Vidyapeetham, Coimbatore**. We built this project completely from scratch using **Vanilla Python, Vanilla HTML/CSS/JS, and raw SQL/Neo4j queries**. We intentionally avoided high-level wrappers like FastAPI, SQLAlchemy, or React to demonstrate a deep, fundamental understanding of full-stack engineering and database architecture.

---

## How It Works

A student's question goes through five stages before an answer is returned:

```
Question
  └─► Zero-Shot LLM Router  →  classifies intent (Curriculum / Evaluation / Regulations / …)
         └─► Pre-filters which database partitions to search
               ├─► Hybrid Search  (pgvector cosine + tsvector lexical → RRF fusion)
               └─► Graph Search   (Neo4j: Topic → Question, Course → Unit → Topic)
                     └─► Combined context injected into LLM
                           └─► Streaming grounded answer
```

**Why hybrid search?** Pure vector search finds semantically similar text but misses exact course codes and technical terms. Pure keyword search misses paraphrased questions. Reciprocal Rank Fusion (k=60) combines both ranked lists without needing to calibrate score magnitudes.

**Why a knowledge graph?** Graph traversal answers structural questions instantly ("what topics are in Unit 3 of 23CSE201?", "show all PYQs linked to Mesh Analysis") that would require embedding every possible combination in a vector store.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend Web Server | **Vanilla Python 3.11** (Custom `http.server` WSGI implementation, no frameworks) |
| Graph Database | Neo4j 5.x — Curriculum hierarchy + PYQ→Topic mappings |
| Vector Database | PostgreSQL 16 + pgvector (768-dim) + tsvector (Raw `psycopg2` queries) |
| Retrieval | Hybrid Search with Reciprocal Rank Fusion (RRF, k=60) |
| LLM — Generation | `gemma4:12b-it-qat` via Ollama (12B params, multimodal) |
| Query Routing | Zero-shot LLM classification (Future: Custom NumPy Neural Network / QLoRA) |
| Frontend | **Vanilla HTML / CSS / JavaScript** |
| Infrastructure | Docker Compose (databases), Google Colab + Ngrok (GPU) |

---

## Project Structure

```
CurriculumLens/
│
├── backend/
│   ├── server.py            ← Custom HTTP server & routing logic (Entry point)
│   ├── core/
│   │   ├── config.py        ← Environment variable loading
│   │   ├── database.py      ← PostgreSQL connection pool + Neo4j driver + raw table initialization
│   │   └── security.py      ← JWT creation/validation + bcrypt hashing
│   └── services/
│       ├── ingestion.py     ← PDF extraction · syllabus regex parser · PYQ crop pipeline
│       ├── graph.py         ← Neo4j KG builder (syllabus) + question mapper (PYQ)
│       ├── retrieval.py     ← Hybrid RRF search using raw psycopg2 queries
│       └── llm.py           ← Ollama API calls for embeddings and generation
│
├── frontend/
│   ├── public/              ← HTML entry points (login, student, admin)
│   └── src/                 ← Vanilla JS controllers and API logic
│
├── docs/                    
│   ├── custom_ml_plan.md    ← Blueprint for building our own NumPy neural network
│   └── qlora_finetuning_plan.md ← Infrastructure plan for LLM fine-tuning
│
├── .env.example             ← Copy to .env and fill in your values
├── docker-compose.yml       ← Spins up PostgreSQL + Neo4j
└── requirements.txt         ← Minimal Python dependencies
```

---

## Setup

### Prerequisites
- Docker Desktop
- Python 3.11
- Ollama running locally **or** tunnelled from Google Colab via Ngrok

### 1 — Configure environment
```bash
cp .env.example .env
```
Edit `.env` and set `OLLAMA_BASE_URL` to your Ollama server URL.

### 2 — Start the databases
```bash
docker compose up -d
```

### 3 — Set up the Python environment
```bash
python3.11 -m venv backend/venv
source backend/venv/bin/activate
pip install -r requirements.txt
```

### 4 — Start the Vanilla Server
```bash
cd backend
source venv/bin/activate
python server.py
```

### 5 — Access the App
Open your browser and navigate to **http://localhost:8000** (The Python server handles serving the frontend directly!)

| Username | Password | Access |
|---|---|---|
| `student` | `student123` | Student chat portal |
| `admin`   | `admin123`   | Admin dashboard |

---

## Future ML Implementation

As part of our goal to demonstrate deep ML engineering, we have documented plans to move away from API wrappers for query routing:
1. **NumPy Neural Network**: We plan to implement a Multi-Layer Perceptron (MLP) from scratch using only math and NumPy arrays (see `docs/custom_ml_plan.md`).
2. **QLoRA Fine-Tuning**: Alternatively, we have an infrastructure plan to fine-tune a lightweight local model on Google Colab (see `docs/qlora_finetuning_plan.md`).

---

## Google Colab + Ngrok (Cloud GPU)

Running a 12B-parameter multimodal model requires significant GPU memory. We use Google Colab's free T4 GPU and tunnel it via Ngrok.

Paste this into a Colab cell and click Run:

```python
!apt-get update -qq && apt-get install -y -qq zstd pciutils > /dev/null 2>&1
!curl -fsSL https://ollama.com/install.sh | sh > /dev/null 2>&1
!pip install pyngrok -q

import os, threading, time
from pyngrok import ngrok

def start_ollama():
    os.system("OLLAMA_HOST=0.0.0.0 ollama serve")

threading.Thread(target=start_ollama, daemon=True).start()
time.sleep(3)

print("Pulling models…")
!ollama pull nomic-embed-text
!ollama pull gemma4:12b-it-qat

# Replace with your Ngrok token from https://dashboard.ngrok.com
ngrok.set_auth_token("YOUR_NGROK_TOKEN")
url = ngrok.connect(11434).public_url
print(f"\nSet this in your .env:\nOLLAMA_BASE_URL=\"{url}\"")
```

Copy the printed URL into `OLLAMA_BASE_URL` in your `.env`.
