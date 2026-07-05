"""Emails transactionnels via Brevo (API v3).

Comportement :
- BREVO_API_KEY vide => no-op journalisé (le site fonctionne sans email) ;
- un échec d'envoi est loggé mais ne casse JAMAIS le flux appelant
  (inscription, paiement...) — l'email est un effet de bord, pas une étape.

La réinitialisation de mot de passe reste gérée par Supabase Auth
(email natif), elle n'apparaît donc pas ici.
"""

import logging

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"
TIMEOUT_SECONDS = 10


def _html_layout(title: str, paragraphs: list[str]) -> str:
    body = "".join(
        f'<p style="margin:0 0 14px;font-size:15px;line-height:1.7;color:#374151;">{p}</p>'
        for p in paragraphs
    )
    return f"""\
<div style="background:#f4f5fb;padding:32px 16px;font-family:Arial,Helvetica,sans-serif;">
  <div style="max-width:560px;margin:0 auto;background:#ffffff;border-radius:12px;overflow:hidden;">
    <div style="background:#15123a;padding:20px 28px;">
      <span style="color:#ffffff;font-size:18px;font-weight:bold;">Valmy Mabika</span>
    </div>
    <div style="padding:28px;">
      <h1 style="margin:0 0 18px;font-size:20px;color:#15123a;">{title}</h1>
      {body}
    </div>
    <div style="padding:16px 28px;border-top:1px solid #e5e7eb;">
      <p style="margin:0;font-size:12px;color:#9ca3af;">
        Cet email a été envoyé automatiquement — merci de ne pas y répondre.
      </p>
    </div>
  </div>
</div>"""


def _send(to_email: str, subject: str, title: str, paragraphs: list[str]) -> None:
    settings = get_settings()
    if not settings.brevo_api_key or not settings.email_sender_address:
        logger.info("[email désactivé] à=%s sujet=%r", to_email, subject)
        return
    if not to_email:
        logger.warning("[email] destinataire vide, envoi ignoré (sujet=%r)", subject)
        return
    try:
        response = httpx.post(
            BREVO_API_URL,
            headers={"api-key": settings.brevo_api_key, "accept": "application/json"},
            json={
                "sender": {
                    "name": settings.email_sender_name,
                    "email": settings.email_sender_address,
                },
                "to": [{"email": to_email}],
                "subject": subject,
                "htmlContent": _html_layout(title, paragraphs),
            },
            timeout=TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        logger.info("[email envoyé] à=%s sujet=%r", to_email, subject)
    except Exception:
        # L'email ne doit jamais faire échouer le flux appelant.
        logger.exception("Envoi d'email impossible (à=%s, sujet=%r)", to_email, subject)


# ── Emails transactionnels ───────────────────────────────────────────────────


def send_welcome(to_email: str, full_name: str) -> None:
    first_name = (full_name or "").strip() or "et bienvenue"
    _send(
        to_email,
        "Bienvenue sur le site de Valmy Mabika 🎉",
        f"Bienvenue, {first_name} !",
        [
            "Votre compte a bien été créé.",
            "Vous pouvez dès maintenant parcourir les formations, constituer votre panier "
            "et suivre vos commandes depuis votre espace personnel.",
            "À très vite !",
        ],
    )


def send_cash_order_pending(to_email: str, order_id: str, total: float) -> None:
    _send(
        to_email,
        "Commande enregistrée — en attente de votre paiement",
        "Commande en attente de validation",
        [
            f"Votre commande <strong>{order_id}</strong> d'un montant de "
            f"<strong>{total:.2f} €</strong> a bien été enregistrée.",
            "Elle sera activée dès réception et validation de votre paiement en espèces.",
            "Vous recevrez un email de confirmation dès que l'accès à vos formations sera débloqué.",
        ],
    )


def send_payment_confirmed(to_email: str, order_id: str, total: float) -> None:
    _send(
        to_email,
        "Paiement confirmé — vos formations sont débloquées ✅",
        "Merci pour votre achat !",
        [
            f"Votre paiement de <strong>{total:.2f} €</strong> "
            f"(commande <strong>{order_id}</strong>) est confirmé.",
            "Vos formations sont disponibles dès maintenant dans votre espace "
            "« Mes formations ».",
            "Bon apprentissage !",
        ],
    )


def send_cash_validated(to_email: str, order_id: str, total: float) -> None:
    _send(
        to_email,
        "Paiement cash validé — vos formations sont débloquées ✅",
        "Votre paiement a été validé",
        [
            f"Nous avons bien reçu votre paiement en espèces de <strong>{total:.2f} €</strong> "
            f"(commande <strong>{order_id}</strong>).",
            "L'accès à vos formations vient d'être débloqué : rendez-vous dans votre espace "
            "« Mes formations ».",
            "Merci de votre confiance !",
        ],
    )
