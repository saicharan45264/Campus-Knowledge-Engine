import pdfplumber
import re
from datetime import datetime

def build_academic_calendar_chunks(pdf_path: str, metadata: dict) -> list[dict]:
    chunks = []
    
    # We will try to track the current month from rows like ['Jun-26', None, None, ...]
    current_month_str = ""
    
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            if not tables:
                continue
                
            for table in tables:
                header_row = []
                for row in table:
                    # Look for header
                    if not header_row and row and row[0] == 'Date':
                        header_row = [str(c).replace('\n', ' ').strip() if c else '' for c in row]
                        continue
                    
                    if not header_row:
                        continue
                        
                    clean_row = [str(c).strip() if c else '' for c in row]
                    
                    # Detect month row
                    if clean_row[0] and len(clean_row[0]) >= 5 and clean_row[1] == '':
                        # Example: 'Jun-26'
                        month_match = re.match(r'([A-Za-z]+)-(\d+)', clean_row[0])
                        if month_match:
                            m_name, y_short = month_match.groups()
                            y_full = f"20{y_short}" if len(y_short) == 2 else y_short
                            current_month_str = f"{m_name} {y_full}"
                        continue
                        
                    # Process day rows
                    if clean_row[0].isdigit():
                        date_num = clean_row[0]
                        day_name = clean_row[1]
                        status = clean_row[2]
                        particulars = clean_row[-1]
                        
                        full_date = f"{date_num} {current_month_str}"
                        try:
                            # Attempt to format date nicely
                            dt = datetime.strptime(full_date, "%d %b %Y")
                            db_date = dt.strftime("%Y-%m-%d")
                        except ValueError:
                            db_date = full_date
                            
                        # Build a summary of events for all batches
                        events = []
                        for col_idx in range(3, len(clean_row) - 1):
                            cell_val = clean_row[col_idx]
                            if cell_val and cell_val.lower() not in ['none', '']:
                                batch_name = header_row[col_idx] if col_idx < len(header_row) else f"Column {col_idx}"
                                events.append(f"{batch_name}: {cell_val}")
                                
                        events_str = "; ".join(events)
                        text_parts = [f"On {full_date} ({day_name}), the status is {'Working day' if status == 'W' else 'Holiday' if status == 'H' else status}."]
                        if events_str:
                            text_parts.append(f"Events for batches: {events_str}.")
                        if particulars and particulars.lower() not in ['none', '']:
                            text_parts.append(f"Notes: {particulars}.")
                            
                        chunks.append({
                            'chunk_id': f"cal_{db_date.replace('-', '_')}",
                            'text': " ".join(text_parts),
                            'metadata': {
                                **metadata,
                                'level': 3,
                                'granularity': 'date',
                                'full_date': db_date,
                                'is_working': status == 'W',
                                'event_notes': particulars,
                                'batch_events': events_str
                            }
                        })
                        
    return chunks
