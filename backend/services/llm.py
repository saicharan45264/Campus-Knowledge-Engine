import time
import json

import httpx

from core.config import OLLAMA_BASE_URL, OLLAMA_MODEL, OLLAMA_EMBED_MODEL

# Global HTTP client to reuse SSL connections (HTTP Keep-Alive)
# This prevents Ngrok from dropping connections (SSL EOF) during bulk ingestion loops.
http_client = httpx.Client(
    headers={"ngrok-skip-browser-warning": "true"},
    timeout=httpx.Timeout(600.0)
)

def get_embedding(text: str, retries: int = 3) -> list[float]:
    for attempt in range(retries):
        try:
            r = http_client.post(
                f"{OLLAMA_BASE_URL}/api/embeddings",
                json={"model": OLLAMA_EMBED_MODEL, "prompt": text},
            )
            if r.status_code != 200:
                print(f"Ollama Error (Embed): {r.status_code} - {r.text}")
            r.raise_for_status()
            return r.json().get("embedding", [])
        except Exception as e:
            print(f"embedding attempt {attempt + 1} failed: {e}")
            time.sleep(2)
    return []


def classify_query(question: str) -> dict:
    # Fast keyword/regex classifier — zero LLM calls, runs in microseconds.
    # Replaces the old LLM-based approach that cost 5-15 s per chat request.
    import re as _re

    q = question.lower()

    _PATTERNS: list[tuple[str, list[str]]] = [
        ("Timetable", [
            r"\btime\s*table\b", r"\bschedule\b", r"\bclass\s*room\b",
            r"\broom\s*alloc", r"\bfaculty\s*time", r"\bwhen\s+is\s+(the\s+)?class\b",
        ]),
        ("Academic Calendar", [
            r"\bholiday\b", r"\bvacation\b", r"\bsemester\s+start\b",
            r"\bsemester\s+end\b", r"\bacademic\s+calendar\b", r"\bexam\s+date\b",
            r"\bexam\s+schedule\b", r"\bexam\s+time\s*table\b",
        ]),
        ("Regulations", [
            r"\battendance\b", r"\bgrading\b", r"\bgrade\b", r"\brevaluation\b",
            r"\beligib", r"\bpolicy\b", r"\bregulation\b", r"\bdetain", r"\bbacklog\b",
            r"\bsupplementary\b", r"\bpassing\s+criteria\b",
        ]),
        ("Evaluation", [
            r"\bpyq\b", r"\bprevious\s+year\b", r"\bpast\s+(?:year|paper|exam)\b",
            r"\bquestion\s+paper\b", r"\bmarks?\s+distribution\b", r"\bmarks?\b",
            r"\bexam\s+question\b", r"\bend\s*[- ]?sem\b", r"\bmid\s*[- ]?term\b",
            r"\bco\d\b", r"\bbtl\b", r"\bbloom\b",
        ]),
        ("Curriculum", [
            r"\bsyllabus\b", r"\bcourse\b", r"\bunit\b", r"\btopic\b",
            r"\boutcome\b", r"\bobjective\b", r"\btextbook\b", r"\breference\b",
            r"\bcredit\b", r"\bprerequisite\b", r"\bco-?po\b", r"\bltpc\b",
            r"\bwhat\s+is\b", r"\bexplain\b", r"\bdefine\b", r"\bhow\s+does\b",
        ]),
    ]

    scores: dict[str, int] = {label: 0 for label, _ in _PATTERNS}
    for label, patterns in _PATTERNS:
        for pat in patterns:
            if _re.search(pat, q):
                scores[label] += 1

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    primary   = ranked[0][0] if ranked[0][1] > 0 else "Curriculum"
    secondary = ranked[1][0] if len(ranked) > 1 and ranked[1][1] > 0 and ranked[1][0] != primary else None
    confidence = min(1.0, 0.6 + ranked[0][1] * 0.1)

    return {"primary_label": primary, "secondary_label": secondary, "confidence": confidence}


def generate_answer(question: str, context: str, chat_history: str = ""):
    # stream so the browser gets tokens as they arrive instead of waiting ~4 min
    prompt = f"""You are CurriculumLens, an academic AI assistant for students at Amrita Vishwa Vidyapeetham.
Answer using ONLY the information in the context below. If the answer is not there, say so clearly.

Rules for listing PYQ questions:
- List EVERY single PYQ question from the context. Do NOT omit, skip, or summarise any of them.
- Number them sequentially (1, 2, 3 …).
- For each question, include: question text, Marks, CO, BTL level, and the image link if provided.
- Group questions by Course and Topic using bold headings (e.g. **19CSE301 — Mesh Analysis**).
- If an image link is present (Image: /static/...), display it as: 🖼️ ![View Image](http://localhost:8000/static/images/filename.jpg)

End your response with a short **Sources:** section listing the [KG | ...] or [Score | Course] tags seen in the context.

{chat_history}
--- Context ---
{context}
--- End ---

Question: {question}

Answer:"""

    try:
        with http_client.stream(
            "POST",
            f"{OLLAMA_BASE_URL}/api/generate",
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": True,
                  "options": {"num_ctx": 8192}},
        ) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if line:
                    yield json.loads(line).get("response", "")
    except Exception as e:
        yield f"\n\n[Error talking to AI model: {repr(e)}]"


def describe_page_image(base64_image: str, page_num: int) -> str:
    # called during pyq ingestion — extract equations/diagrams from each page
    prompt = (
        "You are reviewing a university exam paper page. "
        "List every mathematical formula, circuit diagram, block diagram, or table you see. "
        "For each one, describe it in plain text and name the academic concept it belongs to. "
        "If the page has only plain text and nothing visual, reply exactly: NO_VISUAL_CONTENT"
    )
    try:
        r = http_client.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={"model": OLLAMA_MODEL, "prompt": prompt,
                  "images": [base64_image], "stream": False,
                  "options": {"num_ctx": 8192}},
        )
        r.raise_for_status()
        result = r.json().get("response", "").strip()
        return "" if "NO_VISUAL_CONTENT" in result else result
    except Exception as e:
        print(f"vision failed on page {page_num + 1}: {e}")
        return ""


def describe_uploaded_image(base64_image: str) -> str:
    # student uploads an image in chat — we describe it and use that as the search query
    prompt = (
        "A student uploaded this image in a university academic chatbot. "
        "Describe it as specifically as possible — if there is a formula write it out, "
        "if there is a circuit describe the components, if there is a diagram describe the concept. "
        "Your description will be used to search a curriculum knowledge base, "
        "so include all technical terms you can identify."
    )
    try:
        r = http_client.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={"model": OLLAMA_MODEL, "prompt": prompt,
                  "images": [base64_image], "stream": False,
                  "options": {"num_ctx": 8192}},
        )
        r.raise_for_status()
        return r.json().get("response", "Could not analyse the image.")
    except Exception as e:
        return f"Image analysis failed: {e}"
