"""Tests formations : sérialisation, sections/leçons, progression, contrôle
d'accès, panier."""

import pytest

from app.dependencies import get_current_user
from tests.fakes import TableAwareFake
from tests.test_blog import override_user

USER_ID = "0d9c8a1e-0000-0000-0000-0000000000ff"

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
    "learning_objectives": [{"fr": "Construire une app React", "en": "Build a React app"}],
    "preview": {"kind": "code", "content": "console.log('hi')"},
}

SECTION_ROW = {
    "id": "0d9c8a1e-0000-0000-0000-00000000s001",
    "course_id": COURSE_ROW["id"],
    "position": 1,
    "title_fr": "Introduction",
    "title_en": "Introduction",
}

LESSON_ROW = {
    "id": "0d9c8a1e-0000-0000-0000-00000000l001",
    "course_id": COURSE_ROW["id"],
    "section_id": SECTION_ROW["id"],
    "position": 1,
    "title_fr": "Bienvenue",
    "title_en": "Welcome",
    "duration_minutes": 30,
    "is_free_preview": False,
    "content_type": "video",
    "content_fr": "Contenu FR",
    "content_en": "Content EN",
    "video_url": None,
}


@pytest.fixture()
def fake_courses_db(monkeypatch):
    def install(tables: dict[str, list[dict]]) -> TableAwareFake:
        fake = TableAwareFake(tables)
        for module in ("courses", "cart", "enrollments", "admin"):
            monkeypatch.setattr(f"app.routers.{module}.get_service_client", lambda: fake)
        return fake

    return install


def test_list_courses_serialization(client, fake_courses_db):
    fake_courses_db({"courses": [COURSE_ROW]})
    response = client.get("/api/courses")
    assert response.status_code == 200
    course = response.json()[0]
    assert course["slug"] == "web-react-ts"
    assert course["price"] == 69.0
    assert course["title"] == {"fr": "React & TypeScript", "en": "React & TypeScript"}
    assert course["learning_objectives"] == [{"fr": "Construire une app React", "en": "Build a React app"}]


def test_list_courses_rejects_bad_theme(client, fake_courses_db):
    fake_courses_db({"courses": []})
    assert client.get("/api/courses?theme=hacking").status_code == 422


def test_course_detail_includes_sections_and_objectives(client, fake_courses_db):
    fake_courses_db(
        {
            "courses": [COURSE_ROW],
            "course_sections": [dict(SECTION_ROW)],
            "course_lessons": [dict(LESSON_ROW)],
        }
    )
    response = client.get("/api/courses/web-react-ts")
    assert response.status_code == 200
    body = response.json()
    assert body["prerequisites"] == [{"fr": "Bases JS", "en": "JS basics"}]
    assert body["preview"]["kind"] == "code"
    assert len(body["sections"]) == 1
    assert body["sections"][0]["title"]["fr"] == "Introduction"
    assert body["sections"][0]["lessons"][0]["content_type"] == "video"
    # Le contenu complet (content) n'est pas exposé sur la liste des leçons résumée.
    assert "content" not in body["sections"][0]["lessons"][0]


def test_lessons_require_auth(client, fake_courses_db):
    fake_courses_db({"courses": [COURSE_ROW]})
    assert client.get("/api/courses/web-react-ts/lessons").status_code == 401


def test_lessons_forbidden_without_enrollment(client, fake_courses_db, monkeypatch):
    fake_courses_db({"courses": [COURSE_ROW]})
    monkeypatch.setattr("app.routers.courses.has_active_enrollment", lambda *_: False)
    app = override_user(client, role="user")
    try:
        assert client.get("/api/courses/web-react-ts/lessons").status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_lessons_allowed_for_admin(client, fake_courses_db):
    fake_courses_db(
        {
            "courses": [COURSE_ROW],
            "course_sections": [dict(SECTION_ROW)],
            "course_lessons": [dict(LESSON_ROW)],
        }
    )
    app = override_user(client, role="admin")
    try:
        response = client.get("/api/courses/web-react-ts/lessons")
        assert response.status_code == 200
        body = response.json()
        assert body[0]["title"]["fr"] == "Introduction"
        assert body[0]["lessons"][0]["content"]["fr"] == "Contenu FR"
    finally:
        app.dependency_overrides.clear()


def test_cart_requires_auth(client):
    assert client.get("/api/cart").status_code == 401
    assert client.post("/api/cart/items", json={"course_slug": "x"}).status_code == 401


def test_cart_returns_enriched_items(client, fake_courses_db):
    fake_courses_db(
        {"cart_items": [{"user_id": USER_ID, "added_at": "2026-07-04T10:00:00+00:00", "courses": COURSE_ROW}]}
    )
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


