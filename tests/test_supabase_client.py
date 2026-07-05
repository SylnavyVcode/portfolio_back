"""Garde-fou : les sous-clients Supabase ne doivent jamais repasser en
HTTP/2 (cf. app/db/supabase_client.py — RemoteProtocolError reproductible
à 100% en environnement multi-thread avec le client HTTP/2 par défaut)."""

from app.db.supabase_client import get_service_client, new_anon_client


def test_service_client_disables_http2():
    client = get_service_client()
    assert client.postgrest.session._transport._pool._http2 is False
    assert client.storage.session._transport._pool._http2 is False
    assert client.auth._http_client._transport._pool._http2 is False
    assert client.auth.admin._http_client._transport._pool._http2 is False


def test_anon_client_disables_http2():
    client = new_anon_client()
    assert client.postgrest.session._transport._pool._http2 is False
    assert client.auth._http_client._transport._pool._http2 is False
