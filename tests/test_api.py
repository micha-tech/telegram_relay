import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

@pytest.mark.django_db
def test_register_login_and_me():
    client = APIClient()
    response = client.post("/api/auth/register", {"email": "person@example.com", "password": "a-strong-password-123"}, format="json")
    assert response.status_code == 201
    assert response.data["success"] is True
    token = response.data["data"]["access"]
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    assert client.get("/api/auth/me").data["data"]["email"] == "person@example.com"
    assert get_user_model().objects.get().check_password("a-strong-password-123")

@pytest.mark.django_db
def test_login_rejects_invalid_credentials():
    client = APIClient()
    response = client.post("/api/auth/login", {"email": "nobody@example.com", "password": "wrong"}, format="json")
    assert response.status_code == 400
    assert response.data["success"] is False

