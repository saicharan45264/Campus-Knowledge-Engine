"""backend/core/intent_router.py — Multi-tier intent classification engine.

Routes student queries using fast deterministic regex rules with semantic
embedding cosine similarity fallback.
"""
import re

from backend.services.embedding_service import (
    cosine_similarity,
    embed_text,
    embed_texts,
    format_query_embedding_text,
)

_SEM_QUERY_RE = re.compile(
    r"\b(?:sem(?:ester)?\s*(?:\d+|i{1,3}v?|vi{0,3})|(?:\d+)(?:st|nd|rd|th)?\s*sem(?:ester)?)\b",
    re.IGNORECASE,
)
_COURSE_CODE_IN_Q_RE = re.compile(r"\b\d{2}[a-z]{3}\d{3}\b", re.IGNORECASE)

_TYPO_REPLACEMENTS = (
    (re.compile(r"\btexbooks?\b"), "textbook"),
    (re.compile(r"\bprerequ(?:ite|iste|sit|isite)s?\b"), "prerequisite"),
    (re.compile(r"\bsylla?bus\b|\bsylabus\b"), "syllabus"),
    (re.compile(r"\bevalution\b|\bevaluaton\b"), "evaluation"),
    (re.compile(r"\brefernces?\b|\breferencs?\b"), "references"),
    (re.compile(r"\bsemister\b|\bsemestr\b"), "semester"),
)


def normalize_query_typos(q_lower: str) -> str:
    """Normalizes common student spelling mistakes before deterministic keyword routing."""
    normalized = q_lower
    for pattern, replacement in _TYPO_REPLACEMENTS:
        normalized = pattern.sub(replacement, normalized)
    return normalized

INTENT_DESCRIPTIONS = {
    "PROBLEM_LIST": (
        "Ask for a list of problems, practice questions, or past year exam questions "
        "on a specific academic topic or concept."
    ),
    "MULTI_HOP_PREREQ": (
        "Ask about prerequisites, what should I know before taking a course, "
        "or what is required or needed for a subject."
    ),
    "GRAPH_PYQ_MAPPING": (
        "Ask about Bloom's taxonomy level (BTL), course outcomes (CO), "
        "or how past year questions map to learning outcomes."
    ),
    "SIMPLE_CURRICULUM": (
        "Ask about syllabus, topics, units, objectives, evaluation pattern, "
        "textbooks, or the curriculum structure of a course."
    ),
    "SIMPLE_PYQ": (
        "Ask for a specific past year question, exam paper, midterm, "
        "end-semester question, or circuit problem."
    ),
    "GENERAL_QUERY": (
        "General open-ended concept question, explanation request, "
        "or academic query outside specific PYQ/syllabus lookup."
    ),
}


