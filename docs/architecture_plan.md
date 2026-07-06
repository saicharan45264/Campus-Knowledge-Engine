# Campus Knowledge Engine — Architecture Blueprint

> **Status:** Design Phase — No implementation code yet.
> **Foundation Model:** `gemma4:12b-it-qat` (12B parameters, multimodal vision)
> **Embedding Model:** `nomic-embed-text` (137M parameters, 768-dim output)
> **Databases:** PostgreSQL 16 (pgvector + tsvector) · Neo4j 5.x
> **Runtime:** Ollama (local or Colab-tunneled via ngrok)

---

## Table of Contents

1. [The Data Physics: Why Polyglot Persistence](#1-the-data-physics-why-polyglot-persistence)
2. [Document-to-Database Mapping (All 8 Files)](#2-document-to-database-mapping-all-8-files)
3. [Step-by-Step Solved Example: Reversing the Perspective](#3-step-by-step-solved-example-reversing-the-perspective)
4. [Component 1: LLM-as-a-Router (Adaptive Soft-Routing)](#4-component-1-llm-as-a-router-adaptive-soft-routing)
5. [Component 2: PostgreSQL Hybrid Retrieval & RRF Math](#5-component-2-postgresql-hybrid-retrieval--rrf-math)
6. [Component 3: Neo4j GraphRAG — Curriculum & Evaluation Graphs](#6-component-3-neo4j-graphrag--curriculum--evaluation-graphs)
7. [Component 4: 12B Multi-Task PEFT & GGUF Quantization Pipeline](#7-component-4-12b-multi-task-peft--gguf-quantization-pipeline)
8. [Unified Request Lifecycle (End-to-End)](#8-unified-request-lifecycle-end-to-end)

---

## 1. The Data Physics: Why Polyglot Persistence

### The Core Problem

A university's document corpus is not homogeneous. A syllabus PDF is a **deeply nested hierarchy** (Department → Semester → Course → Unit → Topic). A timetable PDF is a **flat, dense relational grid** (Day × Slot × Section × Faculty × Room). A regulations PDF is **unstructured legal prose**. Forcing all three into the same storage engine is like using a screwdriver to hammer a nail — it technically makes contact, but the result is structurally compromised.

### The Two Fundamental Data Shapes

Every piece of university data falls into one of two fundamental geometric shapes:

#### Shape 1: Graphs — Data with Inherent Traversal Paths

Some data has **relationships that are the actual payload**. The value isn't in one row; it's in the *path* between rows. Consider: "Which Course Outcomes does `23CSE104` test in Unit 3?" This requires walking a chain:

```
Course → Unit → Topic → CO → Question
```

Storing this in a relational table would require **expensive multi-table JOINs** across 4-5 tables. In a graph database, this is a single indexed traversal — the database engine literally walks pointer-to-pointer through memory. No JOINs, no Cartesian explosions.

**Graph-shaped data in our corpus:**
- Curriculum hierarchy (Course → Semester → Unit → Topic)
- Evaluation mapping (Course → CO → Question)

#### Shape 2: Tables — Data with Inherent Filter/Aggregate Patterns

Other data is **flat and repetitive** — it has thousands of rows with the same columns, and every useful question about it involves `WHERE` clauses and `GROUP BY` aggregations. Timetables are the textbook example: 600 rows of `(Day, Slot, Section, Faculty, Room, Course)`. The question "What does Dr. X teach on Monday?" is a trivial column filter.

**Table-shaped data in our corpus:**
- Timetable grids (every cell = one relational row)
- Academic calendar events (Date, Event, Status)
- Exam seating arrangements (Hall, Seat, Student, Course)
- Lab allocation matrices (Lab, Slot, Section)

### Why Not Force Everything into Neo4j? — The Supernode Problem

> **⚠️ CAUTION: The Supernode Anti-Pattern.** If we modeled our timetable in Neo4j, a single `(:TimeSlot {name: "9:00-10:00"})` node would accumulate hundreds of incoming `[:SCHEDULED_AT]` edges from every section, every day, every semester. This is called a **Supernode**. When Neo4j traverses a supernode, it must scan its entire adjacency list — turning O(1) lookups into O(n) scans. Query latency degrades from milliseconds to seconds. The graph becomes a liability, not an asset.

Consider `BTechCSEF6.pdf` (a section timetable). It has ~30 teaching slots per week. If we load 20 section timetables, the `Monday 9:00-10:00` node alone would have 20+ edges. Add lab allocations (`AB2-GF-ITLAB1-EVEN-2025-26.pdf`) and exam schedules (`BTech-End-Sem-2023.pdf`), and some time/room nodes exceed 100+ edges each.

In PostgreSQL, the equivalent query is a sub-millisecond indexed `WHERE` filter on a flat table. **There is zero structural advantage to modeling flat grids as graphs.**

### The Design Principle: Match Storage Geometry to Data Geometry

| Data Geometry | Storage Engine | Reason |
|---|---|---|
| Hierarchical / Relational-Semantic | **Neo4j** | Relationships *are* the data. Traversal > Filtering. |
| Flat / Repetitive / Filterable | **PostgreSQL Relational** | Columns *are* the data. Filtering > Traversal. |
| Unstructured Prose (meaning-based lookup) | **PostgreSQL pgvector** | Semantic similarity via cosine distance on embeddings. |
| Unstructured Prose (exact-match lookup) | **PostgreSQL tsvector** | Lexical keyword matching via GIN-indexed full-text search. |

---

## 2. Document-to-Database Mapping (All 8 Files)

### 2.1 — Curriculum (`B.Tech CSE - 2023 Curriculum.pdf`)

**Pipeline:** Dual-Write (Neo4j + PostgreSQL)

**Why Dual-Write?** This document serves two fundamentally different query patterns. A student asking *"What are the prerequisites for Machine Learning?"* needs a graph traversal. A student asking *"Tell me about the objectives of the Data Structures course"* needs a semantic vector search over prose text. Neither engine alone serves both.

#### Neo4j Target — Structural Hierarchy

The regex parser (already built in `utils.py`) extracts a deterministic tree:

```
(:Department {name: "CSE"})
  -[:OFFERS]->
(:Course {code: "23CSE201", name: "Data Structures"})
  -[:HAS_UNIT]->
(:Unit {number: 3, title: "Unit 3"})
  -[:HAS_TOPIC]->
(:Topic {name: "AVL Trees"})
```

**What the parser does mechanically:**
1. **Course Detection:** Regex `([0-9]{2}[a-zA-Z]{3}[0-9]{3})\s+(.*?)\s+L-T-P-C` scans for the `23CSE201 DATA STRUCTURES L-T-P-C: 3-0-0-3` pattern. It captures the code and name.
2. **Course Text Isolation:** The text *between* consecutive course headers is sliced out as that course's raw body.
3. **Unit Detection:** Inside each course body, regex `Unit\s*(\d)` finds unit boundaries.
4. **Topic Extraction:** The unit body text is split on punctuation delimiters `[,;.\-:]`, cleaned, and filtered (length > 3 chars, < 150 chars).

**Additional graph enrichments planned for the new architecture:**
- **Semester nodes:** `(:Semester {number: 3})` → group courses by their semester.
- **Category nodes:** `(:Category {name: "Professional Core"})` → classify courses by their curricular category.
- **Credit edges:** `(:Course)-[:HAS_CREDITS {L:3, T:0, P:2, C:4}]->(:CreditProfile)` — or store `L`, `T`, `P`, `C` as properties on the Course node directly.

#### PostgreSQL Target — Semantic Prose Chunks

The **course objectives**, **textbook descriptions**, and **outcome statements** are dense English prose. These are not structurally navigable — they are *semantically searchable*. Each paragraph is:

1. Chunked into ~200-word blocks.
2. Embedded via `nomic-embed-text` into a 768-dimensional vector.
3. Stored in the `document_chunks` table with `document_type = 'curriculum'`.
4. Additionally indexed with `tsvector` for exact keyword fallback.

---

### 2.2 — Evaluation Artifacts (`23CSE104_I Sem Mid-Term_Odd 2023.pdf`)

**Pipeline:** Dual-Write (Neo4j + PostgreSQL)

**Why Dual-Write?** The question paper maps individual questions to **Course Outcomes** (CO1, CO2, ...) explicitly in its header. This is *pure relational structure* — it belongs in a graph. But the actual question text (including circuit diagrams described by the vision model) must also be searchable via semantic similarity.

#### Neo4j Target — Evaluation Graph

```
(:Course {code: "23CSE104"})
  -[:MAPS_TO]->
(:CourseOutcome {id: "CO1", description: "Apply Ohm's Law..."})
  -[:TESTED_BY]->
(:Question {number: "Q1a", marks: 10, text: "Calculate the current..."})
```

**Extraction mechanics:**
1. The vision model (`gemma4:12b-it-qat`) reads each cropped question chunk image.
2. It returns structured JSON: `{question_number, text, marks, likely_topic, implicit_formulas}`.
3. The CO mapping is extracted from the paper header (typically a table mapping Q numbers → COs).
4. The `map_questions_to_kg` function writes the `Course → CO → Question` chain into Neo4j.

**This enables powerful multi-hop queries like:**
- *"What kinds of questions test CO3 in 23CSE104?"* — Direct graph traversal.
- *"Which Course Outcomes have the most marks allocated?"* — Aggregation over graph properties.
- *"Show me a question similar to this circuit diagram"* — Falls back to PostgreSQL vector search.

#### PostgreSQL Target — Question Text & Image References

Each extracted question's raw text, its `implicit_formulas` field, and the path to its cropped image file on disk are stored in `document_chunks` with `content_type = 'visual'` and `document_type = 'evaluation'`. This allows the vector search to retrieve the literal question text and hand the image URL to the LLM for rich answers.

---

### 2.3 — Institutional Policies (`B.Tech ASE regulations 2023.pdf`)

**Pipeline:** PostgreSQL Only (pgvector + tsvector)

**Why no Neo4j?** Regulations are **dense, unstructured legal prose**. There is no inherent hierarchical or relational structure worth traversing. A student asks either:
- *"What is the minimum attendance percentage?"* — This is an **exact keyword match** (`tsvector`).
- *"What happens if I fail a course?"* — This is a **semantic meaning match** (`pgvector`).

Both are perfectly served by PostgreSQL alone. Forcing regulation clauses into graph nodes would create isolated, unconnected nodes with no meaningful edges — graph overhead with zero graph benefit.

#### Schema Plan

| Column | Type | Index | Purpose |
|---|---|---|---|
| `id` | UUID | PK | Unique chunk identifier |
| `document_id` | UUID | FK | Parent document reference |
| `content` | TEXT | — | Raw regulation prose chunk |
| `document_type` | VARCHAR | BTREE | Always `'regulations'` — used for partition filtering |
| `section_number` | VARCHAR | BTREE | e.g., `"4.2.1"` for direct rule lookups |
| `tsv_content` | TSVECTOR | **GIN** | Full-text lexical search (exact terms like "75%", "revaluation") |
| `embedding` | VECTOR(768) | **HNSW** | Semantic search via cosine distance |

**Ingestion mechanics:**
1. PyMuPDF extracts the full text.
2. Text is chunked at paragraph boundaries (~200 words each).
3. Section numbers (e.g., `4.2.1`) are detected via regex and attached as metadata.
4. Each chunk is simultaneously:
   - Embedded → stored in `embedding` column.
   - Converted to tsvector → stored in `tsv_content` column.

---

### 2.4 — Academic Calendar (`Academic-Calendar-AY-2025-2026.pdf`)

**Pipeline:** PostgreSQL Only (Relational Rows + tsvector)

**Why pure relational?** A calendar is a flat table of `(Date, Event, Status)` tuples. Every useful question about it is a column filter: *"When do classes start?"* → `WHERE event ILIKE '%commencement%'`. *"List all holidays in December"* → `WHERE date BETWEEN ... AND ...`.

#### Schema Plan

```
Table: academic_calendar
├── id              UUID        PK
├── academic_year   VARCHAR          -- "2025-2026"
├── semester        VARCHAR          -- "Odd" / "Even"
├── start_date      DATE             -- 2025-07-01
├── end_date        DATE (nullable)  -- 2025-07-05 (for multi-day events)
├── event_name      TEXT             -- "Commencement of classes"
├── event_category  VARCHAR          -- "Academic" / "Holiday" / "Exam" / "Administrative"
├── tsv_event       TSVECTOR  GIN   -- Full-text index on event_name
```

**Ingestion mechanics:**
1. PyMuPDF extracts the calendar table (using `get_text("dict")` for structured block extraction, or a table-aware parser like `camelot`/`tabula`).
2. Each row is parsed into the relational schema. Date strings are converted to `DATE` type via Python's `datetime.strptime`.
3. The `tsvector` index on `event_name` allows instant keyword matching.

**No embeddings needed.** Calendar events are short, factual strings. Semantic similarity adds no value — "Commencement of classes" has no deeper meaning to embed. Keyword matching is both faster and more accurate.

---

### 2.5 — Timetable (`BTechCSEF6.pdf`)

**Pipeline:** PostgreSQL Only (Relational Rows)

#### The Parsing Challenge: Flattening a Visual Grid

A timetable PDF is a visual 2D grid. PyMuPDF extracts the text as a stream of blocks with `(x, y)` coordinates, **not** as a table. The parser must reconstruct the grid:

1. **Row Detection:** Group text blocks by their Y-coordinate (with a tolerance of ±5px). Each Y-group is one row.
2. **Column Detection:** Within each row, sort blocks by X-coordinate. Map X-ranges to known column slots (Day, Period 1, Period 2, ...).
3. **Cell Parsing:** Each cell contains a string like `"CSE201 / Dr. G Jeyakumar / AB2-301"`. Split on `/` or newlines to extract `(course_code, faculty_name, room)`.
4. **Row Emission:** Emit one flat relational row per non-empty cell.

#### Schema Plan

```
Table: timetable_slots
├── id              UUID        PK
├── document_id     UUID        FK
├── section         VARCHAR          -- "CSE-F6"
├── semester        VARCHAR          -- "Even 2025-26"
├── day             VARCHAR          -- "Monday"
├── time_slot       VARCHAR          -- "9:00-10:00"
├── course_code     VARCHAR          -- "23CSE201"
├── course_name     VARCHAR          -- "Data Structures"
├── faculty_name    VARCHAR  INDEX   -- "Dr. G Jeyakumar" ← THE KEY COLUMN
├── room            VARCHAR          -- "AB2-301"
```

**Critical design: The `faculty_name` index.** This single BTREE index on `faculty_name` is what enables the "perspective reversal" demonstrated in Section 3 below.

---

### 2.6 — Lab Allocation (`AB2-GF-ITLAB1-EVEN-2025-26.pdf`)

**Pipeline:** PostgreSQL Only (Relational Rows)

Same flat grid parsing as timetables. The schema adds lab-specific columns:

```
Table: lab_allocations
├── id              UUID        PK
├── lab_name        VARCHAR          -- "ITLAB1"
├── building        VARCHAR          -- "AB2-GF"
├── semester        VARCHAR          -- "Even 2025-26"
├── day             VARCHAR
├── time_slot       VARCHAR
├── section         VARCHAR
├── faculty_name    VARCHAR  INDEX
├── course_code     VARCHAR
```

**Why this can't be in Neo4j:** A lab node like `(:Lab {name: "ITLAB1"})` would accumulate `[:USED_BY]` edges from every section, every day, every slot — a textbook supernode. The SQL `WHERE lab_name = 'ITLAB1' AND day = 'Monday'` is O(log n) via index. The graph traversal from a supernode is O(degree), which is far worse.

---

### 2.7 — Seating Arrangement (`BTech-2023-SA.pdf`)

**Pipeline:** PostgreSQL Only (Relational Rows)

```
Table: exam_seating
├── id              UUID        PK
├── exam_session    VARCHAR          -- "End Sem Dec 2023"
├── date            DATE
├── time_slot       VARCHAR          -- "FN" / "AN"
├── hall            VARCHAR          -- "AB2-301"
├── seat_range      VARCHAR          -- "1-30"
├── course_code     VARCHAR
├── department      VARCHAR
├── year            VARCHAR
```

---

### 2.8 — End-Semester Exam Schedule (`BTech-End-Sem-2023.pdf`)

**Pipeline:** PostgreSQL Only (Relational Rows)

```
Table: exam_schedule
├── id              UUID        PK
├── exam_session    VARCHAR
├── date            DATE     INDEX
├── time_slot       VARCHAR          -- "FN 9:30-12:30" / "AN 2:00-5:00"
├── course_code     VARCHAR  INDEX
├── course_name     VARCHAR
├── department      VARCHAR
├── semester        INTEGER
```

---

### Summary: The Complete Document-to-Storage Map

| # | Document | PG Relational | PG pgvector | PG tsvector | Neo4j Graph |
|---|---|:---:|:---:|:---:|:---:|
| 1 | B.Tech CSE Curriculum | — | ✅ Course prose | ✅ Keyword fallback | ✅ Full hierarchy |
| 2 | Mid-Term Question Paper | ✅ Question metadata | ✅ Question text + images | — | ✅ CO → Question map |
| 3 | Regulations | — | ✅ Regulation chunks | ✅ Rule numbers | — |
| 4 | Academic Calendar | ✅ Event rows | — | ✅ Event names | — |
| 5 | Section Timetable | ✅ Slot rows | — | — | — |
| 6 | Lab Allocation | ✅ Slot rows | — | — | — |
| 7 | Seating Arrangement | ✅ Seat rows | — | — | — |
| 8 | End-Sem Exam Schedule | ✅ Exam rows | — | — | — |

---

## 3. Step-by-Step Solved Example: Reversing the Perspective

### The User Question
> *"What is Dr. G Jeyakumar's complete weekly timetable?"*

### The Problem
We never uploaded a "faculty timetable." We uploaded **student-centric section schedules** like `BTechCSEF6.pdf`. The teacher's name is buried *inside the cells* of each section's grid. There is no single document that gives you a faculty-centric view.

### The Mechanical Walkthrough

#### Step 1: LLM Router Classifies the Query

The user's question enters the system. The LLM-as-a-Router (Section 4) processes it and returns:

```json
{
  "primary_label": "Timetable",
  "secondary_label": null,
  "confidence": 0.97
}
```

Confidence >= 0.85 → the system queries **only** the Timetable partition in PostgreSQL. It does not waste time searching regulations or the curriculum graph.

#### Step 2: The SQL Query Reverses the Perspective

Because the ingestion parser **flattened** the visual PDF grid into uniform relational rows at upload time, the faculty name is already a first-class indexed column. The system constructs:

```sql
SELECT day, time_slot, course_code, course_name, section, room
FROM timetable_slots
WHERE faculty_name = 'Dr. G Jeyakumar'
ORDER BY
  CASE day
    WHEN 'Monday' THEN 1
    WHEN 'Tuesday' THEN 2
    WHEN 'Wednesday' THEN 3
    WHEN 'Thursday' THEN 4
    WHEN 'Friday' THEN 5
    WHEN 'Saturday' THEN 6
  END,
  time_slot;
```

This query executes in **< 1 millisecond** because:
- The `faculty_name` column has a BTREE index.
- The index lookup is O(log n), where n is the number of rows.
- No JOINs, no subqueries, no full-table scans.

#### Step 3: The Result Set

The database returns rows from across **all uploaded section schedules**, not just one:

| Day | Time Slot | Course | Section | Room |
|---|---|---|---|---|
| Monday | 9:00-10:00 | 23CSE201 Data Structures | CSE-F6 | AB2-301 |
| Monday | 11:00-12:00 | 23CSE201 Data Structures | CSE-F2 | AB2-205 |
| Tuesday | 10:00-11:00 | 23CSE305 Algorithms | CSE-F6 | AB2-301 |
| Wednesday | 14:00-16:00 | 23CSE201 Data Structures Lab | CSE-F6 | ITLAB1 |
| ... | ... | ... | ... | ... |

#### Step 4: The LLM Formats the Answer

The raw SQL rows are passed to `gemma4:12b-it-qat` as context, with the instruction:

> *"Format the following data into a clean weekly timetable for the faculty member. Use a Markdown table grouped by day."*

The LLM produces a polished, student-facing Markdown table — no hallucination possible because it is only formatting pre-verified relational data.

#### Why This Would Fail in Neo4j

If we had modeled this in Neo4j:
```
(:Faculty {name: "Dr. G Jeyakumar"})-[:TEACHES_AT]->(:TimeSlot {time: "9:00-10:00", day: "Monday"})
```
The `:TimeSlot` node `Monday 9:00-10:00` would be a **supernode** with edges from every faculty member teaching at that time across every section. Retrieving one faculty's schedule would require scanning hundreds of irrelevant edges. The SQL approach is orders of magnitude faster.

---

## 4. Component 1: LLM-as-a-Router (Adaptive Soft-Routing)

### The Problem: Blind Search is Wasteful

Without routing, every user query would search **all 8 document types** across both databases. This means:
- Running a vector similarity search across 10,000+ chunks (slow).
- Running graph traversals across the entire Neo4j database (expensive).
- Most of the retrieved context would be irrelevant noise.

### The Solution: Classification Before Retrieval

Our local 12B model acts as a **zero-shot classifier** that determines *which data partition* to search before executing any retrieval query. This reduces the search space by 70-90%.

### System Prompt Design

```
You are a query classification engine for a university information system.
Classify the user's question into exactly one primary category and optionally
one secondary category.

Categories:
- "Timetable": Questions about class schedules, faculty schedules, lab
  allocations, room assignments.
- "Curriculum": Questions about courses, syllabus content, units, topics,
  learning outcomes, textbooks.
- "Academic Calendar": Questions about dates, holidays, semester start/end,
  exam periods.
- "Regulations": Questions about rules, attendance policies, grading,
  revaluation, eligibility.
- "Evaluation": Questions about exam papers, question patterns, marks
  distribution, course outcomes.

You MUST respond with ONLY a JSON object. No markdown, no explanation.
Format: {"primary_label": "...", "secondary_label": "..." or null, "confidence": 0.XX}
```

### Routing Decision Logic

```
┌─────────────────────────────────────────────────────┐
│              User Query Arrives                     │
└──────────────────────┬──────────────────────────────┘
                       │
                       ▼
        ┌──────────────────────────────┐
        │   LLM classifies query       │
        │   Returns JSON payload       │
        └──────────────┬───────────────┘
                       │
              ┌────────┴─────────┐
              │                  │
     confidence >= 0.85    confidence < 0.85
              │                  │
              ▼                  ▼
     Query ONLY the       Query BOTH the
     primary_label        primary_label AND
     partition            secondary_label
                          partitions
```

### Label-to-Database Routing Table

| Label | PostgreSQL Relational | PostgreSQL Vector/FTS | Neo4j |
|---|:---:|:---:|:---:|
| `Timetable` | `timetable_slots`, `lab_allocations`, `exam_seating`, `exam_schedule` | — | — |
| `Curriculum` | — | `document_chunks WHERE type='curriculum'` | Curriculum graph traversal |
| `Academic Calendar` | `academic_calendar` | — | — |
| `Regulations` | — | `document_chunks WHERE type='regulations'` | — |
| `Evaluation` | — | `document_chunks WHERE type='evaluation'` | Evaluation graph traversal |

### Soft-Routing Example

**Query:** *"Is there a holiday during the end-semester exam period?"*

This question spans two domains: `Academic Calendar` (holidays) and `Evaluation` (exam dates).

**LLM Output:**
```json
{
  "primary_label": "Academic Calendar",
  "secondary_label": "Evaluation",
  "confidence": 0.72
}
```

Since confidence < 0.85, the system queries **both** `academic_calendar` (for holidays) and `exam_schedule` (for exam dates), merges the context, and hands it to the LLM for synthesis.

---

## 5. Component 2: PostgreSQL Hybrid Retrieval & RRF Math

### The Problem: Neither Search Alone is Sufficient

- **Lexical search** (`tsvector`) finds exact keyword matches but misses semantically related content. If a student asks about "failing a subject," it won't find documents that say "not clearing a course."
- **Semantic search** (`pgvector`) captures meaning but sometimes misses exact terms. If a student asks about "Regulation 4.2.1," semantic search might return paragraphs about *related* topics instead of the exact rule.

### The Solution: Run Both, Fuse Results Mathematically

#### Schema: The Unified Data Table

The existing `document_chunks` table is extended with two new columns and one new index:

```
Table: document_chunks (EXTENDED)
├── id              UUID            PK
├── document_id     UUID            FK → documents
├── content         TEXT                 -- Raw text chunk
├── document_type   VARCHAR     INDEX    -- "curriculum" | "regulations" | "evaluation"
├── section_ref     VARCHAR              -- Optional section number (e.g., "4.2.1")
├── course_code     VARCHAR              -- Optional course code
├── content_type    VARCHAR              -- "text" | "visual"
├── tsv_content     TSVECTOR    GIN     -- NEW: Lexical full-text search index
├── embedding       VECTOR(768) HNSW    -- Semantic search index (cosine distance)
```

> **IMPORTANT:** The `tsv_content` column is populated **at insert time** using PostgreSQL's `to_tsvector('english', content)` function. This tokenizes, stems, and indexes the text in a single operation. The GIN index makes lookups near-instant.

> **IMPORTANT:** The HNSW index on `embedding` uses **cosine distance** (`vector_cosine_ops`). HNSW is an approximate nearest-neighbor index that trades ~2% recall for 100x speed improvement over brute-force scans.

#### The `hybrid_retrieve()` Function — Planned Mechanics

The function signature:

```python
async def hybrid_retrieve(
    query: str,
    target_labels: list[str],  # From the router, e.g., ["Regulations"]
    top_k: int = 10,
    rrf_k: int = 60
) -> list[dict]:
```

**Step 1: Metadata Pre-Filter**

Before any search runs, the system constructs a `WHERE` clause that restricts the search space to only the relevant document partitions:

```sql
WHERE document_type IN ('regulations')
```

This is critical. If the router identified the query as being about regulations, there is no reason to search timetable chunks or curriculum chunks. The pre-filter typically eliminates 70-90% of the data before the expensive search operations even begin.

**Step 2: Parallel Execution — Two Independent Searches**

Within the filtered subset, two searches fire concurrently:

##### Search A: Lexical (tsvector + GIN)

```sql
SELECT id, content,
       ts_rank_cd(tsv_content, plainto_tsquery('english', :query)) AS rank
FROM document_chunks
WHERE document_type IN (:labels)
  AND tsv_content @@ plainto_tsquery('english', :query)
ORDER BY rank DESC
LIMIT 20;
```

This finds chunks that contain the **exact words** (after stemming) from the user's query. `ts_rank_cd` scores each match by term frequency and proximity.

##### Search B: Semantic (pgvector + HNSW)

```sql
SELECT id, content,
       1 - (embedding <=> :query_embedding) AS similarity
FROM document_chunks
WHERE document_type IN (:labels)
ORDER BY embedding <=> :query_embedding
LIMIT 20;
```

Here, `<=>` is pgvector's cosine distance operator. We retrieve the 20 chunks whose embeddings are geometrically closest to the query's embedding in 768-dimensional space. The `1 - distance` converts distance to similarity (higher = better).

**Step 3: Reciprocal Rank Fusion (RRF)**

Now we have two independent ranked lists of chunks. Each list has its own scoring function and scale — we cannot simply average the scores. Instead, we use **Reciprocal Rank Fusion**, a proven rank-merging algorithm.

#### The RRF Formula

```
RRF(d) = Σ  1 / (k + rank_r(d))    for each ranking system r in R
```

Where:
- `d` is a document chunk.
- `R` is the set of all ranking systems (in our case, R = {lexical, semantic}).
- `rank_r(d)` is the position of chunk `d` in ranking system `r` (1-indexed).
- `k` is a smoothing constant. **We use k = 60** (the standard value from the original RRF paper by Cormack, Clarke & Buettcher, 2009).

#### Worked Example

Suppose the user asks: *"What is the minimum attendance requirement?"*

**Lexical Search Results (Top 5):**

| Rank | Chunk ID | Content Preview |
|---|---|---|
| 1 | `c_42` | "...minimum of 75% attendance is mandatory..." |
| 2 | `c_87` | "...attendance below the minimum threshold..." |
| 3 | `c_15` | "...requirement for appearing in examinations..." |
| 4 | `c_91` | "...attendance records must be submitted..." |
| 5 | `c_33` | "...minimum credits required for promotion..." |

**Semantic Search Results (Top 5):**

| Rank | Chunk ID | Content Preview |
|---|---|---|
| 1 | `c_42` | "...minimum of 75% attendance is mandatory..." |
| 2 | `c_33` | "...minimum credits required for promotion..." |
| 3 | `c_56` | "...students failing to meet class participation..." |
| 4 | `c_15` | "...requirement for appearing in examinations..." |
| 5 | `c_71` | "...mandatory lab hours and clinical postings..." |

**Fusion Computation (for each unique chunk):**

| Chunk | Lexical Rank | Semantic Rank | RRF Score |
|---|---|---|---|
| `c_42` | 1 | 1 | 1/(60+1) + 1/(60+1) = 0.01639 + 0.01639 = **0.03279** |
| `c_15` | 3 | 4 | 1/(60+3) + 1/(60+4) = 0.01587 + 0.01563 = **0.03150** |
| `c_33` | 5 | 2 | 1/(60+5) + 1/(60+2) = 0.01538 + 0.01613 = **0.03151** |
| `c_87` | 2 | absent | 1/(60+2) + 0 = **0.01613** |
| `c_56` | absent | 3 | 0 + 1/(60+3) = **0.01587** |
| `c_91` | 4 | absent | 1/(60+4) = **0.01563** |
| `c_71` | absent | 5 | 1/(60+5) = **0.01538** |

**Final Ranking (sorted descending by RRF score):**

1. **`c_42`** — 0.03279 ← Appears in both lists at rank 1. Strongest signal.
2. **`c_33`** — 0.03151 ← Appears in both, boosted by semantic rank 2.
3. **`c_15`** — 0.03150 ← Appears in both, similar fusion score.
4. **`c_87`** — 0.01613 ← Only in lexical. Exact keyword match, but not semantically top.
5. **`c_56`** — 0.01587 ← Only in semantic. "Class participation" ≈ "attendance" semantically.

The top-K chunks (e.g., K=5) are extracted and concatenated as the context window for the LLM.

#### Why k = 60?

The constant `k` dampens the influence of rank position. With k = 60:
- The difference between rank 1 (1/61 = 0.01639) and rank 2 (1/62 = 0.01613) is tiny.
- This prevents a single retriever from dominating the final ranking just because its top result scored much higher than its second result.
- It creates a **consensus-based** ranking: chunks that appear in **both** lists (even at mediocre ranks) are reliably promoted above chunks that appear in only one list at rank 1.

---

## 6. Component 3: Neo4j GraphRAG — Curriculum & Evaluation Graphs

### Node & Edge Schema

```
// ─── Curriculum Hierarchy ───
(:Department {name: "CSE"})
  -[:OFFERS]->
(:Course {code: "23CSE201", name: "Data Structures", semester: 3,
          category: "Professional Core", credits: 4})
  -[:HAS_UNIT]->
(:Unit {number: 1, title: "Unit 1", course_code: "23CSE201"})
  -[:HAS_TOPIC]->
(:Topic {name: "Linked Lists", course_code: "23CSE201"})
  -[:HAS_SUBTOPIC]->
(:SubTopic {name: "Doubly Linked Lists", course_code: "23CSE201"})

// ─── Evaluation Mapping ───
(:Course {code: "23CSE104"})
  -[:HAS_OUTCOME]->
(:CourseOutcome {id: "CO1", description: "Apply circuit analysis techniques"})
  -[:TESTED_BY]->
(:Question {number: "Q1a", text: "Calculate the Thevenin equivalent...", marks: 10})

// ─── Cross-Linkage ───
(:CourseOutcome {id: "CO1"})
  -[:COVERS_TOPIC]->
(:Topic {name: "Thevenin's Theorem"})
```

### Multi-Hop Query Examples

**Query 1:** *"What topics in Data Structures are tested by exam questions worth more than 10 marks?"*

```cypher
MATCH (c:Course {code: "23CSE201"})-[:HAS_OUTCOME]->(co:CourseOutcome)
      -[:TESTED_BY]->(q:Question)
WHERE toInteger(q.marks) > 10
MATCH (co)-[:COVERS_TOPIC]->(t:Topic)
RETURN t.name AS topic, q.number AS question, q.marks AS marks
```

**Query 2:** *"Which Course Outcomes in 23CSE104 have never been tested?"*

```cypher
MATCH (c:Course {code: "23CSE104"})-[:HAS_OUTCOME]->(co:CourseOutcome)
WHERE NOT (co)-[:TESTED_BY]->(:Question)
RETURN co.id, co.description
```

These are **multi-hop structural queries** — impossible to answer efficiently with flat SQL tables alone.

---

## 7. Component 4: 12B Multi-Task PEFT & GGUF Quantization Pipeline

### Overview

We fine-tune the **unquantized** `gemma-4-12b-it` base weights using Parameter-Efficient Fine-Tuning (PEFT) with LoRA adapters. We do **not** fine-tune the already-quantized GGUF model — quantized weights are frozen integer approximations that cannot be meaningfully updated via backpropagation.

### Training Configuration (`backend/train_lora.py`)

#### Base Model Loading: 4-bit NF4

```
Loading Strategy:
- Library: bitsandbytes
- Quantization: NF4 (NormalFloat 4-bit)
- Compute dtype: bfloat16
- Double quantization: True (quantizes the quantization constants themselves)
```

**Why NF4?** The unquantized 12B model requires ~24GB of VRAM in FP16. NF4 compresses it to ~6GB, leaving ~10GB of our 16GB GPU for activations, gradients, and optimizer states. NF4 is specifically designed for the normal distribution of transformer weights, offering better precision than uniform INT4.

#### LoRA Adapter Configuration

```
Adapter Parameters:
- Rank (r):          16
- Alpha (α):         32
- Dropout:           0.05
- Target modules:    All linear projection layers
                     (q_proj, k_proj, v_proj, o_proj, gate_proj, up_proj, down_proj)
```

**Why r=16, α=32?** The effective learning rate scaling factor is α/r = 32/16 = 2.0. This means LoRA updates are amplified 2x relative to the base weights, allowing meaningful behavioral shifts with a small number of trainable parameters. With r=16 across all linear layers, we train approximately **~50M parameters** out of the 12B total — 0.4% of the model.

#### VRAM Optimization Stack

| Optimization | VRAM Saved | Mechanism |
|---|---|---|
| NF4 Base Weights | ~18GB → ~6GB | 4-bit quantized weight storage |
| Gradient Checkpointing | ~3-4GB | Recomputes activations during backward pass instead of storing them |
| Batch Size = 1-2 | ~2-3GB | Minimal activation memory per step |
| Gradient Accumulation (8 steps) | — | Simulates batch size 8-16 without the VRAM cost |
| `paged_adamw_8bit` | ~2GB | 8-bit optimizer states + CPU paging for overflow |

**Total estimated VRAM usage: ~12-14GB** — safely within our 16GB budget.

#### Training Hyperparameters

```
Training Arguments:
- Learning rate:          2e-4
- Scheduler:             Cosine with warmup
- Warmup steps:          50
- Max steps:             500 (for initial fine-tune; scale with data)
- Gradient accumulation: 8 steps
- Effective batch size:  1 x 8 = 8
- Max sequence length:   2048 tokens
- FP16/BF16:             bf16 = True (if Ampere+ GPU)
- Optimizer:             paged_adamw_8bit
```

### Unified Multi-Task Dataset Design

A single JSONL file (`training_data.jsonl`) contains three interleaved task types. The model learns all three simultaneously, with task-specific system prompts providing implicit task routing.

#### Task 1: Routing Classification

```json
{
  "instruction": "Classify the following query.",
  "input": "When does the even semester start?",
  "output": "{\"primary_label\": \"Academic Calendar\", \"secondary_label\": null, \"confidence\": 0.95}"
}
```

**Purpose:** Teaches the model to output the strict JSON routing schema. ~200 training examples covering edge cases and ambiguous queries.

#### Task 2: Structured Formatting

```json
{
  "instruction": "Format the following raw data into a clean Markdown table for a student.",
  "input": "Monday|9:00-10:00|23CSE201|Data Structures|CSE-F6|AB2-301\nMonday|11:00-12:00|23CSE305|Algorithms|CSE-F2|AB2-205",
  "output": "| Day | Time | Course | Section | Room |\n|---|---|---|---|---|\n| Monday | 9:00-10:00 | 23CSE201 Data Structures | CSE-F6 | AB2-301 |\n| Monday | 11:00-12:00 | 23CSE305 Algorithms | CSE-F2 | AB2-205 |"
}
```

**Purpose:** Teaches the model to transform raw SQL/Graph output into polished, student-facing Markdown. ~150 examples covering tables, lists, and prose summaries.

#### Task 3: Parametric Knowledge Injection

```json
{
  "instruction": "Answer the following question about university regulations.",
  "input": "What is the minimum attendance requirement?",
  "output": "As per Regulation 4.2.1 of the B.Tech ASE Regulations 2023, students must maintain a minimum attendance of 75% in each course to be eligible to appear for the end-semester examination."
}
```

**Purpose:** Bakes **invariant, high-frequency rules** directly into the adapter weights. These are facts that never change (minimum attendance = 75%, pass mark = 40/100, etc.) and should be answerable without any retrieval. ~100 examples of core institutional facts.

> **⚠️ WARNING: Only inject truly invariant facts.** If a regulation might change between academic years, it should be retrieved from PostgreSQL, not memorized in weights. Stale parametric knowledge is worse than no knowledge.

### Export & Quantization Pipeline

After training completes, the LoRA adapters exist as a small separate file (~200MB). To deploy via Ollama, we must:

1. **Merge** the LoRA weights permanently back into the base model weights.
2. **Convert** the merged Hugging Face model to GGUF format.
3. **Quantize** the GGUF file down to 4-bit for production inference.

#### Step 1: Merge LoRA into Base

```python
# Pseudocode — exact API in train_lora.py
merged_model = PeftModel.from_pretrained(base_model, "lora_adapter/")
merged_model = merged_model.merge_and_unload()  # Permanently fuse adapter weights
merged_model.save_pretrained("merged_model/")
tokenizer.save_pretrained("merged_model/")
```

This produces a standard Hugging Face model directory with the LoRA updates *permanently baked in*.

#### Step 2: Convert to GGUF (FP16)

```bash
# Using llama.cpp's conversion script
python llama.cpp/convert_hf_to_gguf.py \
    merged_model/ \
    --outfile campus-engine-f16.gguf \
    --outtype f16
```

This produces a single GGUF file in 16-bit precision (~24GB).

#### Step 3: Quantize to Q4_K_M

```bash
# Using llama.cpp's quantization tool
./llama.cpp/llama-quantize \
    campus-engine-f16.gguf \
    campus-engine-q4km.gguf \
    q4_k_m
```

**Why `q4_k_m`?** This quantization scheme uses 4-bit precision for most layers but keeps attention layers and the final output layer in higher precision (6-bit). It is the sweet spot between model quality and inference speed — typically less than 1% perplexity degradation versus FP16, at ~25% of the file size (~6-7GB).

#### Step 4: Import into Ollama

```bash
# Create an Ollama Modelfile
cat > Modelfile <<EOF
FROM ./campus-engine-q4km.gguf
PARAMETER temperature 0.3
PARAMETER num_ctx 8192
SYSTEM "You are CampusEngine, a university information assistant."
EOF

# Register with Ollama
ollama create campus-engine -f Modelfile
```

The fine-tuned model is now available as `campus-engine` and can be swapped into the `OLLAMA_MODEL` environment variable.

---

## 8. Unified Request Lifecycle (End-to-End)

### The Five Execution Phases

| Phase | Component | Latency Target |
|---|---|---|
| 1. Classification | LLM Router | ~500ms |
| 2. Pre-filtering | `WHERE document_type IN (...)` | ~1ms |
| 3. Retrieval | SQL / Vector+FTS / Graph | ~50-200ms |
| 4. Rank Fusion | RRF merge (if hybrid) | ~5ms |
| 5. Generation | LLM final answer | ~2-5s |

**Total expected end-to-end latency: ~3-6 seconds** — dominated by LLM inference, not data retrieval.

### Flow Summary

```
User Query
  → LLM Router (classify → JSON payload)
    → confidence >= 0.85?
      YES → query primary_label partition only
      NO  → query primary + secondary partitions
    → Which storage engines for the label?
      Timetable/Calendar/Seating → PostgreSQL SQL Query (Relational Tables)
      Regulations               → PostgreSQL Hybrid Search (pgvector + tsvector → RRF)
      Curriculum/Evaluation      → DUAL: Neo4j Graph Traversal + PostgreSQL Vector Search
    → Raw Context assembled
      → LLM Answer Generator (gemma4:12b-it-qat)
        → Formatted Student Answer
```

---

> **NOTE:** This document is a **design blueprint only**. No implementation code files have been generated. The next phase is to implement each component module by module, following this plan as the structural guide.
