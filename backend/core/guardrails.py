"""backend/core/guardrails.py — Tier-0 Security, Prompt Injection Sanitizer & Academic Guardrails.

Executes in < 1ms to reject adversarial prompts, jailbreaks, and out-of-scope non-academic queries
before consuming any GPU or database resources. Also sanitizes untrusted PDF text chunks.
"""

import re

# Fast deterministic regex patterns for direct prompt injections and jailbreaks
PROMPT_INJECTION_PATTERNS = [
    r"ignore\s+(?:all\s+)?(?:previous|above|prior)\s+instructions",
    r"disregard\s+(?:all\s+)?(?:previous|prior)\s+instructions",
    r"system\s*prompt",
    r"reveal\s+(?:your\s+)?instructions",
    r"you\s+are\s+now\s+(?:DAN|developer\s+mode|unfiltered)",
    r"act\s+as\s+(?:an?\s+)?unrestricted",
    r"jailbreak",
    r"bypass\s+(?:filter|safety|policy)",
    r"drop\s+table",
    r"delete\s+from\s+\w+",
    r"union\s+select",
    r"<\s*script\s*>",
    r"exec\s*\(",
    r"eval\s*\(",
    r"/bin/(?:ba)?sh",
    r"rm\s+-rf",
    r"format\s+c:",
]

COMPILED_INJECTION_RE = re.compile("|".join(PROMPT_INJECTION_PATTERNS), re.IGNORECASE)

# Off-topic non-academic indicators
OFF_TOPIC_PATTERNS = [
    r"\b(?:recipe|cocktail|bake\s+a\s+cake|roast\s+chicken)\b",
    r"\b(?:horoscope|astrology|zodiac\s+sign)\b",
    r"\b(?:celebrity\s+gossip|dating\s+advice|flirt\s+with\s+me)\b",
    r"\b(?:crypto\s+trading|buy\s+bitcoin|stock\s+tips)\b",
]

COMPILED_OFF_TOPIC_RE = re.compile("|".join(OFF_TOPIC_PATTERNS), re.IGNORECASE)

# Academic anchor keywords — if present, always treat query as academic even if edge words appear
ACADEMIC_ANCHORS = {
    "course", "syllabus", "exam", "question", "pyq", "btl", "co1", "co2", "co3",
    "co4", "co5", "co6", "bloom", "unit", "credit", "ltpc", "prerequisite",
    "semester", "marks", "circuits", "automata", "algorithm", "probability",
    "database", "systems", "network", "theory", "math", "calculus", "linear",
    "compiler", "microprocessor", "architecture", "amrita", "engineering"
}


def sanitize_student_query(query: str) -> tuple[bool, str]:
    """
    Tier-0 fast filter (< 1ms).
    Returns (is_safe: bool, advisory_message: str).
    """
    if not query or not query.strip():
        return False, "Please enter a valid academic question or course query."

    # 1. Security & Prompt Injection Check
    if COMPILED_INJECTION_RE.search(query):
        return False, (
            "Security Notice: System overrides, injection patterns, and unauthorized command "
            "sequences are strictly prohibited. Please submit an academic question related to "
            "Amrita curriculum syllabi or past year examination papers."
        )

    # 2. Scope & Academic Boundary Check
    q_lower = query.lower()
    has_academic_anchor = any(anchor in q_lower for anchor in ACADEMIC_ANCHORS)

    if not has_academic_anchor and COMPILED_OFF_TOPIC_RE.search(query):
        return False, (
            "Academic Boundary Notice: M.A.C.H. AI is dedicated exclusively to academic "
            "curriculum guidance, course structures, and past examination papers. "
            "Please ask a course, topic, or exam preparation query."
        )

    # Clean query passed guardrails
    return True, ""


def sanitize_retrieved_context(chunk: str) -> str:
    """
    Sanitizes untrusted text chunks extracted from uploaded documents
    before passing them into LLM generation prompts, neutralizing indirect injections.
    """
    if not chunk:
        return ""
    # Strip potential instruction overrides or fake system delimiters (both bracketed and XML style)
    cleaned = re.sub(r"(?i)\[\s*/?\s*(?:system|assistant|human|user)\s*\]", "", chunk)
    cleaned = re.sub(r"(?i)<\s*/?\s*(?:system|assistant|human|user)\s*>", "", cleaned)
    cleaned = re.sub(r"(?i)```\s*(?:system|bash|sh)", "```text", cleaned)
    return cleaned.strip()