class IntentRouter:
    def __init__(self):
        self.intent_embeddings: dict = {}
        self.initialized: bool = False

    async def initialize(self):
        if self.initialized:
            return
        keys = list(INTENT_DESCRIPTIONS.keys())
        prompts = [format_query_embedding_text(INTENT_DESCRIPTIONS[k]) for k in keys]
        vecs = await embed_texts(prompts, batch_size=len(keys))
        for intent, emb in zip(keys, vecs):
            if emb:
                self.intent_embeddings[intent] = emb
        self.initialized = True

    async def classify_intent(
        self, question: str, query_embedding: list[float] | None = None
    ) -> str:
        q_lower = normalize_query_typos(question.lower())
        
        # Tier 1: Fast Deterministic Keyword Routing
        has_pyq_word = any(
            pw in q_lower
            for pw in ["question", "questions", "problem", "problems", "pyq", "pyqs", "exam", "mapped to"]
        )

        # 1. Course Outcome / Bloom's Taxonomy Question Mapping (GRAPH_PYQ_MAPPING)
        if any(k in q_lower for k in [
            "btl", "co1", "co2", "co3", "co4", "co5", "co6", "bloom", "blooms", "mapped to",
            "blooms taxonomy", "level 1", "level 2", "level 3", "level 4", "level 5", "level 6",
            "bt-1", "bt-2", "bt-3", "bt-4", "bt-5", "bt-6",
            "bt 1", "bt 2", "bt 3", "bt 4", "bt 5", "bt 6",
            "bt1", "bt2", "bt3", "bt4", "bt5", "bt6",
            "k1", "k2", "k3", "k4", "k5", "k6"
        ]) or (has_pyq_word and any(co in q_lower for co in ["course outcome", "course outcomes"])):
            return "GRAPH_PYQ_MAPPING"

        # 2. Multi-hop Prerequisites (MULTI_HOP_PREREQ) - Strict explicit keywords only
        if any(k in q_lower for k in [
            "prerequisite", "prerequisites", "before taking", "should i know before",
            "needed before", "before choosing", "prereq", "prereqs", "prior knowledge",
            "pre-requisite", "pre-requisites", "what should i know before", "what to know before"
        ]):
            return "MULTI_HOP_PREREQ"

        # 3. Problem List & PYQs by Topic or Mark (PROBLEM_LIST)
        if any(marker in q_lower for marker in [
            "problems on", "problem on", "questions on", "question on",
            "questions only on", "only questions on", "problems only on", "only problems on",
            "pyqs on", "pyq on", "problems about", "questions about",
            "questions for", "problems for", "find problems", "find questions",
            "get me questions", "give me questions", "show me questions", "show questions",
            "list of practice problems", "practice questions", "practice problems",
            "repeated questions", "frequently asked questions", "important questions",
            "mark questions", "marks questions", "mark problems", "marks problems",
            "pyqs for", "questions like this", "problems like this",
            "find similar", "match this diagram", "similar questions", "match this circuit"
        ]) or ("[uploaded image context:" in q_lower and any(vk in q_lower for vk in ["question", "pyq", "circuit", "resistor", "automata", "find", "similar", "exam"])):
            return "PROBLEM_LIST"

        # 4. Curriculum Structure, Syllabus, Semesters, Electives, Credits, Outcomes, Textbooks (SIMPLE_CURRICULUM)
        if (
            any(k in q_lower for k in [
                "syllabus", "curriculum", "evaluation pattern", "assessment pattern",
                "marks distribution", "marks split", "internal marks", "end semester marks",
                "internal", "end sem", "end-sem", "pass mark",
                "textbook", "textbooks", "reference", "references",
                "reference book", "reference books", "references for",
                "credit", "credits", "how many credits", "ltpc", "l-t-p-c", "l-t-p", "ltp",
                "contact hours", "lecture hours",
                "course code", "subject code", "code of", "code for",
                "which semester", "what semester", "which sem", "what sem", "taught in",
                "topics in", "topics of", "topics covered", "topics under",
                "unit 1", "unit 2", "unit 3", "unit 4", "unit 5",
                "unit i", "unit ii", "unit iii", "unit iv", "unit v",
                "course content", "course contents", "course objective", "course objectives",
                "objectives of", "course outcome", "course outcomes", "course details",
                "cos of", "cos for", "co of", "co for",
                "elective", "electives", "elective basket", "specialisation", "specialization",
                "program outcome", "program outcomes", "program educational objective",
                "program specific outcome", "peo", "peos", "pso", "psos",
                "nptel", "live-in-lab", "live in lab", "credit limit", "exemption", "credit exemption",
                "general framework", "credit distribution", "total credits", "aicte",
                "abbreviation", "abbreviations", "stand for", "full form",
                "lab experiment", "lab experiments", "list of experiments",
                "semester courses", "courses in semester", "subjects in semester",
            ])
            or bool(_SEM_QUERY_RE.search(q_lower))
            or (
                bool(_COURSE_CODE_IN_Q_RE.search(q_lower))
                and not has_pyq_word
                and not any(k in q_lower for k in ["explain", "derive", "solve", "how to", "working of", "calculate"])
            )
        ):
            return "SIMPLE_CURRICULUM"

        # 5. Conceptual explanations, definitions, derivations -> GENERAL_QUERY fast-path
        if any(k in q_lower for k in [
            "explain", "what is", "what are", "how does", "how to", "why is", "why does",
            "difference between", "derive", "definition of", "describe the working",
            "how do i solve", "solve this", "solution for", "working of", "calculate the"
        ]):
            return "GENERAL_QUERY"

        # 6. Generic PYQ Query
        if any(k in q_lower for k in [
            "pyq", "past year", "exam paper", "midterm", "mid-term", "endsem", "end-sem"
        ]):
            return "SIMPLE_PYQ"

        # Tier 2: Semantic Embedding Fallback (for fuzzy curriculum vs problem matching)
        if not self.initialized:
            await self.initialize()
        q_emb = query_embedding or await embed_text(format_query_embedding_text(question))
        if not q_emb:
            return "GENERAL_QUERY"
        best_intent, best_score = "GENERAL_QUERY", -1.0
        # Only allow Tier 2 to select between broad categories; strict graph intents require explicit keywords
        for intent in ["PROBLEM_LIST", "SIMPLE_CURRICULUM", "GENERAL_QUERY"]:
            t_emb = self.intent_embeddings.get(intent)
            if t_emb:
                sim = cosine_similarity(q_emb, t_emb)
                if sim > best_score:
                    best_score = sim
                    best_intent = intent
        if best_score < 0.55:
            return "GENERAL_QUERY"
        return best_intent


intent_router = IntentRouter()


async def classify_query_intent(
    question: str, query_embedding: list[float] | None = None
) -> str:
    """Public wrapper. Drop-in replacement for the keyword classifier in main.py."""
    return await intent_router.classify_intent(question, query_embedding=query_embedding)
