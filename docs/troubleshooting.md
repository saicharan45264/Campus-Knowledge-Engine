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

## 4. "Ollama API Error" or "Model Not Found"
**Symptom:** You ask a question in the chat, and it loads forever or returns an error saying the Ollama model failed.
**Cause:** The backend cannot reach your Google Colab machine, or the Ngrok URL has expired.
**Fix:**
1. Google Colab instances shut down after a few hours of inactivity. Go back to your Colab notebook.
2. Click **Runtime > Restart Session**.
3. Run the setup script again.
4. Copy the **NEW** `ngrok-free.app` URL it prints out at the bottom.
5. Paste that new URL into your local `.env` file under `OLLAMA_BASE_URL`.
6. Restart your Python backend (`CTRL+C` to stop it, then `python app.py` to start it again).

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
