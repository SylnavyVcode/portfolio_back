import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.core.config import get_settings
from app.core.rate_limit import limiter
from app.routers import auth

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app")

settings = get_settings()

app = FastAPI(
    title="Portfolio Valmy — API",
    version="0.1.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    # Gestion d'erreurs centralisée : jamais de stack trace côté client.
    logger.exception("Erreur non gérée sur %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Erreur interne du serveur"})


@app.get("/api/health", tags=["health"])
def health():
    return {"status": "ok"}


app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
