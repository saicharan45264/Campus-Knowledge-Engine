import json
from .router import gemini_model

SYSTEM_PROMPT = (
    'You are an academic assistant for {university_name}. '
    'Answer ONLY using the retrieved document context provided below. '
    'Do NOT use external knowledge. '
    'If the context does not contain enough information to answer the question, '
    'respond with: "The requested information is not available in the uploaded documents." '
    'Always cite which document type your answer comes from '
    '(e.g., Regulations R.12.1, Curriculum Semester 6, Academic Calendar October 2026).\n\n'
    'CRITICAL INSTRUCTIONS FOR RESPONSE FORMAT:\n'
    '1. DO NOT use LaTeX, MathJax, or complex mathematical symbols (like \\Sigma, $$, \\frac, etc.).\n'
    '2. Write all formulas and math in plain, human-understandable English (e.g., use "Sum of (Credits * Grade Points) divided by Sum of Credits").\n'
    '3. Format your response clearly using bullet points and paragraphs for readability.\n'
    '4. Provide a sharp, concise, and direct answer. Add additional helpful information if relevant.\n'
    '5. At the very end of your response, always suggest 1-2 relevant follow-up questions the user might want to ask, prefixed with "Follow-up questions:".'
)

def generate_answer(query: str, chunks: list[dict], session: dict) -> str:
    from datetime import datetime
    context = '\n\n---\n\n'.join([c['text'] for c in chunks])
    univ_name = "Amrita Vishwa Vidyapeetham" # Mock name for now
    
    current_date = datetime.today().strftime('%A, %d %B %Y')
    
    prompt = (
        f'System: {SYSTEM_PROMPT.format(university_name=univ_name)}\n'
        f'Current Date Context: Today is {current_date}.\n\n'
        f'Retrieved Context:\n{context}\n\n'
        f'Student Question: {query}\n\n'
        'Answer:'
    )
    
    try:
        response = gemini_model.generate_content(prompt)
        return response.text
    except Exception as e:
        print(f"Error generating answer: {e}")
        return "I'm sorry, but I am currently unable to process your request, possibly because the Gemini API quota limit has been reached. Please try again later."

def validate_answer(query: str, answer: str, chunks: list[dict], intent: str) -> dict:
    """Stage 2: LLM checks its own answer against retrieved context (Self-Correcting RAG)."""
    if intent not in ('REGULATION', 'EVALUATION', 'SYLLABUS'):
        return {'answer': answer, 'confidence': 10, 'validated': False}
        
    context = '\n'.join([c['text'] for c in chunks])
    prompt = (
        f'Retrieved context:\n{context}\n\n'
        f'Generated answer:\n{answer}\n\n'
        'Rate 0-10: Is EVERY factual claim in the answer directly supported '
        'by the retrieved context above? '
        'Respond ONLY with JSON: {"confidence": <int>, "unsupported": ["<claims>"]}'
    )
    
    try:
        response = gemini_model.generate_content(prompt)
        text = response.text.strip()
        if text.startswith("```json"):
            text = text[7:-3]
        elif text.startswith("```"):
            text = text[3:-3]
        result = json.loads(text)
        confidence = result.get('confidence', 5)
    except Exception as e:
        print(f"Validation error: {e}")
        confidence = 5
        
    if confidence < 7:
        return {
            'answer': (
                'Unable to answer with certainty based on available documents. '
                'Please refer to the official document or consult your Faculty Advisor.'
            ),
            'confidence': confidence, 
            'validated': True
        }
        
    return {'answer': answer, 'confidence': confidence, 'validated': True}
