"""Paiements — lot 1 : Stripe Checkout (carte) + paiement cash validé par admin.

Cycle de vie d'une commande :
- card : pending → (webhook Stripe) → paid + enrollments, ou failed/canceled
- cash : pending_validation → (validation admin) → paid + enrollments

Les webhooks mettent à jour `payments` puis `orders`, et créent les
`enrollments` de façon idempotente (upsert).
"""

import logging

import stripe as stripe_lib
from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.core.rate_limit import limiter
from app.db.supabase_client import get_service_client
from app.dependencies import CurrentUser, get_current_user
from app.routers.cart import _load_cart
from app.schemas.courses import OrderItemOut, OrderOut
from app.schemas.payments import CheckoutRequest, CheckoutResponse
from app.services import email_service, stripe_service

logger = logging.getLogger(__name__)

router = APIRouter()


def fulfill_order(order: dict, *, validated_by: str | None = None, provider: str) -> None:
    """Passe la commande en `paid`, marque le paiement `succeeded` et crée
    les enrollments. Idempotent (upsert + update inconditionnels)."""
    client = get_service_client()
    order_id = order["id"]

    items = (
        client.table("order_items")
        .select("course_id")
        .eq("order_id", order_id)
        .execute()
    )
    enrollments = [
        {"user_id": order["user_id"], "course_id": item["course_id"], "order_id": order_id}
        for item in (items.data or [])
        if item.get("course_id")
    ]
    if enrollments:
        client.table("enrollments").upsert(
            enrollments, on_conflict="user_id,course_id", ignore_duplicates=True
        ).execute()

    payment_update: dict = {"status": "succeeded"}
    if validated_by:
        payment_update["validated_by"] = validated_by
    client.table("payments").update(payment_update).eq("order_id", order_id).eq(
        "provider", provider
    ).execute()

    client.table("orders").update({"status": "paid"}).eq("id", order_id).execute()
    logger.info("Commande %s payée (%s) — %d enrollment(s)", order_id, provider, len(enrollments))


def _order_out(order: dict, items: list[dict]) -> OrderOut:
    return OrderOut(
        id=order["id"],
        status=order["status"],
        payment_method=order["payment_method"],
        total_amount=float(order.get("total_amount") or 0),
        currency=order.get("currency") or "EUR",
        created_at=order["created_at"],
        items=[
            OrderItemOut(
                course_id=item.get("course_id"),
                title_snapshot=item.get("title_snapshot") or "",
                price_snapshot=float(item.get("price_snapshot") or 0),
            )
            for item in items
        ],
    )


@router.post("/checkout", response_model=CheckoutResponse)
@limiter.limit("10/minute")
def checkout(
    request: Request,
    payload: CheckoutRequest,
    user: CurrentUser = Depends(get_current_user),
):
    if payload.payment_method in ("paypal", "mobile_money"):
        raise HTTPException(
            status.HTTP_501_NOT_IMPLEMENTED,
            "Ce moyen de paiement sera bientôt disponible — utilisez la carte ou le cash",
        )

    cart = _load_cart(user.id)
    if not cart.items:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Votre panier est vide")

    client = get_service_client()
    is_cash = payload.payment_method == "cash"

    order_result = (
        client.table("orders")
        .insert(
            {
                "user_id": user.id,
                "status": "pending_validation" if is_cash else "pending",
                "payment_method": payload.payment_method,
                "total_amount": cart.subtotal,
                "currency": "EUR",
            }
        )
        .execute()
    )
    order = order_result.data[0]

    items_values = [
        {
            "order_id": order["id"],
            "course_id": item.course.id,
            "title_snapshot": item.course.title.fr or item.course.title.en,
            "price_snapshot": item.course.price,
        }
        for item in cart.items
    ]
    items_result = client.table("order_items").insert(items_values).execute()
    client.table("cart_items").delete().eq("user_id", user.id).execute()

    redirect_url: str | None = None

    if is_cash:
        client.table("payments").insert(
            {
                "order_id": order["id"],
                "provider": "cash",
                "status": "pending",
                "amount": cart.subtotal,
                "currency": "EUR",
            }
        ).execute()
        email_service.send_cash_order_pending(user.email, order["id"], cart.subtotal)
    else:  # card → Stripe Checkout
        try:
            session = stripe_service.create_checkout_session(
                order_id=order["id"],
                user_email=user.email,
                items=[
                    {"name": item.course.title.fr or item.course.title.en, "amount_eur": item.course.price}
                    for item in cart.items
                ],
            )
        except stripe_lib.error.StripeError:
            logger.exception("Création de session Stripe impossible (commande %s)", order["id"])
            client.table("orders").update({"status": "failed"}).eq("id", order["id"]).execute()
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                "Paiement par carte momentanément indisponible, réessayez plus tard",
            )
        client.table("payments").insert(
            {
                "order_id": order["id"],
                "provider": "stripe",
                "provider_reference": session.id,
                "status": "pending",
                "amount": cart.subtotal,
                "currency": "EUR",
            }
        ).execute()
        redirect_url = session.url

    return CheckoutResponse(
        order=_order_out(order, items_result.data or items_values),
        redirect_url=redirect_url,
    )


