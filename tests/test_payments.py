"""Tests paiements : checkout cash de bout en bout, validation admin,
contrôles d'accès et webhook Stripe (signature)."""

import uuid
from types import SimpleNamespace

import pytest

from tests.test_blog import override_user
from tests.test_courses import COURSE_ROW

USER_ID = "0d9c8a1e-0000-0000-0000-0000000000ff"


class TableQuery:
    """Reproduit le minimum du query builder postgrest sur des dicts en mémoire."""

    def __init__(self, rows: list[dict]):
        self._rows = rows
        self._filters: list[tuple[str, object]] = []
        self._op = "select"
        self._values = None

    # chaînage
    def select(self, *_a, **_k):
        self._op = "select"
        return self

    def insert(self, values):
        self._op = "insert"
        self._values = values if isinstance(values, list) else [values]
        return self

    def upsert(self, values, **_k):
        self._op = "insert"
        self._values = values if isinstance(values, list) else [values]
        return self

    def update(self, values):
        self._op = "update"
        self._values = values
        return self

    def delete(self):
        self._op = "delete"
        return self

    def eq(self, column, value):
        self._filters.append((column, value))
        return self

    def __getattr__(self, _name):  # order, range, limit, single...
        def method(*_a, **_k):
            return self

        return method

    def _matching(self):
        return [
            row
            for row in self._rows
            if all(str(row.get(col)) == str(val) for col, val in self._filters if col in row)
        ]

    def execute(self):
        if self._op == "insert":
            for row in self._values:
                row.setdefault("id", str(uuid.uuid4()))
                row.setdefault("created_at", "2026-07-05T10:00:00+00:00")
                self._rows.append(row)
            return SimpleNamespace(data=list(self._values), count=len(self._values))
        if self._op == "update":
            matched = self._matching()
            for row in matched:
                row.update(self._values)
            return SimpleNamespace(data=matched, count=len(matched))
        if self._op == "delete":
            matched = self._matching()
            for row in matched:
                self._rows.remove(row)
            return SimpleNamespace(data=matched, count=len(matched))
        matched = self._matching()
        return SimpleNamespace(data=matched, count=len(matched))


class TableAwareFake:
    def __init__(self, tables: dict[str, list[dict]]):
        self.tables = tables

    def table(self, name: str) -> TableQuery:
        return TableQuery(self.tables.setdefault(name, []))


@pytest.fixture()
def fake_db(monkeypatch):
    def install(tables: dict[str, list[dict]]) -> TableAwareFake:
        fake = TableAwareFake(tables)
        for module in ("payments", "admin", "cart", "courses", "enrollments"):
            monkeypatch.setattr(f"app.routers.{module}.get_service_client", lambda: fake)
        # Évite tout appel réseau vers l'API admin Supabase (emails)
        monkeypatch.setattr("app.routers.payments.get_user_email", lambda _: "client@test.com")
        monkeypatch.setattr("app.routers.admin.get_user_email", lambda _: "client@test.com")
        return fake

    return install


def cart_row():
    return {"user_id": USER_ID, "added_at": "2026-07-05T09:00:00+00:00", "courses": dict(COURSE_ROW)}


# ── Checkout ─────────────────────────────────────────────────────────────────


def test_checkout_requires_auth(client):
    assert client.post("/api/payments/checkout", json={"payment_method": "cash"}).status_code == 401


def test_checkout_rejects_unknown_method(client, fake_db):
    fake_db({})
    app = override_user(client, role="user")
    try:
        response = client.post("/api/payments/checkout", json={"payment_method": "bitcoin"})
        assert response.status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_checkout_paypal_not_implemented(client, fake_db):
    fake_db({"cart_items": [cart_row()]})
    app = override_user(client, role="user")
    try:
        response = client.post("/api/payments/checkout", json={"payment_method": "paypal"})
        assert response.status_code == 501
    finally:
        app.dependency_overrides.clear()


def test_checkout_empty_cart(client, fake_db):
    fake_db({"cart_items": []})
    app = override_user(client, role="user")
    try:
        response = client.post("/api/payments/checkout", json={"payment_method": "cash"})
        assert response.status_code == 400
    finally:
        app.dependency_overrides.clear()


