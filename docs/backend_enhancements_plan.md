# Backend Infrastructure & Security Execution Plan

**Purpose:** This document serves as the technical blueprint for elevating the Vanilla Python backend to production-grade enterprise standards. Implementing these features proves mastery in System Design, Cybersecurity, Observability, and DevOps.

---

## Phase 1: Microservices Dockerization

Currently, the backend runs natively on the host OS. We will containerize it so the entire stack boots with a single command.

**1. Create `backend/Dockerfile`**
```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for psycopg2 and PyMuPDF
RUN apt-get update && apt-get install -y gcc libpq-dev mupdf mupdf-tools && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000
CMD ["python", "server.py"]
```

**2. Update `docker-compose.yml`**
```yaml
  api:
    build: ./backend
    container_name: cl-backend
    restart: unless-stopped
    ports:
      - "8000:8000"
    depends_on:
      - postgres
      - neo4j
      - redis
    environment:
      - DB_HOST=postgres
      - NEO4J_URI=bolt://neo4j:7687
      - REDIS_HOST=redis
```

---

## Phase 2: Semantic Caching (Redis)

*(Detailed specifically in `redis_caching_plan.md`)*

**Summary:** 
Add a Redis container to `docker-compose.yml`. In `server.py`, normalize and SHA-256 hash incoming user queries. If the hash exists in Redis, bypass the LLM entirely and return the cached answer with a `X-Cache: HIT` header to drastically reduce GPU compute time.

---

## Phase 3: Token Bucket Rate Limiting (Security)

To prevent Denial of Service (DoS) attacks on the Google Colab GPU, we will implement a lightweight in-memory rate limiter for the `/chat` endpoint.

**Script: `backend/core/rate_limiter.py`**
```python
import time

class RateLimiter:
    def __init__(self, max_requests=5, window_seconds=60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.users = {}

    def is_allowed(self, user_id: str) -> bool:
        current_time = time.time()
        
        # Initialize user if not exists
        if user_id not in self.users:
            self.users[user_id] = []
            
        # Filter out old requests outside the window
        self.users[user_id] = [req_time for req_time in self.users[user_id] 
                               if current_time - req_time < self.window_seconds]
        
        if len(self.users[user_id]) < self.max_requests:
            self.users[user_id].append(current_time)
            return True
            
        return False

# Global Singleton
chat_limiter = RateLimiter(max_requests=5, window_seconds=60)
```

**Integration in `server.py` (`/chat` logic):**
```python
from core.rate_limiter import chat_limiter

if self.path == '/chat':
    user = self.get_auth_user()
    if not chat_limiter.is_allowed(user['username']):
        self.send_error_json(429, "Too Many Requests. Please wait 60 seconds.")
        return
```

---

## Phase 4: Automated Testing (Test-Driven Development)

Academic panels look for rigorous software engineering practices. We will use `pytest` to verify our security layer without needing the server running.

**1. Create `backend/tests/test_security.py`**
```python
import pytest
from core.security import get_password_hash, verify_password, create_access_token

def test_password_hashing():
    password = "supersecret"
    hashed = get_password_hash(password)
    assert hashed != password
    assert verify_password(password, hashed) == True
    assert verify_password("wrongpass", hashed) == False

def test_jwt_creation():
    token = create_access_token({"sub": "student1"})
    assert isinstance(token, str)
    assert len(token) > 20 # Basic structural check
```

**Run via:** `pytest tests/`

---

## Phase 5: Production Logging & Observability

Standard `print()` statements are insufficient for production. We will implement structured logging to a file.

**Script: `backend/core/logger.py`**
```python
import logging
import os

os.makedirs('logs', exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [%(levelname)s] - %(message)s',
    handlers=[
        logging.FileHandler("logs/server.log"),
        logging.StreamHandler() # Also print to console
    ]
)

logger = logging.getLogger("CurriculumLens")
```

**Integration in `server.py`:**
Replace `print("Starting server...")` with:
```python
from core.logger import logger

logger.info("Starting Vanilla Python Server on port 8000...")
# Inside except Exception blocks:
logger.error(f"Internal Server Error: {str(e)}")
```
