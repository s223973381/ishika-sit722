import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.db import init_db

# ✅ Ensure DB schema exists
init_db()

client = TestClient(app)

def test_root_endpoint():
    response = client.get("/")
    assert response.status_code == 200
    assert "Welcome" in response.json()["message"]

def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["service"] == "product-service"
