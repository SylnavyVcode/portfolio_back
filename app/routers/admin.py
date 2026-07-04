"""Back-office — routes réservées au rôle admin.

Module blog pour l'instant ; les formations et la validation des paiements
cash rejoindront ce router dans les modules suivants.
"""

import re
import secrets
import unicodedata
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from postgrest.exceptions import APIError

from app.db.supabase_client import get_service_client
from app.dependencies import CurrentUser, require_admin
from app.routers.blog import DETAIL_COLUMNS, row_to_detail
from app.routers.courses import (
    COURSE_DETAIL_COLUMNS,
    LESSON_DETAIL_COLUMNS,
    course_row_to_summary,
    lesson_row_to_detail,
)
from app.schemas.auth import MessageResponse
from app.routers.payments import fulfill_order
from app.schemas.blog import BlogPostAdmin, BlogPostCreate, BlogPostUpdate, LocalizedText
from app.schemas.courses import (
    CourseCreate,
    CourseDetail,
    CourseUpdate,
    LessonCreate,
    LessonDetail,
    LessonUpdate,
    OrderItemOut,
)
from app.schemas.payments import AdminOrderOut

router = APIRouter(dependencies=[Depends(require_admin)])

ADMIN_COLUMNS = DETAIL_COLUMNS + ", status, created_at, updated_at"


