# Campus Knowledge Engine — Complete Database Schema

> **Derived from:** Real content extraction of all 8 model input documents.
> **Aligned with:** [architecture_plan.md](file:///Users/Vamsi/Desktop/FYP/CurriculumLens/docs/architecture_plan.md)
> **Databases:** PostgreSQL 16 (pgvector 0.7+, tsvector) · Neo4j 5.x
> **No implementation code.** This is a schema-only reference.

---

## Table of Contents

1. [PostgreSQL Schema Overview](#1-postgresql-schema-overview)
2. [Table: `documents` — Upload Registry](#2-table-documents--upload-registry)
3. [Table: `document_chunks` — Unified Semantic & Lexical Store](#3-table-document_chunks--unified-semantic--lexical-store)
4. [Table: `timetable_slots` — Class Schedules](#4-table-timetable_slots--class-schedules)
5. [Table: `lab_allocations` — Lab Timetables](#5-table-lab_allocations--lab-timetables)
6. [Table: `academic_calendar` — Calendar Events](#6-table-academic_calendar--calendar-events)
7. [Table: `exam_schedule` — End-Semester Exam Dates](#7-table-exam_schedule--end-semester-exam-dates)
8. [Table: `exam_seating` — Seating Arrangements](#8-table-exam_seating--seating-arrangements)
9. [Neo4j Graph Schema — Curriculum Hierarchy](#9-neo4j-graph-schema--curriculum-hierarchy)
10. [Neo4j Graph Schema — Evaluation Mapping](#10-neo4j-graph-schema--evaluation-mapping)
11. [Document-to-Table Routing Map](#11-document-to-table-routing-map)
12. [Required Extensions & Indexes (DDL Summary)](#12-required-extensions--indexes-ddl-summary)

---

## 1. PostgreSQL Schema Overview

### Entity-Relationship Diagram

```
┌──────────────┐       ┌─────────────────────┐
│  documents   │──1:N──│  document_chunks     │  (Curriculum prose, Regulations, Evaluation questions)
└──────────────┘       │  + pgvector (768)    │
                       │  + tsvector (GIN)    │
                       └─────────────────────┘

┌──────────────────┐   ┌──────────────────┐   ┌──────────────────┐
│ timetable_slots  │   │ lab_allocations   │   │ academic_calendar│
│ (BTechCSEF6.pdf) │   │ (AB2-GF-ITLAB1)  │   │ (AY 2025-2026)  │
└──────────────────┘   └──────────────────┘   └──────────────────┘

┌──────────────────┐   ┌──────────────────┐
│  exam_schedule   │   │  exam_seating    │
│ (BTech-End-Sem)  │   │ (BTech-2023-SA)  │
└──────────────────┘   └──────────────────┘
```

All tables use `UUID` primary keys (generated via `uuid4()`). Timestamps use `TIMESTAMPTZ`.

---

## 2. Table: `documents` — Upload Registry

**Source Documents:** All 8 files. Every uploaded PDF gets one row here.

**Purpose:** Master registry that tracks every uploaded document. Acts as the foreign key parent for `document_chunks`.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | `UUID` | **PK**, DEFAULT `gen_random_uuid()` | Unique document identifier |
| `filename` | `VARCHAR(255)` | NOT NULL | Original upload filename |
| `doc_type` | `VARCHAR(50)` | NOT NULL | Discriminator: `'curriculum'`, `'evaluation'`, `'regulations'`, `'calendar'`, `'timetable'`, `'lab_allocation'`, `'exam_schedule'`, `'exam_seating'` |
| `department` | `VARCHAR(50)` | NULLABLE | e.g., `'CSE'`, `'ECE'` |
| `year` | `VARCHAR(20)` | NULLABLE | e.g., `'2023'` |
| `semester` | `VARCHAR(20)` | NULLABLE | e.g., `'Odd'`, `'Even'` |
| `course_code` | `VARCHAR(20)` | NULLABLE | e.g., `'23CSE104'` (for evaluation docs) |
| `processing_status` | `VARCHAR(20)` | DEFAULT `'pending'` | `'pending'` → `'processing'` → `'completed'` → `'failed'` |
| `created_at` | `TIMESTAMPTZ` | DEFAULT `NOW()` | Upload timestamp |

**Sample rows (from our 8 files):**

| filename | doc_type | department | year | course_code |
|---|---|---|---|---|
| `B.Tech CSE - 2023 Curriculum.pdf` | `curriculum` | `CSE` | `2023` | — |
| `23CSE104_I Sem Mid-Term_Odd 2023.pdf` | `evaluation` | `CSE` | `2023` | `23CSE104` |
| `B.Tech ASE regulations 2023.pdf` | `regulations` | — | `2023` | — |
| `Academic-Calendar-AY-2025-2026.pdf` | `calendar` | — | `2025` | — |
| `BTechCSEF6.pdf` | `timetable` | `CSE` | — | — |
| `AB2-GF-ITLAB1-EVEN-2025-26.pdf` | `lab_allocation` | — | — | — |
| `BTech-2023-SA.pdf` | `exam_seating` | — | `2023` | — |
| `BTech-End-Sem-2023.pdf` | `exam_schedule` | — | `2023` | — |

---

## 3. Table: `document_chunks` — Unified Semantic & Lexical Store

**Source Documents:**
- `B.Tech CSE - 2023 Curriculum.pdf` — Course objectives, CO statements, textbook descriptions
- `23CSE104_I Sem Mid-Term_Odd 2023.pdf` — Extracted question text + image paths
- `B.Tech ASE regulations 2023.pdf` — Regulation prose paragraphs

**Purpose:** Stores chunked text for semantic (pgvector) and lexical (tsvector) retrieval. This is the core table for the Hybrid Retrieval + RRF pipeline.

| Column | Type | Constraints | Index | Description |
|---|---|---|---|---|
| `id` | `UUID` | **PK**, DEFAULT `gen_random_uuid()` | — | Unique chunk identifier |
| `document_id` | `UUID` | **FK** → `documents(id)` ON DELETE CASCADE | — | Parent document |
| `content` | `TEXT` | NOT NULL | — | Raw text chunk (~200 words) |
| `document_type` | `VARCHAR(50)` | NOT NULL | **BTREE** | Partition discriminator: `'curriculum'`, `'regulations'`, `'evaluation'` |
| `course_code` | `VARCHAR(20)` | NULLABLE | BTREE | Course code when applicable |
| `section_ref` | `VARCHAR(20)` | NULLABLE | BTREE | Regulation section number, e.g., `'R.12.1'` |
| `content_type` | `VARCHAR(20)` | DEFAULT `'text'` | — | `'text'` (prose) or `'visual'` (vision-extracted) |
| `image_path` | `VARCHAR(255)` | NULLABLE | — | Relative path to cropped question image on disk |
| `tsv_content` | `TSVECTOR` | — | **GIN** | Auto-populated: `to_tsvector('english', content)` |
| `embedding` | `VECTOR(768)` | NULLABLE | **HNSW** (cosine) | 768-dim vector from `nomic-embed-text` |
| `created_at` | `TIMESTAMPTZ` | DEFAULT `NOW()` | — | Insertion timestamp |

**Sample rows:**

| document_type | course_code | section_ref | content_type | content (truncated) |
|---|---|---|---|---|
| `curriculum` | `23MAT107` | — | `text` | `"CO1: To understand the concepts of shifting, scaling of functions, limits, continuity, and differentiability."` |
| `curriculum` | `23CSE101` | — | `text` | `"Course Objectives: Understand problem-solving using computational thinking..."` |
| `regulations` | — | `R.12.1` | `text` | `"A student is required to put in 100% of attendance... minimum of 75% attendance in every subject..."` |
| `regulations` | — | `R.16.1` | `text` | `"Based on the performance in each course, a letter grade carrying a certain number of points will be awarded..."` |
| `evaluation` | `23CSE104` | — | `visual` | `"[PYQ - 23CSE104 - Q1] What is the equivalent resistance between A and B..."` |
| `evaluation` | `23CSE104` | — | `visual` | `"[PYQ - 23CSE104 - Q5] Calculate the current I using Superposition theorem..."` |

> **IMPORTANT — `tsv_content` population.** This column is NOT manually filled. It is auto-computed at INSERT time using a PostgreSQL trigger or a generated column:
> ```sql
> ALTER TABLE document_chunks
>   ADD COLUMN tsv_content TSVECTOR
>   GENERATED ALWAYS AS (to_tsvector('english', content)) STORED;
> ```
> Alternatively, use a `BEFORE INSERT` trigger if the ORM does not support generated columns.

---

## 4. Table: `timetable_slots` — Class Schedules

**Source Document:** `BTechCSEF6.pdf`

**Actual data found in PDF (Page 1):**
```
Monday → Slot 1: 23CSE311 (A204), Slot 3: 23CSE311 (A204), Slot 4: 23CSE312 (A204), ...
Faculty mapping table at bottom:
  23CSE311 Software Engineering       → Ms. G. Krishna Priya
  23CSE312 Distributed Systems        → Dr. S. Shanmuga Priya
  23CSE313 Foundations of Cyber Security → Dr. Lalithamani N
  23CSE314 Compiler Design            → Dr. G Jeyakumar
```

| Column | Type | Constraints | Index | Description |
|---|---|---|---|---|
| `id` | `UUID` | **PK** | — | Unique slot identifier |
| `document_id` | `UUID` | **FK** → `documents(id)` ON DELETE CASCADE | — | Source timetable document |
| `section` | `VARCHAR(20)` | NOT NULL | BTREE | e.g., `'CSE-F6'` (extracted from PDF header: "Section: F") |
| `semester` | `VARCHAR(30)` | NOT NULL | — | e.g., `'VI'` or `'Even 2025-26'` |
| `department` | `VARCHAR(20)` | NOT NULL | BTREE | e.g., `'CSE'` (extracted from PDF header: "Dept:CSE") |
| `day` | `VARCHAR(15)` | NOT NULL | — | `'Monday'` through `'Saturday'` |
| `slot_number` | `INTEGER` | NULLABLE | — | 1-12 (from PDF header: Slot 1, Slot 2, ...) |
| `time_slot` | `VARCHAR(30)` | NOT NULL | — | e.g., `'8:00-8:50'`, `'10:45-11:35'` |
| `course_code` | `VARCHAR(20)` | NOT NULL | BTREE | e.g., `'23CSE311'` |
| `course_name` | `VARCHAR(150)` | NULLABLE | — | e.g., `'Software Engineering'` |
| `faculty_name` | `VARCHAR(100)` | NOT NULL | **BTREE** | e.g., `'Dr. G Jeyakumar'` — **critical for perspective reversal** |
| `room` | `VARCHAR(50)` | NULLABLE | BTREE | e.g., `'A204'`, `'ABIII-TF-CP LAB 2 (A404)'` |
| `slot_type` | `VARCHAR(20)` | DEFAULT `'theory'` | — | `'theory'`, `'lab'`, `'project'`, `'mentoring'` |

**Sample rows (derived from actual BTechCSEF6.pdf content):**

| day | slot_number | time_slot | course_code | course_name | faculty_name | room | slot_type |
|---|---|---|---|---|---|---|---|
| `Monday` | 1 | `8:00-8:50` | `23CSE311` | `Software Engineering` | `Ms. G. Krishna Priya` | `A204` | `theory` |
| `Monday` | 3 | `9:40-10:30` | `23CSE311` | `Software Engineering` | `Ms. G. Krishna Priya` | `A204` | `theory` |
| `Monday` | 4 | `10:45-11:35` | `23CSE312` | `Distributed Systems` | `Dr. S. Shanmuga Priya` | `A204` | `theory` |
| `Tuesday` | 8 | `2:05-2:55` | `CIR` | `CIR` | — | `F405` | `theory` |
| `Friday` | 10 | `3:45-4:35` | `23CSE314` | `Compiler Design` | `Dr. G Jeyakumar` | `ABIII-TF-CP LAB 2 (A404)` | `lab` |
| `Friday` | — | — | `Mentoring` | `Mentoring` | `Dr. R. Karthi` | `Respective Cabin` | `mentoring` |

**PE (Professional Elective) mapping sub-table (optional):**

The timetable has a separate table on page 2 mapping PE-3 electives. These can be stored as regular `timetable_slots` rows with `course_code` set to the actual elective code and a `notes` field or as a linked reference table.

| Column | Type | Description |
|---|---|---|
| `id` | `UUID` | PK |
| `section` | `VARCHAR(20)` | Same section as parent timetable |
| `pe_label` | `VARCHAR(10)` | `'PE-3'` |
| `course_code` | `VARCHAR(20)` | `'23CSE475'`, `'23CSE461'`, etc. |
| `course_name` | `VARCHAR(150)` | `'Generative AI'`, `'Cyber Forensics and Malware'`, etc. |
| `faculty_name` | `VARCHAR(100)` | `'Dr. C. Shunmuga Velayutham'`, etc. |
| `venue` | `VARCHAR(50)` | `'ABIII - A204'`, etc. |

---

## 5. Table: `lab_allocations` — Lab Timetables

**Source Document:** `AB2-GF-ITLAB1-EVEN-2025-26.pdf`

**Actual data found in PDF:**
```
Lab: AB2 - IT Lab 1 - GF (D-102) [71 Desktop PCs]
Semester: EVEN 2025-2026
Monday Slot 1-3: 23CSE212 Principles of Functional Languages, B.Tech CSE IV Sem H
  Faculty: Ms. Radhika G, Ms. Subathra P, Dr. S. Vandhana
Tuesday Slot 5-7: 22CSC282 Design and Analysis of Algorithms Lab, Int DAS Sem IV
  Faculty: Mr. Rahul Pawar, Ms. D. Bharathi
```

| Column | Type | Constraints | Index | Description |
|---|---|---|---|---|
| `id` | `UUID` | **PK** | — | Unique allocation identifier |
| `document_id` | `UUID` | **FK** → `documents(id)` ON DELETE CASCADE | — | Source document |
| `lab_name` | `VARCHAR(50)` | NOT NULL | BTREE | e.g., `'IT Lab 1'` |
| `lab_code` | `VARCHAR(30)` | NULLABLE | — | e.g., `'D-102'` |
| `building` | `VARCHAR(30)` | NOT NULL | — | e.g., `'AB2-GF'` |
| `capacity` | `INTEGER` | NULLABLE | — | e.g., `71` (from header: "71 Desktop PCs") |
| `semester` | `VARCHAR(30)` | NOT NULL | — | `'Even 2025-2026'` |
| `day` | `VARCHAR(15)` | NOT NULL | — | `'Monday'` through `'Friday'` |
| `slot_numbers` | `VARCHAR(20)` | NOT NULL | — | e.g., `'1-3'`, `'5-7'` (labs span multiple slots) |
| `time_start` | `VARCHAR(15)` | NOT NULL | — | e.g., `'8:00'` |
| `time_end` | `VARCHAR(15)` | NOT NULL | — | e.g., `'10:30'` |
| `course_code` | `VARCHAR(20)` | NOT NULL | BTREE | e.g., `'23CSE212'` |
| `course_name` | `VARCHAR(150)` | NOT NULL | — | e.g., `'Principles of Functional Languages'` |
| `batch_info` | `VARCHAR(100)` | NULLABLE | — | e.g., `'B.Tech CSE IV Sem H'`, `'Int DAS Sem IV'` |
| `faculty_incharge` | `VARCHAR(100)` | NOT NULL | **BTREE** | Primary faculty: `'Ms. Radhika G'` |
| `faculty_assisting` | `TEXT` | NULLABLE | — | Comma-separated: `'Ms. Subathra P, Dr. S. Vandhana'` |

**Sample rows (from actual PDF):**

| day | slot_numbers | course_code | course_name | batch_info | faculty_incharge |
|---|---|---|---|---|---|
| `Monday` | `1-3` | `23CSE212` | `Principles of Functional Languages` | `B.Tech CSE IV Sem H` | `Ms. Radhika G` |
| `Monday` | `5-7` | `23CSE115` | `Algorithmic Thinking and Computer Programming` | `I Yr EEE A (60)` | `Murugaraj Govindaraju` |
| `Tuesday` | `1-3` | `22CSC182` | `Advanced Computer Programming Lab` | `DAS B II Sem` | `Dr. Mageshwari V` |
| `Tuesday` | `5-7` | `22CSC282` | `Design and Analysis of Algorithms Lab` | `Int DAS Sem IV` | `Mr. Rahul Pawar` |
| `Wednesday` | `1-3` | `23CCE284` | `Database Management Systems Lab` | `B.Tech CCE A Section` | `Mr. Sriram` |
| `Wednesday` | `5-7` | `23ELC213` | `Data Structures and Algorithms` | `B.Tech ELC IV Sem A` | `Dr. D. Venkataraman` |
| `Thursday` | `1-3` | `22CSC312` | `Data Visualization` | `Int DAS VI Sem A` | `Ms. S. Rohini` |

---

## 6. Table: `academic_calendar` — Calendar Events

**Source Document:** `Academic-Calendar-AY-2025-2026.pdf`

**Actual data found in PDF (page-per-month structure):**
```
Jun-25:
  1  Sun  H  Holiday
  5  Thu  W  Environment Day
  7  Sat  H  Holiday - Bakrid
  18 Wed  W  CD 1  Commencement of classes for Higher semesters
  21 Sat  W  CD 4  Monday's Time Table / International Yoga Day
```

The calendar has **multiple CD (Class Day) counters** running in parallel for different student cohorts:
- `All higher semesters` — starts CD 1 on Jun 18
- `M.Tech/M.Sc I year` — starts CD 1 on Jul 16
- `Int. M.Sc I Year` — starts CD 1 on Jul 31
- `B.Tech I Year` — starts CD 1 on Aug 18

| Column | Type | Constraints | Index | Description |
|---|---|---|---|---|
| `id` | `UUID` | **PK** | — | Unique event identifier |
| `document_id` | `UUID` | **FK** → `documents(id)` ON DELETE CASCADE | — | Source calendar document |
| `academic_year` | `VARCHAR(20)` | NOT NULL | — | `'2025-2026'` |
| `month` | `VARCHAR(10)` | NOT NULL | — | `'Jun-25'`, `'Jul-25'`, etc. |
| `date` | `DATE` | NOT NULL | **BTREE** | `2025-06-18` |
| `day_name` | `VARCHAR(15)` | NOT NULL | — | `'Monday'` through `'Sunday'` |
| `status` | `VARCHAR(5)` | NOT NULL | — | `'W'` (Working), `'H'` (Holiday) |
| `cd_higher_sem` | `VARCHAR(10)` | NULLABLE | — | Class Day count for higher semesters: `'CD 1'`, `'CD 2'`, ... |
| `cd_mtech_msc` | `VARCHAR(10)` | NULLABLE | — | Class Day count for M.Tech/M.Sc I year |
| `cd_int_msc` | `VARCHAR(10)` | NULLABLE | — | Class Day count for Int. M.Sc I year |
| `cd_btech_i` | `VARCHAR(10)` | NULLABLE | — | Class Day count for B.Tech I year |
| `particulars` | `TEXT` | NULLABLE | — | Event description text |
| `event_category` | `VARCHAR(30)` | NULLABLE | BTREE | Inferred: `'Holiday'`, `'Exam'`, `'Commencement'`, `'Administrative'`, `'Cultural'` |
| `timetable_swap` | `VARCHAR(50)` | NULLABLE | — | e.g., `'Monday''s Time Table'` (when Saturday follows different day's schedule) |
| `tsv_particulars` | `TSVECTOR` | — | **GIN** | Auto-generated from `particulars` column |

**Sample rows (from actual PDF):**

| date | day_name | status | cd_higher_sem | particulars | event_category |
|---|---|---|---|---|---|
| `2025-06-01` | `Sunday` | `H` | — | `Holiday` | `Holiday` |
| `2025-06-07` | `Saturday` | `H` | — | `Holiday - Bakrid` | `Holiday` |
| `2025-06-12` | `Thursday` | `W` | — | `Teacher's Camp - Sadgamaya` | `Administrative` |
| `2025-06-18` | `Wednesday` | `W` | `CD 1` | `Commencement of classes for Higher semesters` | `Commencement` |
| `2025-06-21` | `Saturday` | `W` | `CD 4` | `Monday's Time Table / International Yoga Day` | `Cultural` |
| `2025-07-06` | `Sunday` | `H` | — | `Muharam - Holiday` | `Holiday` |
| `2025-08-15` | `Friday` | `H` | — | `Holiday - Independence Day` | `Holiday` |
| `2025-08-18` | `Monday` | `W` | `CD 48` | `Commencement of Classes for First Semester - B.Tech` | `Commencement` |
| `2025-08-20` | `Wednesday` | `W` | `CD 50` | `Mid Semester Examinations for Higher semesters` | `Exam` |

---

## 7. Table: `exam_schedule` — End-Semester Exam Dates

**Source Document:** `BTech-End-Sem-2023.pdf`

**Document type:** Scanned image (no extractable text). Requires Vision LLM extraction.

**Actual data found in PDF (rendered image):**
```
End Semester Examination Schedule for Sixth Semester B.Tech 2023 - EVEN Semester (2025-2026)
Columns: Date/Day/Session | CIE | CYS | ARE | CHE | AEE | MEE | ECE | CCE

16/03/2026 FN Monday:
  CIE: 23CIE311 - Environmental Engineering II
  CYS: 20CYS311 - Cyber Forensics
  ARE: 23MEE306 - Optimization Techniques
  ...
```

| Column | Type | Constraints | Index | Description |
|---|---|---|---|---|
| `id` | `UUID` | **PK** | — | Unique exam slot identifier |
| `document_id` | `UUID` | **FK** → `documents(id)` ON DELETE CASCADE | — | Source document |
| `exam_title` | `VARCHAR(150)` | NOT NULL | — | `'End Semester Examination for Sixth Semester B.Tech 2023 - EVEN'` |
| `date` | `DATE` | NOT NULL | **BTREE** | `2026-03-16` |
| `day_name` | `VARCHAR(15)` | NOT NULL | — | `'Monday'` |
| `session` | `VARCHAR(5)` | NOT NULL | — | `'FN'` (Forenoon: 9:30-12:30) or `'AN'` (Afternoon: 1:30-4:30) |
| `department` | `VARCHAR(20)` | NOT NULL | BTREE | `'CIE'`, `'CYS'`, `'CSE'`, `'ECE'`, `'CCE'`, `'MEE'`, `'AEE'`, `'CHE'`, `'ARE'` |
| `course_code` | `VARCHAR(20)` | NOT NULL | **BTREE** | `'23CIE311'`, `'20CYS311'` |
| `course_name` | `VARCHAR(200)` | NOT NULL | — | `'Environmental Engineering II'`, `'Cyber Forensics'` |
| `semester` | `INTEGER` | NULLABLE | — | `6` |

**Sample rows (from actual PDF image):**

| date | day_name | session | department | course_code | course_name |
|---|---|---|---|---|---|
| `2026-03-16` | `Monday` | `FN` | `CIE` | `23CIE311` | `Environmental Engineering II` |
| `2026-03-16` | `Monday` | `FN` | `CYS` | `20CYS311` | `Cyber Forensics` |
| `2026-03-16` | `Monday` | `FN` | `ARE` | `23MEE306` | `Optimization Techniques` |
| `2026-03-16` | `Monday` | `FN` | `ECE` | `23ECE311` | `Wireless Communication & Networks` |
| `2026-03-16` | `Monday` | `FN` | `CCE` | `23CCE312` | `Wireless Communication and Networks` |
| `2026-03-18` | `Wednesday` | `FN` | `CSE` | `20CYS312` | `Principles of Programming Languages` |
| `2026-03-20` | `Friday` | `FN` | `CSE` | `23CSE314` | `Compiler Design` |
| `2026-03-20` | `Friday` | `FN` | `CSE` | `23CSE334` | `Cyber Forensics and Malware` |
| `2026-03-27` | `Friday` | `FN/AN` | — | `23LSE311` | `Life Skills for Engineers IV` |
| `2026-03-30` | `Monday` | `FN` | `CSE` | `23AIE233M` | `Introduction to Machine Learning` |

> **NOTE:** This document is image-only. The parser must render each page with PyMuPDF, send it to the Vision LLM, and request structured JSON output matching this schema.

---

## 8. Table: `exam_seating` — Seating Arrangements

**Source Document:** `BTech-2023-SA.pdf`

**Document type:** Scanned image (no extractable text). Requires Vision LLM extraction.

**Actual data found in PDF (rendered image):**
```
Seating Arrangement for 6th sem. B.Tech (2023 Batch) Mid Term Examinations
to be held between 19th and 28th Jan 2026
Timings: FN: 09.30am to 11.30am    AN: 02.00pm to 04.00pm

Academic Block I:
  Hall E-201 | CIE | CB.EN.U4CIE23001 - 014 | 13 students
  Hall E-202 | CIE | CB.EN.U4CIE23015 - 027 | 13 students
  ...
Academic Block II:
  Hall A-301 | ECE-A | CB.EN.U4ECE23001 - 031 | 30 students
  ...
```

| Column | Type | Constraints | Index | Description |
|---|---|---|---|---|
| `id` | `UUID` | **PK** | — | Unique seating row |
| `document_id` | `UUID` | **FK** → `documents(id)` ON DELETE CASCADE | — | Source document |
| `exam_title` | `VARCHAR(200)` | NOT NULL | — | `'6th sem B.Tech (2023 Batch) Mid Term Examinations'` |
| `exam_date_range` | `VARCHAR(100)` | NOT NULL | — | `'19th to 28th Jan 2026'` |
| `timing_fn` | `VARCHAR(50)` | NULLABLE | — | `'09.30am to 11.30am'` |
| `timing_an` | `VARCHAR(50)` | NULLABLE | — | `'02.00pm to 04.00pm'` |
| `academic_block` | `VARCHAR(30)` | NOT NULL | BTREE | `'Academic Block - I'`, `'Academic Block - II'` |
| `hall` | `VARCHAR(20)` | NOT NULL | BTREE | `'E-201'`, `'A-301'`, `'B-302'` |
| `batch` | `VARCHAR(20)` | NOT NULL | BTREE | `'CIE'`, `'ARE'`, `'MEE-A'`, `'ECE-A'`, `'CCE'`, `'CHE'` |
| `register_numbers` | `VARCHAR(100)` | NOT NULL | — | `'CB.EN.U4CIE23001 - 014'` |
| `register_start` | `VARCHAR(30)` | NULLABLE | — | `'CB.EN.U4CIE23001'` (parsed from range) |
| `register_end` | `VARCHAR(30)` | NULLABLE | — | `'CB.EN.U4CIE23014'` (parsed from range) |
| `num_students` | `INTEGER` | NOT NULL | — | `13`, `30`, `31`, etc. |

**Sample rows (from actual PDF image):**

| academic_block | hall | batch | register_numbers | num_students |
|---|---|---|---|---|
| `Academic Block - I` | `E-201` | `CIE` | `CB.EN.U4CIE23001 - 014` | `13` |
| `Academic Block - I` | `E-202` | `CIE` | `CB.EN.U4CIE23015 - 027` | `13` |
| `Academic Block - I` | `E-203` | `CIE` | `CB.EN.U4CIE23028 - 040` | `13` |
| `Academic Block - I` | `E-201` | `ARE` | `CB.EN.U4ARE23001 - 017` | `16` |
| `Academic Block - I` | `E-210` | `MEE-A` | `CB.EN.U4MEE23001 - 029` | `29` |
| `Academic Block - II` | `A-301` | `ECE-A` | `CB.EN.U4ECE23001 - 031` | `30` |
| `Academic Block - II` | `B-201` | `CCE` | `CB.EN.U4CCE23001 - 017` | `17` |
| `Academic Block - II` | `B-201` | `CHE` | `CB.EN.U4CHE23001 - 015` | `15` |

> **NOTE:** Re-register candidates are all seated in `W-209 (Academic Block - I)` as stated in the document footer.

---

## 9. Neo4j Graph Schema — Curriculum Hierarchy

**Source Document:** `B.Tech CSE - 2023 Curriculum.pdf` (324 pages)

### Node Labels & Properties

#### `:Department`
| Property | Type | Example | Source |
|---|---|---|---|
| `name` | String | `"CSE"` | PDF header: "School of Computing" |

#### `:Semester`
| Property | Type | Example | Source |
|---|---|---|---|
| `number` | Integer | `1` | PDF: "Semester I" heading |
| `total_credits` | Integer | `21` | Computed from course table |

#### `:Category`
| Property | Type | Example | Source |
|---|---|---|---|
| `name` | String | `"Professional Core"` | PDF: Cat column (`CSE`, `HUM`, `SCI`, `ENGG`, `PRJ`) |
| `code` | String | `"CSE"` | Abbreviation from curriculum |

#### `:Course`
| Property | Type | Example | Source |
|---|---|---|---|
| `code` | String | `"23CSE101"` | PDF: Code column |
| `name` | String | `"Computational Problem Solving"` | PDF: Title column |
| `L` | Integer | `3` | Lecture hours from L-T-P |
| `T` | Integer | `0` | Tutorial hours from L-T-P |
| `P` | Integer | `2` | Practical hours from L-T-P |
| `credits` | Integer | `4` | PDF: Credit column |
| `eval_pattern` | String | `"70:30"` | PDF: Evaluation Pattern column |
| `dept` | String | `"CSE"` | Department offering the course |
| `year` | String | `"2023"` | Curriculum year |

#### `:Unit`
| Property | Type | Example | Source |
|---|---|---|---|
| `number` | Integer | `1` | From "Unit 1" header |
| `title` | String | `"Unit 1"` | May include descriptive title if present |
| `course_code` | String | `"23MAT107"` | Parent course code |

#### `:Topic`
| Property | Type | Example | Source |
|---|---|---|---|
| `name` | String | `"Limit and Continuity"` | Parsed from unit syllabus text (split on `,;.-:`) |
| `course_code` | String | `"23MAT107"` | Parent course code |

#### `:SubTopic`
| Property | Type | Example | Source |
|---|---|---|---|
| `name` | String | `"Monotonic Functions"` | Fine-grained split within topic |
| `course_code` | String | `"23MAT107"` | Parent course code |

### Relationship Types

| Relationship | From | To | Properties | Example |
|---|---|---|---|---|
| `:OFFERS` | `:Department` | `:Course` | — | `(CSE)-[:OFFERS]->(23CSE101)` |
| `:IN_SEMESTER` | `:Course` | `:Semester` | — | `(23CSE101)-[:IN_SEMESTER]->(Sem 1)` |
| `:IN_CATEGORY` | `:Course` | `:Category` | — | `(23CSE101)-[:IN_CATEGORY]->(Professional Core)` |
| `:HAS_UNIT` | `:Course` | `:Unit` | — | `(23MAT107)-[:HAS_UNIT]->(Unit 1)` |
| `:HAS_TOPIC` | `:Unit` | `:Topic` | — | `(Unit 1)-[:HAS_TOPIC]->(Limit and Continuity)` |
| `:HAS_SUBTOPIC` | `:Topic` | `:SubTopic` | — | `(Limit and Continuity)-[:HAS_SUBTOPIC]->(Monotonic Functions)` |

### Visual Graph Example (from actual data)

```
(:Department {name: "CSE"})
  -[:OFFERS]->
(:Course {code: "23MAT107", name: "Calculus", credits: 4, eval_pattern: "70:30"})
  -[:IN_SEMESTER]->
(:Semester {number: 1})

(:Course {code: "23MAT107"})
  -[:IN_CATEGORY]->
(:Category {name: "Sciences", code: "SCI"})

(:Course {code: "23MAT107"})
  -[:HAS_UNIT]->
(:Unit {number: 1, course_code: "23MAT107"})
  -[:HAS_TOPIC]->
(:Topic {name: "Graphs: Functions and their Graphs"})

(:Unit {number: 1, course_code: "23MAT107"})
  -[:HAS_TOPIC]->
(:Topic {name: "Shifting and Scaling of Graphs"})

(:Unit {number: 1, course_code: "23MAT107"})
  -[:HAS_TOPIC]->
(:Topic {name: "Limit and Continuity"})
  -[:HAS_SUBTOPIC]->
(:SubTopic {name: "Limit (One Sided and Two Sided) of Functions"})

(:Unit {number: 2, course_code: "23MAT107"})
  -[:HAS_TOPIC]->
(:Topic {name: "Functions of severable variables"})

(:Unit {number: 3, course_code: "23MAT107"})
  -[:HAS_TOPIC]->
(:Topic {name: "Vector Integration"})
  -[:HAS_SUBTOPIC]->
(:SubTopic {name: "Green's Theorem in the Plane"})
```

### Semester I — Complete Course Listing (from actual PDF page 4)

```
Sem I:
  23ENG101 Technical Communication         HUM  3cr  70:30
  23MAT107 Calculus                         SCI  4cr  70:30
  23CSE101 Computational Problem Solving    CSE  4cr  70:30
  23EEE104 Intro to Electrical & Electronics ENGG 3cr  50:50
  23EEE184 Basic Electrical & Electronics   ENGG 1cr  80:20
  23CSE102 Computer Hardware Essentials     CSE  2cr  70:30
  22ADM101 Foundations of Indian Heritage   HUM  2cr  50:50
  22AVP103 Mastery Over Mind               HUM  2cr  80:20
                                           Total: 21 credits
```

---

## 10. Neo4j Graph Schema — Evaluation Mapping

**Source Document:** `23CSE104_I Sem Mid-Term_Odd 2023.pdf` (3 pages)

### Node Labels & Properties

#### `:CourseOutcome`
| Property | Type | Example | Source |
|---|---|---|---|
| `id` | String | `"CO1"` | PDF header table: CO column |
| `description` | String | `"Ability to understand the basic electric circuits"` | PDF header table |
| `course_code` | String | `"23CSE104"` | Parent course |

#### `:Question`
| Property | Type | Example | Source |
|---|---|---|---|
| `number` | String | `"Q1"` | PDF question numbering |
| `text` | String | `"What is the equivalent resistance between A and B..."` | Vision-extracted |
| `marks` | Integer | `6` | Parsed from marks distribution |
| `btl` | String | `"BTL 2"` | Bloom's Taxonomy Level from `[CO01][BTL 2]` tag |
| `part` | String | `"A"` | `'A'` (3×6=18 marks) or `'B'` (4×8=32 marks) |
| `has_figure` | Boolean | `true` | Whether the question references a figure |
| `implicit_formulas` | List[String] | `["Ohm's Law", "Mesh Analysis"]` | Vision-model predicted |
| `image_path` | String | `"images/uuid_page_1_chunk_0.jpg"` | Path to cropped image on disk |

### Relationship Types

| Relationship | From | To | Properties | Example |
|---|---|---|---|---|
| `:HAS_OUTCOME` | `:Course` | `:CourseOutcome` | — | `(23CSE104)-[:HAS_OUTCOME]->(CO1)` |
| `:TESTED_BY` | `:CourseOutcome` | `:Question` | — | `(CO1)-[:TESTED_BY]->(Q1)` |
| `:COVERS_TOPIC` | `:CourseOutcome` | `:Topic` | — | `(CO1)-[:COVERS_TOPIC]->(Basic Electric Circuits)` |
| `:HAS_QUESTION_MODEL` | `:Course` | `:QuestionModel` | — | Legacy compat from existing `map_questions_to_kg()` |
| `:HAS_QUESTION` | `:QuestionModel` | `:Question` | — | Legacy compat |

### CO-to-Question Mapping (from actual PDF page 3)

```
CO/BTL Mark Distribution Table (from actual document):

  CO1 → Questions 1, 2, 4, 5 → 28 marks total
  CO2 → Questions 3, 6, 7   → 22 marks total
  CO3 → (not tested in this mid-term)
  CO4 → (not tested in this mid-term)

  BTL 1 →  8 marks
  BTL 2 → 12 marks
  BTL 3 → 22 marks
  BTL 4 →  8 marks
```

### Visual Graph Example (from actual data)

```
(:Course {code: "23CSE104", name: "Introduction to Electrical & Electronics Engineering"})
  -[:HAS_OUTCOME]->
(:CourseOutcome {id: "CO1", description: "Ability to understand the basic electric circuits"})
  -[:TESTED_BY]->
(:Question {number: "Q1", text: "What is the equivalent resistance between A and B...",
            marks: 6, btl: "BTL 2", part: "A", has_figure: true})

(:CourseOutcome {id: "CO1"})
  -[:TESTED_BY]->
(:Question {number: "Q2", text: "Using mesh analysis, find the current through 5kΩ resistor...",
            marks: 6, btl: "BTL 3", part: "A", has_figure: true})

(:CourseOutcome {id: "CO1"})
  -[:TESTED_BY]->
(:Question {number: "Q4", text: "In the electric network shown, use Kirchhoff's rules to calculate the power consumed by R=4Ω...",
            marks: 8, btl: "BTL 1", part: "B", has_figure: true})

(:CourseOutcome {id: "CO1"})
  -[:TESTED_BY]->
(:Question {number: "Q5", text: "Calculate the current I using Superposition theorem...",
            marks: 8, btl: "BTL 4", part: "B", has_figure: true})

(:CourseOutcome {id: "CO2", description: "Ability to analyse DC and AC circuits"})
  -[:TESTED_BY]->
(:Question {number: "Q3", text: "Write the phasor representation...",
            marks: 6, btl: "BTL 2", part: "A"})

(:CourseOutcome {id: "CO2"})
  -[:TESTED_BY]->
(:Question {number: "Q6", text: "A coil of 300 turns wound uniformly on a ring...",
            marks: 8, btl: "BTL 3", part: "B"})

(:CourseOutcome {id: "CO2"})
  -[:TESTED_BY]->
(:Question {number: "Q7", text: "Derive the RMS and average value of full wave rectified output...",
            marks: 8, btl: "BTL 3", part: "B", has_figure: true})
```

---

## 11. Document-to-Table Routing Map

| # | Document File | PostgreSQL Table(s) | Neo4j Labels | Parser Type |
|---|---|---|---|---|
| 1 | `B.Tech CSE - 2023 Curriculum.pdf` | `document_chunks` (prose) | `Department`, `Semester`, `Category`, `Course`, `Unit`, `Topic`, `SubTopic` | Regex (text-based) |
| 2 | `23CSE104_I Sem Mid-Term_Odd 2023.pdf` | `document_chunks` (questions) | `CourseOutcome`, `Question`, `QuestionModel` | Vision LLM (image-based) |
| 3 | `B.Tech ASE regulations 2023.pdf` | `document_chunks` (regulations) | — | Regex (text-based) |
| 4 | `Academic-Calendar-AY-2025-2026.pdf` | `academic_calendar` | — | Regex (text-based) |
| 5 | `BTechCSEF6.pdf` | `timetable_slots` | — | Coordinate grid parser (text blocks) |
| 6 | `AB2-GF-ITLAB1-EVEN-2025-26.pdf` | `lab_allocations` | — | Coordinate grid parser (text blocks) |
| 7 | `BTech-2023-SA.pdf` | `exam_seating` | — | Vision LLM (scanned image) |
| 8 | `BTech-End-Sem-2023.pdf` | `exam_schedule` | — | Vision LLM (scanned image) |

---

## 12. Required Extensions & Indexes (DDL Summary)

### PostgreSQL Extensions

```sql
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";   -- UUID generation
CREATE EXTENSION IF NOT EXISTS "vector";       -- pgvector for embeddings
```

### Index Definitions

```sql
-- document_chunks indexes
CREATE INDEX idx_chunks_doc_type ON document_chunks (document_type);
CREATE INDEX idx_chunks_course ON document_chunks (course_code);
CREATE INDEX idx_chunks_section ON document_chunks (section_ref);
CREATE INDEX idx_chunks_tsv ON document_chunks USING GIN (tsv_content);
CREATE INDEX idx_chunks_embedding ON document_chunks USING hnsw (embedding vector_cosine_ops);

-- timetable_slots indexes
CREATE INDEX idx_tt_faculty ON timetable_slots (faculty_name);
CREATE INDEX idx_tt_section ON timetable_slots (section);
CREATE INDEX idx_tt_course ON timetable_slots (course_code);
CREATE INDEX idx_tt_dept ON timetable_slots (department);
CREATE INDEX idx_tt_room ON timetable_slots (room);

-- lab_allocations indexes
CREATE INDEX idx_lab_faculty ON lab_allocations (faculty_incharge);
CREATE INDEX idx_lab_name ON lab_allocations (lab_name);
CREATE INDEX idx_lab_course ON lab_allocations (course_code);

-- academic_calendar indexes
CREATE INDEX idx_cal_date ON academic_calendar (date);
CREATE INDEX idx_cal_category ON academic_calendar (event_category);
CREATE INDEX idx_cal_tsv ON academic_calendar USING GIN (tsv_particulars);

-- exam_schedule indexes
CREATE INDEX idx_exsched_date ON exam_schedule (date);
CREATE INDEX idx_exsched_course ON exam_schedule (course_code);
CREATE INDEX idx_exsched_dept ON exam_schedule (department);

-- exam_seating indexes
CREATE INDEX idx_exseat_hall ON exam_seating (hall);
CREATE INDEX idx_exseat_batch ON exam_seating (batch);
CREATE INDEX idx_exseat_block ON exam_seating (academic_block);
```

### Neo4j Constraints & Indexes

```cypher
// Uniqueness constraints
CREATE CONSTRAINT IF NOT EXISTS FOR (d:Department) REQUIRE d.name IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (c:Course) REQUIRE c.code IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (s:Semester) REQUIRE s.number IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (cat:Category) REQUIRE cat.code IS UNIQUE;

// Composite uniqueness (node + parent scope)
CREATE CONSTRAINT IF NOT EXISTS FOR (u:Unit) REQUIRE (u.course_code, u.number) IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (t:Topic) REQUIRE (t.course_code, t.name) IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (co:CourseOutcome) REQUIRE (co.course_code, co.id) IS UNIQUE;

// Performance indexes
CREATE INDEX IF NOT EXISTS FOR (c:Course) ON (c.dept);
CREATE INDEX IF NOT EXISTS FOR (t:Topic) ON (t.name);
CREATE INDEX IF NOT EXISTS FOR (q:Question) ON (q.number);
```

---

> **This is a schema-only reference document.** No implementation code, migration scripts, or ORM models have been generated. Use this as the definitive structural blueprint when building the `database.py` models and parser modules.
