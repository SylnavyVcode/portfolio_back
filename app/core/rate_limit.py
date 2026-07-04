"""Rate limiting (slowapi) — stockage en mémoire.

Suffisant pour un déploiement mono-instance ; sur Vercel chaque instance
serverless a son propre compteur (protection best-effort, à remplacer par
un backend Redis si le trafic le justifie).
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
