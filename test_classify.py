import sys
import os
sys.path.append(os.path.join(os.getcwd(), 'backend'))
from services.llm import classify_query
print(classify_query("get me all questions on 23EEE104"))
