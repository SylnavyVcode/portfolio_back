from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.blog import LocalizedText

THEME_PATTERN = r"^(web|python|data|testing)$"
LEVEL_PATTERN = r"^(beginner|intermediate|advanced)$"
CONTENT_TYPE_PATTERN = r"^(video|text|quiz)$"


# ── Catalogue public ─────────────────────────────────────────────────────────

class CourseSummary(BaseModel):
    id: str
    slug: str
    theme: str
    level: str
    title: LocalizedText
    summary: LocalizedText
    preview_label: LocalizedText
    price: float
    currency: str = "EUR"
    duration_hours: float
    instructor: str
    rating: float
    students_count: int
    is_published: bool = True
    published_at: datetime | None = None


class LessonSummary(BaseModel):
    id: str
    position: int
    title: LocalizedText
    duration_minutes: int
    is_free_preview: bool
    content_type: str = "video"


class CourseSectionOut(BaseModel):
    id: str
    position: int
    title: LocalizedText


class CourseSectionWithLessons(CourseSectionOut):
    lessons: list[LessonSummary] = Field(default_factory=list)


class CourseDetail(CourseSummary):
    prerequisites: list[LocalizedText] = Field(default_factory=list)
    learning_objectives: list[LocalizedText] = Field(default_factory=list)
    preview: dict | None = None
    sections: list[CourseSectionWithLessons] = Field(default_factory=list)


class LessonDetail(LessonSummary):
    content: LocalizedText
    video_url: str | None = None


class LessonSectionDetail(CourseSectionOut):
    lessons: list[LessonDetail] = Field(default_factory=list)


# ── Progression apprenant ────────────────────────────────────────────────────

class CourseProgressOut(BaseModel):
    completed_lesson_ids: list[str] = Field(default_factory=list)
    total_lessons: int
    completed_count: int
    percent: float


# ── Panier ───────────────────────────────────────────────────────────────────

class CartAddRequest(BaseModel):
    course_slug: str = Field(min_length=1, max_length=120)


class CartItemOut(BaseModel):
    course: CourseSummary
    added_at: datetime


class CartOut(BaseModel):
    items: list[CartItemOut]
    subtotal: float
    currency: str = "EUR"


# ── Mes données (enrollments / commandes) ────────────────────────────────────

class EnrollmentOut(BaseModel):
    course: CourseSummary
    status: str
    granted_at: datetime
    progress_percent: float = 0.0


class OrderItemOut(BaseModel):
    course_id: str | None = None
    title_snapshot: str
    price_snapshot: float


class OrderOut(BaseModel):
    id: str
    status: str
    payment_method: str
    total_amount: float
    currency: str
    created_at: datetime
    items: list[OrderItemOut] = Field(default_factory=list)


# ── Administration ───────────────────────────────────────────────────────────

class CourseCreate(BaseModel):
    title: LocalizedText
    slug: str | None = Field(default=None, max_length=120, pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")
    theme: str = Field(pattern=THEME_PATTERN)
    level: str = Field(pattern=LEVEL_PATTERN)
    summary: LocalizedText = Field(default_factory=LocalizedText)
    prerequisites: list[LocalizedText] = Field(default_factory=list)
    learning_objectives: list[LocalizedText] = Field(default_factory=list)
    preview: dict | None = None
    preview_label: LocalizedText = Field(default_factory=LocalizedText)
    price: float = Field(ge=0)
    duration_hours: float = Field(default=0, ge=0)
    instructor: str = Field(default="Valmy Mabika", max_length=120)
    rating: float = Field(default=0, ge=0, le=5)
    students_count: int = Field(default=0, ge=0)
    is_published: bool = False


class CourseUpdate(BaseModel):
    title: LocalizedText | None = None
    slug: str | None = Field(default=None, max_length=120, pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")
    theme: str | None = Field(default=None, pattern=THEME_PATTERN)
    level: str | None = Field(default=None, pattern=LEVEL_PATTERN)
    summary: LocalizedText | None = None
    prerequisites: list[LocalizedText] | None = None
    learning_objectives: list[LocalizedText] | None = None
    preview: dict | None = None
    preview_label: LocalizedText | None = None
    price: float | None = Field(default=None, ge=0)
    duration_hours: float | None = Field(default=None, ge=0)
    instructor: str | None = Field(default=None, max_length=120)
    rating: float | None = Field(default=None, ge=0, le=5)
    students_count: int | None = Field(default=None, ge=0)
    is_published: bool | None = None


class LessonCreate(BaseModel):
    title: LocalizedText
    content: LocalizedText = Field(default_factory=LocalizedText)
    content_type: str = Field(default="video", pattern=CONTENT_TYPE_PATTERN)
    video_url: str | None = Field(default=None, max_length=500)
    duration_minutes: int = Field(default=0, ge=0, le=600)
    is_free_preview: bool = False
    position: int | None = Field(default=None, ge=0)


class LessonUpdate(BaseModel):
    title: LocalizedText | None = None
    content: LocalizedText | None = None
    content_type: str | None = Field(default=None, pattern=CONTENT_TYPE_PATTERN)
    video_url: str | None = Field(default=None, max_length=500)
    duration_minutes: int | None = Field(default=None, ge=0, le=600)
    is_free_preview: bool | None = None
    position: int | None = Field(default=None, ge=0)
    section_id: str | None = None


class CourseSectionCreate(BaseModel):
    title: LocalizedText


class CourseSectionUpdate(BaseModel):
    title: LocalizedText | None = None


class AdminSectionWithLessons(CourseSectionOut):
    lessons: list[LessonDetail] = Field(default_factory=list)


class MoveDirection(BaseModel):
    direction: str = Field(pattern=r"^(up|down)$")
