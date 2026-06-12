import fitz
import pdfplumber
import re

# Regulation section boundary patterns
REG_MAIN = re.compile(r'^R\.([0-9]+)\.\s+(.+)', re.MULTILINE)
REG_SUB = re.compile(r'^R\.([0-9]+)\.([0-9]+)\s+(.+)', re.MULTILINE)

TOPIC_MAP = {
    'R.1':'admission', 'R.2':'instruction', 'R.3':'programme_structure',
    'R.4':'fees', 'R.5':'counsellors', 'R.6':'course_mentors',
    'R.9':'registration', 'R.10':'course_drop', 'R.11':'duration',
    'R.12':'attendance', 'R.13':'assessment', 'R.14':'publication',
    'R.15':'remedial', 'R.16':'grading', 'R.17':'results',
    'R.18':'revaluation', 'R.19':'completion',
    'R.20':'sgpa', 'R.21':'cgpa', 'R.22':'ranking',
    'R.24':'classification', 'R.25':'minor_program',
    'R.27':'discipline', 'R.29':'degree_award'
}

def extract_regulations(pdf_path: str) -> str:
    """Extract full text from regulations PDF"""
    doc = fitz.open(pdf_path)
    full_text = ""
    for page in doc:
        full_text += page.get_text("text") + "\n"
    return full_text

def build_regulation_tree(full_text: str) -> dict:
    """Parse regulation text into a hierarchy: {R.12: {title, text, subsections: {R.12.1: ...}}}"""
    tree = {}
    
    # Split on main regulation boundaries
    sections = re.split(r'\n(?=R\.\d+\.\s)', full_text)
    
    for section in sections:
        main_match = REG_MAIN.match(section.strip())
        if main_match:
            reg_id = f"R.{main_match.group(1)}"
            tree[reg_id] = {
                'title': main_match.group(2).strip(),
                'full_text': section.strip(),
                'topic': TOPIC_MAP.get(reg_id, 'general'),
                'subsections': {}
            }
            
            # Find subsections within this section
            for sub in REG_SUB.finditer(section):
                sub_id = f"R.{sub.group(1)}.{sub.group(2)}"
                tree[reg_id]['subsections'][sub_id] = sub.group(3).strip()
                
    return tree

def build_regulation_chunks(tree: dict, metadata: dict) -> list[dict]:
    """Build L1, L2, L3 chunks from regulation tree"""
    chunks = []
    
    reg_year = metadata.get('regulation_year', 'unknown')
    
    # Optional L1 overview chunk
    l1_text = "B.Tech Regulations covers topics including " + ", ".join(t['topic'] for t in tree.values() if t['topic'] != 'general')
    chunks.append({
        'chunk_id': f"regulations_{reg_year}_L1",
        'text': l1_text,
        'metadata': {**metadata, 'level': 1, 'topic': 'overview', 'granularity': 'document'}
    })
    
    # L2 and L3
    for reg_id, content in tree.items():
        topic = content['topic']
        title = content['title']
        
        # L2
        l2_text = f"Regulation {reg_id} {title}:\n{content['full_text']}"
        chunks.append({
            'chunk_id': f"reg_{reg_year}_{reg_id.replace('.', '_')}",
            'text': l2_text,
            'metadata': {**metadata, 'level': 2, 'topic': topic, 'regulation_id': reg_id, 'granularity': 'section'}
        })
        
        # L3
        for sub_id, sub_text in content['subsections'].items():
            l3_text = f"{reg_id} {title} > {sub_id}:\n{sub_text}"
            chunks.append({
                'chunk_id': f"reg_{reg_year}_{sub_id.replace('.', '_')}",
                'text': l3_text,
                'metadata': {**metadata, 'level': 3, 'topic': topic, 'regulation_id': reg_id, 'granularity': 'subsection'}
            })
            
    # Add Grading Table Chunk manually for now
    grading_chunk = (
        'B.Tech grading system: O=Outstanding(10pts), A+=Excellent(9.5pts), '
        'A=Very Good(9pts), B+=Good(8pts), B=Above Average(7pts), C=Average(6pts), '
        'P=Pass(5pts), F=Fail(0pts), FA=Failed due to Attendance Shortage(0pts). '
        'SGPA = Sum(Credits x GradePoints) / Sum(Credits). '
        'First Class with Distinction requires CGPA >= 8.00 within 8 semesters with one Scopus publication.'
    )
    chunks.append({
        'chunk_id': f"reg_{reg_year}_grading_table",
        'text': grading_chunk,
        'metadata': {**metadata, 'level': 3, 'topic': 'grading', 'regulation_id': 'R.16', 'granularity': 'subsection'}
    })
    
    return chunks