# ── Webhook Stripe ───────────────────────────────────────────────────────────


def _find_stripe_payment(session_id: str) -> dict | None:
    result = (
        get_service_client()
        .table("payments")
        .select("id, order_id, status")
        .eq("provider", "stripe")
        .eq("provider_reference", session_id)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def _get_order(order_id: str) -> dict | None:
    result = (
        get_service_client()
        .table("orders")
        .select("id, user_id, status, total_amount")
        .eq("id", order_id)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


@router.post("/webhooks/stripe", status_code=status.HTTP_200_OK)
async def stripe_webhook(request: Request):
    payload = await request.body()
    signature = request.headers.get("stripe-signature", "")
    try:
        event = stripe_service.parse_webhook_event(payload, signature)
    except (ValueError, stripe_lib.error.SignatureVerificationError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Signature de webhook invalide")

    event_type = event["type"]
    client = get_service_client()

    if event_type in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
        session = event["data"]["object"]
        if session.get("payment_status") != "paid":
            return {"received": True}
        payment = _find_stripe_payment(session["id"])
        if payment is None:
            logger.warning("Webhook Stripe: session %s inconnue", session["id"])
            return {"received": True}
        if payment["status"] == "succeeded":
            return {"received": True}  # déjà traité (idempotence)
        order = _get_order(payment["order_id"])
        if order is None:
            return {"received": True}
        client.table("payments").update(
            {"raw_payload": {"payment_intent": session.get("payment_intent"), "event": event_type}}
        ).eq("id", payment["id"]).execute()
        fulfill_order(order, provider="stripe")
        # Email de confirmation (Brevo au module emailing)
        if session.get("customer_details"):
            email_service.send_payment_confirmed(
                session["customer_details"].get("email") or "",
                order["id"],
                float(order.get("total_amount") or 0),
            )

    elif event_type in ("checkout.session.expired", "checkout.session.async_payment_failed"):
        session = event["data"]["object"]
        payment = _find_stripe_payment(session["id"])
        if payment and payment["status"] == "pending":
            client.table("payments").update({"status": "failed"}).eq("id", payment["id"]).execute()
            client.table("orders").update({"status": "failed"}).eq("id", payment["order_id"]).eq(
                "status", "pending"
            ).execute()

    elif event_type == "charge.refunded":
        charge = event["data"]["object"]
        payment_intent = charge.get("payment_intent")
        if payment_intent:
            result = (
                client.table("payments")
                .select("id, order_id")
                .eq("provider", "stripe")
                .eq("raw_payload->>payment_intent", payment_intent)
                .limit(1)
                .execute()
            )
            if result.data:
                payment = result.data[0]
                client.table("payments").update({"status": "refunded"}).eq("id", payment["id"]).execute()
                client.table("orders").update({"status": "refunded"}).eq("id", payment["order_id"]).execute()
                client.table("enrollments").update({"status": "revoked"}).eq(
                    "order_id", payment["order_id"]
                ).execute()
                logger.info("Commande %s remboursée — accès révoqués", payment["order_id"])

    return {"received": True}
