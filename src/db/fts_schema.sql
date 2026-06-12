CREATE VIRTUAL TABLE IF NOT EXISTS document_fts USING fts5(
    chunk_id UNINDEXED,
    university_id UNINDEXED,
    doc_type UNINDEXED,
    content
);
