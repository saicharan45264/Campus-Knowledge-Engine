import pdfplumber
import re

def build_timetable_chunks(pdf_path: str, metadata: dict) -> list[dict]:
    chunks = []
    
    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages):
            tables = page.extract_tables()
            if not tables:
                continue
                
            for table in tables:
                # Find header metadata (Department, Section, Semester, default room)
                dept = metadata.get('department', 'Unknown')
                section = metadata.get('section_id', 'Unknown')
                try:
                    semester = int(metadata.get('semester', 0) or 0)
                except ValueError:
                    semester = 0
                default_room = 'Unknown'
                doc_id = metadata.get('document_id', 'unknown')
                
                # Scan first few rows for metadata
                for row in table[:6]:
                    clean_row = [str(c).strip() for c in row if c]
                    if len(clean_row) >= 4 and 'B.TECH' in clean_row[0].upper():
                        dept = f"{clean_row[0]} {clean_row[1].split('-')[0]}"
                        section = clean_row[1]
                        sem_match = re.search(r'SEM-([A-Z0-9]+)', clean_row[2], re.IGNORECASE)
                        if sem_match:
                            sem_str = sem_match.group(1).upper()
                            roman_to_int = {'I': 1, 'II': 2, 'III': 3, 'IV': 4, 'V': 5, 'VI': 6, 'VII': 7, 'VIII': 8}
                            semester = roman_to_int.get(sem_str, int(sem_str) if sem_str.isdigit() else 0)
                        default_room = clean_row[3]
                        break
                
                # Find slots row
                slot_indices = {}
                start_row = 0
                for i, row in enumerate(table):
                    if any('Slot 1' in str(c) or 'Slot 2' in str(c) for c in row if c):
                        # This is the slot row
                        for col_idx, col_val in enumerate(row):
                            val = str(col_val).strip()
                            if val.startswith('Slot '):
                                slot_indices[col_idx] = val.replace('Slot ', '').strip()
                            elif val.isdigit():
                                slot_indices[col_idx] = val
                        start_row = i + 2
                        break
                    elif row and row[0] and 'Slot/Day' in str(row[0]):
                        for col_idx, col_val in enumerate(row[1:], start=1):
                            val = str(col_val).strip()
                            if val and val.isdigit():
                                slot_indices[col_idx] = val
                        start_row = i + 1
                        break
                        
                if not slot_indices:
                    continue
                    
                # Parse days
                for row in table[start_row:]:
                    if not row or not row[0]:
                        continue
                        
                    day_info = str(row[0]).strip()
                    if not any(d in day_info for d in ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']):
                        continue
                        
                    parts = day_info.split(':')
                    day = parts[0].strip()
                    room_for_day = parts[1].strip() if len(parts) > 1 else default_room
                    
                    last_cell = None
                    span_remaining = 0
                    
                    for col_idx, slot_num in slot_indices.items():
                        if col_idx < len(row):
                            cell = str(row[col_idx]).strip()
                            
                            # Handle merged cells heuristic
                            if not cell or cell.lower() in ['none', '', '-']:
                                if span_remaining > 0:
                                    cell = last_cell
                                    span_remaining -= 1
                            else:
                                if 'lab' in cell.lower() or 'project' in cell.lower():
                                    span_remaining = 1
                                else:
                                    span_remaining = 0
                                last_cell = cell

                            if cell and cell.lower() not in ['none', '', 'break', 'lunch', '-']:
                                # Some cells specify specific room like 'CIR : F-405'
                                course_abbr = cell
                                cell_room = room_for_day
                                if ':' in cell:
                                    cell_parts = cell.split(':')
                                    course_abbr = cell_parts[0].strip()
                                    cell_room = cell_parts[1].strip()
                                    
                                # Create chunk for this cell
                                text = f"On {day}, {dept} section {section} (Semester {semester}) has {course_abbr} in slot {slot_num} at room {cell_room}."
                                chunks.append({
                                    'chunk_id': f"{doc_id}_tt_{section}_{day}_{slot_num}",
                                    'text': text,
                                    'metadata': {
                                        **metadata,
                                        'level': 3,
                                        'granularity': 'slot',
                                        'department': dept,
                                        'section_id': section,
                                        'semester': semester,
                                        'day': day,
                                        'slot_number': slot_num,
                                        'course_abbr': course_abbr,
                                        'room': cell_room
                                    }
                                })
                                
    return chunks
