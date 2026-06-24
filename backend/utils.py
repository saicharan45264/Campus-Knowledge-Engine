import os
import base64
import httpx
import json

from dotenv import load_dotenv

# Load environment variables from the .env file at the project root.
# os.path.dirname(__file__) gives us the 'backend/' folder.
# '..' moves us one level up to the root folder where '.env' is stored.
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))

# PyMuPDF (imported as 'fitz') is the library we use to read text out of PDF files.
# We wrap it in a try/except block just in case it fails to install on some machines.
try:
    import fitz
except ImportError:
    fitz = None

# -----------------------------------------------------------------------------
# Ollama Configuration
# -----------------------------------------------------------------------------
# Fetch the base URL where Ollama is running (defaults to localhost:11434).
OLLAMA_BASE_URL    = os.getenv("OLLAMA_BASE_URL",    "http://localhost:11434")

# This large model (12 billion parameters) is used ONLY for generating the final written answers.
OLLAMA_MODEL       = os.getenv("OLLAMA_MODEL",       "gemma4:12b-it-qat")

# This small, fast model (137 million parameters) is used ONLY to convert text into math vectors.
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")


# =============================================================================
# 1. PDF Processing — Split a PDF file into small text chunks
# =============================================================================
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


# =============================================================================
# 1b. Visual Content Extraction — Describe equations, diagrams, and circuits
# =============================================================================
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
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": prompt,
                    "images": [base64_image],
                    "stream": False,
                    "options": {"num_ctx": 8192}
                },
                timeout=180.0  # Vision processing can take longer than text
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


def process_pdf_visuals(file_path: str) -> list[dict]:
    """
    Renders every page of a PDF as a high-resolution image and returns the
    raw image data (as base64 strings) for each page.
    The actual vision API calls happen asynchronously in the background task.
    Returns a list of {"page": int, "base64": str} dictionaries.
    """
    if not fitz:
        print("Error: PyMuPDF is not installed. Cannot extract visuals.")
        return []

    pages = []
    try:
        doc = fitz.open(file_path)
        # 2x zoom renders at 144 DPI instead of the default 72 DPI,
        # giving the vision model a much clearer image to analyze.
        zoom_matrix = fitz.Matrix(2, 2)

        for page_num in range(len(doc)):
            # Render the entire page as a PNG image
            pixmap = doc[page_num].get_pixmap(matrix=zoom_matrix)
            # Convert the raw pixel data to PNG bytes
            image_bytes = pixmap.tobytes("png")
            # Encode to base64 string (required by Ollama's images parameter)
            b64_string = base64.b64encode(image_bytes).decode("utf-8")
            pages.append({"page": page_num, "base64": b64_string})

        doc.close()
    except Exception as e:
        print(f"[Vision] Error rendering PDF pages: {e}")

    return pages


async def describe_uploaded_image(base64_image: str) -> str:
    """
    Takes a base64-encoded image uploaded by a student (e.g. a photo of an
    equation, formula, or circuit diagram) and asks the vision model to
    describe what it contains. The description is then used as a search query
    to find related curriculum content.
    """
    prompt = (
        "You are an expert academic content analyst. A student has uploaded an image. "
        "Examine it carefully and provide a detailed description of what you see.\n\n"
        "If it contains an equation or formula, write it out in plain text and explain "
        "what each variable represents and which topic or concept it belongs to.\n\n"
        "If it contains a circuit diagram, describe every component, their connections, "
        "and the overall purpose of the circuit.\n\n"
        "If it contains any other diagram (flowchart, block diagram, graph), describe "
        "the elements and the concept it illustrates.\n\n"
        "Be thorough. Your description will be used to search a curriculum database, "
        "so include all relevant academic terms and concept names."
    )

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": prompt,
                    "images": [base64_image],
                    "stream": False,
                    "options": {"num_ctx": 8192}
                },
                timeout=180.0
            )
            response.raise_for_status()
            return response.json().get("response", "Could not analyze the image.")
    except Exception as e:
        return f"Error analyzing image: {e}"


# =============================================================================
# 2. Vector Embeddings — Convert text into a mathematical array
# =============================================================================
import asyncio

async def get_embedding(text: str, retries=3) -> list[float]:
    """
    Sends a string of text to Ollama and asks for its 'vector embedding'.
    An embedding is an array of 768 numbers that represents the 'meaning' of the text.
    We use the specialized 'nomic-embed-text' model for this.
    """
    for attempt in range(retries):
        try:
            # We use httpx.AsyncClient to make non-blocking HTTP requests to Ollama
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{OLLAMA_BASE_URL}/api/embeddings",
                    json={"model": OLLAMA_EMBED_MODEL, "prompt": text},
                    timeout=60.0
                )
                response.raise_for_status()
                # The API returns a JSON object with an 'embedding' array
                return response.json().get("embedding", [])
        except Exception as e:
            print(f"Failed to generate embedding (attempt {attempt+1}/{retries}): {e}")
            await asyncio.sleep(2)
    return []


