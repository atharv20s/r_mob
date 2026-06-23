import unittest
import sys
import os

# Add parent directory to sys.path so we can import src
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi.testclient import TestClient
from src.main import app
from src.db.session import SessionLocal, Base, engine
from src.db import models
from src.core.security import get_password_hash

class TestRouteMobileAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(bind=engine)
        cls.client = TestClient(app)
        
        db = SessionLocal()
        try:
            test_user = db.query(models.User).filter(models.User.email == "test_api_user@route.com").first()
            if test_user:
                db.delete(test_user)
                db.commit()
        finally:
            db.close()

    def test_health_check(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "healthy")

    def test_user_registration_and_login(self):
        # 1. Register User
        reg_payload = {
            "email": "test_api_user@route.com",
            "password": "strongpassword123"
        }
        response = self.client.post("/api/v1/auth/register", json=reg_payload)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["email"], "test_api_user@route.com")
        self.assertEqual(data["role"], "user")
        self.assertIn("api_key", data)
        raw_api_key = data["api_key"]
        self.assertTrue(raw_api_key.startswith("sk_"))

        # Check database: only key_hash is stored, raw key is not stored
        db = SessionLocal()
        try:
            db_api_key = db.query(models.APIKey).filter(models.APIKey.user_id == data["id"]).first()
            self.assertIsNotNone(db_api_key)
            self.assertNotEqual(db_api_key.key_hash, raw_api_key)
            self.assertEqual(db_api_key.key_hash, models.hash_api_key(raw_api_key))
        finally:
            db.close()

        # 2. Login User
        login_payload = {
            "username": "test_api_user@route.com",
            "password": "strongpassword123"
        }
        login_response = self.client.post("/api/v1/auth/login", data=login_payload)
        self.assertEqual(login_response.status_code, 200)
        login_data = login_response.json()
        self.assertIn("access_token", login_data)
        access_token = login_data["access_token"]

        # 3. Get /me with JWT token
        headers = {"Authorization": f"Bearer {access_token}"}
        me_response = self.client.get("/api/v1/usage/me", headers=headers)
        self.assertEqual(me_response.status_code, 200)
        me_data = me_response.json()
        self.assertEqual(me_data["email"], "test_api_user@route.com")
        self.assertEqual(me_data["plan"], "free")

    def test_admin_rbac(self):
        login_payload = {
            "username": "admin@route.com",
            "password": "adminpassword"
        }
        login_response = self.client.post("/api/v1/auth/login", data=login_payload)
        self.assertEqual(login_response.status_code, 200)
        admin_token = login_response.json()["access_token"]
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        # Retrieve users list
        users_response = self.client.get("/api/v1/usage/users", headers=admin_headers)
        self.assertEqual(users_response.status_code, 200)
        users = users_response.json()
        emails = [u["email"] for u in users]
        self.assertIn("admin@route.com", emails)

        # Retrieve plans list
        plans_response = self.client.get("/api/v1/usage/plans", headers=admin_headers)
        self.assertEqual(plans_response.status_code, 200)
        plans = plans_response.json()
        plan_names = [p["name"] for p in plans]
        self.assertIn("free", plan_names)
        self.assertIn("pro", plan_names)
        self.assertIn("enterprise", plan_names)

    def test_mock_chat_endpoint(self):
        login_payload = {
            "username": "admin@route.com",
            "password": "adminpassword"
        }
        login_response = self.client.post("/api/v1/auth/login", data=login_payload)
        token = login_response.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        chat_payload = {
            "prompt": "Hello!"
        }
        chat_response = self.client.post("/api/v1/chat", json=chat_payload, headers=headers)
        self.assertIn(chat_response.status_code, [200, 400])

if __name__ == "__main__":
    unittest.main()
