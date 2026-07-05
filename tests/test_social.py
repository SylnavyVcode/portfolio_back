"""Tests commentaires & notes : lecture publique, écriture authentifiée,
suppression auteur/admin, calcul de moyenne."""

from types import SimpleNamespace

import pytest

from app.dependencies import CurrentUser, get_current_user, get_optional_user

USER_ID = "0d9c8a1e-0000-0000-0000-0000000000ff"
OTHER_ID = "0d9c8a1e-0000-0000-0000-0000000000ee"
POST_ID = "0d9c8a1e-0000-0000-0000-000000000001"

POST_ROW = {"id": POST_ID, "slug": "react-best-practices", "status": "published"}

COMMENT_ROW = {
    "id": "0d9c8a1e-0000-0000-0000-000000000010",
    "content": "Très bon article !",
    "created_at": "2026-07-06T10:00:00+00:00",
    "user_id": USER_ID,
    "profiles": {"full_name": "Test User", "avatar_url": None, "role": "user"},
}


class FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def __getattr__(self, _name):
        def method(*_args, **_kwargs):
            return self

        return method

    def execute(self):
        return SimpleNamespace(data=self._rows, count=len(self._rows))


class FakeSupabase:
    """Renvoie des lignes canées par table."""

    def __init__(self, tables: dict):
        self._tables = tables

    def table(self, name):
        return FakeQuery(self._tables.get(name, []))


@pytest.fixture()
def fake_db(monkeypatch):
    def install(tables: dict):
        fake = FakeSupabase(tables)
        monkeypatch.setattr("app.routers.social.get_service_client", lambda: fake)
        return fake

    return install


def _user(role: str = "user", user_id: str = USER_ID) -> CurrentUser:
    return CurrentUser(
        id=user_id,
        email="test@example.com",
        role=role,
        full_name="Test User",
        avatar_url=None,
        access_token="fake",
    )


def override_user(role: str = "user", user_id: str = USER_ID):
    from app.main import app

    user = _user(role, user_id)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_optional_user] = lambda: user
    return app


@pytest.fixture(autouse=True)
def clear_overrides():
    yield
    from app.main import app

    app.dependency_overrides.clear()


# ── Lecture publique ─────────────────────────────────────────────────────────


def test_list_comments_public(client, fake_db):
    fake_db({"blog_posts": [POST_ROW], "comments": [COMMENT_ROW]})
    response = client.get("/api/blog/react-best-practices/comments")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["content"] == "Très bon article !"
    assert data[0]["author"]["name"] == "Test User"


def test_list_comments_unknown_slug(client, fake_db):
    fake_db({"blog_posts": []})
    response = client.get("/api/blog/inconnu/comments")
    assert response.status_code == 404


def test_rating_summary_anonymous(client, fake_db):
    fake_db(
        {
            "blog_posts": [POST_ROW],
            "ratings": [
                {"score": 5, "user_id": USER_ID},
                {"score": 4, "user_id": OTHER_ID},
            ],
        }
    )
    response = client.get("/api/blog/react-best-practices/rating")
    assert response.status_code == 200
    data = response.json()
    assert data["average"] == 4.5
    assert data["count"] == 2
    assert data["mine"] is None


# ── Écriture authentifiée ────────────────────────────────────────────────────


def test_add_comment_requires_auth(client, fake_db):
    fake_db({"blog_posts": [POST_ROW]})
    response = client.post(
        "/api/blog/react-best-practices/comments", json={"content": "Hello"}
    )
    assert response.status_code == 401


def test_add_comment_authenticated(client, fake_db):
    fake_db({"blog_posts": [POST_ROW], "comments": [COMMENT_ROW]})
    override_user()
    response = client.post(
        "/api/blog/react-best-practices/comments", json={"content": "Très bon article !"}
    )
    assert response.status_code == 201
    assert response.json()["author"]["id"] == USER_ID


def test_add_comment_blank_rejected(client, fake_db):
    fake_db({"blog_posts": [POST_ROW]})
    override_user()
    response = client.post(
        "/api/blog/react-best-practices/comments", json={"content": "   "}
    )
    assert response.status_code == 422


def test_set_rating_and_mine(client, fake_db):
    fake_db(
        {
            "blog_posts": [POST_ROW],
            "ratings": [{"score": 4, "user_id": USER_ID}],
        }
    )
    override_user()
    response = client.put(
        "/api/blog/react-best-practices/rating", json={"score": 4}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["average"] == 4.0
    assert data["mine"] == 4


def test_set_rating_out_of_range(client, fake_db):
    fake_db({"blog_posts": [POST_ROW]})
    override_user()
    response = client.put("/api/blog/react-best-practices/rating", json={"score": 6})
    assert response.status_code == 422


# ── Suppression ──────────────────────────────────────────────────────────────


def test_delete_comment_not_owner(client, fake_db):
    fake_db({"comments": [{"id": "c1", "user_id": OTHER_ID}]})
    override_user(role="user")
    response = client.delete("/api/comments/c1")
    assert response.status_code == 403


def test_delete_comment_owner(client, fake_db):
    fake_db({"comments": [{"id": "c1", "user_id": USER_ID}]})
    override_user(role="user")
    response = client.delete("/api/comments/c1")
    assert response.status_code == 200


def test_delete_comment_admin(client, fake_db):
    fake_db({"comments": [{"id": "c1", "user_id": OTHER_ID}]})
    override_user(role="admin")
    response = client.delete("/api/comments/c1")
    assert response.status_code == 200
