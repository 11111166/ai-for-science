"""Authentication-aware persistent response caching."""
from __future__ import annotations

import hashlib
import hmac
import re

from fastapi import Request
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from .config import get_settings
from .persistent_cache import (
    get_response,
    put_response,
    response_cache_key,
)

_ALLOWED_PREFIX = "/api/v1/drug-discovery"
_SKIP_PARTS = ("/auth/", "/admin/", "/cache/status")
# Business records are authoritative in SQLite and must pass endpoint-level
# project membership checks on every request. They are never served directly
# from the shared PostgreSQL cache.
_BUSINESS_PATH = re.compile(r"/api/v1/drug-discovery/(?:projects|jobs)/")
_SAFE_POST_PATTERNS = (
    re.compile(r"/api/v1/drug-discovery/fungal-discovery/structure-search$"),
    re.compile(r"/api/v1/drug-discovery/fungal-compounds/resolve-target$"),
    re.compile(r"/api/v1/drug-discovery/bacterial-discovery/structure-search$"),
    re.compile(r"/api/v1/drug-discovery/bacterial-compounds/resolve-target$"),
)
_HOP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "set-cookie",
    "content-length", "date", "server",
}


def _eligible_cache_request(request: Request) -> bool:
    if not request.url.path.startswith(_ALLOWED_PREFIX):
        return False
    if any(part in request.url.path for part in _SKIP_PARTS):
        return False
    if _BUSINESS_PATH.search(request.url.path):
        return False
    if request.query_params.get("refresh", "").lower() in {"1", "true", "yes"}:
        return False
    cache_control = request.headers.get("cache-control", "").lower()
    if "no-cache" in cache_control or "no-store" in cache_control:
        return False
    if request.method == "GET":
        return True
    content_type = request.headers.get("content-type", "").lower()
    return (
        request.method == "POST"
        and content_type.startswith("application/json")
        and any(pattern.fullmatch(request.url.path) for pattern in _SAFE_POST_PATTERNS)
    )


def _namespace_for(path: str) -> str:
    if "/fungal-" in path:
        return "target.fungal.reference"
    if "/bacterial-" in path:
        return "target.bacterial.reference"
    if any(part in path for part in ("/methodology", "/data-sources", "/target-discovery-availability")):
        return "platform.reference"
    if "/molecules/" in path:
        return "molecule.reference"
    if "/protein-structure/" in path:
        return "protein.reference"
    return "api.read"


def _shared_reference_namespace(namespace: str) -> bool:
    return namespace in {
        "target.fungal.reference",
        "target.bacterial.reference",
        "platform.reference",
    }


def _ttl_for(path: str) -> int:
    settings = get_settings()
    if any(part in path for part in (
        "/methodology", "/data-sources", "/omics/examples", "/omics/templates/",
        "/fungal-discovery/organisms", "/fungal-targets/templates/",
        "/bacterial-discovery/organisms",
        "/process-knowledge/", "/process-demo/", "/catalog",
    )):
        return settings.cache_static_ttl
    if any(part in path for part in (
        "/molecules/", "/protein-structure/", "/fungal-discovery/",
        "/fungal-compounds/", "/fungal-targets/", "/bacterial-discovery/",
        "/bacterial-compounds/", "/process/engineering/",
        "/process/retrosynthesis/", "/process/intake/aspen-draft",
        "/process/pfd/preview/", "/target-discovery-availability",
    )):
        return settings.cache_public_ttl
    return settings.cache_default_ttl


def _safe_headers(headers) -> dict[str, str]:
    return {
        key: value for key, value in headers.items()
        if key.lower() not in _HOP_HEADERS and not key.lower().startswith("access-control-")
    }


