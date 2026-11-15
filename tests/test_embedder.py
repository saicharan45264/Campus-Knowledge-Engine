
"""tests/test_embedder.py — Unit tests for embedding cache and vector similarity."""
from __future__ import annotations

import pytest

from backend.core import http_client
from backend.services.embedding_service import cosine_similarity, embed_text, embedding_cache


@pytest.mark.asyncio
async def test_embedder_cache(monkeypatch):
    async def mock_post(url, json=None, timeout=None):
        class MockResp:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                if "/api/embed" in str(url):
                    texts = (json or {}).get("input", [])
                    return {"embeddings": [[0.1] * 768 for _ in texts]}
                return {"embedding": [0.1] * 768}

        return MockResp()

    shared = http_client.get_shared_http_client()
    monkeypatch.setattr(shared, "post", mock_post)

    embedding_cache.cache.clear()

    # Cache miss
    emb1 = await embed_text("Hello World")
    assert isinstance(emb1, list)
    assert len(emb1) == 768

    # Cache hit
    emb2 = await embed_text("Hello World")
    assert emb1 is emb2  # Exact same object from cache

def test_cosine_similarity():
    # Identical vectors
    v1 = [1.0, 0.0, 0.0]
    v2 = [1.0, 0.0, 0.0]
    assert cosine_similarity(v1, v2) == 1.0

    # Orthogonal vectors
    v3 = [0.0, 1.0, 0.0]
    assert cosine_similarity(v1, v3) == 0.0

    # Opposite vectors
    v4 = [-1.0, 0.0, 0.0]
    assert cosine_similarity(v1, v4) == -1.0
