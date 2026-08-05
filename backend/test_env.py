import os

env_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '.env'))
print(f"DEBUG: Checking path: {env_path}")
print(f"DEBUG: Does it exist? {os.path.exists(env_path)}")