def _authenticate_for_cache(request: Request, *, shared_reference: bool = False) -> str | None:
    """Validate the request before serving a cached private response.

    The verified identity is attached to request.state so endpoint dependencies
    reuse it on a miss instead of contacting the portal a second time.
    """
    settings = get_settings()
    from .auth import UserContext
    if settings.auth_provider == "portal":
        supplied = request.headers.get("x-ai4science-proxy-secret", "")
        if not settings.proxy_auth_secret or not hmac.compare_digest(supplied, settings.proxy_auth_secret):
            return None
        token = request.cookies.get(settings.auth_session_cookie)
        if not token:
            return None
        try:
            from .portal_auth import PortalAuthClient
            identity = PortalAuthClient().me(token, request.client.host if request.client else "")
        except Exception:
            return None
        user = UserContext(
            id=identity.username,
            name=identity.display_name,
            system_role="admin" if identity.role == "admin" else "researcher",
        )
    elif settings.auth_provider == "proxy":
        supplied = request.headers.get("x-ai4science-proxy-secret", "")
        if not settings.proxy_auth_secret or not hmac.compare_digest(supplied, settings.proxy_auth_secret):
            return None
        user_id = request.headers.get("x-platform-user", "").strip()
        role = request.headers.get("x-platform-role", "viewer").strip().lower()
        if not user_id or role not in {"admin", "researcher", "viewer"}:
            return None
        user = UserContext(
            id=user_id,
            name=request.headers.get("x-platform-name", user_id).strip(),
            system_role=role,
        )
    elif settings.auth_provider == "dev":
        user_id = request.headers.get("x-dev-user", "researcher").strip()
        role = request.headers.get("x-dev-role", "admin").strip().lower()
        user = UserContext(id=user_id, name=user_id, system_role=role)
    else:
        return None
    request.state.verified_user = user
    if settings.cache_shared_scope and shared_reference:
        return "shared:authenticated-reference"
    return "user:" + hashlib.sha256(user.id.encode()).hexdigest()[:32]


class PersistentResponseCacheMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        settings = get_settings()
        if not settings.cache_enabled or not settings.cache_database_url:
            return await call_next(request)

        cache_eligible = _eligible_cache_request(request)
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and not cache_eligible:
            response = await call_next(request)
            # Project mutations only change SQLite business state. Public
            # target reference caches are versioned and remain independent.
            response.headers.setdefault("X-AI4Science-Cache", "BYPASS-BUSINESS")
            return response

        if not cache_eligible:
            response = await call_next(request)
            bypass = "BYPASS-BUSINESS" if _BUSINESS_PATH.search(request.url.path) else "BYPASS"
            response.headers.setdefault("X-AI4Science-Cache", bypass)
            return response

        namespace = _namespace_for(request.url.path)
        scope = await run_in_threadpool(
            _authenticate_for_cache,
            request,
            shared_reference=_shared_reference_namespace(namespace),
        )
        if scope is None:
            response = await call_next(request)
            response.headers.setdefault("X-AI4Science-Cache", "BYPASS")
            return response

        body_sha256 = ""
        if request.method == "POST":
            body_sha256 = hashlib.sha256(await request.body()).hexdigest()
        key = response_cache_key(
            request.method,
            request.url.path,
            request.url.query,
            scope,
            request.headers.get("accept", ""),
            body_sha256,
            namespace,
        )
        cached = await run_in_threadpool(get_response, key)
        if cached is not None:
            headers = dict(cached.headers)
            headers["X-AI4Science-Cache"] = "HIT"
            headers["X-AI4Science-Cache-Hits"] = str(cached.hit_count)
            headers["X-AI4Science-Cache-Key"] = key[:12]
            headers["X-AI4Science-Cache-Namespace"] = namespace
            return Response(
                content=cached.body,
                status_code=cached.status_code,
                headers=headers,
                media_type=None,
            )

        response = await call_next(request)
        body = b"".join([chunk async for chunk in response.body_iterator])
        headers = dict(response.headers)
        headers["X-AI4Science-Cache"] = "MISS"
        headers["X-AI4Science-Cache-Key"] = key[:12]
        headers["X-AI4Science-Cache-Namespace"] = namespace

        content_type = response.headers.get("content-type", "application/octet-stream")
        cache_control = response.headers.get("cache-control", "").lower()
        cacheable = (
            response.status_code == 200
            and len(body) <= settings.cache_max_body_bytes
            and "no-store" not in cache_control
            and not content_type.startswith("text/event-stream")
        )
        if cacheable:
            await run_in_threadpool(
                put_response,
                cache_key=key,
                scope=scope,
                method=request.method,
                path=request.url.path,
                query=request.url.query,
                status_code=response.status_code,
                content_type=content_type,
                headers=_safe_headers(response.headers),
                body=body,
                ttl_seconds=_ttl_for(request.url.path),
                namespace=namespace,
            )

        return Response(
            content=body,
            status_code=response.status_code,
            headers=headers,
            media_type=None,
            background=response.background,
        )
