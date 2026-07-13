import logging
import os
import re
import time
import uuid
from collections import defaultdict, deque
from typing import Callable

from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

logger = logging.getLogger("life_os.requests")

_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _safe_request_id(raw_value: str | None) -> str:
    if raw_value and _SAFE_REQUEST_ID.match(raw_value):
        return raw_value
    return str(uuid.uuid4())


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable):
        request_id = _safe_request_id(request.headers.get("x-request-id"))
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"

        content_security_policy = os.getenv(
            "SECURITY_CONTENT_SECURITY_POLICY",
            "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
        ).strip()
        if content_security_policy:
            response.headers["Content-Security-Policy"] = content_security_policy

        if request.url.path not in {"/", "/health", "/health/db"}:
            response.headers.setdefault("Cache-Control", "no-store")
            response.headers.setdefault("Pragma", "no-cache")

        if os.getenv("APP_ENV", "development").lower() in {"production", "prod"}:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable):
        started_at = time.monotonic()
        response = await call_next(request)
        duration_ms = round((time.monotonic() - started_at) * 1000, 2)
        logger.info(
            "request method=%s path=%s status=%s duration_ms=%s request_id=%s",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            getattr(request.state, "request_id", "-"),
        )
        return response


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_body_bytes: int = 1_048_576, exclude_paths: set[str] | None = None):
        super().__init__(app)
        self.max_body_bytes = max(1, int(max_body_bytes))
        self.exclude_paths = exclude_paths or {"/", "/health", "/health/db"}

    async def dispatch(self, request: Request, call_next: Callable):
        if request.method in {"GET", "HEAD", "OPTIONS"} or request.url.path in self.exclude_paths:
            return await call_next(request)

        content_length = request.headers.get("content-length")
        if content_length:
            try:
                body_size = int(content_length)
            except ValueError:
                return JSONResponse(status_code=400, content={"detail": "Invalid Content-Length header"})
            if body_size > self.max_body_bytes:
                return JSONResponse(
                    status_code=413,
                    content={"detail": "Request body too large"},
                    headers={"X-Max-Body-Bytes": str(self.max_body_bytes)},
                )

        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        requests_per_window: int = 120,
        window_seconds: int = 60,
        exclude_paths: set[str] | None = None,
        trust_proxy_headers: bool | None = None,
    ):
        super().__init__(app)
        self.requests_per_window = requests_per_window
        self.window_seconds = window_seconds
        self.exclude_paths = exclude_paths or {"/", "/health", "/health/db"}
        self.trust_proxy_headers = _bool_env("TRUST_PROXY_HEADERS", False) if trust_proxy_headers is None else trust_proxy_headers
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def client_key(self, request: Request) -> str:
        if self.trust_proxy_headers:
            forwarded_for = request.headers.get("x-forwarded-for")
            if forwarded_for:
                return forwarded_for.split(",")[0].strip() or "unknown"
            real_ip = request.headers.get("x-real-ip")
            if real_ip:
                return real_ip.strip() or "unknown"
        return request.client.host if request.client else "unknown"

    async def dispatch(self, request: Request, call_next: Callable):
        if request.method == "OPTIONS" or request.url.path in self.exclude_paths:
            return await call_next(request)

        now = time.monotonic()
        key = self.client_key(request)
        hits = self._hits[key]
        while hits and now - hits[0] > self.window_seconds:
            hits.popleft()

        if len(hits) >= self.requests_per_window:
            retry_after = max(1, int(self.window_seconds - (now - hits[0])))
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded"},
                headers={"Retry-After": str(retry_after)},
            )

        hits.append(now)
        return await call_next(request)
