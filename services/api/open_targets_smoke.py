"""Opt-in live smoke test for Generic Disease-Target Discovery v1.

This is intentionally separate from deterministic pytest tests because it
depends on the current public Open Targets service and data release.
"""

import argparse
import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path


DISEASES = [
    ("fibrosis", "Idiopathic pulmonary fibrosis"),
    ("neurology", "Alzheimer's disease"),
    ("neurology", "Parkinson's disease"),
    ("immunology", "Rheumatoid arthritis"),
    ("immunology", "Crohn's disease"),
    ("metabolic", "Type 2 diabetes"),
    ("metabolic", "Obesity"),
    ("cardiovascular", "Coronary artery disease"),
    ("oncology", "Non-small-cell lung carcinoma"),
    ("oncology", "Breast cancer"),
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 1 <= args.top_n <= 100:
        raise SystemExit("--top-n must be between 1 and 100")

    with tempfile.TemporaryDirectory(prefix="ai4science-ot-smoke-") as temp_dir:
        os.environ.update(
            {
                "APP_ENV": "test",
                "AUTH_PROVIDER": "dev",
                "DATABASE_URL": f"sqlite:///{temp_dir}/smoke.db",
                "OBJECT_STORAGE_MODE": "local",
                "UPLOAD_DIR": f"{temp_dir}/artifacts",
            }
        )
        from app.database import Base, SessionLocal, engine
        from app.open_targets import OpenTargetsAdapter, OpenTargetsError

        Base.metadata.create_all(engine)
        report = {
            "name": "Generic Disease-Target Discovery v1 live smoke",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "top_n": args.top_n,
            "diseases": [],
        }
        with SessionLocal() as db:
            adapter = OpenTargetsAdapter(db)
            try:
                report["source"] = adapter.metadata
                for therapeutic_area, query in DISEASES:
                    started = time.monotonic()
                    try:
                        result = adapter.discover(query, top_n=args.top_n, include_indirect=True)
                        db.commit()
                        report["diseases"].append(
                            {
                                "therapeutic_area": therapeutic_area,
                                "query": query,
                                "status": "passed",
                                "canonical_name": result["resolution"]["canonical_name"],
                                "disease_id": result["resolution"]["disease_id"],
                                "resolution_confidence": result["resolution"]["resolution_confidence"],
                                "targets_retrieved": len(result["targets"]),
                                "valid_ensembl_ids": all(item["external_id"].startswith("ENSG") for item in result["targets"]),
                                "normalized_evidence_records": len(result["evidence"]),
                                "evidence_materialization": result["data_quality"]["evidence_materialization"],
                                "provenance_complete": all(
                                    [result["metadata"]["source_version"], result["metadata"]["api_version"], result["raw_reference"]]
                                ),
                                "benchmark_status": result["benchmark"]["status"],
                                "benchmark": result["benchmark"],
                                "elapsed_seconds": round(time.monotonic() - started, 2),
                            }
                        )
                    except OpenTargetsError as exc:
                        db.rollback()
                        report["diseases"].append(
                            {
                                "therapeutic_area": therapeutic_area,
                                "query": query,
                                "status": "failed",
                                "error_code": exc.code,
                                "error_message": exc.message,
                                "details": exc.details,
                                "elapsed_seconds": round(time.monotonic() - started, 2),
                            }
                        )
            finally:
                adapter.close()
        report["completed_at"] = datetime.now(timezone.utc).isoformat()
        report["summary"] = {
            "passed": sum(item["status"] == "passed" for item in report["diseases"]),
            "failed": sum(item["status"] == "failed" for item in report["diseases"]),
            "therapeutic_areas": len({item["therapeutic_area"] for item in report["diseases"] if item["status"] == "passed"}),
        }
        rendered = json.dumps(report, ensure_ascii=False, indent=2)
        if args.output:
            args.output.write_text(rendered + "\n", encoding="utf-8")
        print(rendered)
        if report["summary"]["failed"]:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
