"""backend/parsers/visual_cropper.py — VLIL vision analysis & PyMuPDF graphic bbox helpers.

Uses gemma4:12b-it-qat at high resolution (max 1024px) to extract structured
academic concepts, diagram types, components, and values without brittle hardcoded overrides.
"""
from __future__ import annotations

import base64
import hashlib
import io
import ipaddress
import json
import logging
import os
import re
from collections import OrderedDict
from urllib.parse import urlparse

from PIL import Image

try:
    import httpx
except ImportError:
    httpx = None

try:
    import pymupdf as fitz
except ImportError:
    try:
        import fitz
    except ImportError:
        fitz = None

from backend.config import settings
from backend.core.http_client import GPU_SEMAPHORE, get_shared_http_client

logger = logging.getLogger("mach_ai.visual_cropper")

# Global LRU cache for structured vision results
_vision_cache: OrderedDict[str, dict] = OrderedDict()
_VISION_CACHE_SIZE = 200


def _get_cached_vision(image_hash: str) -> dict | None:
    if image_hash in _vision_cache:
        _vision_cache.move_to_end(image_hash)
        return _vision_cache[image_hash]
    return None


def _set_cached_vision(image_hash: str, data: dict) -> None:
    _vision_cache[image_hash] = data
    if len(_vision_cache) > _VISION_CACHE_SIZE:
        _vision_cache.popitem(last=False)


def _is_safe_public_url(url: str) -> bool:
    """Validates remote image URLs against private/loopback/metadata SSRF targets."""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return False
        host = parsed.hostname.lower()
        if host in ("localhost", "127.0.0.1", "0.0.0.0", "::1") or host.endswith(".local"):
            return False
        try:
            ip = ipaddress.ip_address(host)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                return False
        except ValueError:
            pass
        return True
    except Exception:
        return False


async def load_image_as_b64(img_path_or_url: str) -> str | None:
    """Load image safely from uploads/static storage or validated public URL."""
    try:
        if img_path_or_url.startswith(("http://", "https://")):
            if not _is_safe_public_url(img_path_or_url):
                logger.warning("Blocked unsafe image URL fetch attempt: %s", img_path_or_url)
                return None
            if get_shared_http_client:
                client = get_shared_http_client()
                resp = await client.get(img_path_or_url, timeout=15.0)
                resp.raise_for_status()
                return base64.b64encode(resp.content).decode("utf-8")
            async with httpx.AsyncClient() as temp_client:
                resp = await temp_client.get(img_path_or_url, timeout=15.0)
                resp.raise_for_status()
                return base64.b64encode(resp.content).decode("utf-8")

        uploads_root = os.path.realpath(settings.uploads_dir)
        candidate = img_path_or_url
        if img_path_or_url.startswith("/static/"):
            rel = img_path_or_url[len("/static/"):].lstrip("/")
            candidate = os.path.join(uploads_root, rel)

        resolved = os.path.realpath(candidate)
        if not (resolved == uploads_root or resolved.startswith(uploads_root + os.sep)):
            logger.warning("Blocked local image path outside uploads directory: %s", img_path_or_url)
            return None
        if os.path.isfile(resolved):
            with open(resolved, "rb") as f:
                return base64.b64encode(f.read()).decode("utf-8")
    except Exception as e:
        logger.warning("Error loading image as base64: %s", e)
    return None


def _downscale_image_b64(b64_str: str, max_size: int = 1024) -> str:
    """Downscales a base64 image only if it exceeds max_size, preserving crisp labels."""
    try:
        img_data = base64.b64decode(b64_str)
        img = Image.open(io.BytesIO(img_data))
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        if max(img.size) > max_size:
            img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
            buffered = io.BytesIO()
            img.save(buffered, format="JPEG", quality=90)
            return base64.b64encode(buffered.getvalue()).decode("utf-8")
        return b64_str
    except Exception:
        return b64_str


