from fastapi import FastAPI
from api.routes import router as api_router

app = FastAPI(
    title="Campus Knowledge Engine API",
    description="Core backend for academic document RAG and timetable routing.",
    version="1.0.0"
)

# Include all module routes
app.include_router(api_router, prefix="/api/v1")

@app.get("/health")
def health_check():
    return {"status": "healthy", "modules": ["auth", "ingestion", "query", "lost_found"]}
