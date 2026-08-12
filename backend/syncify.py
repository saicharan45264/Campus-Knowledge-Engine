import os
import re

files_to_sync = [
    "services/llm.py",
    "services/retrieval.py",
    "services/ingestion.py",
    "services/graph.py",
]

for file in files_to_sync:
    path = os.path.join(os.path.dirname(__file__), file)
    if not os.path.exists(path):
        continue
    with open(path, "r") as f:
        content = f.read()
    
    # Simple regex replacements
    content = content.replace("async def ", "def ")
    content = content.replace("await ", "")
    content = content.replace("async with ", "with ")
    content = content.replace("async for ", "for ")
    content = content.replace("httpx.AsyncClient", "httpx.Client")
    content = content.replace("aiter_lines", "iter_lines")
    content = content.replace("import asyncio\n", "import time\n")
    content = content.replace("asyncio.sleep", "time.sleep")
    
    # DB async to sync
    content = content.replace("db: AsyncSession", "db_conn")
    content = content.replace("AsyncSession", "Any") # Just in case

    with open(path, "w") as f:
        f.write(content)

print("Syncified!")
