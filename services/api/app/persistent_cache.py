"""PostgreSQL-backed persistent cache for API responses and data artifacts.

The cache is deliberately separate from the transactional application database.
Failures are fail-open: the API continues to use its normal SQLite/file paths.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import threading
from typing import Any
from urllib.parse import parse_qsl, urlencode

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    delete,
    func,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB, insert as pg_insert
from sqlalchemy.engine import Connection, Engine

from .config import get_settings

UTC = timezone.utc
_metadata = MetaData()
_engine: Engine | None = None
_engine_lock = threading.Lock()

response_cache = Table(
    "response_cache",
    _metadata,
    Column("cache_key", String(64), primary_key=True),
    Column("namespace", String(100), nullable=False, default="api.response", index=True),
    Column("scope", String(160), nullable=False, index=True),
    Column("method", String(12), nullable=False),
    Column("path", Text, nullable=False, index=True),
    Column("query", Text, nullable=False, default=""),
    Column("status_code", Integer, nullable=False),
    Column("content_type", String(255), nullable=False),
    Column("headers", JSONB, nullable=False, default=dict),
    Column("body", LargeBinary, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("size_bytes", BigInteger, nullable=False),
    Column("contract_version", String(80), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False, index=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("last_accessed_at", DateTime(timezone=True), nullable=False, index=True),
    Column("hit_count", BigInteger, nullable=False, default=0),
)

artifact_cache = Table(
    "artifact_cache",
    _metadata,
    Column("cache_key", String(160), primary_key=True),
    Column("namespace", String(100), nullable=False, index=True),
    Column("source", String(160), nullable=False, default="ai4science"),
    Column("source_version", String(120), nullable=False, default="unknown"),
    Column("operation", String(160), nullable=False, default="load"),
    Column("parameters", JSONB, nullable=False, default=dict),
    Column("storage_uri", Text, nullable=True),
    Column("inline_data", LargeBinary, nullable=True),
    Column("content_type", String(255), nullable=False, default="application/octet-stream"),
    Column("content_hash", String(64), nullable=False),
    Column("size_bytes", BigInteger, nullable=False),
    Column("status", String(32), nullable=False, default="ready"),
    Column("expires_at", DateTime(timezone=True), nullable=True, index=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("last_accessed_at", DateTime(timezone=True), nullable=False, index=True),
    Column("hit_count", BigInteger, nullable=False, default=0),
    Column("metadata", JSONB, nullable=False, default=dict),
)


@dataclass(frozen=True)
class CachedResponse:
    status_code: int
    content_type: str
    headers: dict[str, str]
    body: bytes
    created_at: datetime
    expires_at: datetime
    hit_count: int


def _now() -> datetime:
    return datetime.now(UTC)


def get_cache_engine() -> Engine | None:
    global _engine
    settings = get_settings()
    url = settings.cache_database_url.strip()
    if not settings.cache_enabled or not url:
        return None
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                _engine = create_engine(
                    url,
                    pool_pre_ping=True,
                    pool_size=5,
                    max_overflow=5,
                    pool_recycle=300,
                )
    return _engine


def init_cache_schema() -> bool:
    engine = get_cache_engine()
    if engine is None:
        return False
    try:
        _metadata.create_all(engine)
        with engine.begin() as connection:
            # ``create_all`` does not add columns to an existing cache table.
            # Cache storage is rebuildable, so this additive, idempotent change
            # is deliberately kept outside the transactional SQLite schema.
            connection.execute(text(
                "ALTER TABLE response_cache ADD COLUMN IF NOT EXISTS "
                "namespace VARCHAR(100) NOT NULL DEFAULT 'legacy'"
            ))
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_response_cache_namespace "
                "ON response_cache (namespace)"
            ))
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_artifact_cache_parameters_gin "
                "ON artifact_cache USING GIN (parameters)"
            ))
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_artifact_cache_metadata_gin "
                "ON artifact_cache USING GIN (metadata)"
            ))
        return True
    except Exception:
        return False


def cache_health() -> dict[str, Any]:
    engine = get_cache_engine()
    if engine is None:
        return {"enabled": False, "available": False}
    try:
        with engine.connect() as connection:
            version = connection.execute(text("select current_setting('server_version')")).scalar_one()
        return {
            "enabled": True,
            "available": True,
            "database": "PostgreSQL",
            "version": version,
            "role": "rebuildable_cache",
            "authoritative": False,
            "fail_open": True,
        }
    except Exception as exc:
        return {"enabled": True, "available": False, "error": type(exc).__name__}


def canonical_query(query: str) -> str:
    return urlencode(sorted(parse_qsl(query, keep_blank_values=True)), doseq=True)


def response_cache_key(
    method: str,
    path: str,
    query: str,
    scope: str,
    accept: str = "",
    body_sha256: str = "",
    namespace: str = "api.response",
) -> str:
    payload = {
        "method": method.upper(),
        "path": path,
        "query": canonical_query(query),
        "scope": scope,
        "accept": accept.split(",", 1)[0].strip().lower(),
        "body_sha256": body_sha256,
        "namespace": namespace,
        "contract_version": get_settings().cache_contract_version,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def get_response(cache_key: str) -> CachedResponse | None:
    engine = get_cache_engine()
    if engine is None:
        return None
    now = _now()
    try:
        with engine.begin() as connection:
            row = connection.execute(
                select(response_cache).where(
                    response_cache.c.cache_key == cache_key,
                    response_cache.c.expires_at > now,
                    response_cache.c.contract_version == get_settings().cache_contract_version,
                )
            ).mappings().first()
            if not row:
                connection.execute(
                    delete(response_cache).where(
                        response_cache.c.cache_key == cache_key,
                        response_cache.c.expires_at <= now,
                    )
                )
                return None
            connection.execute(
                update(response_cache)
                .where(response_cache.c.cache_key == cache_key)
                .values(last_accessed_at=now, hit_count=response_cache.c.hit_count + 1)
            )
            return CachedResponse(
                status_code=row["status_code"],
                content_type=row["content_type"],
                headers=dict(row["headers"] or {}),
                body=bytes(row["body"]),
                created_at=row["created_at"],
                expires_at=row["expires_at"],
                hit_count=int(row["hit_count"]) + 1,
            )
    except Exception:
        return None


def put_response(
    *,
    cache_key: str,
    scope: str,
    method: str,
    path: str,
    query: str,
    status_code: int,
    content_type: str,
    headers: dict[str, str],
    body: bytes,
    ttl_seconds: int,
    namespace: str = "api.response",
) -> bool:
    engine = get_cache_engine()
    if engine is None:
        return False
    now = _now()
    values = {
        "cache_key": cache_key,
        "namespace": namespace[:100],
        "scope": scope,
        "method": method.upper(),
        "path": path,
        "query": canonical_query(query),
        "status_code": status_code,
        "content_type": content_type[:255],
        "headers": headers,
        "body": body,
        "content_hash": hashlib.sha256(body).hexdigest(),
        "size_bytes": len(body),
        "contract_version": get_settings().cache_contract_version,
        "expires_at": now + timedelta(seconds=max(1, ttl_seconds)),
        "created_at": now,
        "last_accessed_at": now,
        "hit_count": 0,
    }
    try:
        statement = pg_insert(response_cache).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=[response_cache.c.cache_key],
            set_={key: value for key, value in values.items() if key != "cache_key"},
        )
        with engine.begin() as connection:
            connection.execute(statement)
        return True
    except Exception:
        return False


def invalidate_response_cache(scope: str | None = None, namespace: str | None = None) -> int:
    engine = get_cache_engine()
    if engine is None:
        return 0
    try:
        statement = delete(response_cache)
        if scope:
            statement = statement.where(response_cache.c.scope == scope)
        if namespace:
            statement = statement.where(response_cache.c.namespace == namespace)
        with engine.begin() as connection:
            result = connection.execute(statement)
            return int(result.rowcount or 0)
    except Exception:
        return 0


def artifact_identity(namespace: str, identity: Any) -> str:
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True, default=str).encode()
    return f"{namespace}:{hashlib.sha256(encoded).hexdigest()}"


def _legacy_identity(namespace: str, identity: Any) -> str:
    encoded = json.dumps(identity).encode()
    return f"{namespace}:{hashlib.sha256(encoded).hexdigest()}"


def get_json_artifact(namespace: str, identity: Any) -> tuple[float, dict[str, Any]] | None:
    engine = get_cache_engine()
    if engine is None:
        return None
    keys = [artifact_identity(namespace, identity), _legacy_identity(namespace, identity)]
    now = _now()
    try:
        with engine.begin() as connection:
            row = connection.execute(
                select(artifact_cache).where(
                    artifact_cache.c.cache_key.in_(keys),
                    artifact_cache.c.inline_data.is_not(None),
                    (artifact_cache.c.expires_at.is_(None)) | (artifact_cache.c.expires_at > now),
                )
            ).mappings().first()
            if not row:
                return None
            connection.execute(
                update(artifact_cache)
                .where(artifact_cache.c.cache_key == row["cache_key"])
                .values(last_accessed_at=now, hit_count=artifact_cache.c.hit_count + 1)
            )
        payload = json.loads(bytes(row["inline_data"]).decode("utf-8"))
        expires = row["expires_at"].timestamp() if row["expires_at"] else float("inf")
        return expires, payload
    except Exception:
        return None


def put_json_artifact(
    namespace: str,
    identity: Any,
    payload: dict[str, Any],
    *,
    ttl_seconds: int,
    source: str = "ai4science",
    source_version: str = "current",
    operation: str = "lookup",
    metadata: dict[str, Any] | None = None,
) -> bool:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return register_artifact(
        cache_key=artifact_identity(namespace, identity),
        namespace=namespace,
        source=source,
        source_version=source_version,
        operation=operation,
        parameters={"identity": identity},
        storage_uri=None,
        inline_data=body,
        content_type="application/json",
        content_hash=hashlib.sha256(body).hexdigest(),
        size_bytes=len(body),
        expires_at=_now() + timedelta(seconds=max(1, ttl_seconds)),
        metadata=metadata or {},
    )


def register_artifact(
    *,
    cache_key: str,
    namespace: str,
    source: str,
    source_version: str,
    operation: str,
    parameters: dict[str, Any],
    storage_uri: str | None,
    inline_data: bytes | None,
    content_type: str,
    content_hash: str,
    size_bytes: int,
    expires_at: datetime | None = None,
    metadata: dict[str, Any] | None = None,
    connection: Connection | None = None,
) -> bool:
    engine = get_cache_engine()
    if engine is None:
        return False
    now = _now()
    values = {
        "cache_key": cache_key[:160],
        "namespace": namespace[:100],
        "source": source[:160],
        "source_version": source_version[:120],
        "operation": operation[:160],
        "parameters": parameters,
        "storage_uri": storage_uri,
        "inline_data": inline_data,
        "content_type": content_type[:255],
        "content_hash": content_hash[:64],
        "size_bytes": size_bytes,
        "status": "ready",
        "expires_at": expires_at,
        "created_at": now,
        "last_accessed_at": now,
        "hit_count": 0,
        "metadata": metadata or {},
    }
    statement = pg_insert(artifact_cache).values(**values)
    statement = statement.on_conflict_do_update(
        index_elements=[artifact_cache.c.cache_key],
        set_={key: value for key, value in values.items() if key not in {"cache_key", "created_at", "hit_count"}},
    )
    try:
        if connection is not None:
            connection.execute(statement)
        else:
            with engine.begin() as own_connection:
                own_connection.execute(statement)
        return True
    except Exception:
        return False


def cache_stats() -> dict[str, Any]:
    engine = get_cache_engine()
    if engine is None:
        return {"enabled": False}
    try:
        with engine.connect() as connection:
            responses = connection.execute(
                select(
                    func.count().label("entries"),
                    func.coalesce(func.sum(response_cache.c.size_bytes), 0).label("bytes"),
                    func.coalesce(func.sum(response_cache.c.hit_count), 0).label("hits"),
                )
            ).mappings().one()
            artifacts = connection.execute(
                select(
                    func.count().label("entries"),
                    func.coalesce(func.sum(artifact_cache.c.size_bytes), 0).label("bytes"),
                    func.coalesce(func.sum(artifact_cache.c.hit_count), 0).label("hits"),
                )
            ).mappings().one()
            response_namespaces = connection.execute(
                select(
                    response_cache.c.namespace,
                    func.count().label("entries"),
                    func.coalesce(func.sum(response_cache.c.size_bytes), 0).label("bytes"),
                    func.coalesce(func.sum(response_cache.c.hit_count), 0).label("hits"),
                ).group_by(response_cache.c.namespace).order_by(response_cache.c.namespace)
            ).mappings().all()
            artifact_namespaces = connection.execute(
                select(
                    artifact_cache.c.namespace,
                    func.count().label("entries"),
                    func.coalesce(func.sum(artifact_cache.c.size_bytes), 0).label("bytes"),
                    func.coalesce(func.sum(artifact_cache.c.hit_count), 0).label("hits"),
                ).group_by(artifact_cache.c.namespace).order_by(artifact_cache.c.namespace)
            ).mappings().all()
        return {
            "enabled": True,
            "responses": {key: int(value or 0) for key, value in responses.items()},
            "artifacts": {key: int(value or 0) for key, value in artifacts.items()},
            "response_namespaces": {
                row["namespace"]: {
                    "entries": int(row["entries"] or 0),
                    "bytes": int(row["bytes"] or 0),
                    "hits": int(row["hits"] or 0),
                }
                for row in response_namespaces
            },
            "artifact_namespaces": {
                row["namespace"]: {
                    "entries": int(row["entries"] or 0),
                    "bytes": int(row["bytes"] or 0),
                    "hits": int(row["hits"] or 0),
                }
                for row in artifact_namespaces
            },
        }
    except Exception as exc:
        return {"enabled": True, "available": False, "error": type(exc).__name__}


def purge_expired() -> dict[str, int]:
    engine = get_cache_engine()
    if engine is None:
        return {"responses": 0, "artifacts": 0}
    now = _now()
    try:
        with engine.begin() as connection:
            response_result = connection.execute(delete(response_cache).where(response_cache.c.expires_at <= now))
            artifact_result = connection.execute(
                delete(artifact_cache).where(
                    artifact_cache.c.expires_at.is_not(None),
                    artifact_cache.c.expires_at <= now,
                )
            )
        return {
            "responses": int(response_result.rowcount or 0),
            "artifacts": int(artifact_result.rowcount or 0),
        }
    except Exception:
        return {"responses": 0, "artifacts": 0}
