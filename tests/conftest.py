import os

# Variables d'environnement factices AVANT tout import de l'app,
# pour que les tests tournent sans .env ni projet Supabase réel.
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "test-anon-key")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-key")
os.environ.setdefault("SUPABASE_JWT_SECRET", "test-jwt-secret-for-hs256-verification")

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    from app.main import app

    return TestClient(app, raise_server_exceptions=False)
