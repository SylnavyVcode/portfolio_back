"""Catalogue des formations.

Liste et détail publics (formations publiées) ; le contenu des leçons est
réservé aux inscrits (enrollment actif) ou aux admins, sauf leçons marquées
"aperçu gratuit". Le contenu est organisé en sections (Section > Leçons)
plutôt qu'en liste plate.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.db.supabase_client import get_service_client
from app.dependencies import CurrentUser, get_current_user
from app.schemas.blog import LocalizedText
from app.schemas.courses import (
    CourseDetail,
    CourseProgressOut,
    CourseSectionOut,
    CourseSectionWithLessons,
    CourseSummary,
    LessonDetail,
    LessonSectionDetail,
    LessonSummary,
)

router = APIRouter()

COURSE_COLUMNS = (
    "id, slug, theme, level, title_fr, title_en, summary_fr, summary_en, "
    "preview_label_fr, preview_label_en, price_amount, currency, duration_hours, "
    "instructor, rating, students_count, is_published, published_at"
)
COURSE_DETAIL_COLUMNS = COURSE_COLUMNS + ", prerequisites, learning_objectives, preview"

SECTION_COLUMNS = "id, position, title_fr, title_en"

LESSON_SUMMARY_COLUMNS = (
    "id, position, title_fr, title_en, duration_minutes, is_free_preview, content_type"
)
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


def _localized_list(row: dict, field: str) -> list[LocalizedText]:
    return [
        LocalizedText(fr=item.get("fr", ""), en=item.get("en", ""))
        for item in (row.get(field) or [])
        if isinstance(item, dict)
    ]


def lesson_row_to_summary(row: dict) -> LessonSummary:
    return LessonSummary(
        id=row["id"],
        position=row.get("position") or 0,
        title=LocalizedText(fr=row.get("title_fr") or "", en=row.get("title_en") or ""),
        duration_minutes=row.get("duration_minutes") or 0,
        is_free_preview=bool(row.get("is_free_preview")),
        content_type=row.get("content_type") or "video",
    )


def lesson_row_to_detail(row: dict) -> LessonDetail:
    summary = lesson_row_to_summary(row)
    return LessonDetail(
        **summary.model_dump(),
        content=LocalizedText(fr=row.get("content_fr") or "", en=row.get("content_en") or ""),
        video_url=row.get("video_url"),
    )


def section_row_to_out(row: dict) -> CourseSectionOut:
    return CourseSectionOut(
        id=row["id"],
        position=row.get("position") or 0,
        title=LocalizedText(fr=row.get("title_fr") or "", en=row.get("title_en") or ""),
    )


def sections_with_lessons(course_id: str, lesson_columns: str, mapper) -> list[dict]:
    """Sections d'une formation, chacune avec ses leçons (ordonnées).

    `mapper` convertit une ligne `course_lessons` en LessonSummary/LessonDetail ;
    le résultat est une liste de dicts prête à être passée à un modèle Pydantic
    de section (CourseSectionWithLessons, LessonSectionDetail, AdminSectionWithLessons...).
    """
    sections = (
        get_service_client()
        .table("course_sections")
        .select(SECTION_COLUMNS)
        .eq("course_id", course_id)
        .order("position")
        .execute()
    )
    lessons = (
        get_service_client()
        .table("course_lessons")
        .select(lesson_columns + ", section_id")
        .eq("course_id", course_id)
        .order("position")
        .execute()
    )
    grouped: dict[str, list] = {}
    for lesson_row in lessons.data or []:
        grouped.setdefault(lesson_row["section_id"], []).append(mapper(lesson_row))
    return [
        {**section_row_to_out(section_row).model_dump(), "lessons": grouped.get(section_row["id"], [])}
        for section_row in (sections.data or [])
    ]


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


def get_enrollment_id(user_id: str, course_id: str) -> str | None:
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
    return result.data[0]["id"] if result.data else None


def ensure_course_access(user: CurrentUser, course_id: str) -> None:
    if user.is_admin:
        return
    if not has_active_enrollment(user.id, course_id):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Accès réservé aux inscrits — achetez la formation pour débloquer son contenu",
        )


def progress_for_enrollment(course_id: str, enrollment_id: str | None) -> CourseProgressOut:
    total = (
        get_service_client()
        .table("course_lessons")
        .select("id")
        .eq("course_id", course_id)
        .execute()
    )
    total_lessons = len(total.data or [])

    completed_ids: list[str] = []
    if enrollment_id:
        progress = (
            get_service_client()
            .table("lesson_progress")
            .select("lesson_id")
            .eq("enrollment_id", enrollment_id)
            .execute()
        )
        completed_ids = [row["lesson_id"] for row in (progress.data or [])]

    percent = round(len(completed_ids) / total_lessons * 100, 1) if total_lessons else 0.0
    return CourseProgressOut(
        completed_lesson_ids=completed_ids,
        total_lessons=total_lessons,
        completed_count=len(completed_ids),
        percent=percent,
    )


def _compute_progress(course_id: str, user: CurrentUser) -> CourseProgressOut:
    return progress_for_enrollment(course_id, get_enrollment_id(user.id, course_id))


# ─────────────────────────────────────────────────────────────────────────────


@router.get("", response_model=list[CourseDetail])
def list_courses(
    theme: str | None = Query(default=None, pattern=r"^(web|python|data|testing)$"),
    level: str | None = Query(default=None, pattern=r"^(beginner|intermediate|advanced)$"),
):
    # Colonnes détail (preview, prérequis) : la page catalogue du front
    # affiche un aperçu de contenu par formation. Le curriculum complet
    # (sections/leçons) n'est chargé que sur la page de détail d'un cours.
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
    return [_row_to_detail_without_sections(row) for row in (result.data or [])]


def _row_to_detail_without_sections(row: dict) -> CourseDetail:
    summary = course_row_to_summary(row)
    return CourseDetail(
        **summary.model_dump(),
        prerequisites=_localized_list(row, "prerequisites"),
        learning_objectives=_localized_list(row, "learning_objectives"),
        preview=row.get("preview"),
    )


@router.get("/{slug}", response_model=CourseDetail)
def get_course(slug: str):
    row = get_published_course(slug, COURSE_DETAIL_COLUMNS)

    sections = [
        CourseSectionWithLessons(**section)
        for section in sections_with_lessons(row["id"], LESSON_SUMMARY_COLUMNS, lesson_row_to_summary)
    ]

    summary = course_row_to_summary(row)
    return CourseDetail(
        **summary.model_dump(),
        prerequisites=_localized_list(row, "prerequisites"),
        learning_objectives=_localized_list(row, "learning_objectives"),
        preview=row.get("preview"),
        sections=sections,
    )


@router.get("/{slug}/lessons", response_model=list[LessonSectionDetail])
def list_lessons(slug: str, user: CurrentUser = Depends(get_current_user)):
    course = get_published_course(slug)
    ensure_course_access(user, course["id"])

    return [
        LessonSectionDetail(**section)
        for section in sections_with_lessons(course["id"], LESSON_DETAIL_COLUMNS, lesson_row_to_detail)
    ]


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


# ── Progression apprenant ────────────────────────────────────────────────────


@router.get("/{slug}/progress", response_model=CourseProgressOut)
def get_course_progress(slug: str, user: CurrentUser = Depends(get_current_user)):
    course = get_published_course(slug)
    ensure_course_access(user, course["id"])
    return _compute_progress(course["id"], user)


@router.put("/{slug}/lessons/{lesson_id}/complete", response_model=CourseProgressOut)
def mark_lesson_complete(slug: str, lesson_id: str, user: CurrentUser = Depends(get_current_user)):
    course = get_published_course(slug)
    ensure_course_access(user, course["id"])

    enrollment_id = get_enrollment_id(user.id, course["id"])
    if not enrollment_id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Inscription requise pour suivre votre progression"
        )

    lesson = (
        get_service_client()
        .table("course_lessons")
        .select("id")
        .eq("id", lesson_id)
        .eq("course_id", course["id"])
        .limit(1)
        .execute()
    )
    if not lesson.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Leçon introuvable")

    get_service_client().table("lesson_progress").upsert(
        {"enrollment_id": enrollment_id, "lesson_id": lesson_id},
        on_conflict="enrollment_id,lesson_id",
    ).execute()
    return _compute_progress(course["id"], user)


@router.delete("/{slug}/lessons/{lesson_id}/complete", response_model=CourseProgressOut)
def unmark_lesson_complete(slug: str, lesson_id: str, user: CurrentUser = Depends(get_current_user)):
    course = get_published_course(slug)
    ensure_course_access(user, course["id"])

    enrollment_id = get_enrollment_id(user.id, course["id"])
    if enrollment_id:
        get_service_client().table("lesson_progress").delete().eq(
            "enrollment_id", enrollment_id
        ).eq("lesson_id", lesson_id).execute()
    return _compute_progress(course["id"], user)