# =============================================================================
# 3. Knowledge Graph Extraction — Find concepts and their relationships
# =============================================================================
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
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json={
                    "model":  OLLAMA_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json"  # Forces Ollama to return valid JSON
                },
                timeout=120.0
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


# =============================================================================
# 4. Save to Neo4j — Write the triplets into the Graph Database
# =============================================================================
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
            subject   = triplet.get("subject")
            
            # Neo4j relationships are typically uppercase with underscores (e.g., IS_RELATED_TO)
            raw_predicate = triplet.get("predicate", "RELATED_TO").upper().replace(" ", "_")
            # Strip out any characters that aren't A-Z, 0-9, or underscore to prevent Cypher syntax errors
            predicate = re.sub(r'[^A-Z0-9_]', '', raw_predicate)
            if not predicate:
                predicate = "RELATED_TO"
                
            obj       = triplet.get("object")

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


# =============================================================================
# 5. Answer Generation — Produce an answer based on retrieved context
# =============================================================================
async def generate_answer(question: str, context: str) -> str:
    """
    Takes the student's original question AND the context we retrieved from
    PostgreSQL and Neo4j, and asks the large LLM to write a helpful answer.
    """
    prompt = f"""
You are CurriculumLens, an academic AI assistant for university students.
Answer the student's question using ONLY the information provided in the Context below.
If the context does not contain enough information to answer, say so clearly.

Context:
{context}

Question: {question}

Answer:
"""

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json={
                    "model":  OLLAMA_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "num_ctx": 8192
                    }
                },
                timeout=120.0
            )
            response.raise_for_status()
            # Return the generated text string
            return response.json().get("response", "I could not generate an answer.")
    except Exception as e:
        return f"Error communicating with the AI model: {e}"


# =============================================================================
# 6. Syllabus Structural Extraction (New Architecture)
# =============================================================================
import re

async def extract_syllabus_structure(text: str, dept: str, year: str) -> dict:
    """
    Extracts the syllabus structure deterministically using Regular Expressions.
    Bypasses the LLM for speed and reliability, parsing hundreds of pages instantly.
    """
    # Find all course headers
    # Example: 23ENG101 TECHNICAL COMMUNICATION L-T-P-C: 2-0-3-3
    course_pattern = re.compile(r'([0-9]{2}[a-zA-Z]{3}[0-9]{3})\s+(.*?)\s+L-T-P-C', re.IGNORECASE)
    
    courses_data = []
    
    # We find all matches, and iterate through them.
    # The text for a course is everything from the end of this match to the start of the next match.
    matches = list(course_pattern.finditer(text))
    
    for i, match in enumerate(matches):
        course_code = match.group(1).strip().upper()
        course_name = match.group(2).strip()
        
        # Get the text block for this course
        start_idx = match.end()
        end_idx = matches[i+1].start() if i + 1 < len(matches) else len(text)
        course_text = text[start_idx:end_idx]
        
        units_data = []
        
        # Now find units inside course_text
        # Matches "Unit 1", "Unit 2", "SyllabusUnit 1", etc.
        unit_pattern = re.compile(r'(?:Syllabus)?Unit\s*(\d)', re.IGNORECASE)
        unit_matches = list(unit_pattern.finditer(course_text))
        
        for j, u_match in enumerate(unit_matches):
            unit_num = u_match.group(1)
            u_start = u_match.end()
            
            # Find the end of this unit (either next unit, or stopping keywords)
            if j + 1 < len(unit_matches):
                u_end = unit_matches[j+1].start()
            else:
                # Find the first occurrence of stopping keywords
                stop_pattern = re.compile(r'^(?:TEXTBOOK|REFERENCE|Evaluation Pattern)', re.IGNORECASE | re.MULTILINE)
                stop_match = stop_pattern.search(course_text, u_start)
                if stop_match:
                    u_end = stop_match.start()
                else:
                    u_end = len(course_text)
                    
            unit_text = course_text[u_start:u_end].strip()
            
            # Extract topics by splitting the unit_text by punctuation
            raw_topics = re.split(r'[,;\.\-\:]', unit_text)
            
            topics_data = []
            for rt in raw_topics:
                t_name = rt.strip().replace('\n', ' ')
                t_name = re.sub(r'\s+', ' ', t_name) # normalize spaces
                
                # Basic cleaning: remove empty topics and very long sentences
                if len(t_name) > 3 and len(t_name) < 150:
                    # Ignore generic words
                    if t_name.lower() not in ['introduction', 'overview', 'summary', 'conclusion']:
                        topics_data.append({
                            "name": t_name,
                            "subtopics": []
                        })
                    
            if topics_data:
                units_data.append({
                    "number": int(unit_num),
                    "title": f"Unit {unit_num}",
                    "topics": topics_data
                })
                
        if units_data:
            courses_data.append({
                "code": course_code,
                "name": course_name,
                "units": units_data
            })
            
    print(f"[Syllabus Parser] Successfully extracted {len(courses_data)} courses from the text!")
    return {"courses": courses_data}

