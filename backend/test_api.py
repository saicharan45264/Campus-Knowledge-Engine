import requests

print("1. Testing Frontend Static Pages...")
login_html = requests.get('http://localhost:8000/')
print("Root redirect to login.html:", login_html.status_code == 200, "Length:", len(login_html.text))

print("\n2. Testing Login API...")
login_res = requests.post('http://localhost:8000/login', data={'username': 'admin', 'password': 'admin123'})
print("Login status:", login_res.status_code)
token = None
if login_res.status_code == 200:
    token = login_res.json().get('access_token')
    print("Successfully got JWT token.")
else:
    print("Failed to login:", login_res.text)

if token:
    print("\n3. Testing /documents API...")
    headers = {'Authorization': f'Bearer {token}'}
    docs_res = requests.get('http://localhost:8000/documents', headers=headers)
    print("Documents API status:", docs_res.status_code)
    print("Documents data:", docs_res.json())
    
    print("\n4. Testing /evaluate API...")
    eval_res = requests.get('http://localhost:8000/evaluate', headers=headers)
    print("Evaluate API status:", eval_res.status_code)
    if eval_res.status_code == 200:
        print("Evaluate data:", eval_res.json())

