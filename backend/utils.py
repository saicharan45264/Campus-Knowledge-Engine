import base64
import io
import json
import os

import httpx
from dotenv import load_dotenv
from embedder import embed_text as _cached_embed_text

# Load environment variables from the .env file at the project root.
# os.path.dirname(__file__) gives us the 'backend/' folder.
# '..' moves us one level up to the root folder where '.env' is stored.
load_dotenv(
    dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"), override=True
)

# PyMuPDF (imported as 'fitz') is the library we use to read text out of PDF files.
# We wrap it in a try/except block just in case it fails to install on some machines.
try:
    import pymupdf as fitz
except ImportError:
    try:
        import fitz
    except ImportError:
        fitz = None

# -----------------------------------------------------------------------------
# Ollama Configuration
# -----------------------------------------------------------------------------
from config import settings

# Fetch the base URL where Ollama is running (defaults to localhost:11434).
OLLAMA_BASE_URL = settings.ollama_base_url

# This large model (12 billion parameters) is used ONLY for generating the final written answers.
OLLAMA_MODEL = settings.ollama_model

# This small, fast model (137 million parameters) is used ONLY to convert text into math vectors.
OLLAMA_EMBED_MODEL = settings.ollama_embed_model

# -----------------------------------------------------------------------------
# Cloudinary Configuration
# -----------------------------------------------------------------------------
try:
    import cloudinary
    import cloudinary.api
    import cloudinary.uploader

    CLOUDINARY_CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD_NAME", "")
    CLOUDINARY_API_KEY = os.getenv("CLOUDINARY_API_KEY", "")
    CLOUDINARY_API_SECRET = os.getenv("CLOUDINARY_API_SECRET", "")
    if CLOUDINARY_CLOUD_NAME:
        cloudinary.config(
            cloud_name=CLOUDINARY_CLOUD_NAME,
            api_key=CLOUDINARY_API_KEY,
            api_secret=CLOUDINARY_API_SECRET,
            secure=True,
        )
        print(f"[Cloudinary] Configured for cloud: {CLOUDINARY_CLOUD_NAME}")
    else:
        print("[Cloudinary] No credentials set — images will be saved locally.")
except ImportError:
    cloudinary = None
    CLOUDINARY_CLOUD_NAME = ""
    print("[Cloudinary] Package not installed — images will be saved locally.")


def upload_question_image_to_cloudinary(image_bytes: bytes, public_id: str) -> str:
    """
    Uploads a cropped question image to Cloudinary and returns its secure HTTPS URL.
    If Cloudinary is not configured, falls back to saving the image locally and
    returns a localhost URL instead — so the system never crashes.

    Args:
        image_bytes: Raw JPEG bytes of the cropped question image.
        public_id:   A unique identifier string used as the Cloudinary asset name.

    Returns:
        A publicly accessible HTTPS URL (Cloudinary) or a localhost URL (fallback).
    """
    if cloudinary and CLOUDINARY_CLOUD_NAME:
        try:
            buf = io.BytesIO(image_bytes)
            result = cloudinary.uploader.upload(
                buf,
                public_id=f"campus_ke/{public_id}",
                resource_type="image",
                overwrite=True,
                transformation=[{"fetch_format": "auto", "quality": "auto"}],
            )
            url = result.get("secure_url", "")
            print(f"[Cloudinary] Uploaded: {url}")
            return url
        except Exception as e:
            print(f"[Cloudinary] Upload failed ({e}), falling back to local storage.")

    # --- Local fallback ---
    filename = f"{public_id}.jpg"
    local_path = f"uploads/images/{filename}"
    os.makedirs("uploads/images", exist_ok=True)
    with open(local_path, "wb") as f:
        f.write(image_bytes)
    return f"http://localhost:8000/static/images/{filename}"


def delete_document_images_from_cloudinary(document_id: str):
    """
    Deletes all Cloudinary resources starting with the document prefix.
    """
    if cloudinary and CLOUDINARY_CLOUD_NAME:
        try:
            prefix = f"campus_ke/{document_id}"
            res = cloudinary.api.delete_resources_by_prefix(prefix)
            print(f"[Cloudinary] Deleted resources with prefix '{prefix}': {res}")
        except Exception as e:
            print(f"[Cloudinary] Failed to delete resources for doc {document_id}: {e}")


def delete_all_images_from_cloudinary():
    """
    Deletes all Cloudinary resources in the campus_ke/ folder.
    """
    if cloudinary and CLOUDINARY_CLOUD_NAME:
        try:
            prefix = "campus_ke/"
            res = cloudinary.api.delete_resources_by_prefix(prefix)
            print(f"[Cloudinary] Wiped all resources with prefix '{prefix}': {res}")
        except Exception as e:
            print(f"[Cloudinary] Failed to wipe all Cloudinary resources: {e}")


# 1. PDF Processing — Split a PDF file into small text chunks
def process_pdf(file_path: str) -> list[str]:
    """
    Opens a PDF file, reads every page, and splits the text into individual paragraphs.
    Returning small 'chunks' of text makes it easier for the AI to find specific answers later.
    """
    if not fitz:
        print("Error: PyMuPDF is not installed. Cannot process the PDF.")
        return []

    chunks = []
    try:
        # Open the PDF file
        doc = fitz.open(file_path)

        # Loop through every page in the document
        for page_num in range(len(doc)):
            # Extract all raw text from the current page
            page_text = doc[page_num].get_text("text")

            # Split the page text by double newlines to separate paragraphs
            paragraphs = page_text.split("\n\n")

            for p in paragraphs:
                cleaned = p.strip()
                # Only save chunks that have actual content (more than 20 characters)
                if len(cleaned) > 20:
                    chunks.append(cleaned)

        doc.close()
    except Exception as e:
        print(f"Error while reading PDF: {e}")

    return chunks


# 1b. Visual Content Extraction — Describe equations, diagrams, and circuits
async def describe_page_image(base64_image: str, page_num: int) -> str:
    """
    Sends a rendered PDF page image to the gemma4 vision model.
    The model identifies and describes all equations, formulas, circuit diagrams,
    block diagrams, and other visual elements on the page.
    Returns a plain-text description of the visual content found.
    """
    prompt = (
        "You are an expert academic content analyst. Examine this page image carefully. "
        "Identify and describe ALL of the following if present:\n"
        "- Mathematical equations and formulas (write them out in plain text notation)\n"
        "- Circuit diagrams (describe components, connections, and their purpose)\n"
        "- Block diagrams and flowcharts (describe the blocks and data flow)\n"
        "- Graphs and plots (describe axes, trends, and what they represent)\n"
        "- Tables with technical data\n\n"
        "For each item found, explain what it represents and which academic concept it belongs to. "
        "If no visual/technical content is found on this page, respond with exactly: NO_VISUAL_CONTENT"
    )

    try:
        async with httpx.AsyncClient(
            headers={"ngrok-skip-browser-warning": "true"}
        ) as client:
            response = await client.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": prompt,
                    "images": [base64_image],
                    "stream": False,
                    "options": {"num_ctx": 8192},
                },
                timeout=180.0,  # Vision processing can take longer than text
            )
            response.raise_for_status()
            result = response.json().get("response", "").strip()

            # If the model found nothing visual, return empty string
            if "NO_VISUAL_CONTENT" in result:
                return ""
            return result

    except Exception as e:
        print(f"[Vision] Failed to describe page {page_num + 1}: {e}")
        return ""


import re

from PIL import Image


def process_pyq_visuals(file_path: str) -> list[dict]:
    """
    Renders pages of a PYQ PDF and dynamically slices them into smaller chunks
    (one per question) based on the physical Y-coordinates of question headers.
    """
    if not fitz:
        print("Error: PyMuPDF is not installed. Cannot extract visuals.")
        return []

    chunks = []
    try:
        doc = fitz.open(file_path)
        zoom_matrix = fitz.Matrix(2, 2)

        # Regex to match question start markers like "1. ", "2(a). ", "Part A"
        # It must be at the very start of the text block to prevent false positives.
        q_pattern = re.compile(r"^(?:\d+[\.\)]\s+|Part\s+[A-Z])", re.IGNORECASE)

        for page_num in range(len(doc)):
            page = doc[page_num]
            blocks = page.get_text("blocks")

            y_coords = []
            for b in blocks:
                _x0, y0, _x1, _y1, text, _block_no, block_type = b
                if block_type == 0:
                    clean_text = text.replace("\n", " ").strip()
                    if q_pattern.search(clean_text):
                        y_coords.append(y0)

            # Sort the y-coordinates
            y_coords = sorted(y_coords)

            # If no questions found, just use the whole page as one chunk
            if not y_coords:
                y_coords = [0]
            else:
                # Always start the first chunk at the top of the page if the first question isn't at the very top
                if y_coords[0] > 50:
                    y_coords.insert(0, 0)

            # Scale coordinates for 2x zoom and add the bottom of the page
            scaled_y = [int(y * 2) for y in y_coords]
            pixmap = page.get_pixmap(matrix=zoom_matrix)
            scaled_y.append(pixmap.height)

            # Convert PyMuPDF Pixmap to Pillow Image for easy cropping
            img = Image.frombytes("RGB", [pixmap.width, pixmap.height], pixmap.samples)

            # Slice the image horizontally
            for i in range(len(scaled_y) - 1):
                top = scaled_y[i]
                # Pad slightly upward for safety, but don't go below 0
                crop_top = max(0, top - 20)
                crop_bottom = scaled_y[i + 1]

                # If chunk is too small (e.g., less than 50 pixels), skip it
                if crop_bottom - crop_top < 50:
                    continue

                chunk_img = img.crop((0, crop_top, pixmap.width, crop_bottom))

                # Convert back to base64
                img_byte_arr = io.BytesIO()
                chunk_img.save(img_byte_arr, format="JPEG", quality=85)
                b64_string = base64.b64encode(img_byte_arr.getvalue()).decode("utf-8")

                chunks.append(
                    {"page": page_num, "chunk_index": i, "base64": b64_string}
                )

        doc.close()
    except Exception as e:
        import traceback

        print(f"[Vision] Error slicing PYQ pages: {e}")
        traceback.print_exc()

    return chunks


