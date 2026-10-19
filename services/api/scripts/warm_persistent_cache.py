#!/usr/bin/env python3
"""Index existing AI4Science data and import reusable JSON caches into PostgreSQL."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import mimetypes
from pathlib import Path
import sqlite3
import sys
from typing import Iterable

from sqlalchemy.dialects.postgresql import insert as pg_insert

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.persistent_cache import artifact_cache, get_cache_engine, init_cache_schema  # noqa: E402

UTC = timezone.utc
SHARED = Path("./data/shared")
SQLITE_DB = SHARED / "data/ai4science.db"
ROOTS = {
    SHARED / "cache": "molecule.file_cache",
    SHARED / "uploads": "business.asset_index",
    SHARED / "snapshots": "reference.snapshot_index",
    SHARED / "fungal-discovery-data": "target.fungal.bundle",
    SHARED / "fungal-expansion-data": "target.fungal.expansion",
    SHARED / "fungal-target-data": "target.fungal.upload_index",
    SHARED / "bacterial-discovery-data": "target.bacterial.bundle",
}
SKIP_PARTS = {"npm-v29", "work", "__pycache__"}
BATCH_SIZE = 400


def now() -> datetime:
    return datetime.now(UTC)


def digest_file(path: Path, size: int) -> tuple[str, str]:
    if size <= 8 * 1024 * 1024:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest(), "sha256"
    stat = path.stat()
    value = f"{path.resolve()}|{size}|{stat.st_mtime_ns}".encode()
    return hashlib.sha256(value).hexdigest(), "stat"


def base_row(
    *,
    cache_key: str,
    namespace: str,
    source: str,
    source_version: str,
    operation: str,
    parameters: dict,
    storage_uri: str | None,
    inline_data: bytes | None,
    content_type: str,
    content_hash: str,
    size_bytes: int,
    expires_at=None,
    metadata: dict | None = None,
) -> dict:
    timestamp = now()
    return {
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
        "created_at": timestamp,
        "last_accessed_at": timestamp,
        "hit_count": 0,
        "metadata": metadata or {},
    }


def upsert(rows: list[dict]) -> int:
    if not rows:
        return 0
    engine = get_cache_engine()
    statement = pg_insert(artifact_cache).values(rows)
    statement = statement.on_conflict_do_update(
        index_elements=[artifact_cache.c.cache_key],
        set_={
            "namespace": statement.excluded.namespace,
            "source": statement.excluded.source,
            "source_version": statement.excluded.source_version,
            "operation": statement.excluded.operation,
            "parameters": statement.excluded.parameters,
            "storage_uri": statement.excluded.storage_uri,
            "inline_data": statement.excluded.inline_data,
            "content_type": statement.excluded.content_type,
            "content_hash": statement.excluded.content_hash,
            "size_bytes": statement.excluded.size_bytes,
            "status": statement.excluded.status,
            "expires_at": statement.excluded.expires_at,
            "last_accessed_at": statement.excluded.last_accessed_at,
            "metadata": statement.excluded.metadata,
        },
    )
    with engine.begin() as connection:
        connection.execute(statement)
    return len(rows)


def batches(rows: Iterable[dict]) -> int:
    total = 0
    batch: list[dict] = []
    for row in rows:
        batch.append(row)
        if len(batch) >= BATCH_SIZE:
            total += upsert(batch)
            batch = []
    total += upsert(batch)
    return total


def file_rows() -> Iterable[dict]:
    for root, namespace in ROOTS.items():
        if not root.exists():
            continue
        for path in root.rglob("*"):
            try:
                if not path.is_file() or any(part in SKIP_PARTS for part in path.parts):
                    continue
                stat = path.stat()
                digest, hash_mode = digest_file(path, stat.st_size)
                rel = path.relative_to(SHARED).as_posix()
                key = "file:" + hashlib.sha256(rel.encode()).hexdigest()
                content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                yield base_row(
                    cache_key=key,
                    namespace=namespace,
                    source="existing_server_data",
                    source_version=datetime.fromtimestamp(stat.st_mtime, UTC).date().isoformat(),
                    operation="load_file",
                    parameters={"relative_path": rel},
                    storage_uri=path.resolve().as_uri(),
                    inline_data=None,
                    content_type=content_type,
                    content_hash=digest,
                    size_bytes=stat.st_size,
                    metadata={"hash_mode": hash_mode, "mtime": stat.st_mtime},
                )
            except (OSError, ValueError):
                continue


def dossier_rows() -> Iterable[dict]:
    root = SHARED / "cache/molecule-dossiers"
    for path in root.glob("*.json"):
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            data = saved["data"]
            if data.get("status") not in {"available", "partial"}:
                continue
            body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
            expires = datetime.fromtimestamp(float(saved["expires"]), UTC)
            yield base_row(
                cache_key=f"molecule_dossier:{path.stem}",
                namespace="molecule_dossier",
                source="legacy_file_cache",
                source_version="import-v1",
                operation="lookup",
                parameters={"legacy_file": path.name},
                storage_uri=path.resolve().as_uri(),
                inline_data=body,
                content_type="application/json",
                content_hash=hashlib.sha256(body).hexdigest(),
                size_bytes=len(body),
                expires_at=expires,
                metadata={"imported": True},
            )
        except (OSError, ValueError, KeyError, TypeError):
            continue


def sqlite_rows() -> Iterable[dict]:
    connection = sqlite3.connect(f"file:{SQLITE_DB}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        tables = [row[0] for row in connection.execute(
            "select name from sqlite_master where type='table' and name not like 'sqlite_%'"
        )]
        db_stat = SQLITE_DB.stat()
        for table in tables:
            count = connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
            payload = {"table": table, "rows": count}
            encoded = json.dumps(payload, sort_keys=True).encode()
            yield base_row(
                cache_key="sqlite_table:" + hashlib.sha256(table.encode()).hexdigest(),
                namespace="business.sqlite.table_index",
                source="ai4science_primary",
                source_version=datetime.fromtimestamp(db_stat.st_mtime, UTC).isoformat(),
                operation="query",
                parameters=payload,
                storage_uri=SQLITE_DB.resolve().as_uri() + f"#{table}",
                inline_data=None,
                content_type="application/x-sqlite3",
                content_hash=hashlib.sha256(encoded).hexdigest(),
                size_bytes=db_stat.st_size,
                metadata={"row_count": count, "authoritative_store": "SQLite", "index_only": True},
            )

        if "external_data_cache" in tables:
            for row in connection.execute("select * from external_data_cache"):
                try:
                    parameters = json.loads(row["parameters"]) if isinstance(row["parameters"], str) else row["parameters"]
                except (TypeError, ValueError):
                    parameters = {}
                uri = row["storage_uri"]
                size = 0
                if uri and uri.startswith("file://"):
                    try:
                        size = Path(uri[7:]).stat().st_size
                    except OSError:
                        pass
                yield base_row(
                    cache_key=f"external_source:{row['cache_key']}",
                    namespace="external_source",
                    source=row["source"],
                    source_version=row["source_version"],
                    operation=row["operation"],
                    parameters=parameters or {},
                    storage_uri=uri,
                    inline_data=None,
                    content_type="application/gzip",
                    content_hash=row["content_hash"],
                    size_bytes=size,
                    metadata={"api_version": row["api_version"], "imported_from": "external_data_cache"},
                )

        if "assets" in tables:
            for row in connection.execute("select id, project_id, name, kind, size, storage_uri, content_hash, version from assets"):
                yield base_row(
                    cache_key=f"project_asset:{row['id']}",
                    namespace="project_asset",
                    source=row["project_id"],
                    source_version=str(row["version"]),
                    operation=row["kind"],
                    parameters={"name": row["name"]},
                    storage_uri=row["storage_uri"],
                    inline_data=None,
                    content_type=mimetypes.guess_type(row["name"])[0] or "application/octet-stream",
                    content_hash=row["content_hash"],
                    size_bytes=row["size"],
                    metadata={"asset_id": row["id"], "imported_from": "assets"},
                )

        if "jobs" in tables:
            for row in connection.execute("select id, project_id, type, status, output_ref, input_version from jobs where output_ref is not null"):
                encoded = f"{row['id']}|{row['output_ref']}".encode()
                yield base_row(
                    cache_key=f"job_output:{row['id']}",
                    namespace="job_output",
                    source=row["project_id"],
                    source_version=row["input_version"] or "unknown",
                    operation=row["type"],
                    parameters={"job_id": row["id"], "status": row["status"]},
                    storage_uri=row["output_ref"],
                    inline_data=None,
                    content_type="application/json",
                    content_hash=hashlib.sha256(encoded).hexdigest(),
                    size_bytes=0,
                    metadata={"imported_from": "jobs"},
                )
    finally:
        connection.close()


def main() -> None:
    if not init_cache_schema():
        raise SystemExit("persistent cache database is unavailable")
    counts = {
        "files": batches(file_rows()),
        "dossiers": batches(dossier_rows()),
        "database": batches(sqlite_rows()),
    }
    engine = get_cache_engine()
    with engine.begin() as connection:
        connection.exec_driver_sql("ANALYZE response_cache")
        connection.exec_driver_sql("ANALYZE artifact_cache")
    print(json.dumps(counts, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