def slugify(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-")
    return slug[:100] or f"article-{secrets.token_hex(3)}"


def row_to_admin(row: dict) -> BlogPostAdmin:
    detail = row_to_detail(row)
    return BlogPostAdmin(
        **detail.model_dump(),
        status=row.get("status", "draft"),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


def _payload_to_columns(payload: BlogPostCreate | BlogPostUpdate) -> dict:
    """Convertit les champs imbriqués {fr,en} en colonnes plates de la table."""
    values: dict = {}
    data = payload.model_dump(exclude_unset=True)
    for field, prefix in (("title", "title"), ("category", "category"), ("excerpt", "excerpt")):
        if data.get(field) is not None:
            values[f"{prefix}_fr"] = data[field]["fr"]
            values[f"{prefix}_en"] = data[field]["en"]
    if data.get("content") is not None:
        values["content_fr"] = data["content"]["fr"]
        values["content_en"] = data["content"]["en"]
    for field in ("slug", "image_url", "read_time", "featured", "status"):
        if field in data and data[field] is not None:
            values[field] = data[field]
    return values


# ── Blog ─────────────────────────────────────────────────────────────────────


@router.get("/blog", response_model=list[BlogPostAdmin])
def admin_list_posts():
    result = (
        get_service_client()
        .table("blog_posts")
        .select(ADMIN_COLUMNS)
        .order("updated_at", desc=True)
        .execute()
    )
    return [row_to_admin(row) for row in (result.data or [])]


@router.get("/blog/{post_id}", response_model=BlogPostAdmin)
def admin_get_post(post_id: str):
    result = (
        get_service_client()
        .table("blog_posts")
        .select(ADMIN_COLUMNS)
        .eq("id", post_id)
        .limit(1)
        .execute()
    )
    if not result.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Article introuvable")
    return row_to_admin(result.data[0])


@router.post("/blog", response_model=BlogPostAdmin, status_code=status.HTTP_201_CREATED)
def admin_create_post(payload: BlogPostCreate, admin: CurrentUser = Depends(require_admin)):
    values = _payload_to_columns(payload)
    values.setdefault("slug", slugify(payload.title.fr or payload.title.en))
    values["author_id"] = admin.id
    if values.get("status") == "published":
        values["published_at"] = datetime.now(timezone.utc).isoformat()

    client = get_service_client()
    try:
        result = client.table("blog_posts").insert(values).execute()
    except APIError as exc:
        if exc.code == "23505":  # slug déjà pris : suffixe aléatoire
            values["slug"] = f"{values['slug']}-{secrets.token_hex(2)}"
            result = client.table("blog_posts").insert(values).execute()
        else:
            raise

    created = (
        client.table("blog_posts").select(ADMIN_COLUMNS).eq("id", result.data[0]["id"]).single().execute()
    )
    return row_to_admin(created.data)


@router.put("/blog/{post_id}", response_model=BlogPostAdmin)
def admin_update_post(post_id: str, payload: BlogPostUpdate):
    client = get_service_client()

    existing = (
        client.table("blog_posts").select("id, status, published_at").eq("id", post_id).limit(1).execute()
    )
    if not existing.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Article introuvable")

    values = _payload_to_columns(payload)
    if not values:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Aucun champ à mettre à jour")

    # Première publication : on fixe la date de publication.
    if values.get("status") == "published" and not existing.data[0].get("published_at"):
        values["published_at"] = datetime.now(timezone.utc).isoformat()

    try:
        client.table("blog_posts").update(values).eq("id", post_id).execute()
    except APIError as exc:
        if exc.code == "23505":
            raise HTTPException(status.HTTP_409_CONFLICT, "Ce slug est déjà utilisé")
        raise

    updated = client.table("blog_posts").select(ADMIN_COLUMNS).eq("id", post_id).single().execute()
    return row_to_admin(updated.data)


@router.delete("/blog/{post_id}", response_model=MessageResponse)
def admin_delete_post(post_id: str):
    result = get_service_client().table("blog_posts").delete().eq("id", post_id).execute()
    if not result.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Article introuvable")
    return MessageResponse(message="Article supprimé")


# ── Formations ───────────────────────────────────────────────────────────────


def _course_row_to_detail(row: dict) -> CourseDetail:
    summary = course_row_to_summary(row)
    prerequisites = [
        LocalizedText(fr=item.get("fr", ""), en=item.get("en", ""))
        for item in (row.get("prerequisites") or [])
        if isinstance(item, dict)
    ]
    return CourseDetail(**summary.model_dump(), prerequisites=prerequisites, preview=row.get("preview"))


def _course_payload_to_columns(payload: CourseCreate | CourseUpdate) -> dict:
    values: dict = {}
    data = payload.model_dump(exclude_unset=True)
    for field, prefix in (("title", "title"), ("summary", "summary"), ("preview_label", "preview_label")):
        if data.get(field) is not None:
            values[f"{prefix}_fr"] = data[field]["fr"]
            values[f"{prefix}_en"] = data[field]["en"]
    if data.get("prerequisites") is not None:
        values["prerequisites"] = data["prerequisites"]
    if "preview" in data:
        values["preview"] = data["preview"]
    if data.get("price") is not None:
        values["price_amount"] = data["price"]
    for field in ("slug", "theme", "level", "duration_hours", "instructor", "rating",
                  "students_count", "is_published"):
        if field in data and data[field] is not None:
            values[field] = data[field]
    return values


@router.get("/courses", response_model=list[CourseDetail])
def admin_list_courses():
    result = (
        get_service_client()
        .table("courses")
        .select(COURSE_DETAIL_COLUMNS)
        .order("created_at", desc=True)
        .execute()
    )
    return [_course_row_to_detail(row) for row in (result.data or [])]


@router.post("/courses", response_model=CourseDetail, status_code=status.HTTP_201_CREATED)
def admin_create_course(payload: CourseCreate):
    values = _course_payload_to_columns(payload)
    values.setdefault("slug", slugify(payload.title.fr or payload.title.en))
    if values.get("is_published"):
        values["published_at"] = datetime.now(timezone.utc).isoformat()

    client = get_service_client()
    try:
        result = client.table("courses").insert(values).execute()
    except APIError as exc:
        if exc.code == "23505":
            values["slug"] = f"{values['slug']}-{secrets.token_hex(2)}"
            result = client.table("courses").insert(values).execute()
        else:
            raise

    created = client.table("courses").select(COURSE_DETAIL_COLUMNS).eq("id", result.data[0]["id"]).single().execute()
    return _course_row_to_detail(created.data)


@router.put("/courses/{course_id}", response_model=CourseDetail)
def admin_update_course(course_id: str, payload: CourseUpdate):
    client = get_service_client()
    existing = client.table("courses").select("id, published_at").eq("id", course_id).limit(1).execute()
    if not existing.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Formation introuvable")

    values = _course_payload_to_columns(payload)
    if not values:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Aucun champ à mettre à jour")

    if values.get("is_published") and not existing.data[0].get("published_at"):
        values["published_at"] = datetime.now(timezone.utc).isoformat()

    try:
        client.table("courses").update(values).eq("id", course_id).execute()
    except APIError as exc:
        if exc.code == "23505":
            raise HTTPException(status.HTTP_409_CONFLICT, "Ce slug est déjà utilisé")
        raise

    updated = client.table("courses").select(COURSE_DETAIL_COLUMNS).eq("id", course_id).single().execute()
    return _course_row_to_detail(updated.data)


@router.delete("/courses/{course_id}", response_model=MessageResponse)
def admin_delete_course(course_id: str):
    try:
        result = get_service_client().table("courses").delete().eq("id", course_id).execute()
    except APIError as exc:
        if exc.code == "23503":  # commandes existantes référencent cette formation
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Formation liée à des commandes — dépubliez-la plutôt que de la supprimer",
            )
        raise
    if not result.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Formation introuvable")
    return MessageResponse(message="Formation supprimée")


# ── Leçons ───────────────────────────────────────────────────────────────────


def _lesson_payload_to_columns(payload: LessonCreate | LessonUpdate) -> dict:
    values: dict = {}
    data = payload.model_dump(exclude_unset=True)
    if data.get("title") is not None:
        values["title_fr"] = data["title"]["fr"]
        values["title_en"] = data["title"]["en"]
    if data.get("content") is not None:
        values["content_fr"] = data["content"]["fr"]
        values["content_en"] = data["content"]["en"]
    for field in ("video_url", "duration_minutes", "is_free_preview", "position"):
        if field in data and data[field] is not None:
            values[field] = data[field]
    return values


@router.get("/courses/{course_id}/lessons", response_model=list[LessonDetail])
def admin_list_lessons(course_id: str):
    result = (
        get_service_client()
        .table("course_lessons")
        .select(LESSON_DETAIL_COLUMNS)
        .eq("course_id", course_id)
        .order("position")
        .execute()
    )
    return [lesson_row_to_detail(row) for row in (result.data or [])]


@router.post("/courses/{course_id}/lessons", response_model=LessonDetail, status_code=status.HTTP_201_CREATED)
def admin_create_lesson(course_id: str, payload: LessonCreate):
    client = get_service_client()
    course = client.table("courses").select("id").eq("id", course_id).limit(1).execute()
    if not course.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Formation introuvable")

    values = _lesson_payload_to_columns(payload)
    values["course_id"] = course_id
    if values.get("position") is None:
        # Position par défaut : à la suite de la dernière leçon.
        last = (
            client.table("course_lessons")
            .select("position")
            .eq("course_id", course_id)
            .order("position", desc=True)
            .limit(1)
            .execute()
        )
        values["position"] = (last.data[0]["position"] + 1) if last.data else 1

    try:
        result = client.table("course_lessons").insert(values).execute()
    except APIError as exc:
        if exc.code == "23505":
            raise HTTPException(status.HTTP_409_CONFLICT, "Cette position est déjà occupée")
        raise
    return lesson_row_to_detail(result.data[0])


@router.put("/lessons/{lesson_id}", response_model=LessonDetail)
def admin_update_lesson(lesson_id: str, payload: LessonUpdate):
    values = _lesson_payload_to_columns(payload)
    if not values:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Aucun champ à mettre à jour")
    try:
        result = get_service_client().table("course_lessons").update(values).eq("id", lesson_id).execute()
    except APIError as exc:
        if exc.code == "23505":
            raise HTTPException(status.HTTP_409_CONFLICT, "Cette position est déjà occupée")
        raise
    if not result.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Leçon introuvable")
    return lesson_row_to_detail(result.data[0])


@router.delete("/lessons/{lesson_id}", response_model=MessageResponse)
def admin_delete_lesson(lesson_id: str):
    result = get_service_client().table("course_lessons").delete().eq("id", lesson_id).execute()
    if not result.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Leçon introuvable")
    return MessageResponse(message="Leçon supprimée")


# ── Commandes (validation cash, annulation) ──────────────────────────────────

ADMIN_ORDER_COLUMNS = (
    "id, user_id, status, payment_method, total_amount, currency, created_at, "
    "order_items(course_id, title_snapshot, price_snapshot), profiles(full_name)"
)

ORDER_STATUSES = ("pending", "pending_validation", "paid", "failed", "canceled", "refunded")


def _admin_order_out(row: dict) -> AdminOrderOut:
    return AdminOrderOut(
        id=row["id"],
        user_id=row["user_id"],
        customer_name=(row.get("profiles") or {}).get("full_name") or "",
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


def _get_admin_order(order_id: str) -> dict:
    result = (
        get_service_client()
        .table("orders")
        .select(ADMIN_ORDER_COLUMNS)
        .eq("id", order_id)
        .limit(1)
        .execute()
    )
    if not result.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Commande introuvable")
    return result.data[0]


@router.get("/orders", response_model=list[AdminOrderOut])
def admin_list_orders(order_status: str | None = Query(default=None, alias="status")):
    if order_status is not None and order_status not in ORDER_STATUSES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Statut inconnu")
    query = get_service_client().table("orders").select(ADMIN_ORDER_COLUMNS)
    if order_status:
        query = query.eq("status", order_status)
    result = query.order("created_at", desc=True).limit(200).execute()
    return [_admin_order_out(row) for row in (result.data or [])]


@router.post("/orders/{order_id}/validate-cash", response_model=AdminOrderOut)
def admin_validate_cash(order_id: str, admin: CurrentUser = Depends(require_admin)):
    order = _get_admin_order(order_id)
    if order["payment_method"] != "cash":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cette commande n'est pas un paiement cash")
    if order["status"] != "pending_validation":
        raise HTTPException(status.HTTP_409_CONFLICT, f"Commande déjà traitée (statut : {order['status']})")

    fulfill_order(order, validated_by=admin.id, provider="cash")
    return _admin_order_out(_get_admin_order(order_id))


@router.post("/orders/{order_id}/cancel", response_model=AdminOrderOut)
def admin_cancel_order(order_id: str):
    order = _get_admin_order(order_id)
    if order["status"] not in ("pending", "pending_validation"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Seules les commandes en attente peuvent être annulées (statut : {order['status']})",
        )
    client = get_service_client()
    client.table("payments").update({"status": "failed"}).eq("order_id", order_id).eq(
        "status", "pending"
    ).execute()
    client.table("orders").update({"status": "canceled"}).eq("id", order_id).execute()
    return _admin_order_out(_get_admin_order(order_id))
