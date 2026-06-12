import asyncio
from src.query.retriever import hybrid_retrieve, intent_to_doctype
import json

session = {
    'university_id': '00000000-0000-0000-0000-000000000000',
    'department': 'CSE',
    'semester': 6,
    'regulation_year': '2023',
    'academic_year': '2025-26',
    'turn_history': []
}

intent = 'SYLLABUS'
query = 'What courses are offered in B.Tech CSE?'

print("Intent mapped to doc_type:", intent_to_doctype(intent))
chunks = hybrid_retrieve(query, intent, session)
print("Chunks retrieved:", len(chunks))
for c in chunks:
    print(c['chunk_id'], c['score'])
