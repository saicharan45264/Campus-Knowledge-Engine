"""
intent_router.py — Semantic intent classification using embedding cosine similarity.
Singleton IntentRouter initialises once at startup.
"""
from embedder import embed_text, cosine_similarity

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
        for intent, description in INTENT_DESCRIPTIONS.items():
            emb = await embed_text(description)
            if emb:
                self.intent_embeddings[intent] = emb
        self.initialized = True

    async def classify_intent(self, question: str) -> str:
        q_lower = question.lower()
        
        # Tier 1: Fast Deterministic Keyword Routing
        # 1. Course Outcome / Bloom's Taxonomy Mapping (GRAPH_PYQ_MAPPING)
        # Evaluated first because questions about CO/BTL often contain words like 'questions' or 'get me questions'
        if any(k in q_lower for k in [
            "btl", "co1", "co2", "co3", "co4", "co5", "co6", "bloom", "blooms", "mapped to",
            "course outcome", "course outcomes", "blooms taxonomy",
            "level 1", "level 2", "level 3", "level 4", "level 5", "level 6",
            "bt-1", "bt-2", "bt-3", "bt-4", "bt-5", "bt-6",
            "bt 1", "bt 2", "bt 3", "bt 4", "bt 5", "bt 6",
            "bt1", "bt2", "bt3", "bt4", "bt5", "bt6",
            "k1", "k2", "k3", "k4", "k5", "k6"
        ]):
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
            "pyqs on", "pyq on", "problems about", "questions about",
            "questions for", "problems for", "find problems", "find questions",
            "get me questions", "give me questions", "show me questions", "show questions",
            "list of practice problems", "practice questions", "practice problems",
            "mark questions", "marks questions", "mark problems", "marks problems",
            "pyqs for", "questions like this", "problems like this",
            "find similar", "match this diagram", "similar questions", "match this circuit"
        ]) or ("[uploaded image context:" in q_lower and any(vk in q_lower for vk in ["question", "pyq", "circuit", "resistor", "automata", "find", "similar", "exam"])):
            return "PROBLEM_LIST"

        # 4. Curriculum Structure, Syllabus, Semesters, Electives, Textbooks (SIMPLE_CURRICULUM)
        if any(k in q_lower for k in [
            "syllabus", "evaluation pattern", "assessment pattern",
            "textbook", "textbooks", "courses do i have in semester", "semester courses",
            "electives in", "elective subjects in", "elective basket",
            "what all courses in sem", "credits for course", "contact hours"
        ]):
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
        q_emb = await embed_text(question)
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


async def classify_query_intent(question: str) -> str:
    """Public wrapper. Drop-in replacement for the keyword classifier in main.py."""
    return await intent_router.classify_intent(question)
