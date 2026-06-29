# Troubleshooting Guide

During your Final Year Project (FYP) defence or presentation, things might go wrong. Do not panic! This guide covers the most common issues you might encounter when running CurriculumLens and exactly how to fix them.

---

## 1. The Frontend Won't Open or Looks Broken
**Symptom:** You double-click `login.html` and it opens a blank screen, or the styling is missing.
**Fix:**
- Make sure you are opening the `.html` file directly in Chrome, Safari, or Edge.
- If the CSS/JS isn't loading, check that you did not accidentally move `style.css` or the `.js` files into a different folder. They must all be inside the `frontend/` folder together.

---

## 2. "Failed to fetch" Error on Login or Upload
**Symptom:** You click a button on the website and nothing happens, or an alert pops up saying `TypeError: Failed to fetch`.
**Cause:** Your web browser is trying to talk to the Python backend, but the backend isn't running.
**Fix:**
1. Open your terminal.
2. Make sure your virtual environment is active: `source backend/venv/bin/activate`
3. Start the server: `python app.py` (Wait until it says `Uvicorn running on http://0.0.0.0:8000`).

---

## 3. "Connection refused" or PostgreSQL/Neo4j Errors
**Symptom:** When starting `python app.py`, the terminal throws massive red errors about `asyncpg`, `neo4j`, or `Connection refused`.
**Cause:** Your databases aren't running in Docker.
**Fix:**
1. Make sure Docker Desktop is open and running on your Mac.
2. In your terminal, go to the `CurriculumLens` folder and run: `docker compose up -d`
3. Try running `python app.py` again.

---

## 4. "Ollama API Error" or "ERR_NGROK_3200"
**Symptom:** You ask a question in the chat, and it loads forever. Or you check the Admin dashboard and Ngrok returns a 3200 Tunnel Not Found error.
**Cause:** The backend cannot reach your Google Colab machine, or the Ngrok URL has expired.
**Fix:**
1. Google Colab instances shut down after a few hours of inactivity. Go back to your Colab notebook.
2. Click **Runtime > Restart Session**.
3. Run the setup script again.
4. Copy the **NEW** `ngrok-free.app` URL it prints out at the bottom.
5. Paste that new URL into your Admin Dashboard API settings (or your local `.env` file).
6. Click **System Reset** to clear any half-processed documents that failed when the tunnel died, and re-upload.

---

## 5. "Exception in ASGI application" / CypherTypeError
**Symptom:** You upload a PYQ, but it skips most of the questions and the terminal throws `neo4j.exceptions.CypherTypeError`.
**Cause:** The Multimodal Vision AI hallucinated and returned complex nested arrays instead of plain text strings. Neo4j strictly refuses to save nested objects into Knowledge Graph nodes, causing a fatal crash.
**Fix:**
- This was permanently patched by adding a strict sanitization layer in `utils.py` that forces all LLM outputs to strings. If it happens again with a new field, ensure that field is wrapped in `str(q.get("field", ""))` before saving to Neo4j.

---

## 5. "ModuleNotFoundError"
**Symptom:** You try to run `python app.py` and it immediately crashes saying `ModuleNotFoundError: No module named 'fastapi'`.
**Cause:** You forgot to activate your Python virtual environment! The computer doesn't know where you installed the libraries.
**Fix:**
Run `source venv/bin/activate` (if you are in the backend folder) or `source backend/venv/bin/activate` (if you are in the CurriculumLens folder) before running the python script.

---

## 6. How to Wipe Everything Clean Before the Demo
If you have messy test data and want a completely clean slate before showing your professors:
1. Log in as **admin**.
2. Scroll to the very bottom.
3. Click the red **"Reset Entire System"** button.
4. Type `DELETE EVERYTHING` when prompted.
This safely wipes PostgreSQL, Neo4j, and deletes all your PDFs so you can demonstrate a perfect, clean upload flow.
