from fastapi import APIRouter, Depends
from pydantic import BaseModel

router = APIRouter(prefix="", tags=["student"])

class QueryRequest(BaseModel):
    query: str
    session_id: str

from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi import HTTPException
from src.api.auth_utils import decode_access_token
from src.db.connection import get_pg_connection, release_pg_connection

security = HTTPBearer()

def get_current_student(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    payload = decode_access_token(token)
    if not payload or payload.get("role") != "student":
        raise HTTPException(status_code=401, detail="Invalid or missing token")
    return payload

sessions_db = {}

def get_session(student_id: str, payload: dict):
    if student_id not in sessions_db:
        conn = get_pg_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT university_id, department, semester, section_id, regulation_year, academic_year FROM student_users WHERE id = %s", (student_id,))
                row = cur.fetchone()
                if not row:
                    raise HTTPException(status_code=404, detail="Student profile not found")
                sessions_db[student_id] = {
                    'university_id': str(row[0]),
                    'department': row[1],
                    'semester': row[2],
                    'section_id': row[3],
                    'regulation_year': row[4],
                    'academic_year': row[5],
                    'turn_history': []
                }
        finally:
            release_pg_connection(conn)
    return sessions_db[student_id]

@router.post("/query")
async def handle_query(request: QueryRequest, current_student: dict = Depends(get_current_student)):
    from src.query.router import process_student_query
    
    session = get_session(current_student['user_id'], current_student)
    result = process_student_query(request.query, session)
    return result

