import logging
import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from dotenv import load_dotenv

load_dotenv()

from app.core.logging import configure_logging
from app.middleware.security import RateLimitMiddleware, RequestLoggingMiddleware, RequestSizeLimitMiddleware, SecurityHeadersMiddleware
from app.routers.analytics_router import router as analytics_router
from app.routers.auth_router import router as auth_router
from app.routers.finance_router import router as finance_router
from app.routers.partner_router import router as partner_router
from app.routers.productivity_router import router as productivity_router
from app.routers.profile_router import router as profile_router
from app.routers.reward_router import router as reward_router
from app.database import engine
from app.services.database_bootstrap import ensure_database_ready
configure_logging()
logger = logging.getLogger("life_os.errors")

app = FastAPI(title="Life OS API", version="1.0.0")

LOCAL_DEV_CORS_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:5174",
    "http://127.0.0.1:5174",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]


def get_allowed_origins() -> list[str]:
    raw_origins = os.getenv("CORS_ORIGINS", "")
    origins = [origin.strip() for origin in raw_origins.split(",") if origin.strip()]
    if origins:
        return origins
    return LOCAL_DEV_CORS_ORIGINS

trusted_hosts = [host.strip() for host in os.getenv("TRUSTED_HOSTS", "").split(",") if host.strip()]
if trusted_hosts:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=trusted_hosts)

request_size_limit_enabled = os.getenv("REQUEST_SIZE_LIMIT_ENABLED", "true").lower() in {"1", "true", "yes"}
if request_size_limit_enabled:
    request_size_exclude_paths = {
        path.strip()
        for path in os.getenv("REQUEST_SIZE_LIMIT_EXCLUDE_PATHS", "/,/health,/health/db").split(",")
        if path.strip()
    }
    app.add_middleware(
        RequestSizeLimitMiddleware,
        max_body_bytes=int(os.getenv("MAX_REQUEST_BODY_BYTES", "1048576")),
        exclude_paths=request_size_exclude_paths,
    )

rate_limit_enabled = os.getenv("RATE_LIMIT_ENABLED", "true").lower() in {"1", "true", "yes"}
if rate_limit_enabled:
    exclude_paths = {path.strip() for path in os.getenv("RATE_LIMIT_EXCLUDE_PATHS", "/,/health,/health/db").split(",") if path.strip()}
    app.add_middleware(
        RateLimitMiddleware,
        requests_per_window=int(os.getenv("RATE_LIMIT_REQUESTS", "120")),
        window_seconds=int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60")),
        exclude_paths=exclude_paths,
        trust_proxy_headers=os.getenv("TRUST_PROXY_HEADERS", "false").lower() in {"1", "true", "yes", "on"},
    )

app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(SecurityHeadersMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(profile_router)
app.include_router(productivity_router)
app.include_router(finance_router)
app.include_router(partner_router)
app.include_router(analytics_router)
app.include_router(reward_router)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    request_id = getattr(request.state, "request_id", "-")
    logger.exception(
        "unhandled_exception path=%s method=%s request_id=%s",
        request.url.path,
        request.method,
        request_id,
        exc_info=True,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "request_id": request_id},
    )


@app.on_event("startup")
def startup_event():
    ensure_database_ready()


@app.on_event("shutdown")
def shutdown_event():
    return None


@app.get("/")
def root():
    return {"message": "Life OS API Running"}


@app.get("/health")
def health_check():
    return {"status": "ok", "service": "life-os-api"}


@app.get("/health/db")
def database_health_check():
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        logger.warning("database_health_check_failed", exc_info=True)
        return JSONResponse(
            status_code=503,
            content={"status": "error", "service": "life-os-api", "database": "unavailable"},
        )

    return {"status": "ok", "service": "life-os-api", "database": "ok"}
