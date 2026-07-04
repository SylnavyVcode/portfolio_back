"""Tests des endpoints auth : validation Pydantic et contrôle d'accès.

Les appels réels à Supabase sont hors périmètre (testés manuellement) —
ici on vérifie que les entrées invalides sont rejetées AVANT tout appel
externe, et que les routes protégées exigent un JWT.
"""


def test_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_register_rejects_invalid_email(client):
    response = client.post(
        "/api/auth/register",
        json={"email": "not-an-email", "password": "longenough1", "full_name": "Test"},
    )
    assert response.status_code == 422


def test_register_rejects_short_password(client):
    response = client.post(
        "/api/auth/register",
        json={"email": "user@example.com", "password": "short", "full_name": "Test"},
    )
    assert response.status_code == 422


def test_register_rejects_empty_name(client):
    response = client.post(
        "/api/auth/register",
        json={"email": "user@example.com", "password": "longenough1", "full_name": ""},
    )
    assert response.status_code == 422


def test_me_requires_auth(client):
    assert client.get("/api/auth/me").status_code == 401


def test_update_profile_requires_auth(client):
    assert client.put("/api/auth/me/profile", json={"full_name": "X"}).status_code == 401


def test_logout_requires_auth(client):
    assert client.post("/api/auth/logout").status_code == 401


def test_change_password_requires_auth(client):
    response = client.post(
        "/api/auth/change-password",
        json={"current_password": "a-password", "new_password": "new-password-1"},
    )
    assert response.status_code == 401


def test_me_rejects_garbage_token(client):
    response = client.get("/api/auth/me", headers={"Authorization": "Bearer garbage"})
    assert response.status_code == 401
