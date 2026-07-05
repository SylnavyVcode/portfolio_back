"""Intégration Flutterwave (mobile money) — API v3, paiement hébergé.

Les prix du site sont en EUR mais les opérateurs mobile money d'Afrique
centrale encaissent en XAF : le montant est converti au taux fixe CFA
(1 EUR = 655.957 XAF) au moment du checkout. La commande reste en EUR,
la transaction Flutterwave est enregistrée en XAF.

Prérequis production : compte marchand Flutterwave vérifié (KYC). En
sandbox (clés de test), le flux complet fonctionne sans vérification.
"""

import httpx

from app.core.config import get_settings

FLUTTERWAVE_API_URL = "https://api.flutterwave.com/v3"
XAF_PER_EUR = 655.957
TIMEOUT_SECONDS = 15


def eur_to_xaf(amount_eur: float) -> int:
    return int(round(amount_eur * XAF_PER_EUR))


def _headers() -> dict:
    return {"Authorization": f"Bearer {get_settings().flutterwave_secret_key}"}


def create_payment_link(
    *,
    tx_ref: str,
    amount_eur: float,
    customer_email: str,
    customer_name: str = "",
    phone_number: str | None = None,
    description: str = "Formations Valmy Mabika",
) -> str:
    """Crée un paiement hébergé et retourne l'URL de redirection.

    Lève httpx.HTTPError ou ValueError si Flutterwave refuse.
    """
    settings = get_settings()
    customer: dict = {"email": customer_email}
    if customer_name:
        customer["name"] = customer_name
    if phone_number:
        customer["phonenumber"] = phone_number

    response = httpx.post(
        f"{FLUTTERWAVE_API_URL}/payments",
        headers=_headers(),
        json={
            "tx_ref": tx_ref,
            "amount": eur_to_xaf(amount_eur),
            "currency": "XAF",
            "redirect_url": f"{settings.frontend_url}/admin?tab=orders&payment=success",
            "customer": customer,
            "customizations": {"title": "Valmy Mabika — Formations", "description": description},
        },
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    body = response.json()
    if body.get("status") != "success" or not body.get("data", {}).get("link"):
        raise ValueError(f"Réponse Flutterwave inattendue: {body.get('message')}")
    return body["data"]["link"]


def verify_transaction(transaction_id: int | str) -> dict:
    """Re-vérifie une transaction côté API (à faire systématiquement dans le
    webhook : le payload entrant ne suffit pas). Retourne le bloc `data`."""
    response = httpx.get(
        f"{FLUTTERWAVE_API_URL}/transactions/{transaction_id}/verify",
        headers=_headers(),
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    body = response.json()
    if body.get("status") != "success":
        raise ValueError(f"Vérification Flutterwave échouée: {body.get('message')}")
    return body.get("data") or {}
