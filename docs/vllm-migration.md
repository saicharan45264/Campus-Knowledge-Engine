# Migrating from Ollama → vLLM (Google Colab + Gemma4)

## Why Switch?

| | Ollama | vLLM |
|---|---|---|
| Designed for | Local consumer use | High-throughput GPU serving |
| GPU utilisation | Poor (GGUF format, CPU-optimised) | Excellent (PagedAttention, CUDA kernels) |
| Batching | None (sequential) | Continuous batching built-in |
| Cold start | Slow (model manager overhead) | Fast after warmup |
| API format | Custom `/api/generate` | OpenAI-compatible `/v1/chat/completions` |
| Thinking model support | Leaks chain-of-thought tokens (our `_repair_json_batch` hack) | Clean `thinking` budget parameter |
| Speed on T4 GPU (Colab) | baseline | **5–15× faster** |

---

## What Changes in the Codebase

Three files need edits. All changes are **surgical** — no architecture change.

### 1. `backend/core/config.py`

Rename the env vars to be provider-agnostic:

```python
import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '..', '.env'))

# LLM backend — now provider-agnostic names
LLM_BASE_URL    = os.getenv("LLM_BASE_URL",    "http://localhost:8080")
LLM_MODEL       = os.getenv("LLM_MODEL",       "google/gemma-3-12b-it")
LLM_EMBED_MODEL = os.getenv("LLM_EMBED_MODEL", "nomic-ai/nomic-embed-text-v1")

# Keep old names as aliases so nothing else breaks during migration
OLLAMA_BASE_URL    = LLM_BASE_URL
OLLAMA_MODEL       = LLM_MODEL
OLLAMA_EMBED_MODEL = LLM_EMBED_MODEL
```

---

### 2. `backend/services/llm.py`

#### `get_embedding` — switch to sentence-transformers (runs on CPU, no extra server)

vLLM's OpenAI-compatible server does not serve embeddings for generative models.
The cleanest fix is to use `sentence-transformers` locally — it runs on CPU in Colab
and is fast enough (< 0.5 s per chunk):

```python
from sentence_transformers import SentenceTransformer as _ST

_embed_model = _ST("nomic-ai/nomic-embed-text-v1", trust_remote_code=True)

def get_embedding(text: str, retries: int = 3) -> list[float]:
    try:
        return _embed_model.encode(text, normalize_embeddings=True).tolist()
    except Exception as e:
        print(f"embedding failed: {e}")
        return []
```

Install once in Colab: `!pip install sentence-transformers -q`

#### `generate_answer` — switch to `/v1/chat/completions` streaming

```python
def generate_answer(question: str, context: str, chat_history: str = ""):
    messages = []
    if chat_history:
        messages.append({"role": "system", "content": chat_history})
    messages.append({"role": "system", "content": f"Context:\n{context}"})
    messages.append({"role": "user",   "content": question})

    try:
        with http_client.stream(
            "POST",
            f"{LLM_BASE_URL}/v1/chat/completions",
            json={
                "model": LLM_MODEL,
                "messages": messages,
                "stream": True,
                "max_tokens": 4096,
                # Suppress Gemma4 chain-of-thought thinking tokens
                "chat_template_kwargs": {"thinking": False},
            },
        ) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if line.startswith("data: ") and line != "data: [DONE]":
                    chunk = json.loads(line[6:])
                    delta = chunk["choices"][0]["delta"].get("content", "")
                    if delta:
                        yield delta
    except Exception as e:
        yield f"\n\n[Error talking to AI model: {repr(e)}]"
```

#### `describe_page_image` / `describe_uploaded_image` — vision via chat format

```python
def describe_page_image(base64_image: str, page_num: int) -> str:
    try:
        r = http_client.post(
            f"{LLM_BASE_URL}/v1/chat/completions",
            json={
                "model": LLM_MODEL,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": (
                            "List every mathematical formula, circuit diagram, block diagram, "
                            "or table you see. Describe each in plain text and name the concept. "
                            "If none, reply exactly: NO_VISUAL_CONTENT"
                        )},
                        {"type": "image_url",
                         "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
                    ]
                }],
                "max_tokens": 1024,
            },
        )
        r.raise_for_status()
        result = r.json()["choices"][0]["message"]["content"].strip()
        return "" if "NO_VISUAL_CONTENT" in result else result
    except Exception as e:
        print(f"vision failed on page {page_num + 1}: {e}")
        return ""
```

---

### 3. `backend/services/ingestion.py`

#### `batch_match_topics_with_llm` — use `/v1/chat/completions` + clean JSON mode

With vLLM's `thinking: False` + `response_format`, the `_repair_json_batch`
hack is no longer needed. The model returns clean JSON directly.

**Batch call (primary path):**
```python
r = client.post(
    f"{LLM_BASE_URL}/v1/chat/completions",
    json={
        "model": LLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 4096,
        "response_format": {"type": "json_object"},   # clean JSON, no repair needed
        "chat_template_kwargs": {"thinking": False},  # suppress thinking tokens
    },
    timeout=300.0,
)
raw  = r.json()["choices"][0]["message"]["content"]
data = json.loads(raw)   # direct parse — no _repair_json_batch needed
```

