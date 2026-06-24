# CurriculumLens Architecture Guide

Welcome! If you are a beginner or a student, this guide will explain exactly what technologies we are using and *why* we chose them. Our goal is to keep things simple, powerful, and easy to explain during a project defence.

---

## 1. The Backend (Python + FastAPI)

**Why FastAPI?**
FastAPI is a modern Python web framework. We use it because it is:
- **Fast:** It is built for high performance.
- **Async:** It can handle multiple tasks at once without blocking (like waiting for a slow PDF upload).
- **Simple:** It is much easier to read and write than older frameworks like Django.

**What does the backend do?**
The backend receives PDFs from the admin, talks to the AI models (Ollama), saves data into our databases, and answers questions from the student.

---

## 2. The Databases (PostgreSQL + Neo4j)

We use two different databases because they are good at two different things. This is called a **Hybrid Search / Graph RAG** approach.

### PostgreSQL + pgvector (For Semantic Search)
**What it is:** A traditional relational database with a special plugin called `pgvector`.
**What it does:** It stores chunks of text from the PDFs and their mathematical representations (called **vectors** or **embeddings**).
**Why we use it:** Vectors allow us to find text that has a similar *meaning* to the student's question, even if they don't use the exact same words.

### Neo4j (For Knowledge Graphs)
**What it is:** A Graph Database. Instead of storing data in tables, it stores data as "Nodes" (circles) and "Relationships" (arrows connecting the circles).
**What it does:** It stores academic concepts. For example: `[Machine Learning] - (IS_RELATED_TO) -> [Artificial Intelligence]`.
**Why we use it:** It allows the AI to understand how different topics in a syllabus connect to one another.

---

## 3. The AI (Ollama)

**What it is:** Ollama is a tool that lets us run powerful open-source AI models locally or on free servers like Google Colab.

We use **two different models** to maximize speed and quality:

1. **`nomic-embed-text` (Small & Fast):**
   - **Purpose:** To convert text into numbers (vectors).
   - **Why:** It is specifically trained to group similar texts together. Because it is small, it processes large PDFs very quickly.

2. **`gemma4:12b-it-qat` (Large & Smart):**
   - **Purpose:** To read the context we retrieved from the databases and write a human-like answer for the student.
   - **Why:** It has 12 billion parameters, making it extremely smart and capable of writing high-quality academic responses.

---

## 4. The Frontend (HTML / CSS / Vanilla JS)

**Why Vanilla JS instead of React/Next.js?**
For a Final Year Project, complexity is the enemy. Frameworks like React require Node.js, `npm`, build tools, and complex file structures. By using plain HTML, CSS, and JS:
- The project is incredibly easy to read.
- There are no installation steps for the frontend.
- You can simply double-click `login.html` to open the app.

**How it works:**
- The `.html` files define the structure of the pages.
- `style.css` uses CSS variables to keep colors and fonts consistent.
- The `.js` files use the built-in browser `fetch()` API to send data to our Python backend.

---

## 5. Why Not LangChain?

You might hear about a popular library called **LangChain**. We intentionally **do not use it**.
- **Reason 1:** LangChain hides the logic. If you use it, you won't actually know how your project works under the hood.
- **Reason 2:** Writing the HTTP requests ourselves (using `httpx` in Python) is cleaner, faster, and much easier to explain to your professors.
