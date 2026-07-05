from dataclasses import dataclass

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.security import verify_supabase_jwt
from app.db.supabase_client import get_service_client

_bearer = HTTPBearer(auto_error=False)


@dataclass
class CurrentUser:
    id: str
    email: str
    role: str
    full_name: str
    avatar_url: str | None
    access_token: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> CurrentUser:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentification requise",
            headers={"WWW-Authenticate": "Bearer"},
        )

    claims = verify_supabase_jwt(credentials.credentials)
    user_id: str = claims["sub"]

    result = (
        get_service_client()
        .table("profiles")
        .select("full_name, role, avatar_url")
        .eq("id", user_id)
        .single()
        .execute()
    )
    profile = result.data or {}

    return CurrentUser(
        id=user_id,
        email=claims.get("email", ""),
        role=profile.get("role", "user"),
        full_name=profile.get("full_name", ""),
        avatar_url=profile.get("avatar_url"),
        access_token=credentials.credentials,
    )


def require_admin(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès réservé aux administrateurs",
        )
    return user
