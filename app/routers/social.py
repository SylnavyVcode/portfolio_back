"""Commentaires et notes sur les articles de blog et les formations.

Lecture publique ; commenter/noter demande d'être connecté. Une seule note
par utilisateur et par cible (upsert). La suppression d'un commentaire est
réservée à son auteur ou à un admin.
"""

from fastapi import APIRouter, Depends, HTTPException, status

from app.db.supabase_client import get_service_client
from app.dependencies import CurrentUser, get_current_user, get_optional_user
from app.schemas.auth import MessageResponse
from app.schemas.social import (
    CommentAuthor,
    CommentCreate,
    CommentOut,
    RatingSet,
    RatingSummary,
)

router = APIRouter()

COMMENT_COLUMNS = "id, content, created_at, user_id, profiles(full_name, avatar_url, role)"


def _resolve_post_id(slug: str) -> str:
    result = (
        get_service_client()
        .table("blog_posts")
        .select("id")
        .eq("slug", slug)
        .eq("status", "published")
        .limit(1)
        .execute()
    )
    if not result.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Article introuvable")
    return result.data[0]["id"]


def _resolve_course_id(slug: str) -> str:
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


def _comment_out(row: dict) -> CommentOut:
    profile = row.get("profiles") or {}
    return CommentOut(
        id=row["id"],
        content=row["content"],
        created_at=row["created_at"],
        author=CommentAuthor(
            id=row["user_id"],
            name=profile.get("full_name") or "",
            avatar_url=profile.get("avatar_url"),
            role=profile.get("role") or "user",
        ),
    )


def _list_comments(column: str, target_id: str) -> list[CommentOut]:
    result = (
        get_service_client()
        .table("comments")
        .select(COMMENT_COLUMNS)
        .eq(column, target_id)
        .order("created_at", desc=True)
        .limit(200)
        .execute()
    )
    return [_comment_out(row) for row in (result.data or [])]


def _add_comment(column: str, target_id: str, user: CurrentUser, payload: CommentCreate) -> CommentOut:
    content = payload.content.strip()
    if not content:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Commentaire vide")

    result = (
        get_service_client()
        .table("comments")
        .insert({"user_id": user.id, column: target_id, "content": content})
        .execute()
    )
    row = result.data[0]
    return CommentOut(
        id=row["id"],
        content=row["content"],
        created_at=row["created_at"],
        author=CommentAuthor(
            id=user.id, name=user.full_name, avatar_url=user.avatar_url, role=user.role
        ),
    )


def _rating_summary(column: str, target_id: str, user: CurrentUser | None) -> RatingSummary:
    result = (
        get_service_client()
        .table("ratings")
        .select("score, user_id")
        .eq(column, target_id)
        .execute()
    )
    rows = result.data or []
    scores = [row["score"] for row in rows]
    mine = next((row["score"] for row in rows if user and row["user_id"] == user.id), None)
    return RatingSummary(
        average=round(sum(scores) / len(scores), 1) if scores else 0.0,
        count=len(scores),
        mine=mine,
    )


def _set_rating(column: str, target_id: str, user: CurrentUser, payload: RatingSet) -> RatingSummary:
    (
        get_service_client()
        .table("ratings")
        .upsert(
            {"user_id": user.id, column: target_id, "score": payload.score},
            on_conflict=f"user_id,{column}",
        )
        .execute()
    )
    return _rating_summary(column, target_id, user)


# ── Blog ─────────────────────────────────────────────────────────────────────


@router.get("/blog/{slug}/comments", response_model=list[CommentOut])
def list_post_comments(slug: str):
    return _list_comments("post_id", _resolve_post_id(slug))


@router.post(
    "/blog/{slug}/comments", response_model=CommentOut, status_code=status.HTTP_201_CREATED
)
def add_post_comment(slug: str, payload: CommentCreate, user: CurrentUser = Depends(get_current_user)):
    return _add_comment("post_id", _resolve_post_id(slug), user, payload)


@router.get("/blog/{slug}/rating", response_model=RatingSummary)
def get_post_rating(slug: str, user: CurrentUser | None = Depends(get_optional_user)):
    return _rating_summary("post_id", _resolve_post_id(slug), user)


@router.put("/blog/{slug}/rating", response_model=RatingSummary)
def set_post_rating(slug: str, payload: RatingSet, user: CurrentUser = Depends(get_current_user)):
    return _set_rating("post_id", _resolve_post_id(slug), user, payload)


# ── Formations ───────────────────────────────────────────────────────────────


@router.get("/courses/{slug}/comments", response_model=list[CommentOut])
def list_course_comments(slug: str):
    return _list_comments("course_id", _resolve_course_id(slug))


@router.post(
    "/courses/{slug}/comments", response_model=CommentOut, status_code=status.HTTP_201_CREATED
)
def add_course_comment(
    slug: str, payload: CommentCreate, user: CurrentUser = Depends(get_current_user)
):
    return _add_comment("course_id", _resolve_course_id(slug), user, payload)


@router.get("/courses/{slug}/rating", response_model=RatingSummary)
def get_course_rating(slug: str, user: CurrentUser | None = Depends(get_optional_user)):
    return _rating_summary("course_id", _resolve_course_id(slug), user)


@router.put("/courses/{slug}/rating", response_model=RatingSummary)
def set_course_rating(slug: str, payload: RatingSet, user: CurrentUser = Depends(get_current_user)):
    return _set_rating("course_id", _resolve_course_id(slug), user, payload)


# ── Suppression (auteur ou admin) ────────────────────────────────────────────


@router.delete("/comments/{comment_id}", response_model=MessageResponse)
def delete_comment(comment_id: str, user: CurrentUser = Depends(get_current_user)):
    client = get_service_client()
    existing = (
        client.table("comments").select("id, user_id").eq("id", comment_id).limit(1).execute()
    )
    if not existing.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Commentaire introuvable")
    if existing.data[0]["user_id"] != user.id and not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Vous ne pouvez pas supprimer ce commentaire")

    client.table("comments").delete().eq("id", comment_id).execute()
    return MessageResponse(message="Commentaire supprimé")
