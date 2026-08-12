import sys
import os
sys.path.append(os.path.join(os.getcwd(), 'backend'))
from services.llm import get_embedding
from core.database import get_db_connection

msg = "get me all questions on 23EEE104"
emb = get_embedding(msg)
conn = get_db_connection()
sql = """
            SELECT chunks.id, chunks.content, documents.course_code,
                   embedding <=> CAST(%s AS vector) as dist
            FROM   chunks
            INNER JOIN documents ON chunks.document_id = documents.id
            WHERE  documents.doc_type = ANY(%s)
            ORDER BY dist ASC
            LIMIT 5
"""
with conn.cursor() as cur:
    cur.execute(sql, (str(emb), ["evaluation", "pyq"]))
    for row in cur.fetchall():
        print(row)
