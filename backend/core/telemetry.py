"""backend/core/telemetry.py — Production Observability, Latency Profiling & Token Telemetry.

Tracks stage-by-stage timings:
  t_total = t_router + t_retrieval + t_llm_ttft + t_llm_generation
Asynchronously commits query audit logs to PostgreSQL query_logs table without blocking event loops.
"""

import asyncio
import logging
import time
from typing import Any

from sqlalchemy import text

from backend.database import AsyncSessionLocal, QueryLog

logger = logging.getLogger("mach_ai.telemetry")


class QueryLatencyTracker:
    """
    Context-aware timing and audit tracker for a single student query lifecycle.
    """

    def __init__(self, session_id: str, user_query: str):
        self.session_id = session_id or "default_session"
        self.user_query = user_query
        self.classified_intent = "UNKNOWN"
        self.retrieval_layer = "unknown"
        self.router_ms: float = 0.0
        self.retrieval_ms: float = 0.0
        self.ttft_ms: float | None = None
        self.total_ms: float = 0.0
        self.prompt_tokens: int = 0
        self.completion_tokens: int = 0
        self.hit_count: int = 0

        self._start_time = time.perf_counter()
        self._router_start: float | None = None
        self._retrieval_start: float | None = None

    def start_router(self):
        self._router_start = time.perf_counter()

    def end_router(self, intent: str):
        if self._router_start:
            self.router_ms = (time.perf_counter() - self._router_start) * 1000.0
        self.classified_intent = intent

    def start_retrieval(self):
        self._retrieval_start = time.perf_counter()

    def end_retrieval(self, layer: str, hit_count: int = 0):
        if self._retrieval_start:
            self.retrieval_ms = (time.perf_counter() - self._retrieval_start) * 1000.0
        self.retrieval_layer = layer
        self.hit_count = hit_count

    def record_ttft(self):
        """Records time-to-first-token relative to query start."""
        if self.ttft_ms is None:
            self.ttft_ms = (time.perf_counter() - self._start_time) * 1000.0

    def record_tokens(self, prompt_tokens: int, completion_tokens: int):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens

    def finalize(self) -> "QueryLatencyTracker":
        self.total_ms = (time.perf_counter() - self._start_time) * 1000.0
        return self

    def log_background(self):
        """Dispatches asynchronous DB commit so request response is never held up."""
        self.finalize()
        asyncio.create_task(self._commit_to_db())

    async def _commit_to_db(self):
        try:
            async with AsyncSessionLocal() as session:
                log_entry = QueryLog(
                    session_id=self.session_id,
                    user_query=self.user_query[:1000],
                    classified_intent=self.classified_intent,
                    retrieval_layer=self.retrieval_layer,
                    router_ms=round(self.router_ms, 2),
                    retrieval_ms=round(self.retrieval_ms, 2),
                    ttft_ms=round(self.ttft_ms, 2) if self.ttft_ms is not None else None,
                    total_ms=round(self.total_ms, 2),
                    prompt_tokens=self.prompt_tokens,
                    completion_tokens=self.completion_tokens,
                    hit_count=self.hit_count,
                )
                session.add(log_entry)
                await session.commit()
        except Exception as e:
            # Telemetry logging should never crash the main application
            logger.warning("Telemetry audit logging notice: %s", e)


# Alias for clarity
TelemetryContext = QueryLatencyTracker


async def fetch_aggregated_telemetry() -> dict[str, Any]:
    """
    Returns aggregated metrics from query_logs for administrative dashboard and IEEE paper sync.
    """
    try:
        async with AsyncSessionLocal() as session:
            count_res = await session.execute(text("SELECT COUNT(*) FROM query_logs"))
            total_queries = count_res.scalar() or 0

            if total_queries == 0:
                return {
                    "total_queries": 0,
                    "avg_total_ms": 0.0,
                    "p50_total_ms": 0.0,
                    "p95_total_ms": 0.0,
                    "avg_ttft_ms": 0.0,
                    "intent_distribution": {},
                    "layer_distribution": {},
                }

            stats_res = await session.execute(text("""
                SELECT 
                    AVG(total_ms) AS avg_total,
                    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY total_ms) AS p50_total,
                    PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY total_ms) AS p95_total,
                    AVG(ttft_ms) AS avg_ttft,
                    AVG(router_ms) AS avg_router,
                    AVG(retrieval_ms) AS avg_retrieval,
                    SUM(prompt_tokens) AS total_prompt_tokens,
                    SUM(completion_tokens) AS total_completion_tokens
                FROM query_logs
            """))
            stats = stats_res.mappings().one()

            # Intent breakdown
            intents_res = await session.execute(text("""
                SELECT classified_intent, COUNT(*) AS cnt 
                FROM query_logs 
                GROUP BY classified_intent 
                ORDER BY cnt DESC
            """))
            intent_dist = {r["classified_intent"]: r["cnt"] for r in intents_res.mappings().all()}

            # Layer breakdown
            layers_res = await session.execute(text("""
                SELECT retrieval_layer, COUNT(*) AS cnt 
                FROM query_logs 
                GROUP BY retrieval_layer 
                ORDER BY cnt DESC
            """))
            layer_dist = {r["retrieval_layer"]: r["cnt"] for r in layers_res.mappings().all()}

            return {
                "total_queries": total_queries,
                "avg_total_ms": round(float(stats["avg_total"] or 0), 2),
                "p50_total_ms": round(float(stats["p50_total"] or 0), 2),
                "p95_total_ms": round(float(stats["p95_total"] or 0), 2),
                "avg_ttft_ms": round(float(stats["avg_ttft"] or 0), 2),
                "avg_router_ms": round(float(stats["avg_router"] or 0), 2),
                "avg_retrieval_ms": round(float(stats["avg_retrieval"] or 0), 2),
                "total_prompt_tokens": int(stats["total_prompt_tokens"] or 0),
                "total_completion_tokens": int(stats["total_completion_tokens"] or 0),
                "intent_distribution": intent_dist,
                "layer_distribution": layer_dist,
            }
    except Exception as e:
        logger.warning("fetch_aggregated_telemetry notice: %s", e)
        return {"error": str(e)}

