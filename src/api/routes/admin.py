import uuid
import shutil
import pathlib
from fastapi import APIRouter, UploadFile, File, Form, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional

from src.db.connection import get_pg_connection, release_pg_connection
from src.ingestion.indexer import process_document

router = APIRouter(prefix="/admin", tags=["admin"])

UPLOAD_DIR = pathlib.Path('./uploads')
UPLOAD_DIR.mkdir(exist_ok=True)

from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from src.api.auth_utils import decode_access_token

security = HTTPBearer()

def get_current_admin(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    payload = decode_access_token(token)
    if not payload or payload.get("role") != "admin":
        raise HTTPException(status_code=401, detail="Invalid or missing token")
    return {'university_id': payload.get("university_id")}
@router.post("/documents/upload")
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    document_type: str = Form(...),
    department: Optional[str] = Form(None),
    semester: Optional[int] = Form(None),
    section_id: Optional[str] = Form(None),
    regulation_year: Optional[str] = Form(None),
    academic_year: Optional[str] = Form(None),
    admin: dict = Depends(get_current_admin)
):
    metadata = {
        'document_type': document_type,
        'department': department,
        'semester': semester,
        'section_id': section_id,
        'regulation_year': regulation_year,
        'academic_year': academic_year
    }
    
    doc_id = str(uuid.uuid4())
    file_path = UPLOAD_DIR / f"{doc_id}.pdf"
    
    with open(file_path, "wb") as f:
        shutil.copyfileobj(file.file, f)
        
    pg_conn = get_pg_connection()
    try:
        with pg_conn.cursor() as cur:
            cur.execute("""
                INSERT INTO documents (id, university_id, document_type, department, 
                semester, section_id, regulation_year, academic_year, file_path, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                doc_id, admin['university_id'], metadata['document_type'],
                metadata.get('department'), metadata.get('semester'),
                metadata.get('section_id'), metadata.get('regulation_year'),
                metadata.get('academic_year'), str(file_path), 'pending'
            ))
        pg_conn.commit()
    finally:
        release_pg_connection(pg_conn)
        
    background_tasks.add_task(process_document, doc_id, str(file_path), metadata, admin)
    
    return {'doc_id': doc_id, 'status': 'queued'}

@router.get("/documents")
async def get_documents(admin: dict = Depends(get_current_admin)):
    pg_conn = get_pg_connection()
    try:
        with pg_conn.cursor() as cur:
            cur.execute("""
                SELECT id, document_type, department, semester, section_id, regulation_year, academic_year, status, uploaded_at
                FROM documents
                WHERE university_id = %s
                ORDER BY uploaded_at DESC
            """, (admin['university_id'],))
            
            docs = []
            for row in cur.fetchall():
                docs.append({
                    "id": str(row[0]),
                    "document_type": row[1],
                    "department": row[2],
                    "semester": row[3],
                    "section_id": row[4],
                    "regulation_year": row[5],
                    "academic_year": row[6],
                    "status": row[7],
                    "uploaded_at": row[8].isoformat() if row[8] else None
                })
            return {"documents": docs}
    finally:
        release_pg_connection(pg_conn)

@router.delete("/documents/{doc_id}")
async def delete_document(doc_id: str, admin: dict = Depends(get_current_admin)):
    pg_conn = get_pg_connection()
    try:
        with pg_conn.cursor() as cur:
            # First verify the document belongs to this university
            cur.execute("SELECT file_path FROM documents WHERE id = %s AND university_id = %s", (doc_id, admin['university_id']))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Document not found")
                
            file_path = row[0]
            
            # Delete record
            cur.execute("DELETE FROM documents WHERE id = %s", (doc_id,))
            
            # Delete physical file
            path_obj = pathlib.Path(file_path)
            if path_obj.exists():
                path_obj.unlink()
                
        pg_conn.commit()
        return {"status": "success", "message": "Document deleted successfully"}
    finally:
        release_pg_connection(pg_conn)

@router.get("/students")
async def get_students(admin: dict = Depends(get_current_admin)):
    pg_conn = get_pg_connection()
    try:
        with pg_conn.cursor() as cur:
            cur.execute("""
                SELECT id, name, college_mail, department, semester, section_id, regulation_year, academic_year, created_at
                FROM student_users
                WHERE university_id = %s
                ORDER BY created_at DESC
            """, (admin['university_id'],))
            
            students = []
            for row in cur.fetchall():
                students.append({
                    "id": str(row[0]),
                    "name": row[1],
                    "college_mail": row[2],
                    "department": row[3],
                    "semester": row[4],
                    "section_id": row[5],
                    "regulation_year": row[6],
                    "academic_year": row[7],
                    "created_at": row[8].isoformat() if row[8] else None
                })
            return {"students": students}
    finally:
        release_pg_connection(pg_conn)
