import sys
import os
sys.path.append(os.path.join(os.getcwd(), 'backend'))
from services.retrieval import hybrid_retrieve
from services.llm import get_embedding
from core.database import get_db_connection

msg = "get me all questions on 23EEE104"
emb = get_embedding(msg)
conn = get_db_connection()
res = hybrid_retrieve(msg, emb, ["Evaluation", "Curriculum"], conn)
for r in res:
    print(r[:100].replace('\n', ' '))
