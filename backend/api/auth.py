"""backend/api/auth.py — Authentication, JWT token lifecycle & RBAC dependencies.

Implements database-backed Bcrypt password verification against the PostgreSQL
users table, cryptographic JWT token generation, and role-based route guards.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import bcrypt
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import ExpiredSignatureError, JWTError, jwt
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.database import StudentAccount, User, get_db

router = APIRouter(tags=["Authentication"])


class StudentRegistration(BaseModel):
    full_name: str = Field(min_length=2, max_length=120)
    age: int = Field(ge=15, le=100)
    gender: str = Field(min_length=1, max_length=40)
    roll_number: str = Field(min_length=3, max_length=40)
    college_email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=8, max_length=72)
    department: str = Field(min_length=2, max_length=20)
    study_year: str = Field(min_length=1, max_length=30)
    academic_year: str = Field(min_length=4, max_length=20)
    semester: str = Field(min_length=1, max_length=12)
    section: str = Field(min_length=1, max_length=20)

_JWT_SECRET = settings.jwt_secret
_JWT_ALGORITHM = settings.jwt_algorithm
_JWT_EXPIRE = settings.jwt_expire_minutes

# Fallback credentials matching login portal hints (stored hashed with bcrypt)
_FALLBACK_ACCOUNTS = {
    "student": {
        "hash": "$2b$12$di7DKNpnaQVRCdL75EzM4unk7a1DFng6Jk2edUtk0fh.VWS428kGq",
        "role": "student",
    },  # student123
    "admin": {
        "hash": "$2b$12$D85lJZx3i0u5w2xmDzwFvOrUWfgWoHGoXh0EJ47Im89fkdA8GLW5K",
        "role": "admin",
    },  # admin123
}


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Safely verify a plain password against a bcrypt hash."""
    try:
        # Bcrypt has a hard limit of 72 bytes
        pwd_bytes = plain_password.encode("utf-8")[:72]
        return bcrypt.checkpw(pwd_bytes, hashed_password.encode("utf-8"))
    except Exception:
        return False


def hash_password(plain_password: str) -> str:
    """Generate a standard bcrypt hash for a plain password."""
    # Bcrypt maximum password length is 72 bytes
    pwd_bytes = plain_password.encode("utf-8")[:72]
    return bcrypt.hashpw(pwd_bytes, bcrypt.gensalt(12)).decode("utf-8")


def _create_token(username: str, role: str) -> str:
    """Create a signed JWT token with expiration and role claims."""
    payload = {
        "sub": username,
        "role": role,
        "exp": datetime.now(UTC) + timedelta(minutes=_JWT_EXPIRE),
    }
    return jwt.encode(payload, _JWT_SECRET, algorithm=_JWT_ALGORITHM)


security_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security_scheme),
) -> dict:
    """
    Decodes and validates cryptographic JWT Bearer tokens.
    Raises HTTP 401 Unauthorized if token is missing, expired, or invalid.
    """
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=401,
            detail="Authentication token is missing. Please log in.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = credentials.credentials
    try:
        payload = jwt.decode(token, _JWT_SECRET, algorithms=[_JWT_ALGORITHM])
        username: str | None = payload.get("sub")
        role: str | None = payload.get("role")
        if not username or not role:
            raise HTTPException(
                status_code=401,
                detail="Invalid session token claims.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return {"username": username, "role": role}
    except ExpiredSignatureError:
        raise HTTPException(
            status_code=401,
            detail="Session has expired. Please log in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except (JWTError, Exception):
        raise HTTPException(
            status_code=401,
            detail="Could not validate credentials.",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def require_admin(user: dict = Depends(get_current_user)) -> dict:
    """Role-Based Access Control: Restricts access strictly to administrative roles."""
    if user.get("role") != "admin":
        raise HTTPException(
            status_code=403,
            detail="Access forbidden: Administrative privileges required.",
        )
    return user


@router.post("/login")
async def login(
    request: Request,
    username: str | None = Form(None),
    password: str | None = Form(None),
    role: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
):
    """
    Validates user credentials against the PostgreSQL users table with Bcrypt verification.
    Supports both URL-encoded form data and JSON payloads, enforcing selected portal role when provided.
    Returns a signed JWT bearer token containing user identity and role.
    """
    if not username or not password:
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            try:
                body_json = await request.json()
                if isinstance(body_json, dict):
                    username = body_json.get("username")
                    password = body_json.get("password")
                    role = role or body_json.get("role")
            except Exception:
                pass

    if not username or not password:
        raise HTTPException(status_code=400, detail="Username and password are required.")

    uname = username.strip().lower()

    stmt = select(User).where(User.username == uname)
    result = await db.execute(stmt)
    db_user = result.scalar_one_or_none()

    if db_user:
        if not verify_password(password, db_user.password_hash):
            raise HTTPException(status_code=401, detail="Incorrect username or password.")
        user_role = db_user.role
    elif uname in _FALLBACK_ACCOUNTS:
        if not verify_password(password, _FALLBACK_ACCOUNTS[uname]["hash"]):
            raise HTTPException(status_code=401, detail="Incorrect username or password.")
        user_role = _FALLBACK_ACCOUNTS[uname]["role"]
    else:
        raise HTTPException(status_code=401, detail="Incorrect username or password.")

    if role is not None and role.strip():
        expected_role = role.strip().lower()
        if expected_role not in ("student", "admin"):
            raise HTTPException(status_code=400, detail="Invalid login portal role selected.")
        if user_role != expected_role:
            target_btn = "Admin" if user_role == "admin" else "Student"
            raise HTTPException(
                status_code=403,
                detail=f"Please use the {target_btn} button to sign in with this account.",
            )

    token = _create_token(uname, user_role)
    return {"access_token": token, "token_type": "bearer", "role": user_role}


@router.post("/register")
async def register_student(payload: StudentRegistration, db: AsyncSession = Depends(get_db)):
    """Create a student login and its academic profile in one transaction."""
    email = payload.college_email.strip().lower()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise HTTPException(status_code=422, detail="Enter a valid college email address.")
    if len(email) > 50:
        raise HTTPException(status_code=422, detail="The email must be 50 characters or fewer for this login system.")
    semester_value = payload.semester.strip().upper()
    semester_value = re.sub(r"^SEM(?:ESTER)?\s*", "", semester_value)
    roman = {"1": "I", "2": "II", "3": "III", "4": "IV", "5": "V", "6": "VI", "7": "VII", "8": "VIII"}
    if semester_value in roman:
        semester_value = roman[semester_value]
    if semester_value not in set(roman.values()):
        raise HTTPException(status_code=422, detail="Semester must be from I to VIII.")

    user = User(username=email, password_hash=hash_password(payload.password), role="student")
    account = StudentAccount(
        full_name=payload.full_name.strip(),
        age=payload.age,
        gender=payload.gender.strip(),
        roll_number=payload.roll_number.strip().upper(),
        college_email=email,
        password_hash=user.password_hash,
        department=payload.department.strip().upper(),
        study_year=payload.study_year.strip(),
        academic_year=payload.academic_year.strip(),
        semester=semester_value,
        section=payload.section.strip().upper(),
    )
    db.add_all([user, account])
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="A student account already exists with this email or roll number.") from exc

    return {
        "access_token": _create_token(email, "student"),
        "token_type": "bearer",
        "role": "student",
        "username": email,
        "profile": {
            "full_name": account.full_name,
            "roll_number": account.roll_number,
            "department": account.department,
            "study_year": account.study_year,
            "academic_year": account.academic_year,
            "semester": account.semester,
            "section": account.section,
        },
    }
