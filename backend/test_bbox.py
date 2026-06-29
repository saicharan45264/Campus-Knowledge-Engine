import httpx
import base64
import json
import asyncio
import fitz
import sys
import os
sys.path.insert(0, os.getcwd())
from utils import OLLAMA_BASE_URL, OLLAMA_MODEL

async def test_vision():
    # Render page 3 of the known pdf
    doc = fitz.open("uploads/a1385f33-05dc-4491-adda-b10b6756eff9_23EEE104_I Sem Mid-Term_Odd 2024.pdf")
    pixmap = doc[2].get_pixmap(matrix=fitz.Matrix(2, 2))
    img_b64 = base64.b64encode(pixmap.tobytes("png")).decode("utf-8")
        
    prompt = """
You are extracting questions. For each question, estimate the vertical start and end position of the question AND its associated circuit diagram on the page, as a percentage from top (0) to bottom (100).
Return JSON:
{
  "questions": [
    {
      "text": "Determine V1 and V2...",
      "y_start_percent": 25,
      "y_end_percent": 50
    }
  ]
}
"""
    print("Asking Gemma...")
    async with httpx.AsyncClient() as client:
        res = await client.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "images": [img_b64],
                "format": "json",
                "stream": False,
                "options": {"num_ctx": 8192}
            },
            timeout=180.0
        )
        print(res.json().get("response"))

asyncio.run(test_vision())
