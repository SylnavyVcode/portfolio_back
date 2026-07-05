"""Tests du service email Brevo : no-op sans clé, payload correct avec clé,
et aucun échec propagé au flux appelant."""

from types import SimpleNamespace

import pytest

from app.services import email_service


def settings_stub(**overrides):
    values = {
        "brevo_api_key": "",
        "email_sender_name": "Valmy Mabika",
        "email_sender_address": "",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_noop_without_api_key(monkeypatch):
    monkeypatch.setattr(email_service, "get_settings", lambda: settings_stub())

    def forbidden(*_a, **_k):
        raise AssertionError("httpx.post ne doit pas être appelé sans clé API")

    monkeypatch.setattr(email_service.httpx, "post", forbidden)
    email_service.send_welcome("user@example.com", "Test")  # ne doit pas lever


def test_sends_correct_payload(monkeypatch):
    monkeypatch.setattr(
        email_service,
        "get_settings",
        lambda: settings_stub(brevo_api_key="key-123", email_sender_address="no-reply@valmy.dev"),
    )
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append({"url": url, "headers": headers, "json": json})
        return SimpleNamespace(raise_for_status=lambda: None)

    monkeypatch.setattr(email_service.httpx, "post", fake_post)

    email_service.send_payment_confirmed("client@example.com", "ord-1", 69.0)

    assert len(calls) == 1
    call = calls[0]
    assert call["url"] == email_service.BREVO_API_URL
    assert call["headers"]["api-key"] == "key-123"
    assert call["json"]["sender"] == {"name": "Valmy Mabika", "email": "no-reply@valmy.dev"}
    assert call["json"]["to"] == [{"email": "client@example.com"}]
    assert "69.00" in call["json"]["htmlContent"]
    assert "ord-1" in call["json"]["htmlContent"]


def test_send_failure_does_not_raise(monkeypatch):
    monkeypatch.setattr(
        email_service,
        "get_settings",
        lambda: settings_stub(brevo_api_key="key-123", email_sender_address="no-reply@valmy.dev"),
    )

    def failing_post(*_a, **_k):
        raise ConnectionError("réseau indisponible")

    monkeypatch.setattr(email_service.httpx, "post", failing_post)
    email_service.send_cash_validated("client@example.com", "ord-2", 30.0)  # ne doit pas lever


def test_empty_recipient_skipped(monkeypatch):
    monkeypatch.setattr(
        email_service,
        "get_settings",
        lambda: settings_stub(brevo_api_key="key-123", email_sender_address="no-reply@valmy.dev"),
    )

    def forbidden(*_a, **_k):
        raise AssertionError("httpx.post ne doit pas être appelé sans destinataire")

    monkeypatch.setattr(email_service.httpx, "post", forbidden)
    email_service.send_welcome("", "Test")
