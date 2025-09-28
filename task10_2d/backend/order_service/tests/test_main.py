import os
from fastapi.testclient import TestClient
from app.main import app  # adjust if your entry file is named differently

client = TestClient(app)


def test_health_check():
    response = client.get("/health")
    data = response.json()

    # ✅ Get expected service name from CI and normalize (underscores → dashes)
    expected_service = os.getenv("EXPECTED_SERVICE", "unknown-service").replace("_", "-")

    assert response.status_code == 200
    assert data["status"] == "ok"
    assert data["service"] == expected_service
