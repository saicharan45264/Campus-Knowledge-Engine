import re
import unicodedata

def clean_text(raw: str) -> str:
    """Apply all cleaning rules in sequence."""
    text = raw
    
    # 1. Fix PDF broken words (soft hyphens, line-break hyphens)
    text = re.sub(r'(?<=\w)-\n(?=[a-z])', '', text) # re-join 'exam-\nation'
    
    # 2. Remove invisible control characters
    text = ''.join(c for c in text if unicodedata.category(c)[0] != 'C' or c in '\n\t')
    
    # 3. Normalize Unicode (convert fancy quotes, dashes to ASCII equivalents)
    text = unicodedata.normalize('NFKC', text)
    text = text.replace('\u2013', '-').replace('\u2014', '-')
    text = text.replace('\u2018','"').replace('\u2019','"')
    
    # 4. Remove repeated headers/footers (detect by repetition across pages)
    text = re.sub(r'(Amrita Vishwa Vidyapeetham.*?Page \d+ of \d+)', '', text, flags=re.S)
    
    # 5. Normalize whitespace
    text = re.sub(r'[ \t]+', ' ', text) # collapse horizontal space
    text = re.sub(r'\n{3,}', '\n\n', text) # max 2 consecutive newlines
    
    return text.strip()
