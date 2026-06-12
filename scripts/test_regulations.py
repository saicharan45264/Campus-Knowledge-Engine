import requests
import time
import sys

BASE_URL = "http://localhost:8000"

def upload_pdf(file_path):
    print(f"Uploading {file_path} to admin endpoint...")
    url = f"{BASE_URL}/admin/documents/upload"
    files = {'file': open(file_path, 'rb')}
    data = {
        'document_type': 'regulations',
        'regulation_year': '2023',
        'academic_year': '2025-26'
    }
    response = requests.post(url, files=files, data=data)
    print("Upload Response:", response.json())
    return response.json().get('doc_id')

def test_query(prompt):
    print(f"\nSending Query: '{prompt}'...")
    url = f"{BASE_URL}/query"
    payload = {
        "query": prompt,
        "session_id": "test_session_regulations"
    }
    response = requests.post(url, json=payload)
    print("Response Status Code:", response.status_code)
    try:
        res = response.json()
        print("Query Response:\n", res.get('answer', res))
    except Exception as e:
        print("Failed to decode JSON response:", e)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/test_regulations.py <path_to_pdf>")
        sys.exit(1)
        
    pdf_path = sys.argv[1]
    doc_id = upload_pdf(pdf_path)
    
    print("\nWaiting 10 seconds for background document ingestion & indexing...")
    time.sleep(10)
    
    # Run test queries suited for regulations
    test_query("What is the minimum attendance requirement?")
    test_query("What is the grading system and CGPA classification?")
