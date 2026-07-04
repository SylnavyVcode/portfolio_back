"""Tests formations : sérialisation, contrôle d'accès aux leçons, panier."""

import pytest

from app.dependencies import get_current_user
from tests.test_blog import FakeSupabase, override_user

COURSE_ROW = {
    "id": "0d9c8a1e-0000-0000-0000-00000000c001",
    "slug": "web-react-ts",
    "theme": "web",
    "level": "intermediate",
    "title_fr": "React & TypeScript",
    "title_en": "React & TypeScript",
    "summary_fr": "Résumé",
    "summary_en": "Summary",
    "preview_label_fr": "Aperçu",
    "preview_label_en": "Preview",
    "price_amount": "69.00",
    "currency": "EUR",
    "duration_hours": "12.0",
    "instructor": "Valmy Mabika",
    "rating": "4.8",
    "students_count": 120,
    "is_published": True,
    "published_at": "2026-05-08T00:00:00+00:00",
    "prerequisites": [{"fr": "Bases JS", "en": "JS basics"}],
    "preview": {"kind": "code", "content": "console.log('hi')"},
}

LESSON_ROW = {
    "id": "0d9c8a1e-0000-0000-0000-00000000l001",
    "position": 1,
    "title_fr": "Introduction",
    "title_en": "Introduction",
    "duration_minutes": 30,
    "is_free_preview": False,
    "content_fr": "Contenu FR",
    "content_en": "Content EN",
    "video_url": None,
}


@pytest.fixture()
def fake_courses_db(monkeypatch):
    def install(rows):
        fake = FakeSupabase(rows)
        for module in ("courses", "cart", "enrollments", "admin"):
            monkeypatch.setattr(f"app.routers.{module}.get_service_client", lambda: fake)
        return fake

    return install


def test_list_courses_serialization(client, fake_courses_db):
    fake_courses_db([COURSE_ROW])
    response = client.get("/api/courses")
    assert response.status_code == 200
    course = response.json()[0]
    assert course["slug"] == "web-react-ts"
    assert course["price"] == 69.0
    assert course["title"] == {"fr": "React & TypeScript", "en": "React & TypeScript"}


def test_list_courses_rejects_bad_theme(client, fake_courses_db):
    fake_courses_db([])
    assert client.get("/api/courses?theme=hacking").status_code == 422


def test_course_detail_includes_prerequisites(client, fake_courses_db):
    fake_courses_db([COURSE_ROW])
    response = client.get("/api/courses/web-react-ts")
    assert response.status_code == 200
    body = response.json()
    assert body["prerequisites"] == [{"fr": "Bases JS", "en": "JS basics"}]
    assert body["preview"]["kind"] == "code"


def test_lessons_require_auth(client, fake_courses_db):
    fake_courses_db([COURSE_ROW])
    assert client.get("/api/courses/web-react-ts/lessons").status_code == 401


def test_lessons_forbidden_without_enrollment(client, fake_courses_db, monkeypatch):
    fake_courses_db([COURSE_ROW])
    monkeypatch.setattr("app.routers.courses.has_active_enrollment", lambda *_: False)
    app = override_user(client, role="user")
    try:
        assert client.get("/api/courses/web-react-ts/lessons").status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_lessons_allowed_for_admin(client, fake_courses_db):
    fake_courses_db([{**COURSE_ROW, **LESSON_ROW}])
    app = override_user(client, role="admin")
    try:
        response = client.get("/api/courses/web-react-ts/lessons")
        assert response.status_code == 200
        assert response.json()[0]["content"]["fr"] == "Contenu FR"
    finally:
        app.dependency_overrides.clear()


def test_cart_requires_auth(client):
    assert client.get("/api/cart").status_code == 401
    assert client.post("/api/cart/items", json={"course_slug": "x"}).status_code == 401


def test_cart_returns_enriched_items(client, fake_courses_db):
    fake_courses_db([{"added_at": "2026-07-04T10:00:00+00:00", "courses": COURSE_ROW}])
    app = override_user(client, role="user")
    try:
        response = client.get("/api/cart")
        assert response.status_code == 200
        body = response.json()
        assert body["subtotal"] == 69.0
        assert body["items"][0]["course"]["slug"] == "web-react-ts"
    finally:
        app.dependency_overrides.clear()


def test_my_enrollments_requires_auth(client):
    assert client.get("/api/me/enrollments").status_code == 401


def test_my_orders_serialization(client, fake_courses_db):
    fake_courses_db(
        [
            {
                "id": "0d9c8a1e-0000-0000-0000-00000000o001",
                "status": "paid",
                "payment_method": "card",
                "total_amount": "69.00",
                "currency": "EUR",
                "created_at": "2026-07-04T10:00:00+00:00",
                "order_items": [
                    {"course_id": COURSE_ROW["id"], "title_snapshot": "React & TS", "price_snapshot": "69.00"}
                ],
            }
        ]
    )
    app = override_user(client, role="user")
    try:
        response = client.get("/api/me/orders")
        assert response.status_code == 200
        order = response.json()[0]
        assert order["status"] == "paid"
        assert order["items"][0]["price_snapshot"] == 69.0
    finally:
        app.dependency_overrides.clear()


def test_admin_courses_rejects_non_admin(client, fake_courses_db):
    fake_courses_db([COURSE_ROW])
    app = override_user(client, role="user")
    try:
        assert client.get("/api/admin/courses").status_code == 403
        assert client.post("/api/admin/courses", json={}).status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_admin_create_course_validates_payload(client, fake_courses_db):
    fake_courses_db([COURSE_ROW])
    app = override_user(client, role="admin")
    try:
        response = client.post(
            "/api/admin/courses",
            json={"title": {"fr": "X", "en": "X"}, "theme": "cuisine", "level": "beginner", "price": 10},
        )
        assert response.status_code == 422
        response = client.post(
            "/api/admin/courses",
            json={"title": {"fr": "X", "en": "X"}, "theme": "web", "level": "beginner", "price": -5},
        )
        assert response.status_code == 422
    finally:
        app.dependency_overrides.clear()
