from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv(override=True)

from src.api.routes import admin, student, auth
from src.db.connection import init_dbs

app = FastAPI(title="Campus Knowledge Engine", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"]
)

@app.on_event("startup")
async def startup():
    print("Initializing databases on startup...")
    init_dbs()
    print("Databases ready.")

app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(student.router)

from fastapi.staticfiles import StaticFiles
app.mount("/", StaticFiles(directory="frontend/dist", html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("src.api.main:app", host="0.0.0.0", port=8000, reload=True)
