"""Tests photo de profil : signature d'upload et mise à jour de l'avatar."""

from types import SimpleNamespace

import pytest

from tests.test_blog import override_user


class FakeStorageBucket:
    def __init__(self):
        self.signed_paths: list[str] = []

    def create_signed_upload_url(self, path: str):
        self.signed_paths.append(path)
        return {"signed_url": f"https://storage.test/upload/{path}"}

    def get_public_url(self, path: str):
        return f"https://storage.test/public/{path}"


class FakeStorage:
    def __init__(self):
        self.bucket = FakeStorageBucket()

    def from_(self, _name):
        return self.bucket


class FakeProfilesTable:
    def __init__(self, rows):
        self._rows = rows
        self._filters = {}

    def update(self, values):
        self._values = values
        return self

    def eq(self, column, value):
        self._filters[column] = value
        return self

    def execute(self):
        for row in self._rows:
            if all(row.get(k) == v for k, v in self._filters.items()):
                row.update(self._values)
                return SimpleNamespace(data=[row])
        return SimpleNamespace(data=[])


class FakeSupabaseWithStorage:
    def __init__(self, rows):
        self.storage = FakeStorage()
        self._rows = rows

    def table(self, _name):
        return FakeProfilesTable(self._rows)


@pytest.fixture()
def fake_avatar_db(monkeypatch):
    def install(rows):
        fake = FakeSupabaseWithStorage(rows)
        monkeypatch.setattr("app.routers.auth.get_service_client", lambda: fake)
        return fake

    return install


def test_sign_avatar_requires_auth(client):
    response = client.post(
        "/api/auth/me/avatar/sign", json={"filename": "photo.png", "content_type": "image/png"}
    )
    assert response.status_code == 401


def test_sign_avatar_rejects_non_image(client, fake_avatar_db):
    fake_avatar_db([])
    app = override_user(client, role="user")
    try:
        response = client.post(
            "/api/auth/me/avatar/sign",
            json={"filename": "video.mp4", "content_type": "video/mp4"},
        )
        assert response.status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_sign_avatar_returns_signed_url(client, fake_avatar_db):
    fake = fake_avatar_db([])
    app = override_user(client, role="user")
    try:
        response = client.post(
            "/api/auth/me/avatar/sign",
            json={"filename": "photo.png", "content_type": "image/png"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["path"].startswith("0d9c8a1e-0000-0000-0000-0000000000ff/")
        assert body["path"] in fake.storage.bucket.signed_paths[0]
        assert body["upload_url"].startswith("https://storage.test/upload/")
        assert body["public_url"].startswith("https://storage.test/public/")
    finally:
        app.dependency_overrides.clear()


def test_update_avatar_requires_auth(client):
    response = client.put("/api/auth/me/avatar", json={"avatar_url": "https://x/y.png"})
    assert response.status_code == 401


def test_update_avatar_persists_url(client, fake_avatar_db):
    fake_avatar_db([{"id": "0d9c8a1e-0000-0000-0000-0000000000ff", "full_name": "Test"}])
    app = override_user(client, role="user")
    try:
        response = client.put("/api/auth/me/avatar", json={"avatar_url": "https://cdn.test/a.png"})
        assert response.status_code == 200
        assert response.json()["avatar_url"] == "https://cdn.test/a.png"
    finally:
        app.dependency_overrides.clear()


def test_update_avatar_rejects_empty_url(client):
    app = override_user(client, role="user")
    try:
        response = client.put("/api/auth/me/avatar", json={"avatar_url": ""})
        assert response.status_code == 422
    finally:
        app.dependency_overrides.clear()
