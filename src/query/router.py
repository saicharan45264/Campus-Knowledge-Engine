import google.generativeai as genai
import os
import json

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
gemini_model = genai.GenerativeModel('gemini-3.5-flash')

def classify_intent(query: str) -> dict:
    """Zero-shot intent classification using Gemini"""
    prompt = f"""
    Classify the following academic student query into exactly ONE of the following 8 intents:
    1. SYLLABUS: Course content, units, topics, textbooks
    2. TIMETABLE: Schedule, slots, rooms, days
    3. CALENDAR: Dates, holidays, semester timeline
    4. REGULATION: Academic rules, policies, procedures (e.g. attendance, grading, CGPA)
    5. EVALUATION: Internal/external marks, exam patterns
    6. CO_PO: Course-Program Outcome mappings
    7. FACULTY: Advisor, instructor, contact info
    8. GENERAL: Cross-domain academic queries

    Query: "{query}"
    
    Respond ONLY with a valid JSON object in this exact format:
    {{"intent": "<INTENT_NAME>", "confidence": <float_between_0_and_1>}}
    """
    
    try:
        response = gemini_model.generate_content(prompt)
        text = response.text.strip()
        if text.startswith("```json"):
            text = text[7:-3]
        elif text.startswith("```"):
            text = text[3:-3]
        result = json.loads(text)
        return result
    except Exception as e:
        print(f"Error parsing intent: {e}")
        return {'intent': 'GENERAL', 'confidence': 0.5}

def extract_fields(query: str, fields: list[str]) -> dict:
    """Uses Gemini to extract specific academic fields from the user's query if present."""
    prompt = f"""
    Analyze the following user query and extract the values for these fields: {fields}
    
    Query: "{query}"
    
    If a field is not present or cannot be inferred from the query, set its value to null.
    For 'semester', convert word/Roman numeral numbers to integers (e.g. "sixth" or "VI" -> 6).
    For 'section_id', look for strings matching sections, typically like "CSE-A", "CSE-F AB3", "Sec-A", "Section B".
    For 'department', look for departments like "CSE", "ECE", "ME", etc.
    For 'regulation_year', look for years like "2023", "2019", "R23", "R19".
    
    Respond ONLY with a valid JSON object matching the keys in the fields list, for example:
    {{"section_id": "CSE-F AB3", "semester": 6}}
    """
    try:
        response = gemini_model.generate_content(prompt)
        text = response.text.strip()
        if text.startswith("```json"):
            text = text[7:-3]
        elif text.startswith("```"):
            text = text[3:-3]
        return json.loads(text)
    except Exception as e:
        print(f"Error extracting fields: {e}")
        return {}

def check_clarification_needed(intent: str, session: dict) -> tuple[str, str] | None:
    REQUIRED_FIELDS = {
        'SYLLABUS': ['department', 'semester', 'regulation_year'],
        'TIMETABLE': ['department', 'semester', 'section_id'],
        'CALENDAR': [], 
        'REGULATION': [], 
        'EVALUATION': ['department', 'regulation_year'],
        'CO_PO': ['department', 'regulation_year'],
        'FACULTY': ['department', 'section_id'],
        'GENERAL': [],
    }
    missing = [f for f in REQUIRED_FIELDS.get(intent, []) if not session.get(f)]
    if not missing: return None
    
    field = missing[0]
    templates = {
        'department': 'Which department are you in? (e.g., CSE, ECE, EEE)',
        'semester': 'Which semester are you currently in? (1-8)',
        'section_id': 'Which section are you in? (e.g., CSE-F AB3)',
        'regulation_year':'Which regulation year? (e.g., 2023)',
    }
    return templates.get(field, f'Please provide your {field}.'), field

