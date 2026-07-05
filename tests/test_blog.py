"""Tests blog : sérialisation (fake Supabase), contrôle d'accès admin."""

from types import SimpleNamespace

import pytest

from app.dependencies import CurrentUser, get_current_user


class FakeQuery:
    """Chaînable comme le query builder postgrest ; renvoie des données canées."""

    def __init__(self, rows):
        self._rows = rows

    def __getattr__(self, _name):
        def method(*_args, **_kwargs):
            return self

        return method

    def execute(self):
        return SimpleNamespace(data=self._rows, count=len(self._rows))


class FakeSupabase:
    def __init__(self, rows):
        self._rows = rows

    def table(self, _name):
        return FakeQuery(self._rows)


PUBLISHED_ROW = {
    "id": "0d9c8a1e-0000-0000-0000-000000000001",
    "slug": "react-best-practices",
    "category_fr": "Web Dev",
    "category_en": "Web Dev",
    "title_fr": "Les meilleures pratiques React",
    "title_en": "React Best Practices",
    "excerpt_fr": "Un guide pratique.",
    "excerpt_en": "A practical guide.",
    "content_fr": ["Premier paragraphe.", "Deuxième paragraphe."],
    "content_en": ["First paragraph.", "Second paragraph."],
    "image_url": "/react_js_image.jpg",
    "read_time": 8,
    "featured": True,
    "published_at": "2026-01-15T00:00:00+00:00",
    "status": "published",
    "created_at": "2026-01-10T00:00:00+00:00",
    "updated_at": "2026-01-15T00:00:00+00:00",
}


@pytest.fixture()
def fake_blog_db(monkeypatch):
    def install(rows):
        fake = FakeSupabase(rows)
        monkeypatch.setattr("app.routers.blog.get_service_client", lambda: fake)
        monkeypatch.setattr("app.routers.admin.get_service_client", lambda: fake)
        return fake

    return install


def override_user(client, role: str):
    from app.main import app

    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id="0d9c8a1e-0000-0000-0000-0000000000ff",
        email="test@example.com",
        role=role,
        full_name="Test",
        avatar_url=None,
        access_token="fake",
    )
    return app


# ── Public ───────────────────────────────────────────────────────────────────


def test_list_posts_serialization(client, fake_blog_db):
    fake_blog_db([PUBLISHED_ROW])
    response = client.get("/api/blog")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    post = body["items"][0]
    assert post["slug"] == "react-best-practices"
    assert post["title"] == {"fr": "Les meilleures pratiques React", "en": "React Best Practices"}
    assert "content" not in post  # la liste n'expose pas le contenu


def test_get_post_returns_content(client, fake_blog_db):
    fake_blog_db([PUBLISHED_ROW])
    response = client.get("/api/blog/react-best-practices")
    assert response.status_code == 200
    assert response.json()["content"]["fr"] == ["Premier paragraphe.", "Deuxième paragraphe."]


def test_get_post_404(client, fake_blog_db):
    fake_blog_db([])
    assert client.get("/api/blog/inconnu").status_code == 404


def test_page_size_capped(client, fake_blog_db):
    fake_blog_db([])
    assert client.get("/api/blog?page_size=999").status_code == 422


# ── Admin : contrôle d'accès ─────────────────────────────────────────────────


def test_admin_blog_requires_auth(client):
    assert client.get("/api/admin/blog").status_code == 401


def test_admin_blog_rejects_non_admin(client, fake_blog_db):
    fake_blog_db([PUBLISHED_ROW])
    app = override_user(client, role="user")
    try:
        assert client.get("/api/admin/blog").status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_admin_blog_allows_admin(client, fake_blog_db):
    fake_blog_db([PUBLISHED_ROW])
    app = override_user(client, role="admin")
    try:
        response = client.get("/api/admin/blog")
        assert response.status_code == 200
        assert response.json()[0]["status"] == "published"
    finally:
        app.dependency_overrides.clear()


def test_admin_create_rejects_bad_slug(client, fake_blog_db):
    fake_blog_db([PUBLISHED_ROW])
    app = override_user(client, role="admin")
    try:
        response = client.post(
            "/api/admin/blog",
            json={"title": {"fr": "Titre", "en": "Title"}, "slug": "Slug Invalide !"},
        )
        assert response.status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_slugify():
    from app.routers.admin import slugify

    assert slugify("Les meilleures pratiques React en 2026 !") == "les-meilleures-pratiques-react-en-2026"
    assert slugify("Éléphant à l'école") == "elephant-a-l-ecole"
