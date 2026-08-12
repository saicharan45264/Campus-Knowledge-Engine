# Redis Query Caching Execution Plan

**Purpose:** This document is the technical blueprint to implement an in-memory caching layer using Redis. This will drastically reduce GPU usage and response times for frequently asked questions (e.g., "What is the syllabus for Database Management?") by serving cached answers instantly.

---

## Phase 1: Infrastructure Setup (Docker)

We must add a lightweight Redis container to our existing Docker network.

**File: `docker-compose.yml`**
```yaml
  # Add this block under the services section
  redis:
    image: redis:7-alpine
    container_name: cl-redis
    restart: unless-stopped
    ports:
      - "6379:6379"
    command: redis-server --save 60 1 --loglevel warning
```

## Phase 2: Redis Connection Layer

We need to install the Redis client for Python and create a connection manager similar to our PostgreSQL `database.py`.

```bash
# Run inside the backend virtual environment
pip install redis
```

**Script: `backend/core/redis_client.py`**
```python
import redis
import os

# Create a Redis connection pool
redis_pool = redis.ConnectionPool(
    host=os.getenv("REDIS_HOST", "localhost"),
    port=6379,
    db=0,
    decode_responses=True # Automatically decodes byte strings to Python strings
)

def get_redis():
    """Returns a Redis client instance from the pool."""
    return redis.Redis(connection_pool=redis_pool)
```

## Phase 3: Query Normalization & Hashing

To ensure maximum cache hits, we must normalize the question (lowercase, strip punctuation) before hashing it.

**Script: `backend/services/cache.py`**
```python
import hashlib
import re

def normalize_query(query: str) -> str:
    """Removes punctuation, extra spaces, and converts to lowercase."""
    # Remove all non-alphanumeric characters except spaces
    clean = re.sub(r'[^\w\s]', '', query).lower()
    # Replace multiple spaces with a single space
    clean = re.sub(r'\s+', ' ', clean).strip()
    return clean

def generate_cache_key(query: str, course_context: str = "ALL") -> str:
    """Generates a unique SHA-256 hash for the normalized query."""
    normalized = normalize_query(query)
    # Include course context so answers for "Unit 1" don't get mixed across different courses
    raw_key = f"{course_context}:{normalized}"
    return "query_cache:" + hashlib.sha256(raw_key.encode('utf-8')).hexdigest()
```

## Phase 4: Backend Integration (Streaming Support)

We must intercept the `/chat` request in `server.py` *before* it hits the LLM. If the answer is in Redis, we stream it back instantly. If not, we generate it normally and save it to Redis.

**File: `backend/server.py` (Inside `do_POST` -> `if self.path == '/chat':`)**
```python
from core.redis_client import get_redis
from services.cache import generate_cache_key

# 1. Generate Cache Key
cache_key = generate_cache_key(message)
r = get_redis()

# 2. Check Cache
cached_answer = r.get(cache_key)

if cached_answer:
    # --- CACHE HIT ---
    self.send_response(200)
    self.send_header('Content-Type', 'text/plain')
    self.send_header('Transfer-Encoding', 'chunked')
    self.send_header('X-Cache', 'HIT')
    self.end_headers()
    
    # We simulate streaming for the UI so the frontend typing effect still works nicely
    # by splitting the cached answer into words
    words = cached_answer.split(' ')
    for i, word in enumerate(words):
        chunk = word + (" " if i < len(words) - 1 else "")
        encoded = chunk.encode('utf-8')
        self.wfile.write(f"{len(encoded):X}\r\n".encode('utf-8'))
        self.wfile.write(encoded + b"\r\n")
        self.wfile.flush()
        import time; time.sleep(0.01) # fast typing effect
    self.wfile.write(b"0\r\n\r\n")
    return

# --- CACHE MISS ---
# (Run standard LLM generation logic here)

# At the end of LLM generation, save the accumulated final text to Redis
# Store it with a TTL of 24 hours (86400 seconds)
r.setex(cache_key, 86400, final_accumulated_answer)
```

## Phase 5: Cache Invalidations (Admin Panel)

If an Admin uploads a new syllabus or deletes a document, the cached answers might become outdated. We must invalidate the cache when a database reset or document delete occurs.

**File: `backend/server.py` (Inside `do_POST` -> `if self.path == '/reset':`)**
```python
# Wipe the entire Redis query cache
r = get_redis()
for key in r.scan_iter("query_cache:*"):
    r.delete(key)
```
