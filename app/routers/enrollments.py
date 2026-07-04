"""Données du compte connecté : formations débloquées et commandes."""

from fastapi import APIRouter, Depends, HTTPException, status

from app.db.supabase_client import get_service_client
from app.dependencies import CurrentUser, get_current_user
from app.routers.courses import COURSE_COLUMNS, course_row_to_summary
from app.schemas.courses import EnrollmentOut, OrderItemOut, OrderOut

router = APIRouter()

ORDER_COLUMNS = "id, status, payment_method, total_amount, currency, created_at, order_items(course_id, title_snapshot, price_snapshot)"


def _order_row_to_out(row: dict) -> OrderOut:
    return OrderOut(
        id=row["id"],
        status=row["status"],
        payment_method=row["payment_method"],
        total_amount=float(row.get("total_amount") or 0),
        currency=row.get("currency") or "EUR",
        created_at=row["created_at"],
        items=[
            OrderItemOut(
                course_id=item.get("course_id"),
                title_snapshot=item.get("title_snapshot") or "",
                price_snapshot=float(item.get("price_snapshot") or 0),
            )
            for item in (row.get("order_items") or [])
        ],
    )


@router.get("/enrollments", response_model=list[EnrollmentOut])
def my_enrollments(user: CurrentUser = Depends(get_current_user)):
    result = (
        get_service_client()
        .table("enrollments")
        .select(f"status, granted_at, courses({COURSE_COLUMNS})")
        .eq("user_id", user.id)
        .eq("status", "active")
        .order("granted_at", desc=True)
        .execute()
    )
    return [
        EnrollmentOut(
            course=course_row_to_summary(row["courses"]),
            status=row["status"],
            granted_at=row["granted_at"],
        )
        for row in (result.data or [])
        if row.get("courses")
    ]


@router.get("/orders", response_model=list[OrderOut])
def my_orders(user: CurrentUser = Depends(get_current_user)):
    result = (
        get_service_client()
        .table("orders")
        .select(ORDER_COLUMNS)
        .eq("user_id", user.id)
        .order("created_at", desc=True)
        .execute()
    )
    return [_order_row_to_out(row) for row in (result.data or [])]


@router.get("/orders/{order_id}", response_model=OrderOut)
def my_order(order_id: str, user: CurrentUser = Depends(get_current_user)):
    result = (
        get_service_client()
        .table("orders")
        .select(ORDER_COLUMNS)
        .eq("id", order_id)
        .eq("user_id", user.id)
        .limit(1)
        .execute()
    )
    if not result.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Commande introuvable")
    return _order_row_to_out(result.data[0])
