import requests

BASE_URL = "http://localhost:8000"

def test_auth_flow():
    # 1. Register Student
    print("--- Registering Student ---")
    reg_payload = {
        "name": "Test Student",
        "college_mail": "test.student@amrita.edu",
        "password": "securepassword",
        "department": "B.TECH CSE",
        "semester": 6,
        "section_id": "CSE-F",
        "regulation_year": "2023",
        "academic_year": "2025-26"
    }
    res = requests.post(f"{BASE_URL}/auth/student/register", json=reg_payload)
    print("Register Response:", res.json())
    
    # 2. Login Student
    print("\n--- Logging in Student ---")
    login_payload = {
        "email_or_username": "test.student@amrita.edu",
        "password": "securepassword"
    }
    res = requests.post(f"{BASE_URL}/auth/student/login", json=login_payload)
    print("Login Status:", res.status_code)
    
    if res.status_code != 200:
        print("Login failed!", res.text)
        return
        
    token = res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    
    # 3. Query the Engine (Should NOT ask for section since it's injected from JWT!)
    print("\n--- Sending Query (What classes do I have on Monday?) ---")
    query_payload = {
        "query": "What classes do I have on Monday?",
        "session_id": "ignored_in_v2" # The backend now looks at JWT for identity
    }
    res = requests.post(f"{BASE_URL}/query", json=query_payload, headers=headers)
    print("Query Response Status:", res.status_code)
    try:
        print("Query Response:", res.json())
    except Exception as e:
        print("Error reading response:", res.text)

if __name__ == "__main__":
    test_auth_flow()
