"""Catalogue des formations.

Liste et détail publics (formations publiées) ; le contenu des leçons est
réservé aux inscrits (enrollment actif) ou aux admins, sauf leçons marquées
"aperçu gratuit".
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.db.supabase_client import get_service_client
from app.dependencies import CurrentUser, get_current_user
from app.schemas.blog import LocalizedText
from app.schemas.courses import CourseDetail, CourseSummary, LessonDetail, LessonSummary

router = APIRouter()

COURSE_COLUMNS = (
    "id, slug, theme, level, title_fr, title_en, summary_fr, summary_en, "
    "preview_label_fr, preview_label_en, price_amount, currency, duration_hours, "
    "instructor, rating, students_count, is_published, published_at"
)
COURSE_DETAIL_COLUMNS = COURSE_COLUMNS + ", prerequisites, preview"

LESSON_SUMMARY_COLUMNS = "id, position, title_fr, title_en, duration_minutes, is_free_preview"
LESSON_DETAIL_COLUMNS = LESSON_SUMMARY_COLUMNS + ", content_fr, content_en, video_url"


def course_row_to_summary(row: dict) -> CourseSummary:
    return CourseSummary(
        id=row["id"],
        slug=row["slug"],
        theme=row["theme"],
        level=row["level"],
        title=LocalizedText(fr=row.get("title_fr") or "", en=row.get("title_en") or ""),
        summary=LocalizedText(fr=row.get("summary_fr") or "", en=row.get("summary_en") or ""),
        preview_label=LocalizedText(
            fr=row.get("preview_label_fr") or "", en=row.get("preview_label_en") or ""
        ),
        price=float(row.get("price_amount") or 0),
        currency=row.get("currency") or "EUR",
        duration_hours=float(row.get("duration_hours") or 0),
        instructor=row.get("instructor") or "",
        rating=float(row.get("rating") or 0),
        students_count=row.get("students_count") or 0,
        is_published=bool(row.get("is_published")),
        published_at=row.get("published_at"),
    )


def lesson_row_to_summary(row: dict) -> LessonSummary:
    return LessonSummary(
        id=row["id"],
        position=row.get("position") or 0,
        title=LocalizedText(fr=row.get("title_fr") or "", en=row.get("title_en") or ""),
        duration_minutes=row.get("duration_minutes") or 0,
        is_free_preview=bool(row.get("is_free_preview")),
    )


def lesson_row_to_detail(row: dict) -> LessonDetail:
    summary = lesson_row_to_summary(row)
    return LessonDetail(
        **summary.model_dump(),
        content=LocalizedText(fr=row.get("content_fr") or "", en=row.get("content_en") or ""),
        video_url=row.get("video_url"),
    )


def get_published_course(slug: str, columns: str = COURSE_COLUMNS) -> dict:
    result = (
        get_service_client()
        .table("courses")
        .select(columns)
        .eq("slug", slug)
        .eq("is_published", True)
        .limit(1)
        .execute()
    )
    if not result.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Formation introuvable")
    return result.data[0]


def has_active_enrollment(user_id: str, course_id: str) -> bool:
    result = (
        get_service_client()
        .table("enrollments")
        .select("id")
        .eq("user_id", user_id)
        .eq("course_id", course_id)
        .eq("status", "active")
        .limit(1)
        .execute()
    )
    return bool(result.data)


def ensure_course_access(user: CurrentUser, course_id: str) -> None:
    if user.is_admin:
        return
    if not has_active_enrollment(user.id, course_id):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Accès réservé aux inscrits — achetez la formation pour débloquer son contenu",
        )


# ─────────────────────────────────────────────────────────────────────────────


@router.get("", response_model=list[CourseDetail])
def list_courses(
    theme: str | None = Query(default=None, pattern=r"^(web|python|data|testing)$"),
    level: str | None = Query(default=None, pattern=r"^(beginner|intermediate|advanced)$"),
):
    # Colonnes détail (preview, prérequis) : la page catalogue du front
    # affiche un aperçu de contenu par formation.
    query = (
        get_service_client()
        .table("courses")
        .select(COURSE_DETAIL_COLUMNS)
        .eq("is_published", True)
    )
    if theme:
        query = query.eq("theme", theme)
    if level:
        query = query.eq("level", level)
    result = query.order("published_at", desc=True).execute()
    return [_row_to_detail_without_lessons(row) for row in (result.data or [])]


def _row_to_detail_without_lessons(row: dict) -> CourseDetail:
    summary = course_row_to_summary(row)
    prerequisites = [
        LocalizedText(fr=item.get("fr", ""), en=item.get("en", ""))
        for item in (row.get("prerequisites") or [])
        if isinstance(item, dict)
    ]
    return CourseDetail(**summary.model_dump(), prerequisites=prerequisites, preview=row.get("preview"))


@router.get("/{slug}", response_model=CourseDetail)
def get_course(slug: str):
    row = get_published_course(slug, COURSE_DETAIL_COLUMNS)

    lessons = (
        get_service_client()
        .table("course_lessons")
        .select(LESSON_SUMMARY_COLUMNS)
        .eq("course_id", row["id"])
        .order("position")
        .execute()
    )

    summary = course_row_to_summary(row)
    prerequisites = [
        LocalizedText(fr=item.get("fr", ""), en=item.get("en", ""))
        for item in (row.get("prerequisites") or [])
        if isinstance(item, dict)
    ]
    return CourseDetail(
        **summary.model_dump(),
        prerequisites=prerequisites,
        preview=row.get("preview"),
        lessons=[lesson_row_to_summary(lesson) for lesson in (lessons.data or [])],
    )


@router.get("/{slug}/lessons", response_model=list[LessonDetail])
def list_lessons(slug: str, user: CurrentUser = Depends(get_current_user)):
    course = get_published_course(slug)
    ensure_course_access(user, course["id"])

    result = (
        get_service_client()
        .table("course_lessons")
        .select(LESSON_DETAIL_COLUMNS)
        .eq("course_id", course["id"])
        .order("position")
        .execute()
    )
    return [lesson_row_to_detail(row) for row in (result.data or [])]


@router.get("/{slug}/lessons/{lesson_id}", response_model=LessonDetail)
def get_lesson(slug: str, lesson_id: str, user: CurrentUser = Depends(get_current_user)):
    course = get_published_course(slug)

    result = (
        get_service_client()
        .table("course_lessons")
        .select(LESSON_DETAIL_COLUMNS)
        .eq("id", lesson_id)
        .eq("course_id", course["id"])
        .limit(1)
        .execute()
    )
    if not result.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Leçon introuvable")

    row = result.data[0]
    if not row.get("is_free_preview"):
        ensure_course_access(user, course["id"])
    return lesson_row_to_detail(row)
