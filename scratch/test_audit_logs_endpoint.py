import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi.testclient import TestClient
from src.main import app

client = TestClient(app)

# Login as admin
resp = client.post("/api/v1/auth/login", data={"username": "admin@route.com", "password": "adminpassword"})
print("Login status:", resp.status_code)
if resp.status_code == 200:
    token = resp.json().get("access_token")
    headers = {"Authorization": f"Bearer {token}"}
    print("Calling /api/v1/usage/audit-logs...")
    # Using client.get without catching exceptions to let the full error bubble up to Python console
    resp2 = client.get("/api/v1/usage/audit-logs", headers=headers)
    print("Response status:", resp2.status_code)
    print("Response content:", resp2.text)
else:
    print("Login failed:", resp.text)
