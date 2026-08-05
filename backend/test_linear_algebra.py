import urllib.request
import urllib.error
import json

data = json.dumps({"message": "syllabus for linear algebra"}).encode("utf-8")
req = urllib.request.Request("http://localhost:8000/chat", data=data, headers={"Content-Type": "application/json"})

try:
    response = urllib.request.urlopen(req, timeout=400)
    print("STATUS CODE:", response.getcode())
    print("JSON:", response.read().decode('utf-8'))
except urllib.error.HTTPError as e:
    print("HTTP ERROR:", e.code)
    print(e.read().decode('utf-8'))
except Exception as e:
    print("EXCEPTION:", e)