from collections import OrderedDict

# Global LRU cache for vision results to prevent redundant processing
_vision_cache = OrderedDict()
_VISION_CACHE_SIZE = 100

def _get_cached_vision(image_hash: str):
    if image_hash in _vision_cache:
        _vision_cache.move_to_end(image_hash)
        return _vision_cache[image_hash]
    return None

def _set_cached_vision(image_hash: str, text: str):
    _vision_cache[image_hash] = text
    if len(_vision_cache) > _VISION_CACHE_SIZE:
        _vision_cache.popitem(last=False)

def _downscale_image_b64(b64_str: str, max_size: int = 800) -> str:
    """Downscales a base64 image if it exceeds max_size, maintaining aspect ratio."""
    try:
        import base64
        from PIL import Image
        import io
        
        img_data = base64.b64decode(b64_str)
        img = Image.open(io.BytesIO(img_data))
        
        # If it's already small enough, return original
        if max(img.width, img.height) <= max_size:
            return b64_str
            
        # Calculate new dimensions
        ratio = max_size / max(img.width, img.height)
        new_size = (int(img.width * ratio), int(img.height * ratio))
        
        img = img.resize(new_size, Image.Resampling.LANCZOS)
        
        # Save back to base64
        buffer = io.BytesIO()
        # Convert to RGB to ensure JPEG compatibility if it has alpha channel
        if img.mode in ('RGBA', 'P'): img = img.convert('RGB')
        img.save(buffer, format="JPEG", quality=85)
        return base64.b64encode(buffer.getvalue()).decode('utf-8')
    except Exception as e:
        print(f"[Vision] Failed to downscale image: {e}")
        return b64_str # Fallback to original

async def describe_uploaded_image(base64_image: str) -> str:
    """
    Takes a base64-encoded image uploaded by a student, downscales it, 
    checks the MD5 cache, and asks the vision model to extract structured keywords
    and academic concepts.
    """
    import hashlib
    import re
    
    # 1. Hashing and Caching
    img_hash = hashlib.md5(base64_image.encode('utf-8')).hexdigest()
    cached = _get_cached_vision(img_hash)
    if cached:
        print(f"[Vision] Cache hit! Returning instant analysis: {cached}")
        return cached

    # 2. Image Downscaling (400px is optimal for schematics and 4x faster vision encoding)
    optimized_b64 = _downscale_image_b64(base64_image, max_size=400)

    # 3. Prompt & Schema Tuning (Universal across CS, EEE, Math & Engineering)
    prompt = (
        "Analyze this academic diagram, schematic, chart, graph, flowchart, or equation.\n"
        "Identify the specific academic concept or problem topic (for example: Equivalent Capacitance, Equivalent Resistance, Finite Automata, Binary Search Tree, Logic Gates, CPU Scheduling, Sinusoidal Waveforms, Probability Distribution, Nodal Analysis, Sorting Algorithm, Two-Phase Commit).\n"
        "Return ONLY a JSON object: {\"topic\": \"<specific concept or topic>\", \"keywords\": [\"<term1>\", \"<term2>\", \"<term3>\"]}"
    )

    try:
        async with httpx.AsyncClient(
            headers={"ngrok-skip-browser-warning": "true"}
        ) as client:
            response = await client.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": prompt,
                    "images": [optimized_b64],
                    "format": "json",
                    "think": False,
                    "stream": False,
                    "keep_alive": -1,
                    "options": {
                        "num_ctx": 2048,
                        "num_predict": 180,
                        "temperature": 0.0,
                        "top_p": 0.9,
                    },
                },
                timeout=30.0,
            )
            response.raise_for_status()
            res_data = response.json()
            resp_text = (res_data.get("response") or "").strip()
            thinking_text = (res_data.get("thinking") or "").strip()

            result = ""

            # 1. Attempt structured JSON parsing
            if resp_text:
                try:
                    parsed = json.loads(resp_text)
                    topic = str(parsed.get("topic") or "").strip()
                    comp = str(parsed.get("component") or "").strip()
                    kw = parsed.get("keywords", [])
                    kw_list = [str(k).strip() for k in kw if k] if isinstance(kw, list) else [str(kw).strip()]
                    parts = [
                        p.title() for p in [topic, comp] 
                        if p and p.lower() not in ("none", "null", "n/a", "unknown")
                    ] + [k for k in kw_list if k and k.lower() not in ("none", "null", "n/a", "unknown")]
                    if parts:
                        result = ", ".join(parts)
                except Exception:
                    pass

            # 2. Regex fallback for JSON fields
            if not result:
                combined = f"{resp_text} {thinking_text}"
                topic_m = re.search(r'"topic":\s*"([^"]+)"', combined, re.IGNORECASE)
                comp_m = re.search(r'"component":\s*"([^"]+)"', combined, re.IGNORECASE)
                if topic_m or comp_m:
                    topic = topic_m.group(1).strip() if topic_m else ""
                    comp = comp_m.group(1).strip() if comp_m else ""
                    parts = [
                        p.title() for p in [topic, comp] 
                        if p and p.lower() not in ("none", "null", "n/a", "unknown")
                    ]
                    if parts:
                        result = ", ".join(parts)

            # 3. Domain-specific engineering heuristics over combined text
            if not result:
                lower = f"{resp_text} {thinking_text}".lower()
                if "resistor" in lower or "resistance" in lower:
                    if any(w in lower for w in ["equivalent", "series", "parallel", "bridge", "ladder", "network"]):
                        result = "Equivalent Resistance, Resistors"
                    else:
                        result = "Resistors, Resistance"
                elif "capacitor" in lower or "capacitance" in lower:
                    if any(w in lower for w in ["equivalent", "series", "parallel"]):
                        result = "Equivalent Capacitance, Capacitors"
                    else:
                        result = "Capacitors, Capacitance"
                elif "opamp" in lower or "op-amp" in lower or "operational amplifier" in lower:
                    result = "Opamp, Operational Amplifier"
                elif "thevenin" in lower:
                    result = "Thevenin Theorem"
                elif "nodal" in lower:
                    result = "Nodal Analysis"
                elif "mesh" in lower:
                    result = "Mesh Analysis"

            # 4. Final sanitization fallback: remove markdown headings / thinking markers
            if not result:
                clean = re.sub(r'(?i)^(thinking process:?|the image shows|this is a|circuit diagram of)\s*', '', resp_text or thinking_text)
                clean = re.sub(r'^\d+\.\s*\*+[^*]+\*+:?\s*', '', clean)
                result = clean.strip()
            
            # Save clean result to cache
            if result:
                _set_cached_vision(img_hash, result)
                
            return result
    except Exception as e:
        print(f"[Vision] Error analyzing image: {repr(e)}")
        return f"Error analyzing image: {repr(e)}"


# 2. Vector Embeddings — Convert text into a mathematical array
import asyncio


async def get_embedding(text: str) -> list:
    return await _cached_embed_text(text)


