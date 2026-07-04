import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status

try:  # le paquet gotrue a été renommé supabase_auth selon la version de supabase-py
    from supabase_auth.errors import AuthApiError
except ImportError:  # pragma: no cover
    from gotrue.errors import AuthApiError

from app.core.config import get_settings
from app.core.rate_limit import limiter
from app.core.security import verify_supabase_jwt
from app.db.supabase_client import get_service_client, new_anon_client
from app.dependencies import CurrentUser, get_current_user
from app.schemas.auth import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    MeResponse,
    MessageResponse,
    ProfilePublic,
    ProfileUpdateRequest,
    RefreshRequest,
    RegisterRequest,
    RegisterResponse,
    ResetPasswordRequest,
    SessionResponse,
    UserPublic,
)

logger = logging.getLogger(__name__)

router = APIRouter()

PROFILE_FIELDS = "full_name, role, date_of_birth, phone, address, city, country"


def _fetch_profile(user_id: str) -> dict:
    result = (
        get_service_client()
        .table("profiles")
        .select(PROFILE_FIELDS)
        .eq("id", user_id)
        .single()
        .execute()
    )
    return result.data or {}


def _session_response(session, user) -> SessionResponse:
    profile = _fetch_profile(user.id)
    return SessionResponse(
        access_token=session.access_token,
        refresh_token=session.refresh_token,
        expires_in=session.expires_in or 3600,
        user=UserPublic(
            id=user.id,
            email=user.email or "",
            name=profile.get("full_name") or (user.email or "").split("@")[0],
            role=profile.get("role", "user"),
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────


@router.post("/register", response_model=RegisterResponse)
@limiter.limit("5/minute")
def register(request: Request, payload: RegisterRequest):
    settings = get_settings()
    try:
        result = new_anon_client().auth.sign_up(
            {
                "email": payload.email,
                "password": payload.password,
                "options": {
                    "data": {"full_name": payload.full_name},
                    "email_redirect_to": f"{settings.frontend_url}/auth?mode=login",
                },
            }
        )
    except AuthApiError as exc:
        if "already registered" in str(exc.message).lower():
            raise HTTPException(status.HTTP_409_CONFLICT, "Un compte existe déjà avec cet email")
        logger.warning("Echec d'inscription: %s", exc.message)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Inscription impossible, vérifiez vos informations")

    # Si la confirmation d'email est activée côté Supabase, session est None :
    # l'utilisateur doit cliquer le lien reçu avant de pouvoir se connecter.
    if result.session is None:
        return RegisterResponse(requires_email_confirmation=True)

    return RegisterResponse(
        requires_email_confirmation=False,
        session=_session_response(result.session, result.user),
    )


@router.post("/login", response_model=SessionResponse)
@limiter.limit("10/minute")
def login(request: Request, payload: LoginRequest):
    try:
        result = new_anon_client().auth.sign_in_with_password(
            {"email": payload.email, "password": payload.password}
        )
    except AuthApiError as exc:
        message = str(exc.message).lower()
        if "email not confirmed" in message:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Email non confirmé — vérifiez votre boîte mail")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Email ou mot de passe incorrect")

    return _session_response(result.session, result.user)


@router.post("/refresh", response_model=SessionResponse)
def refresh(payload: RefreshRequest):
    try:
        result = new_anon_client().auth.refresh_session(payload.refresh_token)
    except AuthApiError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expirée, reconnectez-vous")

    if result.session is None or result.user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expirée, reconnectez-vous")

    return _session_response(result.session, result.user)


@router.post("/logout", response_model=MessageResponse)
def logout(user: CurrentUser = Depends(get_current_user)):
    try:
        get_service_client().auth.admin.sign_out(user.access_token)
    except AuthApiError:
        pass  # le token est peut-être déjà révoqué — la déconnexion reste effective
    return MessageResponse(message="Déconnecté")


@router.post("/forgot-password", response_model=MessageResponse)
@limiter.limit("3/minute")
def forgot_password(request: Request, payload: ForgotPasswordRequest):
    settings = get_settings()
    try:
        new_anon_client().auth.reset_password_for_email(
            payload.email,
            {"redirect_to": f"{settings.frontend_url}/reset-password"},
        )
    except AuthApiError as exc:
        # Ne pas révéler si l'email existe ou non
        logger.warning("forgot-password: %s", exc.message)
    return MessageResponse(message="Si un compte existe, un email de réinitialisation a été envoyé")


@router.post("/reset-password", response_model=MessageResponse)
@limiter.limit("5/minute")
def reset_password(request: Request, payload: ResetPasswordRequest):
    # Le token de l'email Supabase (type recovery) est un JWT de session valide.
    claims = verify_supabase_jwt(payload.access_token)
    try:
        get_service_client().auth.admin.update_user_by_id(
            claims["sub"], {"password": payload.new_password}
        )
    except AuthApiError as exc:
        logger.warning("reset-password: %s", exc.message)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Réinitialisation impossible, redemandez un lien")
    return MessageResponse(message="Mot de passe mis à jour")


@router.post("/change-password", response_model=MessageResponse)
@limiter.limit("5/minute")
def change_password(
    request: Request,
    payload: ChangePasswordRequest,
    user: CurrentUser = Depends(get_current_user),
):
    # Vérifie le mot de passe actuel avant d'accepter le changement.
    try:
        new_anon_client().auth.sign_in_with_password(
            {"email": user.email, "password": payload.current_password}
        )
    except AuthApiError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Mot de passe actuel incorrect")

    try:
        get_service_client().auth.admin.update_user_by_id(
            user.id, {"password": payload.new_password}
        )
    except AuthApiError as exc:
        logger.warning("change-password: %s", exc.message)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Changement de mot de passe impossible")
    return MessageResponse(message="Mot de passe mis à jour")


# ─────────────────────────────────────────────────────────────────────────────


@router.get("/me", response_model=MeResponse)
def me(user: CurrentUser = Depends(get_current_user)):
    profile = _fetch_profile(user.id)
    return MeResponse(
        user=UserPublic(id=user.id, email=user.email, name=user.full_name, role=user.role),
        profile=ProfilePublic(**{k: v for k, v in profile.items() if k != "role"}),
    )


@router.put("/me/profile", response_model=ProfilePublic)
def update_profile(
    payload: ProfileUpdateRequest,
    user: CurrentUser = Depends(get_current_user),
):
    values = payload.model_dump()
    if values["date_of_birth"] is not None:
        values["date_of_birth"] = values["date_of_birth"].isoformat()

    result = (
        get_service_client()
        .table("profiles")
        .update(values)
        .eq("id", user.id)
        .execute()
    )
    if not result.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Profil introuvable")
    row = result.data[0]
    return ProfilePublic(**{k: row.get(k) for k in ProfilePublic.model_fields})
