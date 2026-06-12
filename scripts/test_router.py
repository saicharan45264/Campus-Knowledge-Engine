import os
from src.query.router import classify_intent

queries = [
    "What courses are offered in B.Tech CSE?",
    "What is the syllabus of Compiler Design?"
]

for q in queries:
    res = classify_intent(q)
    print(q, res)
