from datetime import date

from pydantic import BaseModel, EmailStr, Field


# ── Entrées ──────────────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(min_length=1, max_length=120)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    # Token "recovery" extrait par le front de l'URL de l'email Supabase
    access_token: str = Field(min_length=1)
    new_password: str = Field(min_length=8, max_length=128)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class ProfileUpdateRequest(BaseModel):
    full_name: str = Field(min_length=1, max_length=120)
    date_of_birth: date | None = None
    phone: str | None = Field(default=None, max_length=30)
    address: str | None = Field(default=None, max_length=200)
    city: str | None = Field(default=None, max_length=100)
    country: str | None = Field(default=None, max_length=100)


# ── Sorties ──────────────────────────────────────────────────────────────────

class UserPublic(BaseModel):
    id: str
    email: str
    name: str
    role: str = "user"


class ProfilePublic(BaseModel):
    full_name: str = ""
    date_of_birth: date | None = None
    phone: str | None = None
    address: str | None = None
    city: str | None = None
    country: str | None = None


class SessionResponse(BaseModel):
    access_token: str
    refresh_token: str
    expires_in: int
    user: UserPublic


class RegisterResponse(BaseModel):
    requires_email_confirmation: bool
    session: SessionResponse | None = None


class MeResponse(BaseModel):
    user: UserPublic
    profile: ProfilePublic


class MessageResponse(BaseModel):
    message: str
