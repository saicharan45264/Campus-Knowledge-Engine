"""
CurriculumLens Backend — Core Configuration

Centralized configuration using Pydantic Settings.
All environment variables are loaded from .env file.
"""

from pydantic_settings import BaseSettings
from pydantic import Field
from functools import lru_cache


class Settings(BaseSettings):
    """Application configuration loaded from environment variables."""

    # ── App ──
    APP_NAME: str = "CurriculumLens"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = True
    API_PREFIX: str = "/api/v1"

    # ── PostgreSQL ──
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "curriculumlens"
    POSTGRES_USER: str = "cluser"
    POSTGRES_PASSWORD: str = "clpassword"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def sync_database_url(self) -> str:
        return (
            f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    # ── Qdrant ──
    QDRANT_HOST: str = "localhost"
    QDRANT_PORT: int = 6333
    QDRANT_GRPC_PORT: int = 6334
    QDRANT_COLLECTION_TEXT: str = "cl_text_embeddings"
    QDRANT_COLLECTION_VISUAL: str = "cl_visual_embeddings"

    # ── Neo4j ──
    NEO4J_URI: str = "bolt://localhost:7687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str = "clpassword"

    # ── Elasticsearch ──
    ELASTICSEARCH_URL: str = "http://localhost:9200"
    ELASTICSEARCH_INDEX: str = "cl_documents"

    # ── Redis ──
    REDIS_URL: str = "redis://localhost:6379/0"

    # ── LLM ──
    LLM_PROVIDER: str = "ollama"  # ollama | groq
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "qwen2.5:7b"
    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "llama-3.1-8b-instant"

    # ── Embedding ──
    EMBEDDING_MODEL: str = "BAAI/bge-m3"
    EMBEDDING_DIMENSION: int = 1024

    # ── VACI (Visual Academic Concept Identifier) ──
    VACI_MODEL: str = "Qwen/Qwen2-VL-7B-Instruct"
    VACI_DEVICE: str = "auto"  # auto | cpu | cuda

    # ── Document Processing ──
    UPLOAD_DIR: str = "./data/uploads"
    MAX_UPLOAD_SIZE_MB: int = 100
    CHUNK_SIZE: int = 512
    CHUNK_OVERLAP: int = 64

    # ── Security ──
    SECRET_KEY: str = "change-me-in-production-use-openssl-rand-hex-32"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days

    # ── CORS ──
    CORS_ORIGINS: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True


@lru_cache()
def get_settings() -> Settings:
    """Cached settings instance — avoids re-reading .env on every call."""
    return Settings()
