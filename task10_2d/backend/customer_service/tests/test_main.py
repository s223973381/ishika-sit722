import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.db import init_db

# ✅ Ensure DB schema exists
init_db()

client = TestClient(app)

def test_create_customer_success():
    response = client.post(
        "/customers/",
        json={
            "email": "test@example.com",
            "password": "secret",
            "first_name": "John",
            "last_name": "Doe",
            "phone_number": "1234567890",
            "shipping_address": "123 Street"
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["email"] == "test@example.com"

def test_list_customers():
    response = client.get("/customers/")
    assert response.status_code == 200
    assert isinstance(response.json(), list)