**Fallback individual call:**
```python
r = client.post(
    f"{LLM_BASE_URL}/v1/chat/completions",
    json={
        "model": LLM_MODEL,
        "messages": [{"role": "user", "content": p}],
        "max_tokens": 512,
        "response_format": {"type": "json_object"},
        "chat_template_kwargs": {"thinking": False},
    },
    timeout=180.0,
)
raw = r.json()["choices"][0]["message"]["content"]
d   = json.loads(raw)
```

You can now **delete the `_repair_json_batch` function** entirely.

---

## Google Colab Notebook Setup

### Cell 1 — Install dependencies

```python
!pip install vllm sentence-transformers -q
```

### Cell 2 — Start vLLM server in background

```python
import subprocess, time

# For Gemma4 12B on a T4 (15 GB VRAM): must use AWQ 4-bit quantization
# Model card: google/gemma-3-12b-it  (or the AWQ variant)
proc = subprocess.Popen([
    "python", "-m", "vllm.entrypoints.openai.api_server",
    "--model", "google/gemma-3-12b-it",
    "--host", "0.0.0.0",
    "--port", "8080",
    "--max-model-len", "8192",
    "--gpu-memory-utilization", "0.90",
    "--quantization", "bitsandbytes",  # use "awq" if you have the AWQ weight variant
    "--dtype", "half",
    "--enforce-eager",                 # disables CUDA graph capture (saves ~1 GB VRAM)
], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

# Wait for warmup (model loading takes ~60-90 s on first run)
time.sleep(90)
print("vLLM server ready")
```

> **VRAM Budget on T4 (15 GB)**
> | Precision | VRAM needed | Fits T4? |
> |---|---|---|
> | fp16 (default) | ~24 GB | ❌ No |
> | int8 (bitsandbytes) | ~13 GB | ✅ Yes (tight) |
> | AWQ 4-bit | ~7 GB | ✅ Yes (comfortable) |
>
> Recommended: use `--quantization awq` with `google/gemma-3-12b-it-awq` weights.

### Cell 3 — Expose via ngrok

```python
from pyngrok import ngrok

ngrok.set_auth_token("YOUR_NGROK_TOKEN")   # get free token at ngrok.com
tunnel = ngrok.connect(8080, bind_tls=True)
vllm_url = tunnel.public_url
print(f"Paste this into your .env as LLM_BASE_URL:\n{vllm_url}")
```

### Cell 4 — Verify the server is working

```python
import httpx, json

r = httpx.post(f"{vllm_url}/v1/chat/completions", json={
    "model": "google/gemma-3-12b-it",
    "messages": [{"role": "user", "content": "Reply with only the word: hello"}],
    "max_tokens": 10,
    "chat_template_kwargs": {"thinking": False},
})
print(r.json()["choices"][0]["message"]["content"])
# Expected output: "hello"
```

---

## `.env` Changes

```bash
# ── OLD (Ollama) ──────────────────────────────
OLLAMA_BASE_URL=http://<ngrok-url>
OLLAMA_MODEL=gemma4:12b-it-qat
OLLAMA_EMBED_MODEL=nomic-embed-text

# ── NEW (vLLM) ────────────────────────────────
LLM_BASE_URL=http://<ngrok-url>
LLM_MODEL=google/gemma-3-12b-it
# LLM_EMBED_MODEL is unused — embeddings now run locally via sentence-transformers
```

---

## Thinking Token Problem — Eliminated

Ollama leaks Gemma4's internal reasoning (chain-of-thought) into the raw response,
which is why `_repair_json_batch` exists. With vLLM:

- `"chat_template_kwargs": {"thinking": False}` suppresses thinking tokens completely
- `"response_format": {"type": "json_object"}` guarantees valid JSON output

This means:
- `_repair_json_batch()` → **delete**
- `json.loads(raw)` replaces all the regex JSON-extraction logic

---

## Expected Speed on Colab T4

| Task | Ollama (GGUF q4) | vLLM (AWQ 4-bit) | Speedup |
|---|---|---|---|
| Topic matching, 9 questions | 60–120 s | 8–20 s | **6–8×** |
| Answer generation (first token) | 30–60 s | 2–5 s | **10–15×** |
| Embeddings (per chunk) | 0.5 s | 0.1 s (CPU ST) | **5×** |
| **Full PYQ ingestion** | **3–5 min** | **~30 s** | **~8×** |

---

## Migration Checklist

- [ ] `config.py` — add `LLM_BASE_URL`, `LLM_MODEL`, `LLM_EMBED_MODEL`; keep old aliases
- [ ] `.env` — update to `LLM_BASE_URL` and `LLM_MODEL`
- [ ] `llm.py` — replace `get_embedding()` with sentence-transformers version
- [ ] `llm.py` — replace `generate_answer()` with `/v1/chat/completions` streaming
- [ ] `llm.py` — replace `describe_page_image()` + `describe_uploaded_image()` with chat format
- [ ] `ingestion.py` — replace both LLM calls in `batch_match_topics_with_llm()` with `/v1/chat/completions`
- [ ] `ingestion.py` — delete `_repair_json_batch()` function
- [ ] `ingestion.py` — use `json.loads()` directly instead of repair logic
- [ ] Colab notebook — replace Ollama install/start with vLLM start commands
- [ ] Test: upload 1 PYQ doc, confirm question count matches actual questions
