import os
import chromadb
from sentence_transformers import SentenceTransformer
from src.db.connection import get_pg_connection, release_pg_connection, get_sqlite_connection

# Load embedding model (default generic model for Phase 1)
try:
    embedder = SentenceTransformer('all-MiniLM-L6-v2')
except Exception as e:
    print(f"Warning: Failed to load sentence-transformer. Will download. {e}")
    embedder = None

# Initialize ChromaDB client
CHROMA_STORE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "chroma_store")
chroma_cli = chromadb.PersistentClient(path=CHROMA_STORE_PATH)

def get_or_create_collection(university_id: str, doc_type: str):
    name = f"univ_{university_id.replace('-', '_')}_{doc_type}"
    return chroma_cli.get_or_create_collection(
        name=name,
        metadata={'hnsw:space': 'cosine'}
    )

def index_document(chunks: list[dict], university_id: str, doc_type: str):
    global embedder
    if not embedder:
        embedder = SentenceTransformer('all-MiniLM-L6-v2')
        
    collection = get_or_create_collection(university_id, doc_type)
    
    BATCH = 64
    total = 0
    for i in range(0, len(chunks), BATCH):
        batch = chunks[i:i+BATCH]
        texts = [c['text'] for c in batch]
        ids = [c['chunk_id'] for c in batch]
        metas = [c['metadata'] for c in batch]
        
        # Format metadata for ChromaDB (no nested dicts allowed)
        flat_metas = []
        for m in metas:
            flat = {}
            for k, v in m.items():
                if isinstance(v, (str, int, float, bool)):
                    flat[k] = v
                else:
                    flat[k] = str(v)
            flat_metas.append(flat)
            
        embeds = embedder.encode(texts, normalize_embeddings=True, show_progress_bar=False).tolist()
        
        collection.upsert(ids=ids, embeddings=embeds, documents=texts, metadatas=flat_metas)
        total += len(batch)
    return total

def update_bm25_index(chunks: list[dict]):
    conn = get_sqlite_connection()
    try:
        with conn:
            conn.executemany(
                'INSERT OR REPLACE INTO document_fts(chunk_id, university_id, doc_type, content) VALUES (?,?,?,?)',
                [(c['chunk_id'], c['metadata'].get('university_id'), c['metadata'].get('document_type'), c['text']) for c in chunks]
            )
    finally:
        conn.close()

def process_document(doc_id: str, file_path: str, metadata: dict, admin: dict):
    """Full processing pipeline."""
    from .chunker import chunk_document
    from .cleaner import clean_text
    
    pg_conn = get_pg_connection()
    try:
        # 1 & 2 & 3. Extract, Clean (implicitly in chunker for now) and Chunk
        chunks = chunk_document(file_path, metadata['document_type'], {
            **metadata, 
            'university_id': admin['university_id'],
            'document_id': doc_id
        })
        
        if not chunks:
            raise ValueError("Chunking produced zero chunks")
            
        # Apply cleaner to text of each chunk
        for c in chunks:
            c['text'] = clean_text(c['text'])
            
        # 4. Index
        n = index_document(chunks, admin['university_id'], metadata['document_type'])
        update_bm25_index(chunks)
        
        # 4b. If curriculum, populate courses and abbreviations maps
        if metadata['document_type'] == 'curriculum':
            with pg_conn.cursor() as cur:
                for chunk in chunks:
                    if chunk['metadata'].get('granularity') == 'course':
                        m = chunk['metadata']
                        cur.execute("""
                            INSERT INTO curriculum_courses (course_code, university_id, department, regulation_year, semester, course_title, credits, category)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (course_code) DO UPDATE SET
                                course_title = EXCLUDED.course_title,
                                credits = EXCLUDED.credits;
                        """, (
                            m['course_code'], admin['university_id'], m.get('department') or 'Unknown',
                            m.get('regulation_year'), m.get('semester') or 0, m['course_title'],
                            m.get('credits', 0.0), m.get('category', 'Core')
                        ))
                        # Generate course abbreviation
                        title = m['course_title']
                        abbr = "".join(word[0] for word in title.split() if word[0].isupper())
                        if len(abbr) >= 2:
                            cur.execute("""
                                INSERT INTO course_abbr_map (abbr, university_id, department, semester, course_code)
                                VALUES (%s, %s, %s, %s, %s)
                                ON CONFLICT (abbr, university_id, department, semester) DO NOTHING;
                            """, (
                                abbr, admin['university_id'], m.get('department') or 'Unknown',
                                m.get('semester') or 0, m['course_code']
                            ))
            pg_conn.commit()
            
        # 4c. Populate timetable
        elif metadata['document_type'] == 'timetable':
            with pg_conn.cursor() as cur:
                for chunk in chunks:
                    m = chunk['metadata']
                    cur.execute("""
                        INSERT INTO timetable_cells (university_id, section_id, semester, academic_year, day, slot_number, course_abbr, room)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """, (
                        admin['university_id'], m.get('section_id'), m.get('semester'),
                        metadata.get('academic_year'), m.get('day'), str(m.get('slot_number')),
                        m.get('course_abbr'), m.get('room')
                    ))
            pg_conn.commit()
            
        # 4d. Populate academic calendar
        elif metadata['document_type'] == 'academic_calendar':
            with pg_conn.cursor() as cur:
                for chunk in chunks:
                    m = chunk['metadata']
                    if m.get('granularity') == 'date':
                        cur.execute("""
                            INSERT INTO academic_calendar (university_id, academic_year, full_date, is_working, event_notes)
                            VALUES (%s, %s, %s, %s, %s)
                        """, (
                            admin['university_id'], metadata.get('academic_year'),
                            m.get('full_date'), m.get('is_working'),
                            f"{m.get('event_notes', '')} | Batches: {m.get('batch_events', '')}"
                        ))
            pg_conn.commit()
            
        # 5. Mark success
        with pg_conn.cursor() as cur:
            cur.execute("UPDATE documents SET status='indexed' WHERE id=%s", (doc_id,))
        pg_conn.commit()
        print(f"[OK] {doc_id}: {n} chunks indexed")
        
    except Exception as e:
        pg_conn.rollback()
        with pg_conn.cursor() as cur:
            cur.execute("UPDATE documents SET status=%s WHERE id=%s", (f"error:{e}", doc_id))
        pg_conn.commit()
        print(f"[ERR] {doc_id}: {e}")
    finally:
        release_pg_connection(pg_conn)
