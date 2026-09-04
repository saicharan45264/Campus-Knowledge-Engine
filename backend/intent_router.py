import json
from embedder import embed_text, cosine_similarity

INTENT_DESCRIPTIONS = {
    "PROBLEM_LIST": "Ask for a list of problems or past year questions on a specific topic or concept.",
    "MULTI_HOP_PREREQ": "Ask about prerequisites, what should I know before taking, or what is needed for a topic.",
    "GRAPH_PYQ_MAPPING": "Ask about Bloom's taxonomy level (BTL), course outcomes (CO), or how questions map to outcomes.",
    "SIMPLE_CURRICULUM": "Ask about syllabus, topics, units, objectives, or curriculum structure.",
    "SIMPLE_PYQ": "Ask for a specific past year question, exam paper, midterm, endsem, or circuit problem.",
    "GENERAL": "General conversation, greetings, or questions outside the curriculum or previous year questions scope."
}

class IntentRouter:
    def __init__(self):
        self.intent_embeddings = {}
        self.initialized = False
    
    async def initialize(self):
        if self.initialized:
            return
        
        for intent, description in INTENT_DESCRIPTIONS.items():
            emb = await embed_text(description)
            if emb:
                self.intent_embeddings[intent] = emb
                
        self.initialized = True
            
    async def classify_intent(self, question: str) -> str:
        if not self.initialized:
            await self.initialize()
            
        q_emb = await embed_text(question)
        if not q_emb:
            return "GENERAL"
            
        best_intent = "GENERAL"
        best_score = -1.0
        
        for intent, t_emb in self.intent_embeddings.items():
            sim = cosine_similarity(q_emb, t_emb)
            if sim > best_score:
                best_score = sim
                best_intent = intent
                
        if best_score < 0.4:
            return "GENERAL"
            
        return best_intent

# Global singleton router
intent_router = IntentRouter()

async def classify_query_intent(question: str) -> str:
    """Wrapper function to maintain compatibility with app.py"""
    return await intent_router.classify_intent(question)