async def analyze_diagram_structured(base64_image: str) -> dict:
    """Analyze a diagram or question crop using gemma4:12b-it-qat at crisp 1024px resolution."""
    img_hash = hashlib.md5(base64_image.encode("utf-8")).hexdigest()
    cached = _get_cached_vision(img_hash)
    if cached:
        return cached

    optimized_b64 = _downscale_image_b64(base64_image, max_size=1024)
    prompt = (
        "Analyze this academic diagram, circuit schematic, waveform, automata, flowchart, or equation.\n"
        "Look closely at all text, labels, theorem names (e.g. Mesh Analysis, Kirchhoff's Laws, "
        "Superposition Theorem, Equivalent Resistance, Full Wave Rectifier, Phasor Diagram, DFA, BST), "
        "components, and numeric values.\n"
        "IMPORTANT: 'topic' MUST be the specific theorem, method, circuit/waveform type, or sub-concept "
        "(e.g. 'Mesh Analysis', NOT broad subjects like 'Electrical Engineering' or 'Circuit Analysis').\n"
        "Return ONLY a JSON object with keys:\n"
        '{"topic": "<specific concept or theorem>", '
        '"subtopics": ["<related concept 1>", "<related concept 2>"], '
        '"diagram_type": "<circuit|waveform|automata|graph|tree|table|equation|other>", '
        '"components": ["<element 1>", "<element 2>"], '
        '"given_values": ["<value/unit 1>", "<value/unit 2>"], '
        '"summary": "<1 concise sentence describing what is shown and asked>"}'
    )

    try:
        async with GPU_SEMAPHORE:
            client = get_shared_http_client() if get_shared_http_client else httpx.AsyncClient()
            response = await client.post(
                f"{settings.ollama_base_url}/api/generate",
                json={
                    "model": settings.ollama_model,
                    "prompt": prompt,
                    "images": [optimized_b64],
                    "format": "json",
                    "think": False,
                    "stream": False,
                    "options": {
                        "num_ctx": 4096,
                        "num_predict": 260,
                        "temperature": 0.0,
                        "top_p": 0.9,
                    },
                },
                timeout=45.0,
            )
            response.raise_for_status()
            res_data = response.json()

        resp_text = (res_data.get("response") or "").strip()
        parsed: dict = {}
        if resp_text:
            try:
                parsed = json.loads(resp_text)
            except Exception:
                m = re.search(r"\{.*\}", resp_text, re.DOTALL)
                if m:
                    try:
                        parsed = json.loads(m.group(0))
                    except Exception:
                        parsed = {}

        def _clean_list(val) -> list[str]:
            if isinstance(val, list):
                items = [str(v).strip() for v in val]
            elif val:
                items = [str(val).strip()]
            else:
                items = []
            return [x for x in items if x and x.lower() not in ("none", "null", "n/a", "unknown")]

        topic = str(parsed.get("topic") or "").strip()
        if topic.lower() in ("none", "null", "n/a", "unknown"):
            topic = ""
        subtopics = _clean_list(parsed.get("subtopics"))
        diagram_type = str(parsed.get("diagram_type") or "other").strip().lower()
        components = _clean_list(parsed.get("components"))
        given_values = _clean_list(parsed.get("given_values"))
        summary = str(parsed.get("summary") or "").strip()

        parts = [p for p in ([topic] + subtopics[:3] + components[:4]) if p]
        description = ", ".join(dict.fromkeys(parts))
        if summary and summary.lower() not in description.lower():
            description = f"{description} — {summary}" if description else summary

        if not description and resp_text:
            description = re.sub(r"\s+", " ", resp_text)[:250]

        result = {
            "topic": topic,
            "subtopics": subtopics,
            "diagram_type": diagram_type,
            "components": components,
            "given_values": given_values,
            "summary": summary,
            "description": description,
        }
        if description:
            _set_cached_vision(img_hash, result)
        return result
    except Exception as e:
        logger.warning("Error analyzing image: %r", e)
        return {
            "topic": "",
            "subtopics": [],
            "diagram_type": "other",
            "components": [],
            "given_values": [],
            "summary": "",
            "description": f"Error analyzing image: {e!r}",
        }


async def describe_uploaded_image(base64_image: str) -> str:
    """Return a concise text description/topic string for a base64 academic image."""
    structured = await analyze_diagram_structured(base64_image)
    return structured.get("description") or structured.get("topic") or ""


async def ocr_scanned_page_with_vision(base64_image: str) -> str:
    """Transcribe a scanned PDF page using gemma4 vision OCR when text extraction is empty."""
    optimized_b64 = _downscale_image_b64(base64_image, max_size=1280)
    prompt = (
        "Transcribe all printed text, course codes, questions, marks, [CO]/[BTL] tags, "
        "and equations cleanly from this academic document page. Preserve question numbering."
    )
    try:
        async with GPU_SEMAPHORE:
            client = get_shared_http_client() if get_shared_http_client else httpx.AsyncClient()
            resp = await client.post(
                f"{settings.ollama_base_url}/api/generate",
                json={
                    "model": settings.ollama_model,
                    "prompt": prompt,
                    "images": [optimized_b64],
                    "think": False,
                    "stream": False,
                    "options": {"num_ctx": 4096, "num_predict": 1200, "temperature": 0.0},
                },
                timeout=60.0,
            )
            resp.raise_for_status()
            return (resp.json().get("response") or "").strip()
    except Exception as e:
        logger.warning("Scanned page OCR notice: %s", e)
        return ""


