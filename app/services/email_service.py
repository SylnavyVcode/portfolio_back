"""Emails transactionnels.

Implémentation Brevo prévue au module emailing : tant que BREVO_API_KEY
n'est pas configurée, chaque envoi est simplement journalisé (no-op).
Les appels sont déjà en place dans les flux paiement/inscription pour que
le branchement soit transparent.
"""

import logging

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def _send(to_email: str, subject: str, text_content: str) -> None:
    settings = get_settings()
    if not settings.brevo_api_key:
        logger.info("[email désactivé] à=%s sujet=%r", to_email, subject)
        return
    # Module emailing : appel API Brevo ici.
    logger.warning("BREVO_API_KEY définie mais l'envoi n'est pas encore implémenté (module emailing)")


def send_cash_order_pending(to_email: str, order_id: str, total: float) -> None:
    _send(
        to_email,
        "Votre commande est en attente de validation",
        f"Commande {order_id} ({total:.2f} €) enregistrée. "
        "Elle sera activée dès réception de votre paiement en espèces.",
    )


def send_payment_confirmed(to_email: str, order_id: str, total: float) -> None:
    _send(
        to_email,
        "Paiement confirmé — accès débloqué",
        f"Votre paiement de {total:.2f} € (commande {order_id}) est confirmé. "
        "Vos formations sont disponibles dans votre espace « Mes formations ».",
    )
