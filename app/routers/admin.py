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

from app.db.supabase_client import get_service_client, get_user_email
from app.dependencies import CurrentUser, require_admin
from app.services import email_service
from app.routers.blog import DETAIL_COLUMNS, row_to_detail
from app.routers.courses import (
    COURSE_DETAIL_COLUMNS,
    LESSON_DETAIL_COLUMNS,
    _localized_list,
    course_row_to_summary,
    lesson_row_to_detail,
    section_row_to_out,
    sections_with_lessons,
)
from app.schemas.auth import MessageResponse
from app.routers.payments import fulfill_order
from app.schemas.blog import (
    BlogPostAdmin,
    BlogPostCreate,
    BlogPostUpdate,
    LocalizedText,
    MediaSignRequest,
    MediaSignResponse,
)
from app.schemas.courses import (
    AdminSectionWithLessons,
    CourseCreate,
    CourseDetail,
    CourseSectionCreate,
    CourseSectionOut,
    CourseSectionUpdate,
    CourseUpdate,
    LessonCreate,
    LessonDetail,
    LessonUpdate,
    MoveDirection,
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


# ── Médias (upload direct navigateur → Supabase Storage via URL signée) ─────

MEDIA_BUCKET = "blog-media"

# Extensions autorisées : le bucket est public, on n'y accepte que des
# images et des vidéos lisibles par le navigateur.
MEDIA_EXTENSIONS = {
    "image": {"png", "jpg", "jpeg", "webp", "gif", "svg", "avif"},
    "video": {"mp4", "webm", "ogg", "mov", "m4v"},
}


@router.post("/media/sign", response_model=MediaSignResponse)
def admin_sign_media_upload(payload: MediaSignRequest):
    kind = payload.content_type.split("/", 1)[0]
    ext = payload.filename.rsplit(".", 1)[-1].lower() if "." in payload.filename else ""
    if kind not in MEDIA_EXTENSIONS or ext not in MEDIA_EXTENSIONS[kind]:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Format non supporté — images (png, jpg, webp, gif, svg, avif) "
            "ou vidéos (mp4, webm, ogg, mov, m4v) uniquement",
        )

    base = slugify(payload.filename.rsplit(".", 1)[0])[:60]
    now = datetime.now(timezone.utc)
    path = f"{now:%Y/%m}/{base}-{secrets.token_hex(4)}.{ext}"

    storage = get_service_client().storage.from_(MEDIA_BUCKET)
    signed = storage.create_signed_upload_url(path)
    return MediaSignResponse(
        upload_url=signed["signed_url"],
        path=path,
        public_url=storage.get_public_url(path),
    )


# ── Formations ───────────────────────────────────────────────────────────────


def _course_row_to_detail(row: dict) -> CourseDetail:
    summary = course_row_to_summary(row)
    return CourseDetail(
        **summary.model_dump(),
        prerequisites=_localized_list(row, "prerequisites"),
        learning_objectives=_localized_list(row, "learning_objectives"),
        preview=row.get("preview"),
    )


def _course_payload_to_columns(payload: CourseCreate | CourseUpdate) -> dict:
    values: dict = {}
    data = payload.model_dump(exclude_unset=True)
    for field, prefix in (("title", "title"), ("summary", "summary"), ("preview_label", "preview_label")):
        if data.get(field) is not None:
            values[f"{prefix}_fr"] = data[field]["fr"]
            values[f"{prefix}_en"] = data[field]["en"]
    if data.get("prerequisites") is not None:
        values["prerequisites"] = data["prerequisites"]
    if data.get("learning_objectives") is not None:
        values["learning_objectives"] = data["learning_objectives"]
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


# ── Sections ─────────────────────────────────────────────────────────────────
# Le programme d'une formation est organisé en sections, chacune contenant
# ses leçons (au lieu d'une liste plate) — cf. benchmark Udemy/OpenClassrooms/
# Coursera/FUN-MOOC.


def _move_item(table: str, scope_column: str, item_id: str, direction: str) -> bool:
    """Échange la position de `item_id` avec son voisin (au sein de la même
    section pour une leçon, de la même formation pour une section).
    Renvoie False si l'élément est déjà à l'extrémité (rien à faire)."""
    client = get_service_client()
    current = client.table(table).select(f"id, position, {scope_column}").eq("id", item_id).limit(1).execute()
    if not current.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Élément introuvable")
    cur = current.data[0]

    neighbor_query = client.table(table).select("id, position").eq(scope_column, cur[scope_column])
    if direction == "up":
        neighbor_query = neighbor_query.lt("position", cur["position"]).order("position", desc=True)
    else:
        neighbor_query = neighbor_query.gt("position", cur["position"]).order("position", desc=False)
    neighbor = neighbor_query.limit(1).execute()
    if not neighbor.data:
        return False
    nb = neighbor.data[0]

    # Passage par une position temporaire négative pour ne pas violer la
    # contrainte unique (scope, position) le temps de l'échange.
    client.table(table).update({"position": -1}).eq("id", item_id).execute()
    client.table(table).update({"position": cur["position"]}).eq("id", nb["id"]).execute()
    client.table(table).update({"position": nb["position"]}).eq("id", item_id).execute()
    return True


