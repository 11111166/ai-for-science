#!/usr/bin/env python3
"""Build an auditable five-artifact complete-disease validation manifest."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path


def artifact_quality(payload: dict, disease_id: str) -> bool:
    schema = payload.get("schema_version")
    if schema == "target-discovery-product-snapshot-v1":
        benchmark_passed = bool(
            payload.get("quality", {}).get("benchmark", {}).get("quality_gates", {}).get("passed")
        )
        disease_present = any(row.get("disease_id") == disease_id for row in payload.get("targets", []))
        return benchmark_passed and disease_present

    if "quality_passed" in payload:
        return bool(payload["quality_passed"])

    quality = payload.get("quality", {})
    build_passed = quality.get("build", {}).get("status") == "passed"
    benchmark_passed = bool(quality.get("benchmark", {}).get("quality_gates", {}).get("passed"))
    if quality:
        return build_passed and benchmark_passed

    return bool(payload.get("benchmark", {}).get("quality_gates", {}).get("passed"))


def build_manifest(disease_id: str, output: Path, artifact_args: list[str]) -> dict:
    artifacts = []
    passed = True
    for raw in artifact_args:
        label, separator, raw_path = raw.partition("=")
        if not separator or not label or not raw_path:
            raise ValueError(f"invalid artifact argument: {raw!r}; expected LABEL=PATH")
        path = Path(raw_path).resolve()
        payload = json.loads(path.read_text())
        quality_passed = artifact_quality(payload, disease_id)
        passed = passed and quality_passed
        artifacts.append(
            {
                "module": label,
                "schema_version": payload.get("schema_version"),
                "path": str(path),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "quality_passed": quality_passed,
            }
        )

    result = {
        "schema_version": "complete-disease-validation-v1",
        "disease_id": disease_id,
        "validated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "quality_passed": passed,
        "artifacts": artifacts,
        "definition": (
            "All five computational artifacts exist and each prespecified quality gate passes: "
            "established Target-to-Hit, novel target, intervention/knockout, virtual cell, and compound evaluation. "
            "This validates data and pipeline behavior, not clinical efficacy."
        ),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--disease-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--artifact", action="append", default=[], help="LABEL=PATH")
    args = parser.parse_args()
    if len(args.artifact) != 5:
        parser.error("exactly five --artifact arguments are required")
    result = build_manifest(args.disease_id, args.output, args.artifact)
    print(json.dumps({"output": str(args.output), "quality_passed": result["quality_passed"]}))
    return 0 if result["quality_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
