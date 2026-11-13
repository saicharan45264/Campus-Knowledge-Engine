"""backend/core/http_client.py — High-Performance Shared Async HTTP Client & GPU Concurrency Limiter.

Eliminates TCP connection renegotiation and socket overhead (~35ms per call) via connection pooling.
Enforces an asyncio.Semaphore(2) to prevent Apple Silicon unified memory thrashing under load.
"""

import asyncio

import httpx

# Global GPU Concurrency Limiter for Apple Silicon Metal MPS
# Restricts concurrent local LLM inference to 2 active streams to protect 16GB Unified RAM
GPU_SEMAPHORE = asyncio.Semaphore(2)

_SHARED_HTTP_CLIENT: httpx.AsyncClient | None = None


def get_shared_http_client() -> httpx.AsyncClient:
    """
    Returns the process-wide persistent httpx.AsyncClient singleton
    with connection pooling and keep-alive limits enabled.
    """
    global _SHARED_HTTP_CLIENT
    if _SHARED_HTTP_CLIENT is None or _SHARED_HTTP_CLIENT.is_closed:
        _SHARED_HTTP_CLIENT = httpx.AsyncClient(
            headers={"ngrok-skip-browser-warning": "true"},
            limits=httpx.Limits(
                max_connections=50,
                max_keepalive_connections=20,
                keepalive_expiry=60.0,
            ),
            timeout=httpx.Timeout(connect=5.0, read=120.0, write=10.0, pool=5.0),
        )
    return _SHARED_HTTP_CLIENT


async def close_shared_http_client():
    """Cleanly closes open socket pools on application shutdown."""
    global _SHARED_HTTP_CLIENT
    if _SHARED_HTTP_CLIENT is not None and not _SHARED_HTTP_CLIENT.is_closed:
        await _SHARED_HTTP_CLIENT.aclose()
        _SHARED_HTTP_CLIENT = None


# Aliases for convenience
get_http_client = get_shared_http_client
gpu_semaphore = GPU_SEMAPHORE

