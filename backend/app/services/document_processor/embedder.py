"""
CurriculumLens — Embedding Generator

Wrapper for sentence-transformers to generate dense embeddings using BAAI/bge-m3.
Handles batch processing for efficiency.
"""

import logging
from typing import List
import torch
from sentence_transformers import SentenceTransformer
from app.core.config import get_settings

logger = logging.getLogger("curriculumlens.embedder")
settings = get_settings()


class BGEEmbedder:
    """Singleton wrapper for the BGE-M3 embedding model."""

    _instance = None
    _model = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(BGEEmbedder, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        # Prevent re-initialization
        if self._model is not None:
            return

        self.model_name = settings.EMBEDDING_MODEL
        self.device = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
        
        logger.info(f"Loading embedding model: {self.model_name} on {self.device}")
        try:
            self._model = SentenceTransformer(self.model_name, device=self.device)
            logger.info("Embedding model loaded successfully.")
        except Exception as e:
            logger.error(f"Failed to load embedding model {self.model_name}: {e}")
            raise

    def embed_texts(self, texts: List[str], batch_size: int = 32) -> List[List[float]]:
        """
        Generate dense embeddings for a list of texts.
        
        Args:
            texts: List of strings to embed.
            batch_size: Number of texts to process at once.
            
        Returns:
            List of embeddings (lists of floats).
        """
        if not texts:
            return []

        logger.info(f"Generating embeddings for {len(texts)} texts in batches of {batch_size}...")
        
        # BGE-M3 handles passage encoding natively. We just call encode().
        # normalize_embeddings=True is recommended for BGE-M3 for cosine similarity.
        embeddings = self._model.encode(
            texts,
            batch_size=batch_size,
            show_progress_bar=False,
            normalize_embeddings=True,
            convert_to_numpy=True
        )
        
        return embeddings.tolist()

    @property
    def dimension(self) -> int:
        return settings.EMBEDDING_DIMENSION

