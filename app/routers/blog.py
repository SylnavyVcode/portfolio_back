"""Blog public : lecture seule, articles publiés uniquement."""

import re

from fastapi import APIRouter, HTTPException, Query, status

from app.db.supabase_client import get_service_client
from app.schemas.blog import (
    BlogListResponse,
    BlogPostDetail,
    BlogPostSummary,
    LocalizedParagraphs,
    LocalizedText,
)

router = APIRouter()

SUMMARY_COLUMNS = (
    "id, slug, category_fr, category_en, title_fr, title_en, "
    "excerpt_fr, excerpt_en, image_url, read_time, featured, published_at"
)
DETAIL_COLUMNS = SUMMARY_COLUMNS + ", content_fr, content_en"


def row_to_summary(row: dict) -> BlogPostSummary:
    return BlogPostSummary(
        id=row["id"],
        slug=row["slug"],
        category=LocalizedText(fr=row.get("category_fr") or "", en=row.get("category_en") or ""),
        title=LocalizedText(fr=row.get("title_fr") or "", en=row.get("title_en") or ""),
        excerpt=LocalizedText(fr=row.get("excerpt_fr") or "", en=row.get("excerpt_en") or ""),
        read_time=row.get("read_time") or 5,
        date=row.get("published_at"),
        image=row.get("image_url"),
        featured=bool(row.get("featured")),
    )


def row_to_detail(row: dict) -> BlogPostDetail:
    summary = row_to_summary(row)
    return BlogPostDetail(
        **summary.model_dump(),
        content=LocalizedParagraphs(
            fr=row.get("content_fr") or [],
            en=row.get("content_en") or [],
        ),
    )


@router.get("", response_model=BlogListResponse)
def list_posts(
    category: str | None = Query(default=None, max_length=60),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=12, ge=1, le=50),
):
    query = (
        get_service_client()
        .table("blog_posts")
        .select(SUMMARY_COLUMNS, count="exact")
        .eq("status", "published")
    )
    if category:
        # Filtre sur le libellé fr OU en ; on neutralise les caractères
        # spéciaux de la syntaxe de filtre PostgREST.
        safe = re.sub(r"[,()\"\\]", "", category)
        query = query.or_(f'category_fr.eq."{safe}",category_en.eq."{safe}"')

    offset = (page - 1) * page_size
    result = (
        query.order("published_at", desc=True)
        .range(offset, offset + page_size - 1)
        .execute()
    )

    return BlogListResponse(
        items=[row_to_summary(row) for row in (result.data or [])],
        total=result.count or 0,
        page=page,
        page_size=page_size,
    )


@router.get("/{slug}", response_model=BlogPostDetail)
def get_post(slug: str):
    result = (
        get_service_client()
        .table("blog_posts")
        .select(DETAIL_COLUMNS)
        .eq("slug", slug)
        .eq("status", "published")
        .limit(1)
        .execute()
    )
    if not result.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Article introuvable")
    return row_to_detail(result.data[0])