# 3. Knowledge Graph Extraction — Find concepts and their relationships
async def extract_knowledge_graph(course_code: str, text: str) -> list[dict]:
    """
    Sends a text chunk to the large LLM (gemma4) and asks it to identify academic
    concepts and how they relate to each other.
    Returns a list of Subject-Predicate-Object triplets in JSON format.
    """
    prompt = f"""
You are an expert at extracting Knowledge Graphs from academic text.
Extract key concepts and their relationships from the following text about {course_code}.

TEXT:
{text}

Return ONLY a JSON object containing a "triplets" key with an array of relationships. No markdown, no explanation. Use this exact format:
{{
  "triplets": [
    {{"subject": "Concept A", "predicate": "is related to", "object": "Concept B"}}
  ]
}}
"""

    try:
        async with httpx.AsyncClient(
            headers={"ngrok-skip-browser-warning": "true"}
        ) as client:
            response = await client.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json",  # Forces Ollama to return valid JSON
                },
                timeout=120.0,
            )
            response.raise_for_status()
            data = response.json()

            # Parse the string response into an actual Python dictionary/list
            result_str = data.get("response", "[]")
            triplets = json.loads(result_str)

            # Sometimes the LLM wraps the array inside an object like {"triplets": [...]}.
            # We unwrap it here if necessary.
            if isinstance(triplets, dict):
                triplets = triplets.get("triplets", [])

            # Ensure we are returning a list
            if not isinstance(triplets, list):
                return []

            # Filter out any invalid items (like floats or strings generated by the LLM by mistake)
            triplets = [t for t in triplets if isinstance(t, dict)]

            return triplets

    except Exception as e:
        print(f"Failed to extract knowledge graph: {e}")
        return []


# 4. Save to Neo4j — Write the triplets into the Graph Database
def save_to_neo4j(neo4j_driver, course_code: str, triplets: list[dict]):
    """
    Takes the list of extracted triplets and writes them into Neo4j using the Cypher query language.
    Nodes represent 'Concepts', and edges represent 'Predicates' (relationships).
    """
    if not triplets:
        return

    # Open a new database session
    with neo4j_driver.session() as session:
        # MERGE ensures the Course node exists. If it doesn't, it creates it.
        session.run("MERGE (c:Course {code: $code})", code=course_code)

        import re

        for triplet in triplets:
            subject = triplet.get("subject")

            # Neo4j relationships are typically uppercase with underscores (e.g., IS_RELATED_TO)
            raw_predicate = (
                triplet.get("predicate", "RELATED_TO").upper().replace(" ", "_")
            )
            # Strip out any characters that aren't A-Z, 0-9, or underscore to prevent Cypher syntax errors
            predicate = re.sub(r"[^A-Z0-9_]", "", raw_predicate)
            if not predicate:
                predicate = "RELATED_TO"

            obj = triplet.get("object")

            if not subject or not obj:
                continue

            # Ensure subject and object are scalar strings to prevent creating nodes with array properties
            if not isinstance(subject, str) or not isinstance(obj, str):
                continue

            # Cypher Query Logic:
            # 1. Ensure Course exists
            # 2. Ensure Subject Concept exists
            # 3. Ensure Object Concept exists
            # 4. Create a relationship from Subject to Object
            # 5. Create a relationship from the main Course to the Subject
            query = f"""
                MERGE (c:Course   {{code: $course_code}})
                MERGE (s:Concept  {{name: $subject}})
                MERGE (o:Concept  {{name: $obj}})
                MERGE (s)-[:{predicate}]->(o)
                MERGE (c)-[:HAS_CONCEPT]->(s)
            """
            try:
                session.run(query, course_code=course_code, subject=subject, obj=obj)
            except Exception as e:
                print(f"Failed to write triplet {triplet} to Neo4j: {e}")


# 5. Answer Generation — Produce an answer based on retrieved context
async def generate_answer(question: str, context: str) -> str:
    """
    Takes the student's original question AND the context we retrieved from
    PostgreSQL and Neo4j, and asks the large LLM to write a helpful answer.
    """
    prompt = f"""
You are MACH AI (M.A.C.H. - Multimodel Academic Cognitive Hub - A Fine-tuned Graph-RAG framework for Campus Knowledge Systems), an academic AI assistant for university students.
Answer the student's question using ONLY the information provided in the Context below.
If the student asks for questions on a topic (e.g. PYQs or exam questions), list ALL available matching questions provided in the Context across all documents. Do not omit any question.

IMPORTANT: If the Context contains Markdown image links for diagrams (e.g. `![Diagram for ...](http://...)`), you MUST include EVERY Markdown image link directly under its corresponding question in your final answer so the student can see the circuit/diagram images.

Context:
{context}

Question: {question}

Answer:
"""

    try:
        async with httpx.AsyncClient(
            headers={"ngrok-skip-browser-warning": "true"}
        ) as client:
            response = await client.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"num_ctx": 8192},
                },
                timeout=120.0,
            )
            response.raise_for_status()
            # Return the generated text string
            return response.json().get("response", "I could not generate an answer.")
    except Exception as e:
        print(f"Ollama API Error: {e}")
        # Graceful fallback: return the raw retrieved context directly from Neo4j/PostgreSQL
        return f"ℹ️ *(Note: AI reformatting timed out. Showing direct database search results below)*\n\n{context}"


async def generate_answer_stream(question: str, context: str):
    """
    Streams the AI response chunk by chunk as it generates.
    """
    prompt = f"""You are MACH AI (M.A.C.H. - Multimodal Academic Cognitive Hub), an intelligent academic AI assistant and expert tutor for university students.
Use the provided Context as your primary authoritative source for course-specific syllabus units, course outcomes, exam questions, and department curriculum.
For conceptual explanations, theoretical derivations, algorithms, or definitions (e.g. comparing concepts, explaining algorithms, or solving problems), provide a clear, comprehensive, and accurate academic explanation using your engineering and scientific knowledge, seamlessly connecting it to any relevant course topics in the context.
If specific course exam questions or practice problems exist in the Context, cite or include them to help the student practice.

IMPORTANT: If the Context contains Markdown image links for diagrams (e.g. `![Diagram for ...](http://...)`), you MUST include EVERY Markdown image link directly under its corresponding question in your final answer so the student can see the circuit/diagram images.

Context:
{context}

Question: {question}

Answer:
"""
    try:
        async with httpx.AsyncClient(
            headers={"ngrok-skip-browser-warning": "true"}
        ) as client, client.stream(
            "POST",
            f"{OLLAMA_BASE_URL}/api/generate",
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": True,
                "think": False,
                "options": {"num_ctx": 2048, "temperature": 0.2},
            },
            timeout=300.0,
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line:
                    data = json.loads(line)
                    yield data.get("response", "")
    except Exception as e:
        print(f"Ollama API Error in generate_answer_stream: {e}")
        yield f"*(AI reformatting unavailable: {type(e).__name__}). Showing database search results below:*\n\n{context}"


# 6. Syllabus Structural Extraction (New Architecture)


async def extract_syllabus_structure(text: str, dept: str, year: str) -> dict:
    """
    Extracts the syllabus structure deterministically using Regular Expressions.
    Bypasses the LLM for speed and reliability, parsing hundreds of pages instantly.
    """
    # Find all course headers
    # Example: 23ENG101 TECHNICAL COMMUNICATION L-T-P-C: 2-0-3-3
    course_pattern = re.compile(
        r"([0-9]{2}[a-zA-Z]{3}[0-9]{3})\s+(.*?)\s+L-T-P-C", re.IGNORECASE
    )

    courses_data = []

    # We find all matches, and iterate through them.
    # The text for a course is everything from the end of this match to the start of the next match.
    matches = list(course_pattern.finditer(text))

    for i, match in enumerate(matches):
        course_code = match.group(1).strip().upper()
        course_name = match.group(2).strip()

        # Get the text block for this course
        start_idx = match.end()
        end_idx = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        course_text = text[start_idx:end_idx]

        units_data = []

        # Now find units inside course_text
        # Matches "Unit 1", "Unit 2", "SyllabusUnit 1", etc.
        unit_pattern = re.compile(r"(?:Syllabus)?Unit\s*(\d)", re.IGNORECASE)
        unit_matches = list(unit_pattern.finditer(course_text))

        for j, u_match in enumerate(unit_matches):
            unit_num = u_match.group(1)
            u_start = u_match.end()

            # Find the end of this unit (either next unit, or stopping keywords)
            if j + 1 < len(unit_matches):
                u_end = unit_matches[j + 1].start()
            else:
                # Find the first occurrence of stopping keywords
                stop_pattern = re.compile(
                    r"^(?:TEXTBOOK|REFERENCE|Evaluation Pattern)",
                    re.IGNORECASE | re.MULTILINE,
                )
                stop_match = stop_pattern.search(course_text, u_start)
                if stop_match:
                    u_end = stop_match.start()
                else:
                    u_end = len(course_text)

            unit_text = course_text[u_start:u_end].strip()

            # Extract topics by splitting the unit_text by punctuation
            raw_topics = re.split(r"[,;\.\-\:]", unit_text)

            topics_data = []
            for rt in raw_topics:
                t_name = rt.strip().replace("\n", " ")
                t_name = re.sub(r"\s+", " ", t_name)  # normalize spaces

                # Basic cleaning: remove empty topics and very long sentences
                if len(t_name) > 3 and len(t_name) < 150:
                    # Ignore generic words
                    if t_name.lower() not in [
                        "introduction",
                        "overview",
                        "summary",
                        "conclusion",
                    ]:
                        topics_data.append({"name": t_name, "subtopics": []})

            if topics_data:
                units_data.append(
                    {
                        "number": int(unit_num),
                        "title": f"Unit {unit_num}",
                        "topics": topics_data,
                    }
                )

        if units_data:
            courses_data.append(
                {"code": course_code, "name": course_name, "units": units_data}
            )

    print(
        f"[Syllabus Parser] Successfully extracted {len(courses_data)} courses from the text!"
    )
    return {"courses": courses_data}