def normalize_section_id(input_section: str, university_id: str) -> str:
    """Normalizes section input to match one of the section_ids stored in the database."""
    from src.db.connection import get_pg_connection, release_pg_connection
    import re
    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT section_id FROM timetable_cells WHERE university_id = %s", (university_id,))
            db_sections = [r[0] for r in cur.fetchall() if r[0]]
    except Exception as e:
        print(f"Error fetching sections for normalization: {e}")
        db_sections = []
    finally:
        release_pg_connection(conn)
        
    if not db_sections:
        return input_section
        
    val = input_section.strip().upper()
    
    # 1. Exact or direct match
    for db_sec in db_sections:
        if val == db_sec.upper():
            return db_sec
            
    # 2. Check if a database section name is contained within the user input (e.g. "CSE-F AB3" contains "CSE-F")
    for db_sec in db_sections:
        if db_sec.upper() in val:
            return db_sec
            
    # 3. Check if user input is contained within a database section (e.g. user typed "F" and database has "CSE-F")
    for db_sec in db_sections:
        parts = re.split(r'[-_\s]', db_sec.upper())
        if val in parts:
            return db_sec
            
    return input_section

def process_student_query(query: str, session: dict) -> dict:
    from .retriever import hybrid_retrieve
    from .generator import generate_answer, validate_answer
    
    # 0. Check pending clarification response
    pending = session.get('pending_clarification')
    intent = None
    if pending:
        field_to_extract = pending['field']
        extracted = extract_fields(query, [field_to_extract])
        if extracted.get(field_to_extract) is not None:
            val = extracted[field_to_extract]
            if field_to_extract == 'section_id':
                val = normalize_section_id(val, session['university_id'])
            session[field_to_extract] = val
            intent = pending['intent']
            query = pending.get('original_query', query)
            session.pop('pending_clarification', None)
        else:
            intent = None

    # 1. Classify intent if not restored from pending
    if not intent:
        intent_result = classify_intent(query)
        intent = intent_result.get('intent', 'GENERAL')
        confidence = intent_result.get('confidence', 0.5)
    else:
        confidence = 1.0
        
    # 2. Extract any missing fields for this intent from the query
    REQUIRED_FIELDS = {
        'SYLLABUS': ['department', 'semester', 'regulation_year'],
        'TIMETABLE': ['department', 'semester', 'section_id'],
        'CALENDAR': [], 
        'REGULATION': [], 
        'EVALUATION': ['department', 'regulation_year'],
        'CO_PO': ['department', 'regulation_year'],
        'FACULTY': ['department', 'section_id'],
        'GENERAL': [],
    }
    missing_for_intent = [f for f in REQUIRED_FIELDS.get(intent, []) if not session.get(f)]
    if missing_for_intent:
        extracted = extract_fields(query, missing_for_intent)
        for k, v in extracted.items():
            if v is not None:
                if k == 'section_id':
                    v = normalize_section_id(v, session['university_id'])
                session[k] = v
    
    # 3. Check clarification
    clarification_result = check_clarification_needed(intent, session)
    if clarification_result:
        clarification_text, missing_field = clarification_result
        session['pending_clarification'] = {
            'field': missing_field,
            'intent': intent,
            'original_query': query
        }
        return {'type': 'clarification', 'question': clarification_text, 'intent': intent}
        
    # 4. Retrieve chunks
    raw_chunks = hybrid_retrieve(query, intent, session)
    
    # 5. Check similarity (mock threshold check)
    if not raw_chunks:
        return {'type': 'no_data', 'answer': 'No academic data is available for this query.'}
        
    # 6. Generate answer
    answer = generate_answer(query, raw_chunks, session)
    
    # 7. Self-correct
    validated = validate_answer(query, answer, raw_chunks, intent)
    
    # Update history (in-memory mock)
    session['turn_history'].append({'query': query, 'intent': intent, 'answer': validated['answer']})
    
    return {
        'type': 'answer', 
        'answer': validated['answer'],
        'intent': intent, 
        'confidence': confidence,
        'chunks_used': len(raw_chunks)
    }

