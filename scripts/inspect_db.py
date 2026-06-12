import sys
from src.db.connection import get_pg_connection

def inspect_db():
    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM timetable_cells")
            tt_count = cur.fetchone()[0]
            print(f"Total timetable cells: {tt_count}")
            
            cur.execute("SELECT day, slot_number, course_abbr, room FROM timetable_cells LIMIT 5")
            for row in cur.fetchall():
                print("TT Cell:", row)
                
            cur.execute("SELECT count(*) FROM academic_calendar")
            cal_count = cur.fetchone()[0]
            print(f"Total academic calendar dates: {cal_count}")
            
            cur.execute("SELECT document_type, status FROM documents ORDER BY uploaded_at DESC LIMIT 5")
            for row in cur.fetchall():
                print("Document Status:", row)
                
    finally:
        conn.close()

if __name__ == "__main__":
    inspect_db()
