# 🎓 CurriculumLens

**Curriculum-Grounded Multimodal Knowledge Retrieval and Examination Intelligence for Academic Assistance**

> Final Year Project — Team 81 | VLIL TAG | Dept. of CSE, Amrita Vishwa Vidyapeetham, Coimbatore

## 🔬 Research Contributions

1. **Curriculum Knowledge Graph (CKG) Auto-Construction** — Automatically builds a structured `Course → Unit → Topic → Concept → Formula` graph from syllabi and course materials using LLM-guided NER and relation extraction.

2. **Visual Academic Concept Identifier (VACI)** — A fine-tuned Vision-Language Model (Qwen2-VL + LoRA) that identifies academic concepts from uploaded images of lecture slides, diagrams, formulas, and handwritten notes — going beyond OCR to true visual semantic understanding.

3. **KG-Enhanced Hybrid Retrieval** — Four-path retrieval combining dense vector search (BGE-M3), sparse keyword search (BM25), knowledge graph traversal (Neo4j), and visual similarity search (ColPali) — demonstrably outperforming vanilla RAG through ablation studies.

4. **PYQ Examination Intelligence** — Automated extraction, classification, and analysis of Previous Year Question papers to generate concept frequency heatmaps, difficulty curves, question-type distributions, and data-driven topic importance predictions.

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Next.js 14 Frontend                       │
│  Chat UI │ KG Explorer │ PYQ Analytics │ Document Manager    │
├─────────────────────────────────────────────────────────────┤
│                    FastAPI Backend                            │
│  Query Router │ VACI │ Retrieval Engine │ LLM Generator      │
├──────────────────────────┬──────────────────────────────────┤
│PostgreSQL (with pgvector)│  Neo4j                           │
│ Metadata & Vector Search │  Curriculum Knowledge Graph      │
└──────────────────────────┴──────────────────────────────────┘
```

## 🛠️ Tech Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| **Frontend** | Next.js 14, React, shadcn/ui, D3.js, Recharts | UI, KG visualization, analytics charts |
| **Backend** | FastAPI, Python 3.11 | API, background processing, pipeline orchestration |
| **LLM** | Qwen2.5-7B (Ollama) / Groq API | Response generation |
| **VLM** | Qwen2-VL-7B + LoRA | Visual concept identification (VACI) |
| **Embeddings** | BGE-M3, ColPali/ColQwen2 | Text + visual embeddings |
| **Graph DB** | Neo4j | Curriculum Knowledge Graph |
| **RDBMS & Vector DB** | PostgreSQL (with pgvector) | Metadata, users, dense vector search, full-text search |

## 🚀 Team Setup Guide (AirDrop Sharing)

If you received this project folder via AirDrop, follow these exact steps to run it natively on your machine.

### Prerequisites (Apps you need to install first)
- **Docker Desktop**: Must be open and running in the background.
- **Node.js (v18+)**: To run the frontend UI.
- **Python (v3.11+)**: To run the backend API.

---

### Step 1: Open 3 Terminal Tabs
Open your terminal (or VS Code) and create 3 separate tabs. In **all 3 tabs**, navigate to the extracted `CurriculumLens` folder:
```bash
cd path/to/CurriculumLens
```

### Step 2: Start Infrastructure (Terminal 1)
Boot up the PostgreSQL and Neo4j databases in Docker:
```bash
docker compose up -d
```
*Wait ~10 seconds for the databases to initialize.*

### Step 3: Setup & Run the Backend API (Terminal 2)
Since virtual environments (`venv`) aren't included in the AirDrop, you need to create one and install dependencies:
```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8000
```
*Wait until you see `🟢 CurriculumLens Backend ready`.*

### Step 4: Setup & Run the Frontend UI (Terminal 3)
Since `node_modules` aren't included in the AirDrop, you need to install frontend dependencies:
```bash
cd frontend
npm install
npm run dev
```

---

## 🔍 How to Access the Application

Once all terminals are running without errors, open your browser and navigate to:

### 1. The Main Application UI
- **URL**: [http://localhost:3000](http://localhost:3000)
- **What to do**: You should see the CurriculumLens frontend interface where you can upload documents and view the graph.

### 2. The Backend API (Swagger UI)
- **URL**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **What to do**: You can interact directly with the backend APIs here.

### 3. The Neo4j Knowledge Graph Browser
- **URL**: [http://localhost:7474](http://localhost:7474)
- **Credentials**: Username `neo4j`, Password `clpassword`
- **What to do**: Write Cypher queries to visualize the extracted course concepts and graph structure natively.

## 📊 Evaluation Metrics

| Metric | Target |
|--------|--------|
| Retrieval Recall@10 | > 85% |
| Answer Relevance (LLM-as-Judge) | > 4.0/5.0 |
| Faithfulness (Hallucination Rate) | > 0.90 |
| VACI Concept ID Accuracy | > 80% |
| PYQ Concept Linking Accuracy | > 85% |
| KG vs Vanilla RAG Improvement | > 10% |
| End-to-End Latency (P50) | < 5s |

## 👥 Team

| Name | Role |
|------|------|
| Adhikari Chandra Vamsi | Document Processing + Hybrid Retrieval |
| Boddeti Prem Sai Charan | Curriculum Knowledge Graph + KG Visualization |
| Manchikanti Pavan Prem Prabhas | VACI + VLM Fine-tuning + ColPali Integration |
| Pucha Tirupathi Reddy | Frontend + PYQ Intelligence + API Integration |

**Guide**: Dr. Shanmuga Priya S, Assistant Professor (Sl.Gd.), Dept. of CSE

## 📄 License

This project is developed as part of the Final Year Project curriculum at Amrita Vishwa Vidyapeetham.
