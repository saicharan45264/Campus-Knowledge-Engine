# syntax=docker/dockerfile:1
FROM python:3.11-slim

# Prevent Python from buffering stdout/stderr and writing .pyc files
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    HEADLESS_MODE=true \
    APP_ENV=production \
    PYTHONPATH=/app

WORKDIR /app

# Install system dependencies required by PyMuPDF, image processing, and health checks
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libgl1 \
    libglib2.0-0 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first for optimal Docker layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy backend application, evaluation scripts, and frontend assets (served at /public)
COPY backend/ ./backend/
COPY frontend/ ./frontend/
COPY scripts/ ./scripts/
COPY tests/ ./tests/
COPY pytest.ini ./

# Create uploads directory structure
RUN mkdir -p /app/backend/uploads/images

# Expose FastAPI application port
EXPOSE 8000

# Container healthcheck ensuring API is responsive
HEALTHCHECK --interval=15s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Start FastAPI application with production Uvicorn runner
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--access-log"]
