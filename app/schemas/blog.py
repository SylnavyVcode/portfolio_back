from datetime import datetime

from pydantic import BaseModel, Field


class LocalizedText(BaseModel):
    fr: str = ""
    en: str = ""


class LocalizedParagraphs(BaseModel):
    fr: list[str] = Field(default_factory=list)
    en: list[str] = Field(default_factory=list)


# ── Lecture publique ─────────────────────────────────────────────────────────

class BlogPostSummary(BaseModel):
    id: str
    slug: str
    category: LocalizedText
    title: LocalizedText
    excerpt: LocalizedText
    read_time: int
    date: datetime | None = None  # published_at
    image: str | None = None
    featured: bool = False


class BlogPostDetail(BlogPostSummary):
    content: LocalizedParagraphs


class BlogListResponse(BaseModel):
    items: list[BlogPostSummary]
    total: int
    page: int
    page_size: int


# ── Administration ───────────────────────────────────────────────────────────

class BlogPostAdmin(BlogPostDetail):
    status: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


class BlogPostCreate(BaseModel):
    title: LocalizedText
    slug: str | None = Field(default=None, max_length=120, pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")
    category: LocalizedText = Field(default_factory=LocalizedText)
    excerpt: LocalizedText = Field(default_factory=LocalizedText)
    content: LocalizedParagraphs = Field(default_factory=LocalizedParagraphs)
    image_url: str | None = Field(default=None, max_length=500)
    read_time: int = Field(default=5, ge=1, le=120)
    featured: bool = False
    status: str = Field(default="draft", pattern=r"^(draft|published)$")


class BlogPostUpdate(BaseModel):
    # Tous optionnels : mise à jour partielle
    title: LocalizedText | None = None
    slug: str | None = Field(default=None, max_length=120, pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")
    category: LocalizedText | None = None
    excerpt: LocalizedText | None = None
    content: LocalizedParagraphs | None = None
    image_url: str | None = Field(default=None, max_length=500)
    read_time: int | None = Field(default=None, ge=1, le=120)
    featured: bool | None = None
    status: str | None = Field(default=None, pattern=r"^(draft|published)$")


# ── Médias (images / vidéos du contenu) ──────────────────────────────────────

class MediaSignRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=200)
    content_type: str = Field(min_length=1, max_length=100)


class MediaSignResponse(BaseModel):
    upload_url: str
    path: str
    public_url: str
