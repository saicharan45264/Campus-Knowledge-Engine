import sys
import os
import requests
import time

BASE_URL = "http://localhost:8000"

def upload_pdf(file_path, doc_type):
    print(f"Uploading {file_path} as {doc_type} to admin endpoint...")
    url = f"{BASE_URL}/admin/documents/upload"
    files = {'file': open(file_path, 'rb')}
    data = {
        'document_type': doc_type,
        'academic_year': '2026-27'
    }
    response = requests.post(url, files=files, data=data)
    print("Upload Response:", response.json())
    return response.json().get('doc_id')

def test_query(prompt, intent_hint):
    print(f"\nSending Query: '{prompt}'...")
    url = f"{BASE_URL}/query"
    payload = {
        "query": prompt,
        "session_id": f"test_session_{intent_hint}"
    }
    response = requests.post(url, json=payload)
    print("Response Status Code:", response.status_code)
    try:
        res = response.json()
        print("Query Response:\n", res.get('answer', res))
    except Exception as e:
        print("Failed to decode JSON response:", e)

if __name__ == "__main__":
    pdf_path_tt = "/Users/saicharanboddeti/Downloads/sample_timetable.pdf"
    pdf_path_cal = "/Users/saicharanboddeti/Downloads/Academic-Calendar-AY-2026-2027 (1).pdf"
    
    upload_pdf(pdf_path_tt, 'timetable')
    upload_pdf(pdf_path_cal, 'academic_calendar')
    
    print("\nWaiting 10 seconds for background document ingestion & indexing...")
    time.sleep(10)
    
    # Multi-turn conversation simulation for Timetable
    session_id = "test_timetable_session"
    
    print(f"\n--- Turn 1: Asking schedule without section info ---")
    url = f"{BASE_URL}/query"
    payload = {
        "query": "What classes do I have on Monday?",
        "session_id": session_id
    }
    response = requests.post(url, json=payload).json()
    print("Response 1:", response)
    
    print(f"\n--- Turn 2: Providing missing section ('CSE-F AB3') ---")
    payload = {
        "query": "CSE-F AB3",
        "session_id": session_id
    }
    response = requests.post(url, json=payload).json()
    print("Response 2:", response)
    
    print("\n--- Testing Academic Calendar Query ---")
    test_query("When is the first working day in June 2026?", "CALENDAR")

