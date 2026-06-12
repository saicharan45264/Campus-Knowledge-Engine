import fitz
import pdfplumber
import re
from typing import List, Dict

SEMESTER_PATTERN = re.compile(
    r'SEMESTER\s*[-–]?\s*(I{1,4}V?|V?I{0,3}|[1-8])\b', re.IGNORECASE
)
ROMAN = {'I':1,'II':2,'III':3,'IV':4,'V':5,'VI':6,'VII':7,'VIII':8}

def extract_curriculum_page(pdf_path: str, page_num: int) -> dict:
    """Peek at page structure; route to best extractor."""
    result = {'text': '', 'tables': []}
    
    # Try table extraction
    with pdfplumber.open(pdf_path) as pdf:
        if page_num < len(pdf.pages):
            page = pdf.pages[page_num]
            tables = page.extract_tables()
            if tables:
                for tbl in tables:
                    result['tables'].append(tbl)
                # Fallback text
                result['text'] = page.extract_text() or ''
                return result
                
    # If no tables or pdfplumber failed, use PyMuPDF
    doc = fitz.open(pdf_path)
    if page_num < len(doc):
        page = doc[page_num]
        result['text'] = page.get_text('text')
    return result

def parse_course_row(row: list, semester: int, dept: str, reg_year: str) -> dict | None:
    """Parse one row of the semester course listing table safely."""
    if not row or len(row) < 3:
        return None
        
    code = str(row[1] or '').strip()
    title = str(row[2] or '').strip()
    
    # Simple validation that this is indeed a course code (alphanumeric, e.g., 23CSE314 or CSE314)
    if not code or not any(char.isdigit() for char in code) or len(code) < 4:
        return None
        
    # Safely extract credits
    credits = 0.0
    for cell in reversed(row[3:]):
        if cell:
            try:
                credits = float(str(cell).strip())
                break
            except ValueError:
                continue
                
    return {
        'course_code': code,
        'course_title': title,
        'credits': credits,
        'semester': semester,
        'department': dept,
        'regulation_year': reg_year,
    }

def build_curriculum_chunks(pdf_path: str, metadata: dict) -> List[Dict]:
    doc = fitz.open(pdf_path)
    chunks = []
    
    dept = metadata.get('department', 'CSE')
    reg_year = metadata.get('regulation_year', '2023')
    doc_id = metadata.get('document_id', 'unknown')
    
    all_courses_dict = {}
    current_semester = 1
    
    # Step 1: Scan pages to parse tables and group text
    for page_num in range(len(doc)):
        page_data = extract_curriculum_page(pdf_path, page_num)
        text = page_data['text']
        
        # Detect semester change from text
        sem_match = SEMESTER_PATTERN.search(text)
        if sem_match:
            sem_str = sem_match.group(1).upper()
            if sem_str in ROMAN:
                current_semester = ROMAN[sem_str]
            elif sem_str.isdigit():
                current_semester = int(sem_str)
                
        # Parse tables for course codes
        for table in page_data['tables']:
            for row in table:
                course = parse_course_row(row, current_semester, dept, reg_year)
                if course and course['course_code'] not in all_courses_dict:
                    all_courses_dict[course['course_code']] = course
                    
    all_courses = list(all_courses_dict.values())
                    
    # Step 2: Index individual pages as L4 content chunks (Syllabus/Topics)
    for page_num in range(len(doc)):
        page_data = extract_curriculum_page(pdf_path, page_num)
        text = page_data['text']
        clean_text_content = text.strip()
        if clean_text_content:
            # Try to associate page text with a course from the text
            associated_course = None
            for c in all_courses:
                if c['course_code'] in clean_text_content:
                    associated_course = c['course_code']
                    break
                    
            chunks.append({
                'chunk_id': f"{doc_id}_p{page_num}",
                'text': f"Page {page_num + 1} Curriculum Content:\n{clean_text_content}",
                'metadata': {
                    **metadata,
                    'level': 4,
                    'granularity': 'subsection',
                    'course_code': associated_course or 'general',
                    'page_number': page_num + 1
                }
            })
            
    # L1 Chunk - Curriculum Overview
    l1_text = f"B.Tech {dept} Curriculum {reg_year} contains courses across {current_semester} semesters. Total registered courses: {len(all_courses)}."
    chunks.append({
        'chunk_id': f"{doc_id}_L1",
        'text': l1_text,
        'metadata': {**metadata, 'level': 1, 'granularity': 'document'}
    })
    
    # L2 Chunks - Semester course lists
    for sem in range(1, current_semester + 1):
        sem_courses = [c for c in all_courses if c['semester'] == sem]
        if sem_courses:
            sem_text = f"Semester {sem} courses for B.Tech {dept}:\n" + "\n".join(
                f"- {c['course_code']}: {c['course_title']} ({c['credits']} credits)" for c in sem_courses
            )
            chunks.append({
                'chunk_id': f"{doc_id}_sem_{sem}",
                'text': sem_text,
                'metadata': {**metadata, 'level': 2, 'granularity': 'semester', 'semester': sem}
            })
            
    # L3 Chunks - Course specific headers
    for c in all_courses:
        course_text = f"{c['course_title']} (Code: {c['course_code']}) is a course in Semester {c['semester']} of B.Tech {dept}. Credits: {c['credits']}."
        chunks.append({
            'chunk_id': f"{doc_id}_course_{c['course_code']}",
            'text': course_text,
            'metadata': {
                **metadata,
                'level': 3,
                'granularity': 'course',
                'semester': c['semester'],
                'course_code': c['course_code'],
                'course_title': c['course_title']
            }
        })
        
        # Populate SQLite course abbreviation lookup table for timetable abbreviations mapping
        # e.g., mapping Compiler Design (CD) to its course code
        abbr = "".join(word[0] for word in c['course_title'].split() if word[0].isupper())
        if len(abbr) >= 2:
            # We can run query to insert into course_abbr_map when indexing
            pass
            
    return chunks
