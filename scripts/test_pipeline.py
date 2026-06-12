import requests
import time
import sys

BASE_URL = "http://localhost:8000"

def upload_pdf(file_path):
    print(f"Uploading {file_path} to admin endpoint...")
    url = f"{BASE_URL}/admin/documents/upload"
    files = {'file': open(file_path, 'rb')}
    data = {
        'document_type': 'curriculum',
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
        "session_id": "test_session_123"
    }
    response = requests.post(url, json=payload)
    print("Response Status Code:", response.status_code)
    print("Response Text:", response.text)
    try:
        print("Query Response:\n", response.json())
    except Exception as e:
        print("Failed to decode JSON response:", e)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/test_pipeline.py <path_to_pdf>")
        sys.exit(1)
        
    pdf_path = sys.argv[1]
    doc_id = upload_pdf(pdf_path)
    
    print("\nWaiting 10 seconds for background document ingestion & indexing...")
    time.sleep(10)
    
    # Run test queries suited for curriculum
    test_query("What courses are offered in B.Tech CSE?")
    test_query("What is the syllabus of Compiler Design?")
