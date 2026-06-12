from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
import datetime

from src.db.connection import get_pg_connection, release_pg_connection
from src.api.auth_utils import verify_password, get_password_hash, create_access_token, ACCESS_TOKEN_EXPIRE_MINUTES

router = APIRouter(prefix="/auth", tags=["auth"])

class StudentRegisterRequest(BaseModel):
    name: str
    college_mail: str
    password: str
    department: str
    semester: int
    section_id: str
    regulation_year: str
    academic_year: str

class LoginRequest(BaseModel):
    email_or_username: str
    password: str

class ResetPasswordRequest(BaseModel):
    college_mail: str
    old_password: str
    new_password: str

@router.post("/student/register")
async def register_student(request: StudentRegisterRequest):
    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            # Check if email exists
            cur.execute("SELECT id FROM student_users WHERE college_mail = %s", (request.college_mail,))
            if cur.fetchone():
                raise HTTPException(status_code=400, detail="Email already registered")
            
            # For Phase 2, assign to the first university we find
            cur.execute("SELECT id FROM universities LIMIT 1")
            univ = cur.fetchone()
            if not univ:
                raise HTTPException(status_code=500, detail="System not initialized with a university")
            university_id = univ[0]

            hashed_pw = get_password_hash(request.password)
            cur.execute("""
                INSERT INTO student_users (university_id, name, college_mail, password_hash, department, semester, section_id, regulation_year, academic_year)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (university_id, request.name, request.college_mail, hashed_pw, request.department, request.semester, request.section_id, request.regulation_year, request.academic_year))
        conn.commit()
        return {"status": "success", "message": "Student registered successfully"}
    finally:
        release_pg_connection(conn)

@router.post("/student/login")
async def login_student(request: LoginRequest):
    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, university_id, password_hash, department, semester, section_id, regulation_year, academic_year 
                FROM student_users WHERE college_mail = %s
            """, (request.email_or_username,))
            user = cur.fetchone()
            if not user or not verify_password(request.password, user[2]):
                raise HTTPException(status_code=401, detail="Invalid credentials")
            
            access_token_expires = datetime.timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
            token_data = {
                "sub": request.email_or_username,
                "user_id": str(user[0]),
                "university_id": str(user[1]),
                "role": "student"
            }
            token = create_access_token(data=token_data, expires_delta=access_token_expires)
            
            return {
                "access_token": token,
                "token_type": "bearer",
                "student_profile": {
                    "department": user[3],
                    "semester": user[4],
                    "section_id": user[5],
                    "regulation_year": user[6],
                    "academic_year": user[7]
                }
            }
    finally:
        release_pg_connection(conn)

@router.post("/student/reset-password")
async def reset_password(request: ResetPasswordRequest):
    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, password_hash FROM student_users WHERE college_mail = %s", (request.college_mail,))
            user = cur.fetchone()
            if not user or not verify_password(request.old_password, user[1]):
                raise HTTPException(status_code=401, detail="Invalid credentials or old password")
            
            new_hashed_pw = get_password_hash(request.new_password)
            cur.execute("UPDATE student_users SET password_hash = %s WHERE id = %s", (new_hashed_pw, user[0]))
        conn.commit()
        return {"status": "success", "message": "Password reset successfully"}
    finally:
        release_pg_connection(conn)

@router.post("/admin/login")
async def login_admin(request: LoginRequest):
    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, university_id, password_hash FROM admin_users WHERE username = %s", (request.email_or_username,))
            user = cur.fetchone()
            if not user or not verify_password(request.password, user[2]):
                raise HTTPException(status_code=401, detail="Invalid admin credentials")
            
            access_token_expires = datetime.timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
            token_data = {
                "sub": request.email_or_username,
                "user_id": str(user[0]),
                "university_id": str(user[1]),
                "role": "admin"
            }
            token = create_access_token(data=token_data, expires_delta=access_token_expires)
            return {"access_token": token, "token_type": "bearer"}
    finally:
        release_pg_connection(conn)
