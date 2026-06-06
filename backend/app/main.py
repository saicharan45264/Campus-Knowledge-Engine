"""
CurriculumLens — Main FastAPI Application

Curriculum-Grounded Multimodal Knowledge Retrieval and Examination Intelligence.

This is the entry point for the backend application. It sets up:
- CORS middleware
- API route registration
- Startup/shutdown lifecycle hooks for database and service client initialization
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.database import engine, Base
from app.api import documents, chat, knowledge_graph, pyq_intelligence

settings = get_settings()

# Configure logging
logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("curriculumlens")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle — initialize and teardown resources."""
    logger.info("🚀 Starting CurriculumLens Backend v%s", settings.APP_VERSION)

    # ── Startup ──
    # Create PostgreSQL tables and enable pgvector
    from sqlalchemy import text
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
    logger.info("✅ PostgreSQL tables and vector extension created")

    # Initialize Neo4j constraints
    try:
        from app.core.clients import get_neo4j_driver, init_neo4j_constraints
        driver = get_neo4j_driver()
        await init_neo4j_constraints(driver)
        await driver.close()
        logger.info("✅ Neo4j constraints created")
    except Exception as e:
        logger.warning("⚠️  Neo4j not available: %s", e)

    logger.info("🟢 CurriculumLens Backend ready")

    yield  # Application runs here

    # ── Shutdown ──
    await engine.dispose()
    logger.info("🔴 CurriculumLens Backend shutdown complete")


# ── Create Application ──
app = FastAPI(
    title="CurriculumLens API",
    description=(
        "Curriculum-Grounded Multimodal Knowledge Retrieval and "
        "Examination Intelligence for Academic Assistance.\n\n"
        "**Core Capabilities:**\n"
        "- 📄 Multimodal document processing (PDF, PPT, images, handwritten notes)\n"
        "- 🧠 Curriculum Knowledge Graph (auto-constructed from syllabi)\n"
        "- 🔍 Hybrid retrieval (dense + sparse + KG + visual)\n"
        "- 🎯 Visual Academic Concept Identification (VACI)\n"
        "- 📊 PYQ Examination Intelligence\n"
        "- 💬 Context-grounded academic Q&A"
    ),
    version=settings.APP_VERSION,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# ── CORS Middleware ──
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Register API Routes ──
api_prefix = settings.API_PREFIX

app.include_router(documents.router, prefix=api_prefix)
app.include_router(chat.router, prefix=api_prefix)
app.include_router(knowledge_graph.router, prefix=api_prefix)
app.include_router(pyq_intelligence.router, prefix=api_prefix)


# ── Health Check ──
@app.get("/health", tags=["System"])
async def health_check():
    """Health check endpoint for monitoring and load balancers."""
    services = {}

    # Check PostgreSQL
    try:
        from sqlalchemy import text
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        services["postgresql"] = "healthy"
    except Exception:
        services["postgresql"] = "unhealthy"



    overall = "healthy" if all(v == "healthy" for v in services.values()) else "degraded"

    return {
        "status": overall,
        "version": settings.APP_VERSION,
        "services": services,
    }


@app.get("/", tags=["System"])
async def root():
    """Root endpoint — API information."""
    return {
        "name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "docs": "/docs",
        "health": "/health",
    }