def build_syllabus_kg(neo4j_driver, dept: str, year: str, courses: list):
    if not courses:
        return
    with neo4j_driver.session() as session:
        session.run("MERGE (d:Department {name: $dept})", dept=dept)
        for course in courses:
            c_code = course.get("code")
            c_name = course.get("name")
            if not c_code: continue
            
            session.run("""
                MERGE (d:Department {name: $dept})
                MERGE (c:Course {code: $c_code})
                ON CREATE SET c.name = $c_name, c.year = $year, c.dept = $dept
                MERGE (d)-[:OFFERS]->(c)
            """, dept=dept, c_code=c_code, c_name=c_name, year=year)
            
            for unit in course.get("units", []):
                u_num = str(unit.get("number", ""))
                u_title = unit.get("title", "")
                if not u_title: continue
                
                session.run("""
                    MATCH (c:Course {code: $c_code})
                    MERGE (u:Unit {number: $u_num, title: $u_title, course_code: $c_code})
                    MERGE (c)-[:HAS_UNIT]->(u)
                """, c_code=c_code, u_num=u_num, u_title=u_title)
                
                for topic in unit.get("topics", []):
                    t_name = topic.get("name")
                    if not t_name: continue
                    
                    session.run("""
                        MATCH (u:Unit {number: $u_num, title: $u_title, course_code: $c_code})
                        MERGE (t:Topic {name: $t_name, course_code: $c_code})
                        MERGE (u)-[:HAS_TOPIC]->(t)
                    """, u_num=u_num, u_title=u_title, c_code=c_code, t_name=t_name)
                    
                    for subtopic in topic.get("subtopics", []):
                        if not subtopic: continue
                        session.run("""
                            MATCH (t:Topic {name: $t_name, course_code: $c_code})
                            MERGE (st:SubTopic {name: $subtopic, course_code: $c_code})
                            MERGE (t)-[:HAS_SUBTOPIC]->(st)
                        """, t_name=t_name, c_code=c_code, subtopic=subtopic)


# =============================================================================
# 7. PYQ Structural Extraction and Mapping
# =============================================================================
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
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": prompt,
                    "images": [base64_image],
                    "format": "json",
                    "stream": False,
                    "options": {"num_ctx": 8192}
                },
                timeout=180.0
            )
            response.raise_for_status()
            res_text = response.json().get("response", "{}")
            
            # Robust JSON extraction to handle markdown and extra text
            import re
            match = re.search(r'\{.*\}', res_text, re.DOTALL)
            if match:
                res_text = match.group(0)
                
            data = json.loads(res_text)
            return data.get("questions", [])
    except Exception as e:
        print(f"Failed to extract PYQ questions: {e}")
        return []

def map_questions_to_kg(neo4j_driver, course_code: str, questions: list):
    if not questions:
        return
    with neo4j_driver.session() as session:
        for q in questions:
            q_text = q.get("text", "")
            likely_topic = q.get("likely_topic", "General")
            implicit_formulas = q.get("implicit_formulas", [])
            q_num = str(q.get("question_number", ""))
            marks = str(q.get("marks", ""))
            
            # The QuestionModel groups similar questions by topic/structure
            qm_name = f"Model: {likely_topic}"
            
            session.run("""
                MERGE (c:Course {code: $c_code})
                MERGE (qm:QuestionModel {name: $qm_name, course_code: $c_code})
                MERGE (c)-[:HAS_QUESTION_MODEL]->(qm)
                CREATE (q:Question {
                    text: $q_text, 
                    question_number: $q_num, 
                    marks: $marks, 
                    implicit_formulas: $implicit_formulas
                })
                MERGE (qm)-[:HAS_QUESTION]->(q)
            """, c_code=course_code, qm_name=qm_name, q_text=q_text, q_num=q_num, marks=marks, implicit_formulas=implicit_formulas)