def test_checkout_cash_end_to_end(client, fake_db):
    fake = fake_db({"cart_items": [cart_row()], "orders": [], "order_items": [], "payments": []})
    app = override_user(client, role="user")
    try:
        response = client.post("/api/payments/checkout", json={"payment_method": "cash"})
        assert response.status_code == 200
        body = response.json()
        assert body["order"]["status"] == "pending_validation"
        assert body["order"]["total_amount"] == 69.0
        assert body["redirect_url"] is None
        assert fake.tables["cart_items"] == []  # panier vidé
        assert fake.tables["payments"][0]["provider"] == "cash"
        assert fake.tables["payments"][0]["status"] == "pending"
        assert fake.tables["order_items"][0]["title_snapshot"] == "React & TypeScript"
    finally:
        app.dependency_overrides.clear()


# ── Validation cash par admin ────────────────────────────────────────────────


def _cash_order_tables():
    order_id = str(uuid.uuid4())
    return order_id, {
        "orders": [
            {
                "id": order_id,
                "user_id": USER_ID,
                "status": "pending_validation",
                "payment_method": "cash",
                "total_amount": "69.00",
                "currency": "EUR",
                "created_at": "2026-07-05T10:00:00+00:00",
                "order_items": [
                    {"course_id": COURSE_ROW["id"], "title_snapshot": "React & TS", "price_snapshot": "69.00"}
                ],
                "profiles": {"full_name": "Client Test"},
            }
        ],
        "order_items": [{"order_id": order_id, "course_id": COURSE_ROW["id"]}],
        "payments": [{"id": str(uuid.uuid4()), "order_id": order_id, "provider": "cash", "status": "pending"}],
        "enrollments": [],
    }


def test_validate_cash_requires_admin(client, fake_db):
    order_id, tables = _cash_order_tables()
    fake_db(tables)
    app = override_user(client, role="user")
    try:
        assert client.post(f"/api/admin/orders/{order_id}/validate-cash").status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_validate_cash_grants_access(client, fake_db):
    order_id, tables = _cash_order_tables()
    fake = fake_db(tables)
    app = override_user(client, role="admin")
    try:
        response = client.post(f"/api/admin/orders/{order_id}/validate-cash")
        assert response.status_code == 200
        assert response.json()["status"] == "paid"
        assert fake.tables["enrollments"][0]["user_id"] == USER_ID
        assert fake.tables["enrollments"][0]["course_id"] == COURSE_ROW["id"]
        assert fake.tables["payments"][0]["status"] == "succeeded"
    finally:
        app.dependency_overrides.clear()


def test_validate_cash_rejects_already_paid(client, fake_db):
    order_id, tables = _cash_order_tables()
    tables["orders"][0]["status"] = "paid"
    fake_db(tables)
    app = override_user(client, role="admin")
    try:
        assert client.post(f"/api/admin/orders/{order_id}/validate-cash").status_code == 409
    finally:
        app.dependency_overrides.clear()


def test_cancel_order(client, fake_db):
    order_id, tables = _cash_order_tables()
    fake = fake_db(tables)
    app = override_user(client, role="admin")
    try:
        response = client.post(f"/api/admin/orders/{order_id}/cancel")
        assert response.status_code == 200
        assert response.json()["status"] == "canceled"
        assert fake.tables["payments"][0]["status"] == "failed"
    finally:
        app.dependency_overrides.clear()


def test_admin_orders_list_filter(client, fake_db):
    _, tables = _cash_order_tables()
    fake_db(tables)
    app = override_user(client, role="admin")
    try:
        response = client.get("/api/admin/orders?status=pending_validation")
        assert response.status_code == 200
        assert response.json()[0]["customer_name"] == "Client Test"
        assert client.get("/api/admin/orders?status=nimporte").status_code == 422
    finally:
        app.dependency_overrides.clear()


# ── Webhook Stripe ───────────────────────────────────────────────────────────


def test_stripe_webhook_rejects_bad_signature(client, fake_db):
    fake_db({})
    response = client.post(
        "/api/payments/webhooks/stripe",
        content=b'{"type": "checkout.session.completed"}',
        headers={"stripe-signature": "t=1,v1=invalid"},
    )
    assert response.status_code == 400


