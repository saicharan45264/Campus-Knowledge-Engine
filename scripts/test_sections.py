import requests
import time

BASE_URL = "http://localhost:8000"

def test_query(prompt, session_id):
    print(f"\nSending Query: '{prompt}' for Session: {session_id}...")
    url = f"{BASE_URL}/query"
    payload = {
        "query": prompt,
        "session_id": session_id
    }
    response = requests.post(url, json=payload)
    print("Response Status Code:", response.status_code)
    try:
        res = response.json()
        print("Query Response:\n", res.get('answer', res))
    except Exception as e:
        print("Failed to decode JSON response:", e)

if __name__ == "__main__":
    # Simulate Section A student
    print("=== Testing Section A Student ===")
    test_query("What classes do I have on Monday?", "student_a_session")
    # Provide section CSE-A (if it prompts or if we do it in two turns)
    test_query("CSE-A", "student_a_session")
    
    # Simulate Section F student
    print("\n=== Testing Section F Student ===")
    test_query("What classes do I have on Monday?", "student_f_session")
    # Provide section CSE-F AB3
    test_query("CSE-F AB3", "student_f_session")