def build_syllabus_kg(neo4j_driver, dept: str, year: str, courses: list):
    if not courses:
        return
    with neo4j_driver.session() as session:
        session.run("MERGE (d:Department {name: $dept})", dept=dept)
        for course in courses:
            c_code = course.get("code")
            c_name = course.get("name")
            if not c_code:
                continue

            session.run(
                """
                MERGE (d:Department {name: $dept})
                MERGE (c:Course {code: $c_code})
                ON CREATE SET c.name = $c_name, c.year = $year, c.dept = $dept
                MERGE (d)-[:OFFERS]->(c)
            """,
                dept=dept,
                c_code=c_code,
                c_name=c_name,
                year=year,
            )

            for unit in course.get("units", []):
                u_num = str(unit.get("number", ""))
                u_title = unit.get("title", "")
                if not u_title:
                    continue

                session.run(
                    """
                    MATCH (c:Course {code: $c_code})
                    MERGE (u:Unit {number: $u_num, title: $u_title, course_code: $c_code})
                    MERGE (c)-[:HAS_UNIT]->(u)
                """,
                    c_code=c_code,
                    u_num=u_num,
                    u_title=u_title,
                )

                for topic in unit.get("topics", []):
                    t_name = topic.get("name")
                    if not t_name:
                        continue

                    session.run(
                        """
                        MATCH (u:Unit {number: $u_num, title: $u_title, course_code: $c_code})
                        MERGE (t:Topic {name: $t_name, course_code: $c_code})
                        MERGE (u)-[:HAS_TOPIC]->(t)
                    """,
                        u_num=u_num,
                        u_title=u_title,
                        c_code=c_code,
                        t_name=t_name,
                    )

                    for subtopic in topic.get("subtopics", []):
                        if not subtopic:
                            continue
                        session.run(
                            """
                            MATCH (t:Topic {name: $t_name, course_code: $c_code})
                            MERGE (st:SubTopic {name: $subtopic, course_code: $c_code})
                            MERGE (t)-[:HAS_SUBTOPIC]->(st)
                        """,
                            t_name=t_name,
                            c_code=c_code,
                            subtopic=subtopic,
                        )


# 7. PYQ Structural Extraction and Mapping
async def extract_pyq_questions(base64_image: str) -> list[dict]:
    prompt = """
You are an expert at extracting exam questions from PYQ (Past Year Question) pages.
Examine this page image carefully. Extract all questions found on the page into structured JSON.
Predict any formulas that the student needs to use to solve the question, even if not explicitly mentioned.

Return ONLY a JSON object with this exact format:
{
  "questions": [
    {
      "question_number": "Q1(a)",
      "text": "Find the transfer function...",
      "marks": 10,
      "formula_hints": ["transfer function", "Laplace transform"],
      "likely_topic": "Control Systems / Transfer Functions",
      "implicit_formulas": ["G(s)H(s) = 1"]
    }
  ]
}
If no questions are found, return {"questions": []}. No markdown, no explanation.
"""
    try:
        async with httpx.AsyncClient(
            headers={"ngrok-skip-browser-warning": "true"}
        ) as client:
            response = await client.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": prompt,
                    "images": [base64_image],
                    "format": "json",
                    "stream": False,
                    "options": {"num_ctx": 8192, "num_predict": 4096},
                },
                timeout=180.0,
            )
            response.raise_for_status()
            res_text = response.json().get("response", "{}")

            # Robust JSON extraction to handle markdown, control characters, and invalid JSON escaping
            import re

            def is_valid_question(q_text: str) -> bool:
                if not q_text or len(q_text) < 20:
                    return False
                lower_text = q_text.lower()
                junk_patterns = [
                    r"^answer all",
                    r"^part [a-z]",
                    r"^section [a-z]",
                    r"maximum marks",
                    r"^time:",
                    r"q\.p\. code",
                    r"^\d+\s*x\s*\d+\s*=\s*\d+",
                    r"^[a-z]\)",
                ]
                for pattern in junk_patterns:
                    if re.search(pattern, lower_text):
                        return False
                return True

            match = re.search(r"\{.*\}", res_text, re.DOTALL)
            clean_text = match.group(0) if match else res_text

            try:
                data = json.loads(clean_text)
                if isinstance(data, dict) and "questions" in data:
                    return [
                        q
                        for q in data["questions"]
                        if is_valid_question(q.get("text", ""))
                    ]
            except Exception:
                pass

            # Sanitize control characters (e.g. \u001e)
            try:
                sanitized = re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", clean_text)
                data = json.loads(sanitized)
                if isinstance(data, dict) and "questions" in data:
                    return [
                        q
                        for q in data["questions"]
                        if is_valid_question(q.get("text", ""))
                    ]
            except Exception:
                pass

            # Regex fallback: extract "text": "..." fields directly
            extracted = []
            text_matches = re.findall(r'"text"\s*:\s*"([^"]+)"', clean_text)
            for q_t in text_matches:
                if is_valid_question(q_t):
                    extracted.append(
                        {
                            "question_number": "PYQ",
                            "text": q_t,
                            "likely_topic": "General",
                            "implicit_formulas": [],
                        }
                    )
            if extracted:
                return extracted

            # Final fallback: return clean text instead of raw JSON string, but only if it looks like a valid question
            clean_human_text = re.sub(r'[{}"\[\]]', "", clean_text).strip()
            if is_valid_question(clean_human_text):
                return [
                    {
                        "question_number": "PYQ",
                        "text": clean_human_text,
                        "likely_topic": "General",
                        "implicit_formulas": [],
                    }
                ]
            else:
                return []

    except Exception as e:
        print(f"Failed to extract PYQ questions: {e}")
        return []


