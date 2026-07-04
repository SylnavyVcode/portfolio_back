"""Panier serveur — remplace le panier localStorage du front.

Les identifiants exposés au front sont les slugs des formations ; la
résolution slug → uuid se fait ici.
"""

from fastapi import APIRouter, Depends, HTTPException, status

from app.db.supabase_client import get_service_client
from app.dependencies import CurrentUser, get_current_user
from app.routers.courses import COURSE_COLUMNS, course_row_to_summary
from app.schemas.auth import MessageResponse
from app.schemas.courses import CartAddRequest, CartItemOut, CartOut

router = APIRouter()


def _resolve_published_course_id(slug: str) -> str:
    result = (
        get_service_client()
        .table("courses")
        .select("id")
        .eq("slug", slug)
        .eq("is_published", True)
        .limit(1)
        .execute()
    )
    if not result.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Formation introuvable")
    return result.data[0]["id"]


def _load_cart(user_id: str) -> CartOut:
    # Embedding PostgREST : chaque ligne du panier embarque sa formation.
    result = (
        get_service_client()
        .table("cart_items")
        .select(f"added_at, courses({COURSE_COLUMNS})")
        .eq("user_id", user_id)
        .order("added_at")
        .execute()
    )
    items = [
        CartItemOut(course=course_row_to_summary(row["courses"]), added_at=row["added_at"])
        for row in (result.data or [])
        if row.get("courses")
    ]
    return CartOut(items=items, subtotal=sum(item.course.price for item in items))


@router.get("", response_model=CartOut)
def get_cart(user: CurrentUser = Depends(get_current_user)):
    return _load_cart(user.id)


@router.post("/items", response_model=CartOut)
def add_item(payload: CartAddRequest, user: CurrentUser = Depends(get_current_user)):
    course_id = _resolve_published_course_id(payload.course_slug)
    get_service_client().table("cart_items").upsert(
        {"user_id": user.id, "course_id": course_id},
        on_conflict="user_id,course_id",
        ignore_duplicates=True,
    ).execute()
    return _load_cart(user.id)


@router.delete("/items/{course_slug}", response_model=CartOut)
def remove_item(course_slug: str, user: CurrentUser = Depends(get_current_user)):
    course_id = _resolve_published_course_id(course_slug)
    get_service_client().table("cart_items").delete().eq("user_id", user.id).eq(
        "course_id", course_id
    ).execute()
    return _load_cart(user.id)


@router.delete("", response_model=MessageResponse)
def clear_cart(user: CurrentUser = Depends(get_current_user)):
    get_service_client().table("cart_items").delete().eq("user_id", user.id).execute()
    return MessageResponse(message="Panier vidé")
