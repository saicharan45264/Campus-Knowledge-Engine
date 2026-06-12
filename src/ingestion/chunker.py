from typing import Callable
from .extractors.regulations import build_regulation_tree, build_regulation_chunks, extract_regulations
from .extractors.curriculum import build_curriculum_chunks
from .extractors.timetable import build_timetable_chunks
from .extractors.academic_calendar import build_academic_calendar_chunks

def chunk_regulations(file_path: str, metadata: dict) -> list[dict]:
    full_text = extract_regulations(file_path)
    tree = build_regulation_tree(full_text)
    chunks = build_regulation_chunks(tree, metadata)
    return chunks

def chunk_curriculum(file_path: str, metadata: dict) -> list[dict]:
    return build_curriculum_chunks(file_path, metadata)

def chunk_timetable(file_path: str, metadata: dict) -> list[dict]:
    return build_timetable_chunks(file_path, metadata)

def chunk_academic_calendar(file_path: str, metadata: dict) -> list[dict]:
    return build_academic_calendar_chunks(file_path, metadata)

CHUNKERS: dict[str, Callable] = {
    'regulations': chunk_regulations,
    'curriculum': chunk_curriculum,
    'timetable': chunk_timetable,
    'academic_calendar': chunk_academic_calendar,
}

def chunk_document(file_path: str, doc_type: str, metadata: dict) -> list[dict]:
    chunker = CHUNKERS.get(doc_type)
    if not chunker:
        raise ValueError(f"Unknown document type: {doc_type}")
    return chunker(file_path, metadata)
