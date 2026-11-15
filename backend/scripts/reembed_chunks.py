#!/usr/bin/env python3
"""backend/scripts/reembed_chunks.py — Re-embed all document chunks in PostgreSQL with embeddinggemma-2:latest.
Flushes the semantic cache to eliminate stale cross-model embeddings.
"""

import asyncio
import os
import sys
import time
from pathlib import Path

# Add project root to module search path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import delete, func, select, update

from backend.core.http_client import close_shared_http_client
from backend.database import AsyncSessionLocal, DocumentChunk, SemanticCache
from backend.services.embedding_service import embed_texts, format_doc_embedding_text


async def reembed_all_chunks():
    start_time = time.perf_counter()
    print("=" * 70)
    print("M.A.C.H. AI — Batch Re-embedding Migration Suite")
    print(f"Target Embedding Model: {os.getenv('OLLAMA_EMBED_MODEL', 'embeddinggemma-2:latest')}")
    print("=" * 70)

    async with AsyncSessionLocal() as session:
        # 1. Count chunks
        total_chunks = await session.scalar(select(func.count(DocumentChunk.id)))
        print(f"[1/4] Discovered {total_chunks} document chunks in PostgreSQL.")

        # 2. Flush semantic cache
        cache_count = await session.scalar(select(func.count(SemanticCache.id)))
        print(f"[2/4] Flushing semantic cache ({cache_count} stale entries)...")
        await session.execute(delete(SemanticCache))
        await session.commit()
        print("      Semantic cache flushed.")

        if total_chunks == 0:
            print("[3/4] No document chunks to re-embed. Exiting.")
            return

        # 3. Fetch all chunks
        print(f"[3/4] Fetching {total_chunks} chunks for re-embedding...")
        result = await session.execute(select(DocumentChunk.id, DocumentChunk.content))
        chunks = result.all()

        print("[4/4] Starting re-embedding loop with embeddinggemma-2...")
        batch_size = 20
        updated = 0
        failed = 0

        for i in range(0, len(chunks), batch_size):
            batch = chunks[i : i + batch_size]
            valid_items = [(cid, txt) for cid, txt in batch if txt and txt.strip()]
            if not valid_items:
                continue

            batch_updates = []
            try:
                vecs = await embed_texts(
                    [format_doc_embedding_text(txt) for _, txt in valid_items],
                    batch_size=batch_size,
                )
                for (chunk_id, _txt), vec in zip(valid_items, vecs):
                    if vec and len(vec) == 768:
                        batch_updates.append({"_id": chunk_id, "_vec": vec})
                        updated += 1
                    else:
                        print(f"      Warning: Invalid vector returned for chunk {chunk_id}")
                        failed += 1
            except Exception as ex:
                print(f"      Error batch embedding chunks: {ex}")
                failed += len(valid_items)

            # Execute batch update
            if batch_updates:
                for item in batch_updates:
                    await session.execute(
                        update(DocumentChunk)
                        .where(DocumentChunk.id == item["_id"])
                        .values(embedding=item["_vec"])
                    )
                await session.commit()

            progress = min(i + batch_size, len(chunks))
            pct = (progress / len(chunks)) * 100
            elapsed = time.perf_counter() - start_time
            print(f"      Progress: {progress}/{len(chunks)} ({pct:.1f}%) | Updated: {updated} | Elapsed: {elapsed:.1f}s")

    # Clean up shared HTTP client
    await close_shared_http_client()

    total_time = time.perf_counter() - start_time
    print("=" * 70)
    print(f"Batch Migration Finished in {total_time:.2f} seconds.")
    print(f"Successfully Re-embedded: {updated}/{total_chunks} chunks.")
    if failed > 0:
        print(f"Failed chunks: {failed}")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(reembed_all_chunks())

