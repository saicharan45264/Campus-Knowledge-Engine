import sys
import os

# Add project root to python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.db.connection import get_pg_connection
from src.ingestion.indexer import process_document

def reprocess():
    conn = get_pg_connection()
    docs = []
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, file_path, document_type, department, semester, section_id, regulation_year, academic_year, university_id 
                FROM documents 
                WHERE status LIKE 'error:%'
            """)
            for row in cur.fetchall():
                docs.append({
                    'id': row[0],
                    'file_path': row[1],
                    'metadata': {
                        'document_type': row[2],
                        'department': row[3],
                        'semester': row[4],
                        'section_id': row[5],
                        'regulation_year': row[6],
                        'academic_year': row[7]
                    },
                    'admin': {
                        'university_id': row[8]
                    }
                })
    finally:
        conn.close()

    print(f"Found {len(docs)} failed documents to reprocess.")
    
    for doc in docs:
        print(f"Reprocessing {doc['id']} ({doc['metadata']['document_type']})...")
        if not os.path.exists(doc['file_path']):
            print(f"File {doc['file_path']} not found!")
            continue
        process_document(doc['id'], doc['file_path'], doc['metadata'], doc['admin'])
        
if __name__ == "__main__":
    reprocess()