def _render_page_image(page, scale: float = 2.0) -> Image.Image:
    """Render a PyMuPDF page into a Pillow Image at the given resolution scale."""
    matrix = fitz.Matrix(scale, scale)
    pixmap = page.get_pixmap(matrix=matrix)
    return Image.frombytes("RGB", [pixmap.width, pixmap.height], pixmap.samples)


def _expand_bbox_for_graphics(
    page, y_top: float, y_bottom: float, page_height: float, hard_ceiling: float
) -> float:
    """Expand y_bottom to enclose raster images or vector drawings belonging to the question."""
    new_bottom = y_bottom
    look_ahead = min(y_bottom + 45, hard_ceiling)

    for img_info in page.get_image_info():
        bbox = img_info.get("bbox")
        if bbox:
            img_y0, img_y1 = bbox[1], bbox[3]
            if y_top - 5 <= img_y0 <= look_ahead and img_y1 <= hard_ceiling + 10:
                new_bottom = max(new_bottom, min(img_y1 + 6, hard_ceiling))

    for drawing in page.get_drawings():
        rect = drawing.get("rect")
        if rect:
            # Skip tiny hairline horizontal rules (< 4pt tall) that are only underlines
            if rect.height < 3 and rect.width > page.rect.width * 0.7:
                continue
            draw_y0, draw_y1 = rect.y0, rect.y1
            if y_top - 5 <= draw_y0 <= look_ahead and draw_y1 <= hard_ceiling + 10:
                new_bottom = max(new_bottom, min(draw_y1 + 6, hard_ceiling))

    return min(new_bottom, hard_ceiling)


def region_has_graphics(page, y_top: float, y_bottom: float) -> bool:
    """Return True if raster images or meaningful vector diagrams overlap [y_top, y_bottom]."""
    for img_info in page.get_image_info():
        bbox = img_info.get("bbox")
        if bbox:
            img_y0, img_y1 = bbox[1], bbox[3]
            if img_y1 >= y_top - 5 and img_y0 <= y_bottom + 15:
                # Ignore tiny header/footer logos at very top (< 60pt) unless inside span
                if img_y1 > 65 or img_y0 >= y_top - 2:
                    return True

    # Count non-trivial vector drawing primitives inside the question box
    draw_hits = 0
    for drawing in page.get_drawings():
        rect = drawing.get("rect")
        if not rect:
            continue
        if rect.height < 2 and rect.width < 12:
            continue
        if rect.y1 >= y_top - 5 and rect.y0 <= y_bottom + 15:
            draw_hits += 1
            if draw_hits >= 3:
                return True
    return False


def process_pyq_visuals(file_path: str) -> list[dict]:
    """Fallback vertical page slicer when deterministic question numbering is absent."""
    if not fitz:
        logger.warning("PyMuPDF not installed.")
        return []

    chunks = []
    try:
        with fitz.open(file_path) as doc:
            zoom_matrix = fitz.Matrix(2, 2)
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

                y_coords = sorted(y_coords)
                if not y_coords:
                    y_coords = [0]
                elif y_coords[0] > 50:
                    y_coords.insert(0, 0)

                scaled_y = [int(y * 2) for y in y_coords]
                pixmap = page.get_pixmap(matrix=zoom_matrix)
                scaled_y.append(pixmap.height)

                img = Image.frombytes("RGB", [pixmap.width, pixmap.height], pixmap.samples)

                for i in range(len(scaled_y) - 1):
                    top = scaled_y[i]
                    crop_top = max(0, top - 20)
                    crop_bottom = scaled_y[i + 1]

                    if crop_bottom - crop_top < 50:
                        continue

                    chunk_img = img.crop((0, crop_top, pixmap.width, crop_bottom))
                    img_byte_arr = io.BytesIO()
                    chunk_img.save(img_byte_arr, format="JPEG", quality=88)
                    b64_string = base64.b64encode(img_byte_arr.getvalue()).decode("utf-8")

                    chunks.append(
                        {"page": page_num, "chunk_index": i, "base64": b64_string}
                    )
    except Exception as e:
        logger.warning("Error slicing PYQ pages: %s", e)

    return chunks