# ── Flutterwave (mobile money) ───────────────────────────────────────────────


def _flutterwave_settings(monkeypatch):
    from app.core.config import get_settings

    base = get_settings()
    stub = SimpleNamespace(
        **{
            **{name: getattr(base, name) for name in type(base).model_fields},
            "flutterwave_secret_key": "FLWSECK_TEST-xyz",
            "flutterwave_webhook_hash": "hash-secret",
        }
    )
    monkeypatch.setattr("app.routers.payments.get_settings", lambda: stub)


def test_checkout_mobile_money_unavailable_without_key(client, fake_db):
    fake_db({"cart_items": [cart_row()]})
    app = override_user(client, role="user")
    try:
        response = client.post("/api/payments/checkout", json={"payment_method": "mobile_money"})
        assert response.status_code == 503
    finally:
        app.dependency_overrides.clear()


def test_checkout_mobile_money_creates_flutterwave_payment(client, fake_db, monkeypatch):
    fake = fake_db({"cart_items": [cart_row()], "orders": [], "order_items": [], "payments": []})
    _flutterwave_settings(monkeypatch)
    monkeypatch.setattr(
        "app.services.flutterwave_service.create_payment_link",
        lambda **kwargs: "https://checkout.flutterwave.com/pay/test123",
    )
    app = override_user(client, role="user")
    try:
        response = client.post(
            "/api/payments/checkout",
            json={"payment_method": "mobile_money", "operator": "MTN Mobile Money", "phone_number": "061234567"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["redirect_url"] == "https://checkout.flutterwave.com/pay/test123"
        assert body["order"]["status"] == "pending"
        payment = fake.tables["payments"][0]
        assert payment["provider"] == "flutterwave"
        assert payment["currency"] == "XAF"
        assert payment["amount"] == 45261  # 69 EUR au taux fixe CFA
        assert payment["provider_reference"] == body["order"]["id"]
    finally:
        app.dependency_overrides.clear()


def test_flutterwave_webhook_rejects_bad_hash(client, fake_db, monkeypatch):
    fake_db({})
    _flutterwave_settings(monkeypatch)
    response = client.post(
        "/api/payments/webhooks/flutterwave",
        json={"event": "charge.completed"},
        headers={"verif-hash": "mauvais-hash"},
    )
    assert response.status_code == 401


def test_flutterwave_webhook_fulfills_order(client, fake_db, monkeypatch):
    order_id = str(uuid.uuid4())
    fake = fake_db(
        {
            "orders": [
                {
                    "id": order_id,
                    "user_id": USER_ID,
                    "status": "pending",
                    "payment_method": "mobile_money",
                    "total_amount": "69.00",
                    "currency": "EUR",
                    "created_at": "2026-07-05T10:00:00+00:00",
                }
            ],
            "order_items": [{"order_id": order_id, "course_id": COURSE_ROW["id"]}],
            "payments": [
                {
                    "id": str(uuid.uuid4()),
                    "order_id": order_id,
                    "provider": "flutterwave",
                    "provider_reference": order_id,
                    "status": "pending",
                    "amount": 45261,
                    "currency": "XAF",
                }
            ],
            "enrollments": [],
        }
    )
    _flutterwave_settings(monkeypatch)
    monkeypatch.setattr(
        "app.services.flutterwave_service.verify_transaction",
        lambda _id: {"status": "successful", "tx_ref": order_id, "currency": "XAF", "amount": 45261},
    )

    response = client.post(
        "/api/payments/webhooks/flutterwave",
        json={
            "event": "charge.completed",
            "data": {"id": 12345, "tx_ref": order_id, "status": "successful", "amount": 45261, "currency": "XAF"},
        },
        headers={"verif-hash": "hash-secret"},
    )
    assert response.status_code == 200
    assert fake.tables["orders"][0]["status"] == "paid"
    assert fake.tables["payments"][0]["status"] == "succeeded"
    assert fake.tables["enrollments"][0]["course_id"] == COURSE_ROW["id"]
