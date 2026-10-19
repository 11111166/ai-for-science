"""Small, memory-safe indexes for directories of product snapshots.

Large result arrays must not be decoded merely to decide which disease pack
matches a request.  The release contract keeps identifying fields near the
start of every JSON document, so this module decodes only those values.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .open_targets import OpenTargetsError


@dataclass(frozen=True)
class SnapshotHeader:
    path: Path
    snapshot_id: str
    disease: dict[str, Any]


def _header_value(text: str, key: str) -> Any:
    match = re.search(rf'"{re.escape(key)}"\s*:\s*', text)
    if not match:
        raise ValueError(f"missing {key}")
    return json.JSONDecoder().raw_decode(text, match.end())[0]


def read_snapshot_header(
    path: Path,
    *,
    schema_version: str,
    error_code: str,
    error_message: str,
    require_quality_passed: bool = True,
) -> SnapshotHeader:
    try:
        with path.open("r", encoding="utf-8") as handle:
            text = handle.read(1024 * 1024)
        if _header_value(text, "schema_version") != schema_version:
            raise ValueError("schema mismatch")
        if require_quality_passed and _header_value(text, "quality_passed") is not True:
            raise ValueError("quality gate failed")
        disease = _header_value(text, "disease")
        if not isinstance(disease, dict):
            raise ValueError("invalid disease")
        return SnapshotHeader(path=path, snapshot_id=str(_header_value(text, "snapshot_id")), disease=disease)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise OpenTargetsError(error_code, error_message, {"path": str(path), "cause": str(exc)}) from exc


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
