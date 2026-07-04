"""Vérification des JWT émis par Supabase Auth.

Deux modes, selon la configuration du projet Supabase :
- SUPABASE_JWT_SECRET renseigné => signature HS256 (secret partagé "legacy") ;
- sinon => clés asymétriques (RS256/ES256) récupérées sur le endpoint JWKS
  du projet, avec cache géré par PyJWKClient.
"""

from functools import lru_cache

import jwt
from fastapi import HTTPException, status
from jwt import PyJWKClient

from app.core.config import get_settings

_ASYMMETRIC_ALGORITHMS = ["RS256", "ES256"]
_AUDIENCE = "authenticated"


@lru_cache
def _jwks_client() -> PyJWKClient:
    settings = get_settings()
    return PyJWKClient(
        f"{settings.supabase_url}/auth/v1/.well-known/jwks.json",
        cache_keys=True,
    )


def verify_supabase_jwt(token: str) -> dict:
    """Retourne les claims du token, ou lève 401 si invalide/expiré."""
    settings = get_settings()
    try:
        if settings.supabase_jwt_secret:
            return jwt.decode(
                token,
                settings.supabase_jwt_secret,
                algorithms=["HS256"],
                audience=_AUDIENCE,
            )
        signing_key = _jwks_client().get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=_ASYMMETRIC_ALGORITHMS,
            audience=_AUDIENCE,
        )
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session invalide ou expirée",
            headers={"WWW-Authenticate": "Bearer"},
        )
