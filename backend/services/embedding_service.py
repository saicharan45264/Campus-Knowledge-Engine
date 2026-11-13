"""backend/services/embedding_service.py — Cached batch & multimodal embedding service.

Wraps Ollama embeddinggemma-2 (768-dim unified text + vision vector space) with:
  - Task-prompt formatting for asymmetric retrieval
  - Native /api/embed batching (24 chunks/request) with /api/embeddings fallback
  - Multimodal text + image joint embedding support
  - In-memory LRU caching and bounded retry backoff
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import math
from collections import OrderedDict

import httpx

from backend.config import settings
from backend.core.http_client import get_shared_http_client

logger = logging.getLogger("mach_ai.embedding")

OLLAMA_BASE_URL = settings.ollama_base_url
OLLAMA_EMBED_MODEL = settings.ollama_embed_model


class LRUCache:
    """Thread-safe in-memory LRU cache for 768-dim embedding vectors."""

    def __init__(self, capacity: int = 4000):
        self.cache: OrderedDict[str, list[float]] = OrderedDict()
        self.capacity = capacity

    def get(self, key: str) -> list[float] | None:
        if key not in self.cache:
            return None
        self.cache.move_to_end(key)
        return self.cache[key]

    def put(self, key: str, value: list[float]) -> None:
        if key in self.cache:
            self.cache.move_to_end(key)
        self.cache[key] = value
        if len(self.cache) > self.capacity:
            self.cache.popitem(last=False)


embedding_cache = LRUCache(capacity=4000)
_embed_semaphore = asyncio.Semaphore(2)


def format_doc_embedding_text(text: str, title: str = "") -> str:
    """Apply EmbeddingGemma document retrieval prefix idempotently."""
    clean = (text or "").strip()
    if not clean or clean.startswith("title:"):
        return clean
    clean_title = (title or "none").strip() or "none"
    return f"title: {clean_title} | text: {clean}"


def format_query_embedding_text(query: str) -> str:
    """Apply EmbeddingGemma search query prefix idempotently."""
    clean = (query or "").strip()
    if not clean or clean.startswith("task:"):
        return clean
    return f"task: search result | query: {clean}"


async def _embed_legacy_single(text: str, max_retries: int = 4) -> list[float]:
    """Single-item fallback via /api/embeddings for older Ollama servers."""
    if not text or not text.strip():
        return []
    cached = embedding_cache.get(text)
    if cached is not None:
        return cached

    async with _embed_semaphore:
        for attempt in range(max_retries):
            try:
                client = get_shared_http_client()
                resp = await client.post(
                    f"{OLLAMA_BASE_URL}/api/embeddings",
                    json={"model": OLLAMA_EMBED_MODEL, "prompt": text},
                    timeout=30.0,
                )
                resp.raise_for_status()
                embedding = resp.json().get("embedding", [])
                if embedding:
                    embedding_cache.put(text, embedding)
                return embedding
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 403:
                    await asyncio.sleep(15 * (attempt + 1))
                elif attempt < max_retries - 1:
                    await asyncio.sleep(1.0 * (attempt + 1))
                else:
                    logger.error("Legacy embedding failed for: %s...", text[:50])
                    return []
            except Exception as e:
                logger.warning("Attempt %s/%s failed: %s", attempt + 1, max_retries, e)
                if attempt < max_retries - 1:
                    await asyncio.sleep(1.0 * (attempt + 1))
                else:
                    return []
    return []


async def embed_texts(
    texts: list[str], batch_size: int = 24, max_retries: int = 4
) -> list[list[float]]:
    """Batch-embed a list of strings via Ollama /api/embed with LRU cache reuse."""
    if not texts:
        return []

    results: list[list[float]] = [[] for _ in texts]
    missing_indices: list[int] = []
    missing_texts: list[str] = []

    for idx, raw in enumerate(texts):
        cleaned = (raw or "").strip()
        if not cleaned:
            continue
        cached = embedding_cache.get(cleaned)
        if cached is not None:
            results[idx] = cached
        else:
            missing_indices.append(idx)
            missing_texts.append(cleaned)

    if not missing_texts:
        return results

    for start in range(0, len(missing_texts), batch_size):
        batch_strs = missing_texts[start : start + batch_size]
        batch_idxs = missing_indices[start : start + batch_size]
        batch_vectors: list[list[float]] | None = None

        async with _embed_semaphore:
            for attempt in range(max_retries):
                try:
                    client = get_shared_http_client()
                    resp = await client.post(
                        f"{OLLAMA_BASE_URL}/api/embed",
                        json={"model": OLLAMA_EMBED_MODEL, "input": batch_strs},
                        timeout=60.0,
                    )
                    if resp.status_code in (400, 404, 501):
                        batch_vectors = None
                        break
                    resp.raise_for_status()
                    embs = resp.json().get("embeddings", [])
                    if isinstance(embs, list) and len(embs) == len(batch_strs):
                        batch_vectors = embs
                        break
                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 403:
                        await asyncio.sleep(15 * (attempt + 1))
                    elif attempt < max_retries - 1:
                        await asyncio.sleep(1.0 * (attempt + 1))
                except Exception as e:
                    logger.warning("Batch embed attempt %s/%s failed: %s", attempt + 1, max_retries, e)
                    if attempt < max_retries - 1:
                        await asyncio.sleep(1.0 * (attempt + 1))

        if batch_vectors is not None:
            for orig_idx, txt, vec in zip(batch_idxs, batch_strs, batch_vectors):
                if vec:
                    embedding_cache.put(txt, vec)
                    results[orig_idx] = vec
        else:
            # Fallback to single-item calls if batch endpoint unavailable or mismatched
            for orig_idx, txt in zip(batch_idxs, batch_strs):
                results[orig_idx] = await _embed_legacy_single(txt, max_retries=max_retries)

    return results


async def embed_text(text: str, max_retries: int = 5) -> list[float]:
    """Embed a single text string via batch-capable /api/embed + LRU cache."""
    if not text or not text.strip():
        return []
    res = await embed_texts([text], batch_size=1, max_retries=max_retries)
    return res[0] if res else []


async def embed_multimodal(
    text: str,
    images_b64: list[str] | None = None,
    max_retries: int = 3,
) -> list[float]:
    """Embed text + diagram images jointly into embeddinggemma-2's 768-d space.

    Ollama /api/embed requires a non-empty ``input`` string whenever ``images``
    are supplied. Falls back cleanly to text-only embedding if the loaded tag
    does not support image inputs.
    """
    prompt_text = (text or "").strip() or "Academic diagram, circuit, waveform, or equation"
    valid_imgs = [img.strip() for img in (images_b64 or []) if img and img.strip()]
    if not valid_imgs:
        return await embed_text(prompt_text)

    img_sig = hashlib.md5("".join(valid_imgs).encode("utf-8")).hexdigest()
    cache_key = f"mm::{img_sig}::{prompt_text}"
    cached = embedding_cache.get(cache_key)
    if cached is not None:
        return cached

    async with _embed_semaphore:
        for attempt in range(max_retries):
            try:
                client = get_shared_http_client()
                resp = await client.post(
                    f"{OLLAMA_BASE_URL}/api/embed",
                    json={
                        "model": OLLAMA_EMBED_MODEL,
                        "input": prompt_text,
                        "images": valid_imgs,
                    },
                    timeout=45.0,
                )
                if resp.status_code in (400, 404, 501):
                    logger.info("Multimodal embed unsupported by tag %s; falling back to text.", OLLAMA_EMBED_MODEL)
                    break
                resp.raise_for_status()
                embs = resp.json().get("embeddings", [])
                if embs and isinstance(embs[0], list) and embs[0]:
                    embedding_cache.put(cache_key, embs[0])
                    return embs[0]
            except Exception as e:
                logger.warning("Multimodal embed attempt %s/%s failed: %s", attempt + 1, max_retries, e)
                if attempt < max_retries - 1:
                    await asyncio.sleep(1.0 * (attempt + 1))

    return await embed_text(prompt_text)


async def embed_batch(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts using /api/embed batching."""
    return await embed_texts(texts)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Calculate cosine similarity between two float vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)
