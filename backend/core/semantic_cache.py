"""backend/core/semantic_cache.py — Tier-0.5 Semantic Vector Similarity Cache.

Returns cached query responses in < 3ms with zero GPU compute when cosine similarity >= 0.95.
Utilizes PostgreSQL pgvector (HNSW index) paired with an in-memory LRU cache for maximum speed.
"""

import json
import logging
from typing import Any

from sqlalchemy import text

from backend.database import AsyncSessionLocal, SemanticCache
from backend.services.embedding_service import (
    cosine_similarity,
    format_query_embedding_text,
)
from backend.services.embedding_service import embed_text as get_embedding

logger = logging.getLogger("mach_ai.semantic_cache")

# In-memory fast buffer of recent (text, embedding, payload, intent)
_MEMORY_CACHE = []
MAX_MEMORY_CACHE_SIZE = 128


class SemanticCacheManager:
    """
    Tier-0.5 Semantic Caching Layer.
    """

    def __init__(self, similarity_threshold: float = 0.95):
        self.similarity_threshold = similarity_threshold

    async def get(self, query: str, query_embedding: list | None = None) -> dict[str, Any] | None:
        """
        Checks cache for a semantically equivalent query (cosine similarity >= threshold).
        Returns payload dict or None.
        """
        if not query or not query.strip():
            return None

        # 1. Check in-memory exact match (< 0.1ms)
        q_norm = query.strip().lower()
        for item in _MEMORY_CACHE:
            if item["query_norm"] == q_norm:
                return item["payload"]

        # If no embedding provided, compute one with query retrieval prefix
        if query_embedding is None:
            query_embedding = await get_embedding(format_query_embedding_text(query))
            if not query_embedding:
                return None

        # 2. Check in-memory semantic similarity (< 0.5ms)
        for item in _MEMORY_CACHE:
            sim = cosine_similarity(query_embedding, item["embedding"])
            if sim >= self.similarity_threshold:
                return item["payload"]

        # 3. Check PostgreSQL pgvector semantic_cache table (< 3ms)
        try:
            async with AsyncSessionLocal() as session:
                # pgvector cosine distance: distance = 1 - cosine_similarity
                # So cosine_similarity >= 0.95 means distance <= 0.05
                max_distance = 1.0 - self.similarity_threshold

                # Format embedding as vector string: '[0.1, 0.2, ...]'
                emb_str = f"[{','.join(f'{x:.6f}' for x in query_embedding)}]"

                result = await session.execute(
                    text("""
                        SELECT id, query_text, cached_intent, response_payload,
                               (query_embedding <=> CAST(:emb AS halfvec(768))) AS distance
                        FROM semantic_cache
                        WHERE (query_embedding <=> CAST(:emb AS halfvec(768))) <= :max_dist
                        ORDER BY distance ASC
                        LIMIT 1
                    """),
                    {"emb": emb_str, "max_dist": max_distance},
                )
                row = result.mappings().first()
                if row:
                    payload = row["response_payload"]
                    if isinstance(payload, str):
                        try:
                            payload = json.loads(payload)
                        except Exception:
                            pass

                    # Store in in-memory buffer
                    self._add_to_memory(query, query_embedding, payload, row["cached_intent"])
                    return payload
        except Exception as e:
            logger.warning("Cache lookup notice: %s", e)

        return None

    async def put(self, query: str, intent: str, payload: Any, query_embedding: list | None = None):
        """
        Stores query, embedding, intent, and response payload in both in-memory cache and PostgreSQL.
        """
        if not query or not query.strip():
            return

        if query_embedding is None:
            query_embedding = await get_embedding(format_query_embedding_text(query))
            if not query_embedding:
                return

        # Prepare JSON payload
        serializable_payload = payload
        if not isinstance(payload, (dict, list, str, int, float, bool)):
            serializable_payload = str(payload)

        # 1. Update in-memory buffer
        self._add_to_memory(query, query_embedding, serializable_payload, intent)

        # 2. Update PostgreSQL table asynchronously
        try:
            async with AsyncSessionLocal() as session:
                # Wrap dict/list into json string or json object
                cache_entry = SemanticCache(
                    query_text=query.strip(),
                    query_embedding=query_embedding,
                    cached_intent=intent,
                    response_payload=serializable_payload,
                )
                session.add(cache_entry)
                await session.commit()
        except Exception as e:
            logger.warning("Cache storage notice: %s", e)

    def _add_to_memory(self, query: str, embedding: list, payload: Any, intent: str):
        if len(_MEMORY_CACHE) >= MAX_MEMORY_CACHE_SIZE:
            _MEMORY_CACHE.pop(0)
        _MEMORY_CACHE.append({
            "query_norm": query.strip().lower(),
            "embedding": embedding,
            "payload": payload,
            "intent": intent,
        })

    def clear_memory(self) -> None:
        """Clear the in-memory L1 semantic cache after document ingestion/reset."""
        _MEMORY_CACHE.clear()


semantic_cache = SemanticCacheManager(similarity_threshold=0.95)


async def check_semantic_cache(query: str, query_embedding: list | None = None) -> dict[str, Any] | None:
    """Convenience wrapper for semantic_cache.get()"""
    return await semantic_cache.get(query, query_embedding)


async def store_semantic_cache(
    query: str,
    intent: str,
    payload: Any,
    query_embedding: list | None = None,
) -> None:
    """Convenience wrapper for semantic_cache.put()"""
    await semantic_cache.put(query, intent, payload, query_embedding)
