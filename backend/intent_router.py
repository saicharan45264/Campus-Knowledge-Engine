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
    "CALENDAR_LOOKUP": (
        "Ask about academic calendar, holidays, working days, semester commencement, "
        "last instruction day, end semester exam dates, mid term exam schedule, or timetable day substitution."
    ),
    "TIMETABLE_LOOKUP": (
        "Ask about class schedule, timetable, what classes do I have today, tomorrow, or on a specific day/slot, "
        "where is my class, faculty venue, or elective session."
    ),
    "ACADEMIC_DOCUMENT_LOOKUP": (
        "Ask for a laboratory timetable, exam schedule, or personal exam seating arrangement "
        "using the student's department, semester, and register number."
    ),
    "REGULATIONS_QUERY": (
        "Ask about academic regulations, rules, minimum attendance requirements, FA grade, "
        "remedial options, supplementary exams, re-registration, grading system, first class with distinction, "
        "publication bonus marks, or NPTEL credit transfer."
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
        import re

        # 1. Academic Regulations (REGULATIONS_QUERY)
        if re.search(r"\b(r\.\d+(?:\.\d+)*|o\.\d+)\b", q_lower) or any(k in q_lower for k in [
            "regulation", "regulations", "attendance rule", "attendance requirement",
            "attendance percentage", "minimum attendance", "fa grade", "f grade", "i grade",
            "w grade", "re-registration", "re registration", "remedial", "supplementary exam",
            "grading scale", "grading system", "pass mark", "passing criteria", "distinction",
            "publication bonus", "nptel credit", "malpractice", "ordinance", "ordinances",
            "attendance", "exam eligibility", "examination eligibility", "eligible to appear",
            "promotion criteria", "promotion rules", "credit requirement", "credit transfer",
            "earned credits", "cgpa", "sgpa", "revaluation", "re totaling", "re totaling",
            "condonation", "backlog", "backlogs", "course withdrawal", "maximum duration",
            "academic probation", "disciplinary action", "exam fee", "registration requirement"
        ]):
            return "REGULATIONS_QUERY"

        # 2. Multi-hop Prerequisites (MULTI_HOP_PREREQ) - Strict explicit keywords
        if any(k in q_lower for k in [
            "prerequisite", "prerequisites", "before taking", "should i know before",
            "needed before", "before choosing", "prereq", "prereqs", "prior knowledge",
            "pre-requisite", "pre-requisites", "what should i know before", "what to know before"
        ]):
            return "MULTI_HOP_PREREQ"

        # 3. Course Outcome / Bloom's Taxonomy Mapping (GRAPH_PYQ_MAPPING)
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

        # 4. Scoped document lookups for lab use, seating, and exam timetables.
        # Keep ordinary exam commencement/date questions on the academic calendar route.
        if re.search(r"\b(seat(?:ing|ed)?|seating arrangement|seating plan|seat allocation|exam hall|hall allocation|my exam room)\b", q_lower) or (
            re.search(r"\b(register|roll)\s*(?:number|no\.?|#)|\bCB\.?\s*EN\.?\s*U4", q_lower)
            and re.search(r"\b(where|room|hall|sit|seated|seat)\b", q_lower)
        ) or re.search(r"\bwhere\b.*\b(sit|seated|seat)\b", q_lower):
            return "ACADEMIC_DOCUMENT_LOOKUP"
        has_course_code = bool(re.search(r"\b(?:19|20|22|23|24|25)[a-zA-Z]{3}\d{3,4}\b", q_lower))

        # Check for general academic calendar milestone queries first before document lookups
        is_general_exam_milestone = bool(re.search(r"\bwhen\s+(?:do|does|are)\s+(?:the\s+)?(?:mid[- ]?term|end[- ]?sem|odd\s+sem|even\s+sem|classes|semester)\b.*\b(start|begin|commence)\b", q_lower))

        # 4. Scoped document lookups for lab use, seating, and exam timetables.
        if not is_general_exam_milestone and (
            re.search(r"\b(exam(?:ination)?\s*(?:time\s*table|timetable|schedule)|date sheet|mid[- ]?term\s+(?:exam\s+)?schedule|end[- ]?sem(?:ester)?\s+(?:exam\s+)?schedule)\b", q_lower)
            or re.search(r"\b(end[- ]?sem(?:ester)?\s+exam|mid[- ]?term\s+exam|exam\s+(?:date|timing|session))\b", q_lower)
            or (
                has_course_code
                and re.search(r"\b(exam|exams|examination|end[- ]?sem|mid[- ]?term)\b", q_lower)
            )
        ):
            return "ACADEMIC_DOCUMENT_LOOKUP"

        if re.search(r"\b(lab(?:oratory)?\s*(?:time\s*table|timetable|schedule)|it[\s-]*lab|d[- ]?102|lab slot|lab availability)\b", q_lower) or (
            re.search(r"\b(labs?|laborator(?:y|ies)|it[\s-]*lab|d[- ]?102)\b", q_lower)
            and re.search(r"\b(when|what|which|day|slot|schedule|available|free|vacant|timetable|who teaches)\b", q_lower)
        ):
            return "ACADEMIC_DOCUMENT_LOOKUP"

        # 4. Academic Calendar & Institutional Dates (CALENDAR_LOOKUP)
        # Evaluated before general timetable/pyq so calendar dates, overrides & exam schedules route cleanly
        is_calendar_query = any(k in q_lower for k in [
            "holiday", "holidays", "academic calendar", "calendar", "working day",
            "instruction day", "last working day", "last day of instruction", "no instruction day",
            "academic year begin", "academic year start", "year begin", "year start",
            "semester commencement", "reopening date", "re-opening", "college reopen",
            "convocation date", "timetable override", "timetable substitution", "which weekday",
            "class-day count", "class day count", "class-day", "class day", "cohort",
            "what is happening on", "what happens on", "special event", "calendar events",
            "events apply to", "schedule changes are listed", "particular month",
            "normal monday schedule", "saturday schedule", "marked as a holiday",
            "classes start", "classes begin", "semester start", "semester begin",
            "odd semester", "even semester", "odd sem", "even sem",
            "when do classes", "when does classes"
        ]) or re.search(r"\b(commence|commences|commencement)\b", q_lower) \
           or re.search(r"\b(june\s+17|june\s+20|2026-06-17|2026-06-20)\b", q_lower) \
           or re.search(r"\b(end-?sem(?:ester)?|mid-?sem(?:ester)?|mid-?term)\s*(?:exam|exams|examination|examinations)?\s*(?:begin|start|commence|schedule|date|dates)?\b", q_lower) \
           or is_general_exam_milestone \
           or bool(re.search(r"\b(?:next|upcoming|nearest|coming|all|every|working|instructional)\b.{0,32}\bsaturdays?\b|\bsaturdays?\b.{0,32}\b(?:working|all|every|upcoming)\b", q_lower)) \
           or ("weekday" in q_lower and "timetable" in q_lower) \
           or (bool(re.search(r"\b(sem(?:ester)?\s*(?:[1-8]|i{1,3}|iv|v|vi|vii|viii)|[1-8](?:st|nd|rd|th)\s+semester|odd\s+sem(?:ester)?|even\s+sem(?:ester)?)\b", q_lower)) \
               and bool(re.search(r"\b(reopen(?:ing)?|re\s*open|resumption|resume|mid\s*[- ]?term|midterm|mid\s*semester|midsem|end\s*[- ]?sem|last instruction|commencement|start|begin)\b", q_lower)))

        if is_calendar_query:
            return "CALENDAR_LOOKUP"

        # 5. Timetable & Class Schedules (TIMETABLE_LOOKUP)
        is_timetable_query = any(k in q_lower for k in [
            "timetable", "time table", "class schedule", "my classes", "what classes",
            "which class", "classes do i have", "classes on", "classes today", "classes tomorrow",
            "where is my class", "where is the class", "where is", "class venue", "class timing",
            "who teaches", "who is teaching", "who's teaching",
            "how long is the", "which slots", "lunch break",
            "mentoring", "mentoring session", "project phase", "project work", "cir"
        ]) or bool(re.search(r"\b2\d[A-Z]{3}\d{3}\b", question.upper())) \
           or bool(re.search(r"\b(pe\s*[-–]?\s*(?:iv|v|vi|4|5|6)|professional elective)\b", q_lower)) \
           or bool(re.search(r"\b(group\s*[12]|group venues|g[12])\b", q_lower)) \
           or bool(re.search(r"\bslot\s*\d+\b", q_lower)) \
           or bool(re.search(r"\b(monday|tuesday|wednesday|thursday|friday|saturday)\s+schedule\b", q_lower)) \
           or bool(re.search(r"\b\d{1,2}[:.]\d{2}\s*(?:to|and|-)\s*\d{1,2}[:.]\d{2}\b", q_lower)) \
           or ("artificial intelligence" in q_lower and any(w in q_lower for w in ["slot", "lab", "when", "where", "who", "course"]))

        if is_timetable_query:
            return "TIMETABLE_LOOKUP"

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
            "what all courses in sem", "credits for course", "contact hours",
            "topics in unit", "topics of unit", "topic in unit", "content of unit",
            "covered in unit", "unit syllabus"
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

        # Tier 2: Semantic Embedding Fallback
        if not self.initialized:
            await self.initialize()
        q_emb = await embed_text(question)
        if not q_emb:
            return "GENERAL_QUERY"
        best_intent, best_score = "GENERAL_QUERY", -1.0
        candidate_intents = [
            "PROBLEM_LIST", "SIMPLE_CURRICULUM", "CALENDAR_LOOKUP",
            "TIMETABLE_LOOKUP", "REGULATIONS_QUERY", "GENERAL_QUERY"
        ]
        for intent in candidate_intents:
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
