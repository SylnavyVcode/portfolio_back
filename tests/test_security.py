import time

import jwt
import pytest
from fastapi import HTTPException

SECRET = "test-jwt-secret-for-hs256-verification"


def make_token(**overrides) -> str:
    claims = {
        "sub": "11111111-1111-1111-1111-111111111111",
        "email": "user@example.com",
        "aud": "authenticated",
        "role": "authenticated",
        "exp": int(time.time()) + 3600,
    }
    claims.update(overrides)
    return jwt.encode(claims, SECRET, algorithm="HS256")


def test_valid_token_returns_claims():
    from app.core.security import verify_supabase_jwt

    claims = verify_supabase_jwt(make_token())
    assert claims["sub"] == "11111111-1111-1111-1111-111111111111"
    assert claims["email"] == "user@example.com"


def test_expired_token_rejected():
    from app.core.security import verify_supabase_jwt

    with pytest.raises(HTTPException) as exc:
        verify_supabase_jwt(make_token(exp=int(time.time()) - 10))
    assert exc.value.status_code == 401


def test_wrong_audience_rejected():
    from app.core.security import verify_supabase_jwt

    with pytest.raises(HTTPException) as exc:
        verify_supabase_jwt(make_token(aud="anon"))
    assert exc.value.status_code == 401


def test_tampered_token_rejected():
    from app.core.security import verify_supabase_jwt

    token = make_token()
    with pytest.raises(HTTPException):
        verify_supabase_jwt(token[:-4] + "AAAA")
