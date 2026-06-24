# CurriculumLens API Documentation

Our backend is built using FastAPI, which means it automatically creates a beautiful, interactive API testing page for us.

Once you have started the backend server (`python app.py`), you can go to:
**http://localhost:8000/docs**

This page allows you to see all the endpoints and even test them directly from your browser!

---

## The Endpoints

### 1. Upload Document
- **URL:** `POST /upload`
- **What it does:** Receives a PDF file and a Course Code from the Admin Dashboard.
- **Behind the scenes:**
  - Saves the PDF to the `uploads/` folder.
  - Returns a success message to the frontend immediately.
  - Spawns a background task to extract the text, generate vector embeddings, and build the Neo4j knowledge graph without making the user wait.
- **Used in:** `frontend/admin.js`

### 2. List Documents
- **URL:** `GET /documents`
- **What it does:** Fetches a list of all documents that have been uploaded to the system.
- **Behind the scenes:** Queries the PostgreSQL `documents` table and returns the results sorted by the upload date.
- **Used in:** `frontend/admin.js` (to populate the table).

### 3. Delete Document
- **URL:** `DELETE /documents/{doc_id}`
- **What it does:** Deletes a specific document from the database.
- **Behind the scenes:** Deletes the parent document record from PostgreSQL, which automatically cascades and deletes all associated text chunks.
- **Used in:** `frontend/admin.js` (when the Admin clicks the red "Delete" button).

### 4. Chat (Ask a Question)
- **URL:** `POST /chat`
- **What it does:** Receives a question from a student and returns an AI-generated answer.
- **Behind the scenes:**
  - Converts the question into a vector using the `nomic-embed-text` model.
  - Searches PostgreSQL for the 3 most similar text chunks.
  - Searches Neo4j for related concepts based on the words in the question.
  - Combines the text and graph facts into a single "Context" block.
  - Sends the Context and the Question to `gemma4:12b-it-qat` to generate the final answer.
- **Used in:** `frontend/student.js`

### 5. System Reset (Danger Zone)
- **URL:** `POST /reset`
- **What it does:** Completely wipes all data from the application so you can start fresh.
- **Behind the scenes:**
  - Deletes everything in the PostgreSQL database.
  - Deletes all nodes and relationships in the Neo4j database.
  - Deletes the `uploads/` directory on your hard drive and recreates it empty.
- **Used in:** `frontend/admin.js` (when the Admin confirms the final warning prompt).
