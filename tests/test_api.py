import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken


@pytest.mark.django_db
def test_register_login_and_me():
    client = APIClient()
    response = client.post(
        "/api/auth/register",
        {"email": "person@example.com", "password": "a-strong-password-123"},
        format="json",
    )
    assert response.status_code == 201
    assert response.data["success"] is True
    token = response.data["data"]["access"]
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    assert client.get("/api/auth/me").data["data"]["email"] == "person@example.com"
    assert get_user_model().objects.get().check_password("a-strong-password-123")


@pytest.mark.django_db
def test_login_rejects_invalid_credentials():
    client = APIClient()
    response = client.post(
        "/api/auth/login", {"email": "nobody@example.com", "password": "wrong"}, format="json"
    )
    assert response.status_code == 400
    assert response.data["success"] is False


@pytest.mark.django_db
def test_logout_revokes_refresh_token():
    user = get_user_model().objects.create_user(
        username="person@example.com",
        email="person@example.com",
        password="a-strong-password-123",
    )
    client = APIClient()
    login_response = client.post(
        "/api/auth/login",
        {"email": user.email, "password": "a-strong-password-123"},
        format="json",
    )
    data = login_response.data["data"]
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {data['access']}")
    response = client.post("/api/auth/logout", {"refresh": data["refresh"]}, format="json")
    assert response.status_code == 200
    assert BlacklistedToken.objects.count() == 1


@pytest.mark.django_db
def test_protected_endpoint_requires_authentication():
    response = APIClient().get("/api/auth/me")
    assert response.status_code == 401
    assert response.data["error"]["code"] == "not_authenticated"


@pytest.mark.django_db
def test_telegram_auth_rejects_non_e164_phone_before_service_call():
    user = get_user_model().objects.create_user(
        username="person@example.com",
        password="a-strong-password-123",
        is_superuser=True,
        is_staff=True,
    )
    client = APIClient()
    client.force_authenticate(user)
    response = client.post("/api/telegram/auth/start", {"phone_number": "555-1234"}, format="json")
    assert response.status_code == 400


@pytest.mark.django_db
def test_telegram_auth_requires_super_admin():
    user = get_user_model().objects.create_user(
        username="regular@example.com", password="a-strong-password-123"
    )
    client = APIClient()
    client.force_authenticate(user)
    response = client.post(
        "/api/telegram/auth/start", {"phone_number": "+15551234567"}, format="json"
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_me_reports_super_admin_status():
    user = get_user_model().objects.create_user(
        username="admin@example.com", password="a-strong-password-123", is_superuser=True
    )
    client = APIClient()
    client.force_authenticate(user)
    response = client.get("/api/auth/me")
    assert response.status_code == 200
    assert response.data["data"]["is_superuser"] is True
