import hashlib
import io
from pathlib import Path
from urllib.parse import unquote, urlparse

from minio import Minio

from .config import get_settings


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _index_artifact(
    namespace: str,
    source: str,
    operation: str,
    storage_uri: str,
    digest: str,
    size_bytes: int,
    source_version: str = "current",
    identity: str = "",
) -> None:
    """Best-effort index for every persisted source or project artifact."""
    try:
        from .persistent_cache import register_artifact
        cache_digest = hashlib.sha256(
            f"{namespace}|{source}|{source_version}|{identity or storage_uri}".encode()
        ).hexdigest()
        register_artifact(
            cache_key=f"{namespace}:{cache_digest}",
            namespace=namespace,
            source=source,
            source_version=source_version,
            operation=operation,
            parameters={"identity": identity} if identity else {},
            storage_uri=storage_uri,
            inline_data=None,
            content_type="application/octet-stream",
            content_hash=digest,
            size_bytes=size_bytes,
            metadata={"indexed_by": "storage"},
        )
    except Exception:
        pass


def save_asset(project_id: str, filename: str, data: bytes) -> tuple[str, str]:
    settings = get_settings()
    digest = sha256_bytes(data)
    safe_name = Path(filename).name.replace(" ", "_")
    object_name = f"{project_id}/{digest[:12]}-{safe_name}"
    if settings.object_storage_mode == "s3":
        client = Minio(
            settings.s3_endpoint,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
            secure=settings.s3_secure,
        )
        if not client.bucket_exists(settings.s3_bucket):
            client.make_bucket(settings.s3_bucket)
        client.put_object(settings.s3_bucket, object_name, io.BytesIO(data), len(data))
        uri = f"s3://{settings.s3_bucket}/{object_name}"
        _index_artifact("project_asset", project_id, filename, uri, digest, len(data))
        return uri, digest
    root = Path(settings.upload_dir)
    target = root / object_name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    uri = target.resolve().as_uri()
    _index_artifact("project_asset", project_id, filename, uri, digest, len(data))
    return uri, digest


def save_source_artifact(source: str, source_version: str, cache_key: str, filename: str, data: bytes) -> tuple[str, str]:
    """Persist immutable public-source payloads outside project upload paths."""
    settings = get_settings()
    digest = sha256_bytes(data)
    safe_name = Path(filename).name.replace(" ", "_")
    object_name = f"external-cache/{source}/{source_version}/{cache_key[:16]}-{safe_name}"
    if settings.object_storage_mode == "s3":
        client = Minio(
            settings.s3_endpoint,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
            secure=settings.s3_secure,
        )
        if not client.bucket_exists(settings.s3_bucket):
            client.make_bucket(settings.s3_bucket)
        client.put_object(settings.s3_bucket, object_name, io.BytesIO(data), len(data), content_type="application/gzip")
        uri = f"s3://{settings.s3_bucket}/{object_name}"
        _index_artifact("external_source", source, filename, uri, digest, len(data), source_version, cache_key)
        return uri, digest
    target = Path(settings.upload_dir) / object_name
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_bytes(data)
    uri = target.resolve().as_uri()
    _index_artifact("external_source", source, filename, uri, digest, len(data), source_version, cache_key)
    return uri, digest


def read_source_artifact(storage_uri: str) -> bytes:
    return read_storage_bytes(storage_uri)


def read_storage_bytes(storage_uri: str, max_bytes: int | None = None) -> bytes:
    """Read a project or source artifact without trusting a caller-supplied path.

    The URI must have been persisted by this service.  Callers are responsible
    for resolving the owning database row before passing the URI here.
    """
    settings = get_settings()
    parsed = urlparse(storage_uri)
    if parsed.scheme == "s3":
        client = Minio(
            settings.s3_endpoint,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
            secure=settings.s3_secure,
        )
        response = client.get_object(parsed.netloc, parsed.path.lstrip("/"))
        try:
            data = response.read() if max_bytes is None else response.read(max_bytes + 1)
        finally:
            response.close()
            response.release_conn()
        if max_bytes is not None and len(data) > max_bytes:
            raise ValueError("STORAGE_OBJECT_TOO_LARGE")
        return data
    if parsed.scheme != "file":
        raise ValueError("UNSUPPORTED_SOURCE_ARTIFACT_URI")
    path = Path(unquote(parsed.path))
    if max_bytes is not None and path.stat().st_size > max_bytes:
        raise ValueError("STORAGE_OBJECT_TOO_LARGE")
    return path.read_bytes()
