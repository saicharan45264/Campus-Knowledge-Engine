import httpx
import re
from utils import OLLAMA_BASE_URL, OLLAMA_MODEL

async def identify_topic_from_image(image_b64: str) -> str | None:
    """
    Calls the Ollama vision model to identify the topic of a given diagram/image.
    """
    prompt = "You are a university curriculum assistant. Look at this lecture slide/diagram image. Identify the exact engineering or computer science syllabus topic that this diagram illustrates. Return only the topic name, 3-6 words maximum. Do not explain."
    
    try:
        async with httpx.AsyncClient(headers={"ngrok-skip-browser-warning": "true"}) as client:
            resp = await client.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": prompt,
                    "images": [image_b64],
                    "stream": False,
                    "options": {"temperature": 0.0}
                },
                timeout=180.0
            )
            resp.raise_for_status()
            
            result_text = resp.json().get("response", "").strip()
            print(f"[VisionQuery] Raw LLM response: '{result_text}'")
            if result_text:
                # Clean up any potential markdown or prefixes the LLM might have added
                clean_topic = re.sub(r'^(Topic|The topic is|This is a diagram of)[:\s]*', '', result_text, flags=re.IGNORECASE).strip('."\'*`\n')
                print(f"[VisionQuery] Cleaned topic: '{clean_topic}'")
                return clean_topic
            return None
    except Exception as e:
        print(f"[VisionQuery] Error identifying topic from image: {e}")
        return None
