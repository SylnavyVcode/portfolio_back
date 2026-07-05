"""Schémas des commentaires et notes (blog + formations)."""

from datetime import datetime

from pydantic import BaseModel, Field


class CommentAuthor(BaseModel):
    id: str
    name: str = ""
    avatar_url: str | None = None
    role: str = "user"


class CommentOut(BaseModel):
    id: str
    content: str
    created_at: datetime
    author: CommentAuthor
    parent_id: str | None = None  # réponse à un commentaire racine


class CommentCreate(BaseModel):
    content: str = Field(min_length=1, max_length=2000)
    parent_id: str | None = None


class RatingSummary(BaseModel):
    average: float = 0.0
    count: int = 0
    mine: int | None = None  # note de l'utilisateur connecté, s'il a noté


class RatingSet(BaseModel):
    score: int = Field(ge=1, le=5)
