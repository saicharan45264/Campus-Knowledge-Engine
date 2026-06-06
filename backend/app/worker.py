"""
CurriculumLens — Document Processing Pipeline

Background tasks for document processing and pgvector embedding generation.
"""

import logging
from sqlalchemy import func
from app.core.database import async_session_factory
from app.models.models import Document, DocumentChunk
from app.services.document_processor.processor import DocumentProcessor
from app.services.document_processor.embedder import BGEEmbedder
from app.core.config import get_settings

logger = logging.getLogger("curriculumlens.worker")
settings = get_settings()

async def process_document_async(document_id: str):
    """Async implementation of the document processing pipeline using pgvector."""
    async with async_session_factory() as db:
        try:
            # 1. Fetch document
            doc = await db.get(Document, document_id)
            if not doc:
                logger.error(f"Document {document_id} not found.")
                return

            logger.info(f"Starting processing for document {document_id}: {doc.filename}")
            doc.processing_status = "processing"
            db.add(doc)
            await db.commit()

            # 2. Extract and Chunk
            processor = DocumentProcessor()
            output_dir = f"{settings.UPLOAD_DIR}/{doc.id}_processed"
            result = processor.process(doc.file_path, doc.document_type, output_dir)
            
            chunks = result["chunks"]
            logger.info(f"Extracted {len(chunks)} chunks from document {document_id}")

            if not chunks:
                logger.warning(f"No text extracted from document {document_id}")
                doc.processing_status = "completed"
                db.add(doc)
                await db.commit()
                return

            # 3. Save chunks to PostgreSQL
            db_chunks = []
            for chunk in chunks:
                db_chunk = DocumentChunk(
                    document_id=doc.id,
                    chunk_index=chunk["chunk_index"],
                    page_number=chunk.get("page_num"),
                    content=chunk["text"],
                    token_count=chunk.get("char_count")  # Approximation for now
                )
                db.add(db_chunk)
                db_chunks.append(db_chunk)
            
            await db.commit()
            
            for db_chunk in db_chunks:
                await db.refresh(db_chunk)

            # 4. Generate Embeddings (BGE-M3)
            embedder = BGEEmbedder()
            texts = [chunk.content for chunk in db_chunks]
            embeddings = embedder.embed_texts(texts)

            # 5. Index into PostgreSQL (pgvector & tsvector)
            for db_chunk, embedding in zip(db_chunks, embeddings):
                db_chunk.embedding = embedding
                # We use SQLAlchemy's func.to_tsvector to generate the keyword index
                db_chunk.content_tsvector = func.to_tsvector('english', db_chunk.content)
                db.add(db_chunk)

            await db.commit()
            logger.info(f"Indexed {len(db_chunks)} chunks with pgvector and tsvector.")

            # 6. Update document status
            doc.processing_status = "completed"
            db.add(doc)
            await db.commit()
            logger.info(f"Document {document_id} processing completed successfully.")

        except Exception as e:
            logger.error(f"Error processing document {document_id}: {e}", exc_info=True)
            if 'doc' in locals() and doc:
                doc.processing_status = "failed"
                doc.processing_error = str(e)
                db.add(doc)
                await db.commit()
