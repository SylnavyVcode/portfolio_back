"""Clients Supabase.

- Client service_role (partagé) : accès données + API admin auth. Bypass RLS,
  donc réservé au back — l'autorisation est faite dans les endpoints.
- Client anon (neuf à chaque appel) : opérations d'auth utilisateur
  (sign_up, sign_in, refresh...). On ne le partage pas entre requêtes car
  supabase-py stocke la session sur l'instance après un sign_in.
"""

from functools import lru_cache

from supabase import Client, create_client

from app.core.config import get_settings


@lru_cache
def get_service_client() -> Client:
    settings = get_settings()
    return create_client(settings.supabase_url, settings.supabase_service_role_key)


def new_anon_client() -> Client:
    settings = get_settings()
    return create_client(settings.supabase_url, settings.supabase_anon_key)
