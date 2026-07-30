import pytest
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

def test_read_root():
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {"message": "AI Gateway is online"}

def test_login_and_unauthorized_ai_access():
    # 1. Verify unauthenticated requests are locked down
    bad_response = client.post("/process-ai", json={"prompt": "Test"})
    assert bad_response.status_code == 401
    
    # 2. Authenticate and verify token issuance
    login_response = client.post("/login", data={"username": "testuser", "password": "password"})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]
    
    # 3. Use the authorized token to pass the security perimeter
    headers = {"Authorization": f"Bearer {token}"}
    good_response = client.post("/process-ai", json={"prompt": "Test Prompt"}, headers=headers)
    assert good_response.status_code == 200
    assert "completed" in good_response.json()["status"]
