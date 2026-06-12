from src.ingestion import indexer
from src.ingestion.indexer import get_or_create_collection
from src.db.connection import get_sqlite_connection
import re

def intent_to_doctype(intent: str) -> str:
    mapping = {
        'SYLLABUS': 'curriculum',
        'TIMETABLE': 'timetable',
        'CALENDAR': 'academic_calendar',
        'REGULATION': 'regulations',
        'EVALUATION': 'curriculum',
        'CO_PO': 'curriculum',
        'FACULTY': 'timetable',
        'GENERAL': 'regulations' # fallback
    }
    return mapping.get(intent, 'regulations')

def build_metadata_filter(intent: str, session: dict) -> dict:
    doc_type = intent_to_doctype(intent)
    filters = [{"university_id": session['university_id']}]
    
    if doc_type == 'curriculum':
        if session.get('department'):
            filters.append({"department": session['department']})
    elif doc_type == 'timetable':
        if session.get('department'):
            filters.append({"department": session['department']})
        if session.get('section_id'):
            filters.append({"section_id": session['section_id']})
        if session.get('semester'):
            try:
                filters.append({"semester": int(session['semester'])})
            except Exception:
                pass
                
    if len(filters) == 1:
        return filters[0]
    return {"$and": filters}

def detect_day(query: str) -> str | None:
    from datetime import datetime, timedelta
    query_lower = query.lower()
    
    if 'today' in query_lower:
        return datetime.today().strftime('%A')
    if 'tomorrow' in query_lower:
        return (datetime.today() + timedelta(days=1)).strftime('%A')
        
    days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
    for day in days:
        if day.lower() in query_lower:
            return day
    return None

def query_timetable_db(session: dict, day: str) -> list[dict]:
    from src.db.connection import get_pg_connection, release_pg_connection
    conn = get_pg_connection()
    chunks = []
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT day, slot_number::int, course_abbr, room 
                FROM timetable_cells 
                WHERE university_id = %s AND section_id = %s AND day = %s
                ORDER BY slot_number::int
            """, (session['university_id'], session.get('section_id'), day))
            rows = cur.fetchall()
            for r in rows:
                text = f"On {r[0]}, Section {session.get('section_id')} has {r[2]} in slot {r[1]} at room {r[3]}."
                chunks.append({
                    'chunk_id': f"sql_tt_{session.get('section_id')}_{r[0]}_{r[1]}",
                    'text': text,
                    'score': 1.0
                })
    except Exception as e:
        print(f"Error querying timetable cells: {e}")
    finally:
        release_pg_connection(conn)
    return chunks

def hybrid_retrieve(query: str, intent: str, session: dict, n_semantic=10, n_bm25=15, n_final=8) -> list[dict]:
    # Increase limits for CALENDAR to fetch all holidays
    if intent == 'CALENDAR':
        n_semantic = 60
        n_bm25 = 150
        n_final = 40

    # If timetable query mentions a day, pull directly from SQL first
    sql_chunks = []
    if intent == 'TIMETABLE' and session.get('section_id'):
        day = detect_day(query)
        if day:
            sql_chunks = query_timetable_db(session, day)
            
    doc_type = intent_to_doctype(intent)
    collection = get_or_create_collection(session['university_id'], doc_type)
    
    sem_chunks = []
    
    # Load embedder dynamically if needed
    if not indexer.embedder:
        from sentence_transformers import SentenceTransformer
        try:
            indexer.embedder = SentenceTransformer('all-MiniLM-L6-v2')
        except Exception as e:
            print(f"Failed to load sentence-transformer during retrieval: {e}")
            
    if indexer.embedder:
        query_embed = indexer.embedder.encode(query, normalize_embeddings=True).tolist()
        where_filter = build_metadata_filter(intent, session)
        
        sem_results = collection.query(
            query_embeddings=[query_embed],
            n_results=n_semantic,
            where=where_filter
        )
        if sem_results['ids'] and sem_results['ids'][0]:
            sem_chunks = list(zip(sem_results['ids'][0], sem_results['documents'][0], sem_results['distances'][0]))
            
    # BM25 Search
    conn = get_sqlite_connection()
    try:
        bm25_chunks = []
        with conn:
            # Add simple wildcard matching for BM25 - Strip punctuation to avoid fts5 errors
            query_clean = re.sub(r'[^\w\s]', '', query)
            stopwords = {'what', 'is', 'the', 'for', 'how', 'to', 'in', 'of', 'on', 'at', 'and', 'a', 'an'}
            
            fts_terms = []
            for word in query_clean.split():
                if len(word) >= 2 and word.lower() not in stopwords:
                    stemmed = word[:-1] if word.lower().endswith('s') else word
                    fts_terms.append(f"{stemmed}*")
            fts_query = " OR ".join(fts_terms)
            
            if not fts_query: fts_query = query_clean
            
            fts_rows = conn.execute(
                'SELECT chunk_id, content, rank FROM document_fts WHERE content MATCH ? '
                'AND university_id=? AND doc_type=? ORDER BY rank LIMIT ?',
                (fts_query, session['university_id'], doc_type, n_bm25)
            ).fetchall()
            bm25_chunks = [(r['chunk_id'], r['content'], r['rank']) for r in fts_rows]
            
            # Filter FTS results for timetable to matching section
            if intent == 'TIMETABLE' and session.get('section_id'):
                sec = session['section_id'].lower()
                bm25_chunks = [c for c in bm25_chunks if sec in c[1].lower()]
    except Exception as e:
        print(f"BM25 Search error: {e}")
    finally:
        conn.close()
        
    # Reciprocal Rank Fusion (RRF)
    def rrf_score(rank: int, k: int = 60) -> float:
        return 1.0 / (k + rank + 1)
        
    scores = {}
    text_map = {}
    
    for rank, (cid, text, _) in enumerate(sem_chunks):
        scores[cid] = scores.get(cid, 0) + rrf_score(rank)
        text_map[cid] = text
        
    for rank, (cid, text, _) in enumerate(bm25_chunks):
        scores[cid] = scores.get(cid, 0) + rrf_score(rank)
        text_map[cid] = text
        
    # Boost calendar chunks if the query mentions a specific month
    if intent == 'CALENDAR':
        months = ['january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september', 'october', 'november', 'december']
        short_months = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec']
        query_lower = query.lower()
        mentioned_months = [m for m in short_months if m in query_lower]
        if mentioned_months:
            for cid in scores:
                text_lower = text_map[cid].lower()
                if any(m in text_lower for m in mentioned_months):
                    scores[cid] += 5.0 # Huge boost for month match
                    
    sorted_ids = sorted(scores.keys(), key=lambda k: scores[k], reverse=True)[:n_final]
    
    results = [{'chunk_id': cid, 'text': text_map[cid], 'score': scores[cid]} for cid in sorted_ids]
    
    # If we have direct SQL results, prepend them to the retriever output
    if sql_chunks:
        # Avoid duplication if any SQL chunk is also in results
        existing_texts = {r['text'] for r in results}
        unique_sql = []
        for chunk in sql_chunks:
            if chunk['text'] not in existing_texts:
                unique_sql.append(chunk)
                existing_texts.add(chunk['text'])
        # Prepend unique SQL chunks
        results = unique_sql + results
                
    return results

