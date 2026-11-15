"""backend/api/evaluation.py — Observability, Health & Academic Evaluation Endpoints.

Provides health checks for Neo4j, PostgreSQL, and Ollama, plus endpoints
to execute and fetch the 20-sample ground-truth benchmark suite.
"""
from __future__ import annotations

import json
import logging
import os

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text

from backend.api.auth import require_admin
from backend.config import settings
from backend.core.http_client import get_shared_http_client
from backend.core.query_performance_logger import fetch_aggregated_telemetry
from backend.database import engine, get_neo4j

logger = logging.getLogger("mach_ai.eval")

router = APIRouter(tags=["Evaluation & Telemetry"])


@router.get("/health")
async def health_check():
    """Validates connectivity to Neo4j, PostgreSQL, and local Ollama instance."""
    health_status = {
        "status": "ok",
        "postgres": False,
        "neo4j": False,
        "ollama": False,
        "model": settings.ollama_model,
    }
    # 1. PostgreSQL check
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            health_status["postgres"] = True
    except Exception as e:
        logger.warning("Health check PostgreSQL error: %s", e)
        health_status["postgres_error"] = "unavailable"
        health_status["status"] = "degraded"

    # 2. Neo4j check
    try:
        driver = get_neo4j()
        with driver.session() as s:
            s.run("RETURN 1").data()
            health_status["neo4j"] = True
    except Exception as e:
        logger.warning("Health check Neo4j error: %s", e)
        health_status["neo4j_error"] = "unavailable"
        health_status["status"] = "degraded"

    # 3. Ollama check
    try:
        client = get_shared_http_client()
        resp = await client.get(f"{settings.ollama_base_url}/api/tags", timeout=5.0)
        if resp.status_code == 200:
            health_status["ollama"] = True
    except Exception as e:
        logger.warning("Health check Ollama error: %s", e)
        health_status["ollama_error"] = "unavailable"
        health_status["status"] = "degraded"

    return health_status


_ALLOWED_EVAL_CATEGORIES = {"all", "curriculum", "prerequisites", "topic_pyqs", "pedagogical"}


@router.post("/eval/run")
async def run_eval_endpoint(
    payload: dict | None = None,
    user: dict = Depends(require_admin),
):
    """Triggers the 20-sample ground-truth evaluation benchmark suite and persists results."""
    category = "all"
    if payload and isinstance(payload, dict):
        category = str(payload.get("category", "all"))
    if category not in _ALLOWED_EVAL_CATEGORIES:
        raise HTTPException(status_code=400, detail="Invalid benchmark category.")

    from backend.services.evaluation_benchmark import run_benchmark

    summary = await run_benchmark(category_filter=category)

    bench_file = os.path.join(settings.uploads_dir, "benchmark_results.json")
    try:
        os.makedirs(os.path.dirname(bench_file), exist_ok=True)
        with open(bench_file, "w") as f:
            json.dump(summary, f, indent=2)
    except Exception as e:
        logger.warning("Failed to persist benchmark_results.json: %s", e)

    return summary


@router.get("/eval/metrics")
async def get_eval_metrics_endpoint(
    user: dict = Depends(require_admin),
):
    """Returns aggregated evaluation radar metrics and runtime query_logs telemetry."""
    telemetry = await fetch_aggregated_telemetry()

    bench_file = os.path.join(settings.uploads_dir, "benchmark_results.json")
    fallback_bench_file = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "scripts",
        "benchmark_results.json",
    )
    benchmark_data = None
    target_path = bench_file if os.path.exists(bench_file) else fallback_bench_file
    if os.path.exists(target_path):
        try:
            with open(target_path) as f:
                benchmark_data = json.load(f)
        except Exception as e:
            logger.warning("Error reading benchmark results: %s", e)

    return {
        "telemetry": telemetry,
        "benchmark": benchmark_data,
    }
