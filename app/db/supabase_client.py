"""Clients Supabase.

- Client service_role (partagé) : accès données + API admin auth. Bypass RLS,
  donc réservé au back — l'autorisation est faite dans les endpoints.
- Client anon (neuf à chaque appel) : opérations d'auth utilisateur
  (sign_up, sign_in, refresh...). On ne le partage pas entre requêtes car
  supabase-py stocke la session sur l'instance après un sign_in.

Les sous-clients de supabase-py (postgrest-py, storage3, supabase_auth)
forcent tous `http2=True` sans option pour désactiver. Le client
service_role étant partagé (mis en cache) et FastAPI exécutant chaque
dépendance/endpoint synchrone dans un thread du pool anyio, deux threads
distincts peuvent solliciter la même connexion HTTP/2 en quelques
millisecondes — httpx/h2 ne garantit pas cet accès concurrent sans
verrou externe, et Cloudflare (devant Supabase) réinitialise alors la
connexion (`httpx.RemoteProtocolError: ConnectionTerminated`), observé de
façon reproductible sur les requêtes vers /rest/v1 (table profiles). On
force donc HTTP/1.1 (une connexion par requête, pas de multiplexage) sur
les clients internes juste après leur création.
"""

import logging
from functools import lru_cache

import httpx
from supabase import Client, create_client

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def _to_http1(http_client: httpx.Client) -> httpx.Client:
    return httpx.Client(
        base_url=http_client.base_url,
        headers=http_client.headers,
        timeout=http_client.timeout,
        follow_redirects=True,
        http2=False,
    )


def _disable_http2(client: Client) -> Client:
    try:
        client.postgrest.session = _to_http1(client.postgrest.session)
        client.storage.session = _to_http1(client.storage.session)
        client.auth._http_client = _to_http1(client.auth._http_client)
        client.auth.admin._http_client = _to_http1(client.auth.admin._http_client)
    except AttributeError:
        # Nom d'attribut interne changé par une future version de la lib :
        # on garde le client tel quel (HTTP/2) plutôt que de planter ici.
        logger.warning("Impossible de forcer HTTP/1.1 sur le client Supabase (API interne modifiée)")
    return client


@lru_cache
def get_service_client() -> Client:
    settings = get_settings()
    client = create_client(settings.supabase_url, settings.supabase_service_role_key)
    return _disable_http2(client)


def new_anon_client() -> Client:
    settings = get_settings()
    client = create_client(settings.supabase_url, settings.supabase_anon_key)
    return _disable_http2(client)


def get_user_email(user_id: str) -> str:
    """Email d'un utilisateur (auth.users, via l'API admin). Vide si introuvable."""
    try:
        result = get_service_client().auth.admin.get_user_by_id(user_id)
        return (result.user.email if result.user else "") or ""
    except Exception:
        return ""
