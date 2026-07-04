"""Intégration Stripe Checkout (redirection hébergée — aucune donnée de
carte ne transite par ce back-end)."""

import stripe

from app.core.config import get_settings


def _configure() -> None:
    stripe.api_key = get_settings().stripe_secret_key


def create_checkout_session(*, order_id: str, user_email: str, items: list[dict]) -> stripe.checkout.Session:
    """`items` : [{"name": str, "amount_eur": float}]. Retourne la session (id + url)."""
    _configure()
    settings = get_settings()
    return stripe.checkout.Session.create(
        mode="payment",
        customer_email=user_email or None,
        line_items=[
            {
                "quantity": 1,
                "price_data": {
                    "currency": "eur",
                    "unit_amount": int(round(item["amount_eur"] * 100)),
                    "product_data": {"name": item["name"][:250]},
                },
            }
            for item in items
        ],
        metadata={"order_id": order_id},
        success_url=f"{settings.frontend_url}/admin?tab=orders&payment=success",
        cancel_url=f"{settings.frontend_url}/admin?tab=orders&payment=canceled",
    )


def parse_webhook_event(payload: bytes, signature: str) -> stripe.Event:
    """Vérifie la signature du webhook et retourne l'événement (ValueError /
    stripe.error.SignatureVerificationError si invalide)."""
    _configure()
    return stripe.Webhook.construct_event(
        payload, signature, get_settings().stripe_webhook_secret
    )
