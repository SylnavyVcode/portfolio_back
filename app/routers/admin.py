"""Back-office — routes réservées au rôle admin.

Module blog pour l'instant ; les formations et la validation des paiements
cash rejoindront ce router dans les modules suivants.
"""

import re
import secrets
import unicodedata
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from postgrest.exceptions import APIError

from app.db.supabase_client import get_service_client
from app.dependencies import CurrentUser, require_admin
from app.routers.blog import DETAIL_COLUMNS, row_to_detail
from app.schemas.auth import MessageResponse
from app.schemas.blog import BlogPostAdmin, BlogPostCreate, BlogPostUpdate

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