@router.get("/courses/{course_id}/sections", response_model=list[AdminSectionWithLessons])
def admin_list_sections(course_id: str):
    return [
        AdminSectionWithLessons(**section)
        for section in sections_with_lessons(course_id, LESSON_DETAIL_COLUMNS, lesson_row_to_detail)
    ]


@router.post(
    "/courses/{course_id}/sections", response_model=CourseSectionOut, status_code=status.HTTP_201_CREATED
)
def admin_create_section(course_id: str, payload: CourseSectionCreate):
    client = get_service_client()
    course = client.table("courses").select("id").eq("id", course_id).limit(1).execute()
    if not course.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Formation introuvable")

    last = (
        client.table("course_sections")
        .select("position")
        .eq("course_id", course_id)
        .order("position", desc=True)
        .limit(1)
        .execute()
    )
    values = {
        "course_id": course_id,
        "title_fr": payload.title.fr,
        "title_en": payload.title.en,
        "position": (last.data[0]["position"] + 1) if last.data else 1,
    }
    result = client.table("course_sections").insert(values).execute()
    return section_row_to_out(result.data[0])


@router.put("/sections/{section_id}", response_model=CourseSectionOut)
def admin_update_section(section_id: str, payload: CourseSectionUpdate):
    values: dict = {}
    if payload.title is not None:
        values["title_fr"] = payload.title.fr
        values["title_en"] = payload.title.en
    if not values:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Aucun champ à mettre à jour")
    result = get_service_client().table("course_sections").update(values).eq("id", section_id).execute()
    if not result.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Section introuvable")
    return section_row_to_out(result.data[0])


@router.delete("/sections/{section_id}", response_model=MessageResponse)
def admin_delete_section(section_id: str):
    result = get_service_client().table("course_sections").delete().eq("id", section_id).execute()
    if not result.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Section introuvable")
    return MessageResponse(message="Section supprimée (avec ses leçons)")


@router.post("/sections/{section_id}/move", response_model=MessageResponse)
def admin_move_section(section_id: str, payload: MoveDirection):
    moved = _move_item("course_sections", "course_id", section_id, payload.direction)
    return MessageResponse(message="Ordre mis à jour" if moved else "Déjà à l'extrémité")


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
    for field in ("content_type", "video_url", "duration_minutes", "is_free_preview", "position", "section_id"):
        if field in data and data[field] is not None:
            values[field] = data[field]
    return values


@router.post("/sections/{section_id}/lessons", response_model=LessonDetail, status_code=status.HTTP_201_CREATED)
def admin_create_lesson(section_id: str, payload: LessonCreate):
    client = get_service_client()
    section = client.table("course_sections").select("id, course_id").eq("id", section_id).limit(1).execute()
    if not section.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Section introuvable")

    values = _lesson_payload_to_columns(payload)
    values["section_id"] = section_id
    values["course_id"] = section.data[0]["course_id"]
    if values.get("position") is None:
        # Position par défaut : à la suite de la dernière leçon de la section.
        last = (
            client.table("course_lessons")
            .select("position")
            .eq("section_id", section_id)
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
    client = get_service_client()
    values = _lesson_payload_to_columns(payload)
    if not values:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Aucun champ à mettre à jour")

    # Déplacement vers une autre section : on ignore une position fournie et
    # on ajoute la leçon à la fin de la nouvelle section, pour éviter tout
    # conflit avec les positions déjà occupées.
    if "section_id" in values:
        last = (
            client.table("course_lessons")
            .select("position")
            .eq("section_id", values["section_id"])
            .order("position", desc=True)
            .limit(1)
            .execute()
        )
        values["position"] = (last.data[0]["position"] + 1) if last.data else 1

    try:
        result = client.table("course_lessons").update(values).eq("id", lesson_id).execute()
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


@router.post("/lessons/{lesson_id}/move", response_model=MessageResponse)
def admin_move_lesson(lesson_id: str, payload: MoveDirection):
    moved = _move_item("course_lessons", "section_id", lesson_id, payload.direction)
    return MessageResponse(message="Ordre mis à jour" if moved else "Déjà à l'extrémité")


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
    email_service.send_cash_validated(
        get_user_email(order["user_id"]), order_id, float(order.get("total_amount") or 0)
    )
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
