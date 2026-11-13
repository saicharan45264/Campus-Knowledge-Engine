"""backend/config.py — Application configuration loaded from environment variables."""
from __future__ import annotations

import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # JWT Auth Configuration
    jwt_secret: str = "mach-ai-default-jwt-secret-2026"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 480

    # Ollama Configuration
    ollama_model: str = "gemma4:12b-it-qat"
    ollama_base_url: str = "http://localhost:11434"
    ollama_embed_model: str = "embeddinggemma-2:latest"

    # Runtime Environment & Network Security
    headless_mode: bool = False
    app_env: str = "development"
    cors_origins: str = "*"

    # PostgreSQL & pgvector Database Configuration
    postgres_user: str = "mach"
    postgres_password: str = "ai"
    postgres_host: str = "localhost"
    postgres_port: str = "5434"
    postgres_db: str = "mach_ai"

    # Neo4j Property Graph Database Configuration
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "mach_ai_cke"

    # Optional Cloudinary Storage Configuration
    cloudinary_cloud_name: str = ""
    cloudinary_api_key: str = ""
    cloudinary_api_secret: str = ""

    # Centralized Storage Paths
    uploads_dir: str = os.path.join(os.path.dirname(__file__), "uploads")

    @property
    def images_dir(self) -> str:
        return os.path.join(self.uploads_dir, "images")

    @property
    def allowed_origins_list(self) -> list[str]:
        if not self.cors_origins or self.cors_origins.strip() == "*":
            return ["*"]
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()