def clean_formula_text(text: str) -> str:
    if not text:
        return ""
    text = text.replace("\t", "\\t")
    text = text.replace("\x00", "")
    text = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", text)
    # Fix LaTeX commands lost during JSON unescaping (e.g. imes -> \times, rac -> \frac)
    text = re.sub(r"\bimes\b", r"\\times", text)
    text = re.sub(r"\brac\b", r"\\frac", text)
    # Sanitize repetitive OCR noise characters
    text = re.sub(r"[^\w\s\+\-\*\/\=\(\)\[\]\{\}\\\$\.\,\:\_\^\@\%]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


async def map_questions_to_kg(
    neo4j_driver,
    course_code: str,
    questions: list,
    document_id: str | None = None,
    image_url: str | None = None,
):
    if not questions:
        return

    syllabus_context = fetch_course_topics(neo4j_driver, course_code)
    print(
        f"[PYQ Vision] Fetched {len(syllabus_context.split(chr(10)))} topics from syllabus. Running semantic mapping via LLM..."
    )

    mapping = await semantic_map_questions_to_topics(
        course_code, syllabus_context, questions
    )
    print(f"[PYQ Vision] Semantic mapper returned {len(mapping)} matched questions.")

    with neo4j_driver.session() as session:
        for i, q in enumerate(questions):
            if isinstance(q, str):
                q = {
                    "text": q,
                    "question_number": "Unknown",
                    "likely_topic": "General",
                    "implicit_formulas": [],
                }

            q_text = str(q.get("text", ""))

            raw_formulas = q.get("implicit_formulas", [])
            if not isinstance(raw_formulas, list):
                raw_formulas = [raw_formulas]
            implicit_formulas = [
                clean_formula_text(str(f))
                for f in raw_formulas
                if f is not None and len(clean_formula_text(str(f))) > 1
            ]

            q_num = str(q.get("question_number", ""))
            marks = str(q.get("marks", ""))

            # Create question and attach to course
            q_result = session.run(
                """
                MERGE (c:Course {code: $c_code})
                CREATE (q:Question {
                    text: $q_text, 
                    question_number: $q_num, 
                    marks: $marks, 
                    implicit_formulas: $implicit_formulas,
                    document_id: $doc_id,
                    course_code: $c_code,
                    image_url: $image_url
                })
                MERGE (q)-[:BELONGS_TO]->(c)
                RETURN id(q) as internal_id
            """,
                c_code=course_code,
                q_text=q_text,
                q_num=q_num,
                marks=marks,
                implicit_formulas=implicit_formulas,
                doc_id=str(document_id) if document_id else "",
                image_url=str(image_url) if image_url else "",
            ).single()

            if q_result:
                internal_id = q_result["internal_id"]
                topic_ids = mapping.get(i, [])
                for t_id in topic_ids:
                    session.run(
                        """
                        MATCH (q) WHERE id(q) = $internal_id
                        MATCH (t:Topic {topic_id: $t_id})
                        MERGE (q)-[:TESTS]->(t)
                    """,
                        internal_id=internal_id,
                        t_id=t_id,
                    )


import hashlib

from sqlalchemy import text

# 4b. Course Metadata & Neo4j Prerequisite Mapping (Amrita B.Tech CSE Curriculum)
COURSE_METADATA = {
    # Mathematics & Foundational Sciences
    "23MAT101": "Calculus",
    "19MAT101": "Calculus",
    "23MAT116": "Discrete Mathematics",
    "19MAT111": "Discrete Mathematics",
    "23MAT117": "Linear Algebra",
    "19MAT112": "Linear Algebra",
    "23MAT216": "Probability and Random Processes",
    "19MAT205": "Probability and Random Processes",
    "23CHY101": "Engineering Chemistry",
    "23PHY101": "Engineering Physics",
    "23EEE104": "Electric Circuits",

    # Core Computer Science (2023 & 2019 Regulations)
    "23CSE101": "Computational Problem Solving",
    "19CSE101": "Computer Programming",
    "23CSE102": "Computer Hardware Essentials",
    "23CSE111": "Object Oriented Programming",
    "23CSE201": "Procedural Programming Using C",
    "19CSE201": "Object Oriented Programming",
    "23CSE202": "Database Management Systems",
    "19CSE202": "Database Management Systems",
    "23CSE203": "Data Structures and Algorithms",
    "19CSE203": "Data Structures and Algorithms",
    "23CSE204": "Operating Systems",
    "23CSE214": "Operating Systems",
    "19CSE204": "Operating Systems",
    "23CSE205": "Digital Electronics",
    "23CSE211": "Design and Analysis of Algorithms",
    "19CSE211": "Design and Analysis of Algorithms",
    "23CSE212": "Principles of Functional Languages",
    "23CSE213": "Computer Organization and Architecture",
    "19CSE213": "Computer Organization and Architecture",
    "23CSE301": "Machine Learning",
    "19CSE301": "Machine Learning",
    "23CSE302": "Computer Networks",
    "23CSE304": "Embedded Systems",
    "19CSE304": "Computer Networks",
    "23CSE303": "Theory of Computation",
    "19CSE303": "Theory of Computation",
    "23CSE311": "Software Engineering",
    "19CSE311": "Software Engineering",
    "23CSE312": "Distributed Systems",
    "19CSE312": "Distributed Systems",
    "23CSE313": "Foundations of Cyber Security",
    "19CSE313": "Cryptography and Network Security",
    "23CSE314": "Compiler Design",
    "19CSE314": "Compiler Design",

    # Professional Electives & Advanced Streams
    "23CSE341": "Wireless Sensor Networks",
    "23CSE342": "Advanced Computer Networks",
    "23CSE343": "Wireless and Mobile Networks",
    "23CSE344": "Modern Cellular Wireless Networks",
    "23CSE345": "Software Defined Networks",
    "23CSE355": "Mining of Massive Datasets",
    "23CSE361": "Real Time Operating Systems",
    "23CSE363": "Cloud Computing",
    "23CSE364": "Cyber-Physical Systems",
    "23CSE365": "Internet of Things",
    "23CSE440": "Advanced Algorithms and Analysis",
    "23CSE441": "Introduction to Machine Learning",
    "23CSE442": "Distributed Systems",
    "23CSE453": "Competitive Programming",
    "23CSE458": "Embedded Programming",
    "23CSE466": "Parallel Programming",
    "23CSE473": "Deep Learning",
    "23CSE477": "Reinforcement Learning",
    "23AIE201": "Artificial Intelligence",
    "23AIE301": "Natural Language Processing",
}

PREREQUISITE_MAP = {
    # DSA requires Discrete Mathematics and Programming
    "23CSE203": ["23MAT116", "23CSE101"],
    "19CSE203": ["19MAT111", "19CSE101"],

    # DAA requires DSA and Discrete Mathematics
    "23CSE211": ["23CSE203", "23MAT116"],
    "19CSE211": ["19CSE203", "19MAT111"],

    # DBMS requires DSA
    "23CSE202": ["23CSE203"],
    "19CSE202": ["19CSE203"],

    # Operating Systems requires DSA and Computer Architecture
    "23CSE204": ["23CSE203", "23CSE213"],
    "23CSE214": ["23CSE203", "23CSE213"],
    "19CSE204": ["19CSE203", "19CSE213"],

    # Machine Learning requires Linear Algebra, Probability, and DSA
    "23CSE301": ["23MAT117", "23MAT216", "23CSE203"],
    "19CSE301": ["19MAT112", "19MAT205", "19CSE203"],

    # Theory of Computation requires Discrete Mathematics
    "23CSE303": ["23MAT116"],
    "19CSE303": ["19MAT111"],

    # Computer Networks requires Operating Systems
    "23CSE302": ["23CSE204"],
    "19CSE304": ["19CSE204"],

    # Embedded Systems requires Computer Organization and Architecture
    "23CSE304": ["23CSE213"],

    # Distributed Systems requires Operating Systems & Computer Networks
    "23CSE312": ["23CSE204", "23CSE302"],
    "19CSE312": ["19CSE204", "19CSE304"],
    "23CSE442": ["23CSE204", "23CSE302"],

    # Cryptography / Cyber Security requires Discrete Math & Networks
    "23CSE313": ["23MAT116", "23CSE302"],
    "19CSE313": ["19MAT111", "19CSE304"],

    # Compiler Design requires Theory of Computation & DSA
    "23CSE314": ["23CSE303", "23CSE203"],
    "19CSE314": ["19CSE303", "19CSE203"],

    # Elective streams: Networks
    "23CSE341": ["23CSE302"],
    "23CSE342": ["23CSE302"],
    "23CSE343": ["23CSE302"],
    "23CSE344": ["23CSE302"],
    "23CSE345": ["23CSE302"],

    # Elective streams: Systems & IoT
    "23CSE361": ["23CSE204", "23CSE304"],
    "23CSE363": ["23CSE302"],
    "23CSE364": ["23MAT116"],
    "23CSE365": ["23CSE304", "23CSE302"],
    "23CSE458": ["23CSE213"],
    "23CSE466": ["23CSE213"],

    # Elective streams: AI / Data Science
    "23CSE355": ["23CSE301"],
    "23CSE440": ["23CSE203"],
    "23CSE441": ["23MAT117", "23MAT216"],
    "23CSE473": ["23CSE301"],
    "23CSE477": ["23CSE301"],
    "23AIE201": ["23CSE203", "23MAT116"],
    "23AIE301": ["23CSE301"],
}


def add_prerequisite_edges(neo4j_driver, prerequisite_map):
    """
    Executes parameterized Cypher to create REQUIRES and REQUIRES_PREREQUISITE edges between courses.
    Sets course code, name, and title on nodes so multi-hop traversals resolve human-readable titles.
    """
    if not prerequisite_map:
        return

    with neo4j_driver.session() as session:
        for target_code, prereqs in prerequisite_map.items():
            t_name = COURSE_METADATA.get(target_code, target_code)
            session.run(
                """
                MERGE (c1:Course {code: $target_code})
                SET c1.name = coalesce(c1.name, $t_name),
                    c1.title = coalesce(c1.title, $t_name)
                """,
                target_code=target_code,
                t_name=t_name,
            )
            for prereq_code in prereqs:
                p_name = COURSE_METADATA.get(prereq_code, prereq_code)
                session.run(
                    """
                    MERGE (c1:Course {code: $target_code})
                    MERGE (c2:Course {code: $prereq_code})
                    SET c1.name = coalesce(c1.name, $t_name),
                        c1.title = coalesce(c1.title, $t_name),
                        c2.name = coalesce(c2.name, $p_name),
                        c2.title = coalesce(c2.title, $p_name)
                    MERGE (c1)-[:REQUIRES]->(c2)
                    MERGE (c1)-[:REQUIRES_PREREQUISITE]->(c2)
                """,
                    target_code=target_code,
                    prereq_code=prereq_code,
                    t_name=t_name,
                    p_name=p_name,
                )


def _render_page_image(page, scale: float = 2.0) -> "Image.Image":
    """
    Renders a PyMuPDF page into a Pillow Image at the given scale.
    """
    matrix = fitz.Matrix(scale, scale)
    pixmap = page.get_pixmap(matrix=matrix)
    return Image.frombytes("RGB", [pixmap.width, pixmap.height], pixmap.samples)


def _expand_bbox_for_graphics(
    page, y_top: float, y_bottom: float, page_height: float, hard_ceiling: float
) -> float:
    """
    Scans a page for raster images and vector drawings that start between
    y_top and y_bottom (plus a small look-ahead), and expands y_bottom to
    fully enclose them.

    CRITICAL: the result is capped at `hard_ceiling` so that graphics that
    belong to the NEXT question cannot pull this question's crop past that
    boundary.  Pass the next question's y_start as hard_ceiling; pass
    page_height when this is the last question on the page.
    """
    new_bottom = y_bottom
    # Look-ahead: only grab graphics that START within the current box.
    # Never peek more than 30 PDF pts below the current boundary.
    look_ahead = min(y_bottom + 30, hard_ceiling)

    # Check embedded raster images
    for img_info in page.get_image_info():
        bbox = img_info.get("bbox", None)
        if bbox:
            img_y0, img_y1 = bbox[1], bbox[3]
            if y_top - 5 <= img_y0 <= look_ahead:
                new_bottom = max(new_bottom, min(img_y1 + 5, hard_ceiling))

    # Check vector drawings (circuit diagram lines, boxes, arcs)
    for drawing in page.get_drawings():
        rect = drawing.get("rect", None)
        if rect:
            draw_y0, draw_y1 = rect.y0, rect.y1
            if y_top - 5 <= draw_y0 <= look_ahead:
                new_bottom = max(new_bottom, min(draw_y1 + 5, hard_ceiling))

    return min(new_bottom, hard_ceiling)


def extract_pyq_structured(
    file_path: str, course_code: str, document_id: str = ""
) -> list[dict]:
    """
    Extracts structured questions from PYQ PDFs.

    Two-pass bounding-box algorithm:
      Pass 1: Collect every question header's exact pixel y-coordinate on every page.
      Pass 2: For each question, expand the crop box to enclose all diagrams/graphics,
              stitch across page boundaries when a question continues to the next page,
              and upload the final cropped image to Cloudinary (or local fallback).
    """
    if not fitz:
        print("Error: PyMuPDF is not installed. Cannot extract PYQ structured text.")
        return []

    # Regex patterns
    CO_TAG_RE = re.compile(r"\[CO\s*0*(\d+)\]", re.IGNORECASE)
    BTL_TAG_RE = re.compile(r"\[BTL\s*(\d+)\]", re.IGNORECASE)
    MARKS_RE = re.compile(r"\[(\d+)\]|\((\d+)\s*[Mm]arks?\)", re.IGNORECASE)
    FIG_RE = re.compile(r"\bFig\.?\s*\d+\b|\bfigure\b", re.IGNORECASE)
    # TOP-LEVEL questions only: "1." "2." "10." "Q1." "Question 2."
    # Sub-questions like "1a)", "(i)", "a)" are intentionally excluded so
    # they are captured as part of the parent question text + crop.
    # Restrict leading whitespace to 5 chars to avoid matching deeply indented table cells
    Q_NUM_RE = re.compile(
        r"^[ \t]{0,5}((?:Q(?:uestion)?\s*)?\d{1,2})[\.\)]\s+",
        re.MULTILINE | re.IGNORECASE,
    )
    SCALE = 2.0  # Render pages at 2x resolution for crisp crops

    # -------------------------------------------------------------------------
    # PASS 1: Gather all QuestionSpan entries across pages
    # -------------------------------------------------------------------------
    # Each span: {q_num, page_num, y_start (PDF units), text, co_tag, btl_tag, marks, has_figure}
    all_spans = []  # ordered list of spans across all pages
    page_texts = []  # raw text per page (for text extraction)
    doc_separator = None  # Tracks if the exam uses '1)' or '1.' to reject mixed formats like table rows

    try:
        doc = fitz.open(file_path)
        num_pages = len(doc)

        for page_num in range(num_pages):
            page = doc[page_num]
            page_text = page.get_text("text")
            page_texts.append(page_text)
            matches = list(Q_NUM_RE.finditer(page_text))

            for idx, match in enumerate(matches):
                q_num = match.group(1).strip()

                # Skip obvious junk headers
                if q_num.lower() in ("", "q", "question"):
                    continue

                # Find exact pixel Y for this question header via text search
                search_str = match.group(0).strip()
                rects = page.search_for(search_str)
                y_start_pdf = rects[0].y0 if rects else 0.0

                # Determine raw text extent for this question
                start_char = match.end()
                end_char = (
                    matches[idx + 1].start()
                    if idx + 1 < len(matches)
                    else len(page_text)
                )
                raw_text = page_text[start_char:end_char].strip()

                if len(raw_text) < 8:
                    continue

                # Extract metadata tags
                # Extract metadata tags
                co_m = CO_TAG_RE.search(raw_text)
                btl_m = BTL_TAG_RE.search(raw_text)
                mk_m = MARKS_RE.search(raw_text)
                marks_val = int(mk_m.group(1) or mk_m.group(2)) if mk_m else None

                has_figure = bool(FIG_RE.search(raw_text))

                # Clean the display text
                clean_text = CO_TAG_RE.sub("", raw_text)
                clean_text = BTL_TAG_RE.sub("", clean_text)
                clean_text = MARKS_RE.sub("", clean_text).strip()

                # Filter out purely numeric table rows (no alphabetical characters)
                if not re.search(r"[a-zA-Z]", clean_text):
                    continue

                # Skip boilerplate
                if any(
                    junk in clean_text.lower()[:60]
                    for junk in [
                        "answer all",
                        "part a",
                        "part b",
                        "maximum marks",
                        "time:",
                        "course outcomes",
                        "co |",
                        "all questions",
                    ]
                ):
                    continue

                # Exam papers NEVER mix '1)' and '1.' for top-level questions.
                # If we lock in the separator from the first valid question, we can
                # safely ignore nested lists or table columns that happen to use the other format.
                separator = "." if "." in match.group(0) else ")"
                if doc_separator and separator != doc_separator:
                    continue
                if not doc_separator:
                    doc_separator = separator

                all_spans.append(
                    {
                        "q_num": q_num,
                        "page_num": page_num,
                        "y_start": y_start_pdf,  # PDF coordinate units
                        "q_text": clean_text,
                        "co_tag": f"CO{co_m.group(1)}" if co_m else None,
                        "btl_tag": f"BTL{btl_m.group(1)}" if btl_m else None,
                        "marks": marks_val,
                        "has_figure": has_figure,
                    }
                )

        # -------------------------------------------------------------------------
        # PASS 2: Compute crop regions, stitch cross-page questions, upload images
        # -------------------------------------------------------------------------
        structured_questions = []

        for span_idx, span in enumerate(all_spans):
            page_num = span["page_num"]
            page = doc[page_num]
            page_h = page.rect.height  # PDF units
            y_start_pdf = span["y_start"]

            # ── Strict boundary: next question's y_start is the hard ceiling ──
            # This MUST NOT be violated by graphics expansion.
            next_same_page = next(
                (s for s in all_spans[span_idx + 1 :] if s["page_num"] == page_num),
                None,
            )
            if next_same_page and next_same_page["y_start"] > y_start_pdf:
                # Leave a 3-pt gap above the next question header so we don't
                # accidentally clip its first line.
                hard_ceiling_this_page = next_same_page["y_start"] - 3
            else:
                hard_ceiling_this_page = (
                    page_h  # last question or multi-column: use full page
                )

            # Expand to include diagrams/drawings, but NEVER past hard ceiling
            y_end_pdf = _expand_bbox_for_graphics(
                page,
                y_start_pdf,
                hard_ceiling_this_page,
                page_h,
                hard_ceiling_this_page,
            )

            # Detect cross-page continuation:
            # If this is the last question on page N, check if the next page's
            # first question starts far enough down to suggest the current
            # question's content (e.g. a diagram) continues on the next page.
            CONTINUATION_THRESHOLD_PDF = 100  # PDF points (~35 mm)
            next_span = (
                all_spans[span_idx + 1] if span_idx + 1 < len(all_spans) else None
            )
            continues_to_next_page = (
                next_same_page is None  # last question on this page
                and next_span is not None  # there is a following span
                and next_span["page_num"] == page_num + 1  # immediately next page
                and next_span["y_start"] > CONTINUATION_THRESHOLD_PDF
            )

            # ---- Render and crop the image(s) ----
            image_url = ""
            if document_id:
                try:
                    part1_img = _render_page_image(page, SCALE)
                    # Strict pixel ceiling = hard_ceiling_this_page * SCALE
                    px_ceiling = int(hard_ceiling_this_page * SCALE)
                    crop_top = max(0, int((y_start_pdf - 10) * SCALE))
                    crop_bot = min(
                        part1_img.height, min(px_ceiling, int((y_end_pdf + 5) * SCALE))
                    )
                    part1_crop = part1_img.crop(
                        (0, crop_top, part1_img.width, crop_bot)
                    )

                    final_img = part1_crop

                    if continues_to_next_page:
                        # Stitch with the top region of the next page.
                        # The stitch ends STRICTLY before the next question header.
                        next_page = doc[page_num + 1]
                        next_page_h = next_page.rect.height
                        part2_img = _render_page_image(next_page, SCALE)

                        # Hard ceiling on next page = next question's y_start - 3
                        next_hard_ceiling = (
                            (next_span["y_start"] - 3) if next_span else next_page_h
                        )
                        cont_end_pdf = _expand_bbox_for_graphics(
                            next_page,
                            0,
                            next_hard_ceiling,
                            next_page_h,
                            next_hard_ceiling,
                        )
                        cont_bot_px = min(
                            part2_img.height, int((cont_end_pdf + 5) * SCALE)
                        )
                        part2_crop = part2_img.crop(
                            (0, 0, part2_img.width, cont_bot_px)
                        )

                        # Vertically concatenate part1 and part2
                        total_h = part1_crop.height + part2_crop.height
                        combined = Image.new(
                            "RGB", (part1_crop.width, total_h), (255, 255, 255)
                        )
                        combined.paste(part1_crop, (0, 0))
                        combined.paste(part2_crop, (0, part1_crop.height))
                        final_img = combined

                    # Encode to JPEG bytes and upload
                    if final_img.height > 30:
                        buf = io.BytesIO()
                        final_img.save(buf, format="JPEG", quality=88)
                        img_bytes = buf.getvalue()
                        image_hash = hashlib.md5(img_bytes).hexdigest()

                        safe_q = span["q_num"].replace(".", "_").replace(" ", "_")
                        public_id = f"{document_id}_p{page_num + 1}_q{safe_q}"
                        image_url = upload_question_image_to_cloudinary(
                            img_bytes, public_id
                        )

                except Exception as crop_err:
                    print(
                        f"[PYQ Crop] Failed for Q{span['q_num']} page {page_num + 1}: {crop_err}"
                    )

            # Build the unique question ID (include document_id to prevent cross-PDF collisions)
            q_id = hashlib.md5(
                f"{document_id}_{course_code}_{span['q_num']}_p{page_num}".encode()
            ).hexdigest()

            structured_questions.append(
                {
                    "id": q_id,
                    "question_number": span["q_num"],
                    "question_text": span["q_text"],
                    "co_tag": span["co_tag"],
                    "btl_tag": span["btl_tag"],
                    "marks": span["marks"],
                    "has_figure": span["has_figure"],
                    "course_code": course_code,
                    "exam_name": "PYQ Exam",
                    "image_url": image_url,
                    "image_hash": image_hash if image_url else None,
                }
            )

        doc.close()
        return structured_questions

    except Exception as e:
        import traceback

        print(f"Error in extract_pyq_structured: {e}")
        traceback.print_exc()
        return []


async def map_pyq_structured_to_kg(
    neo4j_driver, structured_questions: list[dict], document_id: str | None = None
):
    if not structured_questions:
        return

    c_code_overall = structured_questions[0]["course_code"]
    syllabus_context = fetch_course_topics(neo4j_driver, c_code_overall)
    print(
        f"[PYQ] Fetched {len(syllabus_context.split(chr(10)))} topics from syllabus. Running semantic mapping via LLM..."
    )

    mapping = await semantic_map_questions_to_topics(
        c_code_overall, syllabus_context, structured_questions
    )
    print(f"[PYQ] Semantic mapper returned {len(mapping)} matched questions.")

    with neo4j_driver.session() as session:
        for i, q in enumerate(structured_questions):
            q_id = q["id"]
            q_text = q["question_text"]
            btl = q["btl_tag"]
            marks = q["marks"]
            has_fig = q["has_figure"]
            co_tag = q["co_tag"]
            c_code = q["course_code"]
            image_url = q.get("image_url", "")
            q_num = q.get("question_number", "")

            # Map Question to Course (dual edge: BELONGS_TO + TESTED_BY)
            query = """
                MERGE (q:Question {id: $q_id})
                SET q.q_id = $q_id,
                    q.text = $q_text, 
                    q.btl = $btl, 
                    q.marks = $marks, 
                    q.has_figure = $has_fig, 
                    q.image_url = $image_url,
                    q.document_id = $doc_id,
                    q.course_code = $c_code,
                    q.question_number = $q_num
                MERGE (c:Course {code: $c_code})
                MERGE (q)-[:BELONGS_TO]->(c)
                MERGE (c)-[:TESTED_BY]->(q)
            """
            session.run(
                query,
                q_id=q_id,
                q_text=q_text,
                btl=btl,
                marks=marks,
                has_fig=has_fig,
                c_code=c_code,
                image_url=image_url,
                doc_id=str(document_id) if document_id else "",
                q_num=q_num,
            )

            # Map to BloomLevel if btl present
            if btl:
                session.run(
                    """
                    MATCH (q:Question {id: $q_id})
                    MERGE (bl:BloomLevel {level: $btl})
                    MERGE (q)-[:HAS_BTL]->(bl)
                    """,
                    q_id=q_id,
                    btl=str(btl).upper(),
                )

            # Map to VisualArtefact if image_url present
            if image_url and image_url != "None":
                img_hash = q.get("image_hash") or hashlib.md5(image_url.encode()).hexdigest()
                session.run(
                    """
                    MATCH (q:Question {id: $q_id})
                    MERGE (va:VisualArtefact {url: $image_url})
                    SET va.question_id = $q_id, va.hash = $hash
                    MERGE (q)-[:BINDS_DIAGRAM]->(va)
                    """,
                    q_id=q_id,
                    image_url=image_url,
                    hash=img_hash,
                )

            # Semantic topic mapping (dual edge: TESTS + TESTS_TOPIC)
            topic_ids = mapping.get(i, [])
            for t_id in topic_ids:
                session.run(
                    """
                    MATCH (q:Question {id: $q_id})
                    MATCH (t:Topic {topic_id: $t_id})
                    MERGE (q)-[:TESTS]->(t)
                    MERGE (q)-[:TESTS_TOPIC]->(t)
                """,
                    q_id=q_id,
                    t_id=t_id,
                )

            # Map to CO if exists
            if co_tag:
                co_query = """
                    MATCH (q:Question {id: $q_id})
                    MATCH (c:Course {code: $c_code})
                    MERGE (co:CourseOutcome {id: $co_id, course_code: $c_code})
                    MERGE (q)-[:MAPPED_TO_CO]->(co)
                    MERGE (co)-[:BELONGS_TO]->(c)
                """
                session.run(co_query, q_id=q_id, c_code=c_code, co_id=co_tag)


def execute_neo4j_pyq_search(neo4j_driver, question: str) -> list:
    """Search for PYQ questions in Neo4j by keyword matching on question text."""
    stop_words = {
        "get",
        "me",
        "a",
        "the",
        "all",
        "questions",
        "question",
        "problems",
        "problem",
        "solve",
        "solved",
        "solutions",
        "solution",
        "calculate",
        "write",
        "analyse",
        "analysis",
        "determine",
        "derive",
        "sketch",
        "draw",
        "obtain",
        "evaluate",
        "on",
        "about",
        "find",
        "show",
        "list",
        "give",
        "related",
        "are",
        "there",
        "any",
        "is",
        "what",
        "how",
        "why",
        "who",
        "where",
        "can",
        "you",
        "tell",
        "explain",
        "describe",
        "provide",
        "in",
        "of",
        "to",
        "for",
        "with",
        "and",
        "or",
        "not",
        "this",
        "that",
        "do",
        "does",
        "did",
        "have",
        "has",
        "had",
        "would",
        "could",
        "should",
        "some",
        "from",
        "by",
        "an",
        "it",
        "they",
        "we",
        "he",
        "she",
        "which",
    }
    words = [
        w.strip(".,!?-'\"")
        for w in question.lower().split()
        if len(w) > 2 and w not in stop_words
    ]

    if not words:
        return []

    with neo4j_driver.session() as session:
        records = session.run(
            """
            MATCH (q:Question)
            WHERE all(word IN $words WHERE replace(toLower(q.text), "'", "") CONTAINS word)
            OPTIONAL MATCH (q)-[:BELONGS_TO]->(c:Course)
            OPTIONAL MATCH (c_t:Course)-[:TESTED_BY]->(q)
            RETURN q.text AS q_text, q.btl AS btl, q.marks AS marks,
                   q.image_url AS image_url, coalesce(c.code, c_t.code, q.course_code, '') AS course_code,
                   q.question_number AS q_num
            LIMIT 20
        """,
            words=words,
        ).data()
    return records


# 8. Hybrid Search & Query Classification


async def hybrid_search_rrf(
    db, query_text: str, query_embedding: list[float], k=5, rrf_k=60
):
    """
    Performs Reciprocal Rank Fusion (RRF) using PostgreSQL pgvector (semantic)
    and tsvector (keyword) on the document_chunks table.
    """
    if not query_embedding:
        return []

    # Convert embedding list to string format for pgvector
    embedding_str = "[" + ",".join(map(str, query_embedding)) + "]"

    # We use a FULL OUTER JOIN to combine ranks and calculate RRF score
    sql_query = text("""
        WITH vector_ranked AS (
            SELECT id, content, course_code,
                   ROW_NUMBER() OVER (ORDER BY embedding <=> :embedding) AS rank
            FROM document_chunks
            WHERE embedding IS NOT NULL
            LIMIT 50
        ),
        text_ranked AS (
            SELECT id, content, course_code,
                   ROW_NUMBER() OVER (ORDER BY ts_rank(tsv_content, query) DESC) AS rank
            FROM document_chunks,
                 plainto_tsquery('english', :query_text) query
            WHERE tsv_content @@ query
            LIMIT 50
        ),
        rrf AS (
            SELECT
                COALESCE(v.id, t.id) AS id,
                COALESCE(v.content, t.content) AS content,
                COALESCE(v.course_code, t.course_code) AS course_code,
                (COALESCE(1.0/(:rrf_k + v.rank), 0.0) + COALESCE(1.0/(:rrf_k + t.rank), 0.0)) AS rrf_score
            FROM vector_ranked v
            FULL OUTER JOIN text_ranked t ON v.id = t.id
        )
        SELECT id, content, course_code, rrf_score
        FROM rrf
        ORDER BY rrf_score DESC
        LIMIT :k
    """)

    try:
        result = await db.execute(
            sql_query,
            {
                "embedding": embedding_str,
                "query_text": query_text,
                "rrf_k": rrf_k,
                "k": k,
            },
        )

        chunks = []
        for row in result:
            chunks.append(
                {
                    "id": row.id,
                    "content": row.content,
                    "course_code": row.course_code,
                    "rrf_score": row.rrf_score,
                }
            )
        return chunks
    except Exception as e:
        print(f"Hybrid search error: {e}")
        return []


def fetch_course_topics(neo4j_driver, course_code: str) -> str:
    with neo4j_driver.session() as session:
        records = session.run(
            """
            MATCH (c:Course {code: $c_code})-[:HAS_UNIT]->(u:Unit)-[:HAS_TOPIC]->(t:Topic)
            RETURN u.title AS unit, t.topic_id AS topic_id, t.name AS topic_name
            ORDER BY u.title, t.order
        """,
            c_code=course_code,
        ).data()

    if not records:
        return "No syllabus found."

    syllabus = []
    for r in records:
        syllabus.append(f"[{r['topic_id']}] {r['unit']} - {r['topic_name']}")
    return "\n".join(syllabus)


ACRONYMS_TOPIC_MAP = {
    'pda': 'pushdown automata',
    'nfa': 'nondeterministic finite automata',
    'dfa': 'deterministic finite automata',
    'cfg': 'context free grammar',
    'cnf': 'chomsky normal form',
    'pcp': 'post correspondence problem',
    'kcl': 'kirchhoff current law',
    'kvl': 'kirchhoff voltage law',
    'bjt': 'transistor bipolar junction',
    'psd': 'power spectral density',
    'wss': 'wide sense stationary',
    '2pc': 'two phase commit'
}


def _expand_topic_acronyms(text: str) -> str:
    words = text.lower().split()
    expanded = []
    for w in words:
        w_clean = w.strip('.,()[]:"\'')
        if w_clean in ACRONYMS_TOPIC_MAP:
            expanded.append(f"{w} {ACRONYMS_TOPIC_MAP[w_clean]}")
        else:
            expanded.append(w)
    return " ".join(expanded)


def _topic_keyword_overlap(q_text: str, topic_name: str) -> float:
    stop = {'and', 'the', 'for', 'with', 'in', 'of', 'to', 'a', 'an', 'on', 'by', 'from', 'at', 'as', 'is', 'are', 'using'}
    t_words = [w.strip(".,()[]:'\"") for w in topic_name.lower().split() if len(w) > 2 and w not in stop]
    q_lower = _expand_topic_acronyms(q_text).lower()
    if not t_words:
        return 0.0
    matches = sum(1 for w in t_words if w in q_lower)
    return matches / len(t_words)


def _topic_cosine_similarity(v1, v2) -> float:
    dot = sum(a * b for a, b in zip(v1, v2))
    norm1 = sum(a * a for a in v1) ** 0.5
    norm2 = sum(b * b for b in v2) ** 0.5
    return dot / (norm1 * norm2) if norm1 and norm2 else 0.0


async def semantic_map_questions_to_topics(
    course_code: str, syllabus_context: str, questions: list
) -> dict:
    """
    Accurately maps every question to its specific Topic node in Neo4j using
    a hybrid dense vector embedding + morphological keyword overlap scoring function.
    Bypasses token-heavy external LLM calls to prevent timeouts and guarantees
    every question connects to its genuine topic rather than falling back to Unit 1 Topic 1.
    """
    if not syllabus_context or syllabus_context == "No syllabus found.":
        return {}

    # Parse syllabus topics: [{topic_id}] {unit} - {topic_name}
    topics = []
    for line in syllabus_context.split("\n"):
        line_clean = line.strip()
        if not line_clean:
            continue
        m = re.match(r"\[(.*?)\]\s*(.*)", line_clean)
        if m:
            tid = m.group(1)
            full_name = m.group(2)
            tname = full_name.split(" - ")[-1] if " - " in full_name else full_name
            topics.append({"id": tid, "name": tname, "full": full_name})

    if not topics:
        return {}

    fallback_topic = topics[0]["id"]
    final_mapping = {}

    # Pre-embed course syllabus topics
    topic_embeddings = []
    for t in topics:
        emb = await get_embedding(t["full"][:300])
        topic_embeddings.append((t, emb))

    for i, q in enumerate(questions):
        q_text = (q.get("question_text") or q.get("text") or "").strip()
        if not q_text:
            final_mapping[i] = [fallback_topic]
            continue

        q_exp = _expand_topic_acronyms(q_text)
        q_emb = await get_embedding(q_exp[:500])

        scored = []
        for t, t_emb in topic_embeddings:
            cos = _topic_cosine_similarity(q_emb, t_emb) if q_emb and t_emb else 0.0
            kw = _topic_keyword_overlap(q_text, t["name"])
            hybrid = 0.5 * cos + 0.5 * kw
            scored.append((hybrid, t["id"]))

        scored.sort(key=lambda x: x[0], reverse=True)

        # Select top 1 or 2 highest-scoring topics
        top_score, top_tid = scored[0]
        selected_tids = [top_tid]
        if len(scored) > 1 and scored[1][0] >= 0.65 and scored[1][0] >= top_score * 0.88:
            selected_tids.append(scored[1][1])

        final_mapping[i] = selected_tids

    return final_mapping


def classify_pdf_type(file_path: str) -> dict:
    """
    Auto-classifies an uploaded PDF document into either 'syllabus' or 'pyq'.
    Extracts department & year for syllabus, or course code for PYQ.
    Works with Amrita curriculum files and any past year question papers.
    """
    import pymupdf as fitz
    import re
    import os

    try:
        doc = fitz.open(file_path)
        page_count = len(doc)
        text = ""
        for p in range(min(3, page_count)):
            text += doc[p].get_text() + " "
        doc.close()
    except Exception as e:
        print(f"[CLASSIFIER] Error reading PDF {file_path}: {e}")
        return {"doc_type": "pyq", "course_code": "UNKNOWN", "pages": 1}

    fname = os.path.basename(file_path).upper()
    text_upper = text.upper()

    # 1. Syllabus detection
    if page_count > 25 or "CURRICULUM" in text_upper or "SYLLABUS" in fname or "SCHEME OF INSTRUCTION" in text_upper:
        dept = "CSE"
        if "COMPUTER SCIENCE" in text_upper or "CSE" in fname:
            dept = "CSE"
        elif "ELECTRICAL" in text_upper or "EEE" in fname:
            dept = "EEE"
        elif "ELECTRONICS" in text_upper or "ECE" in fname:
            dept = "ECE"
        elif "MECHANICAL" in text_upper or "MECH" in fname:
            dept = "MECH"
        elif "ARTIFICIAL INTELLIGENCE" in text_upper or "AIE" in fname:
            dept = "AIE"
        elif "CYBER" in text_upper or "CYS" in fname:
            dept = "CYS"

        year = "2023"
        ym = re.search(r"\b(20\d{2})\b", fname) or re.search(r"\b(20\d{2})\b", text_upper)
        if ym:
            year = ym.group(1)
        return {"doc_type": "syllabus", "department": dept, "year": year, "pages": page_count}

    # 2. PYQ detection
    cc_match = re.search(r"(\d{2}[A-Z]{3}\d{3})", fname)
    if not cc_match:
        cc_match = re.search(r"Course\s*(?:Code)?\s*[:\-]?\s*(\d{2}[A-Z]{3}\d{3})", text, re.I)
    if not cc_match:
        cc_match = re.search(r"\b(\d{2}[A-Z]{3}\d{3})\b", text)
    if not cc_match:
        cc_match = re.search(r"\b([A-Z]{2,4}\d{3})\b", fname) or re.search(r"\b([A-Z]{2,4}\d{3})\b", text)

    course_code = cc_match.group(1).upper() if cc_match else "UNKNOWN"
    return {"doc_type": "pyq", "course_code": course_code, "pages": page_count}

