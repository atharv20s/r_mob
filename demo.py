import time
import json
import sys
import os

# Set database URL to sqlite in environment before importing models/app if necessary,
# but main.py defaults to route_mobile.db which is fine.
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from fastapi.testclient import TestClient
from src.main import app
from src.db.session import SessionLocal, Base, engine
from src.db import models

# Print styling functions
def print_header(title):
    print("\n" + "=" * 80)
    print(f" {title.upper()} ".center(80, "="))
    print("=" * 80)

def print_sub_header(subtitle):
    print(f"\n--- {subtitle} ---")

def print_json(data):
    print(json.dumps(data, indent=2))

def main():
    print_header("Route Mobile API & Redis Service Demonstration Flow")
    
    # 0. Clean up previous demo user if any
    db = SessionLocal()
    demo_email = "demo_terminal_user@route.com"
    try:
        user = db.query(models.User).filter(models.User.email == demo_email).first()
        if user:
            # Delete related audit logs and usage records
            db.query(models.AuditLog).filter(models.AuditLog.user_id == user.id).delete()
            db.query(models.UsageRecord).filter(models.UsageRecord.user_id == user.id).delete()
            db.query(models.APIKey).filter(models.APIKey.user_id == user.id).delete()
            db.delete(user)
            db.commit()
            print("[Info] Cleaned up existing demo user from database.")
    finally:
        db.close()

    client = TestClient(app)

    # 1. REGISTER NEW USER
    print_header("1. User Registration & API Key Generation")
    reg_payload = {
        "email": demo_email,
        "password": "demoSecurePassword123!"
    }
    print(f"POST /api/v1/auth/register with payload:")
    print_json(reg_payload)
    
    response = client.post("/api/v1/auth/register", json=reg_payload)
    print(f"Status Code: {response.status_code}")
    reg_response_json = response.json()
    print_json(reg_response_json)
    
    user_id = reg_response_json.get("id")
    raw_api_key = reg_response_json.get("api_key")

    # 2. USER LOGIN TO GET JWT ACCESS TOKEN
    print_header("2. Authentication / Login (JWT Generation)")
    login_payload = {
        "username": demo_email,
        "password": "demoSecurePassword123!"
    }
    print(f"POST /api/v1/auth/login with form data:")
    print_json(login_payload)
    
    response = client.post("/api/v1/auth/login", data=login_payload)
    print(f"Status Code: {response.status_code}")
    login_response_json = response.json()
    print_json(login_response_json)
    
    access_token = login_response_json.get("access_token")
    headers = {"Authorization": f"Bearer {access_token}"}

    # 3. CHAT COMPLETION ENDPOINT CALL
    print_header("3. API Gateway Request: Chat Completion (SQLalchemy Audit + Redis Cache)")
    chat_payload = {
        "prompt": "Explain the concept of quantum computing in one sentence."
    }
    print(f"POST /api/v1/chat with JSON payload:")
    print_json(chat_payload)
    print(f"Headers: Authorization: Bearer <JWT Token>")
    
    # Run chat completions
    # We call it twice to show Redis response caching in action!
    print_sub_header("Call 1: Cache Miss (Calling AI Provider)")
    t0 = time.time()
    response1 = client.post("/api/v1/chat", json=chat_payload, headers=headers)
    t1 = time.time()
    print(f"Status Code: {response1.status_code}")
    print(f"Latency: {int((t1 - t0) * 1000)}ms")
    print_json(response1.json())
    
    print_sub_header("Call 2: Cache Hit (Served from Redis/In-memory cache)")
    t0 = time.time()
    response2 = client.post("/api/v1/chat", json=chat_payload, headers=headers)
    t1 = time.time()
    print(f"Status Code: {response2.status_code}")
    print(f"Latency: {int((t1 - t0) * 1000)}ms")
    print_json(response2.json())

    # 4. REDIS RATE LIMITING (10 requests/second limit)
    print_header("4. Redis Rate Limiting Enforcement (10 req/sec limit)")
    print("Sending 15 rapid chat completion requests to trigger 429 Too Many Requests...")
    
    rate_limited_count = 0
    for i in range(15):
        # Using a simple prompt
        resp = client.post("/api/v1/chat", json={"prompt": f"Rate test {i}"}, headers=headers)
        print(f"Request {i+1:02d}: Status Code {resp.status_code}")
        if resp.status_code == 429:
            rate_limited_count += 1
            if rate_limited_count == 1:
                print_sub_header("First Rate Limited Response JSON:")
                print_json(resp.json())
                
    print(f"\nRate limiting check: {rate_limited_count} out of 15 requests were rate limited (429).")

    # 5. SQLALCHEMY AUDIT LOGS DISPLAY
    print_header("5. SQLAlchemy Enterprise Audit Log")
    print("Querying sqlite database for the audit logs of this demo user...")
    db = SessionLocal()
    try:
        logs = db.query(models.AuditLog).filter(models.AuditLog.user_id == user_id).order_by(models.AuditLog.timestamp.desc()).all()
        log_dicts = []
        for l in logs:
            log_dicts.append({
                "id": l.id,
                "endpoint": l.endpoint,
                "method": l.method,
                "status_code": l.status_code,
                "latency_ms": l.latency_ms,
                "ip_address": l.ip_address,
                "request_id": l.request_id,
                "timestamp": l.timestamp.isoformat()
            })
        print(f"Found {len(log_dicts)} audit log entries in SQLAlchemy:")
        print_json(log_dicts[:5]) # Display first 5
    finally:
        db.close()

    # 6. LOGOUT (Blacklist JWT in Redis)
    print_header("6. Revoking Token on Logout (Redis Blacklisting)")
    print(f"POST /api/v1/auth/logout with token...")
    response = client.post("/api/v1/auth/logout", headers=headers)
    print(f"Status Code: {response.status_code}")
    print_json(response.json())

    # 7. VERIFY ACCESS WITH REVOKED JWT TOKEN
    print_header("7. Verification of Revoked Token Access")
    print("Attempting to access chat endpoint with the same JWT token...")
    response = client.post("/api/v1/chat", json=chat_payload, headers=headers)
    print(f"Status Code: {response.status_code}")
    print_json(response.json())
    
    print_header("Demonstration Flow Completed Successfully")

if __name__ == "__main__":
    main()
