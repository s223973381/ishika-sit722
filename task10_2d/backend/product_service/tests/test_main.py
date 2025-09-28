import os
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_health_check():
    response = client.get("/health")
    data = response.json()

    # ✅ Get expected service from CI matrix
    expected_service = os.getenv("EXPECTED_SERVICE", "unknown-service")

    assert response.status_code == 200
    assert data["status"] == "ok"
    assert data["service"] == expected_service