def test_my_enrollments_includes_progress(client, fake_courses_db):
    enrollment_id = "0d9c8a1e-0000-0000-0000-00000000e001"
    fake_courses_db(
        {
            "enrollments": [
                {
                    "id": enrollment_id,
                    "user_id": USER_ID,
                    "status": "active",
                    "granted_at": "2026-07-04T10:00:00+00:00",
                    "courses": COURSE_ROW,
                }
            ],
            "course_lessons": [dict(LESSON_ROW)],
            "lesson_progress": [{"enrollment_id": enrollment_id, "lesson_id": LESSON_ROW["id"]}],
        }
    )
    app = override_user(client, role="user")
    try:
        response = client.get("/api/me/enrollments")
        assert response.status_code == 200
        body = response.json()
        assert body[0]["progress_percent"] == 100.0
    finally:
        app.dependency_overrides.clear()


def test_my_orders_serialization(client, fake_courses_db):
    fake_courses_db(
        {
            "orders": [
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
        }
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


# ── Progression apprenant ─────────────────────────────────────────────────────


def test_progress_mark_and_unmark_lesson(client, fake_courses_db):
    enrollment_id = "0d9c8a1e-0000-0000-0000-00000000e002"
    fake_courses_db(
        {
            "courses": [COURSE_ROW],
            "course_lessons": [dict(LESSON_ROW)],
            "enrollments": [
                {"id": enrollment_id, "user_id": USER_ID, "course_id": COURSE_ROW["id"], "status": "active"}
            ],
            "lesson_progress": [],
        }
    )
    app = override_user(client, role="user")
    try:
        response = client.put(f"/api/courses/web-react-ts/lessons/{LESSON_ROW['id']}/complete")
        assert response.status_code == 200
        body = response.json()
        assert body["completed_count"] == 1
        assert body["total_lessons"] == 1
        assert body["percent"] == 100.0

        response = client.get("/api/courses/web-react-ts/progress")
        assert response.json()["percent"] == 100.0

        response = client.request(
            "DELETE", f"/api/courses/web-react-ts/lessons/{LESSON_ROW['id']}/complete"
        )
        assert response.status_code == 200
        assert response.json()["percent"] == 0.0
    finally:
        app.dependency_overrides.clear()


def test_progress_requires_enrollment(client, fake_courses_db):
    fake_courses_db({"courses": [COURSE_ROW], "course_lessons": [dict(LESSON_ROW)], "enrollments": []})
    app = override_user(client, role="user")
    try:
        response = client.put(f"/api/courses/web-react-ts/lessons/{LESSON_ROW['id']}/complete")
        assert response.status_code == 403
    finally:
        app.dependency_overrides.clear()


# ── Administration ────────────────────────────────────────────────────────────


def test_admin_courses_rejects_non_admin(client, fake_courses_db):
    fake_courses_db({"courses": [COURSE_ROW]})
    app = override_user(client, role="user")
    try:
        assert client.get("/api/admin/courses").status_code == 403
        assert client.post("/api/admin/courses", json={}).status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_admin_create_course_validates_payload(client, fake_courses_db):
    fake_courses_db({"courses": [COURSE_ROW]})
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


def test_admin_sections_and_lessons_crud(client, fake_courses_db):
    fake = fake_courses_db({"courses": [dict(COURSE_ROW)], "course_sections": [], "course_lessons": []})
    app = override_user(client, role="admin")
    try:
        response = client.post(
            f"/api/admin/courses/{COURSE_ROW['id']}/sections",
            json={"title": {"fr": "Module 1", "en": "Module 1"}},
        )
        assert response.status_code == 201
        section = response.json()
        assert section["position"] == 1

        response = client.post(
            f"/api/admin/sections/{section['id']}/lessons",
            json={"title": {"fr": "Leçon 1", "en": "Lesson 1"}, "content_type": "text"},
        )
        assert response.status_code == 201
        lesson = response.json()
        assert lesson["content_type"] == "text"
        assert lesson["position"] == 1

        response = client.get(f"/api/admin/courses/{COURSE_ROW['id']}/sections")
        assert response.status_code == 200
        body = response.json()
        assert body[0]["lessons"][0]["title"]["fr"] == "Leçon 1"

        # Une seconde section peut être créée puis remontée au-dessus de la première.
        response = client.post(
            f"/api/admin/courses/{COURSE_ROW['id']}/sections",
            json={"title": {"fr": "Module 2", "en": "Module 2"}},
        )
        second_section_id = response.json()["id"]
        response = client.post(
            f"/api/admin/sections/{second_section_id}/move", json={"direction": "up"}
        )
        assert response.status_code == 200

        sections_after = client.get(f"/api/admin/courses/{COURSE_ROW['id']}/sections").json()
        assert sections_after[0]["id"] == second_section_id
    finally:
        app.dependency_overrides.clear()
