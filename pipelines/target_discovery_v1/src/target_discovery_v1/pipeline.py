"""Download, build, validate and publish the Target Discovery v1 snapshot."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import platform
import re
import shutil
import sys
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb

from .benchmark import evaluate


PACKAGE_ROOT = Path(__file__).resolve().parents[2]
CONFIG_ROOT = PACKAGE_ROOT / "config"
EXPECTED_DATATYPES = {
    "genetic_association",
    "genetic_literature",
    "somatic_mutation",
    "rna_expression",
    "affected_pathway",
    "animal_model",
    "known_drug",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default) + "\n", encoding="utf-8")
    temporary.replace(path)


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    raise TypeError(f"Object of type {value.__class__.__name__} is not JSON serializable")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class Pipeline:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.registry = load_json(CONFIG_ROOT / "sources.json")
        self.disease_config = load_json(CONFIG_ROOT / "diseases.json")
        self.snapshot_id = self.registry["snapshot_id"]
        self.raw_root = self.root / "data" / "raw"
        self.processed_root = self.root / "data" / "processed" / self.snapshot_id
        self.manifest_root = self.root / "manifests"
        self.result_root = self.root / "results" / self.snapshot_id
        self.work_root = self.root / "work" / self.snapshot_id
        self.database_path = self.processed_root / "target_discovery.duckdb"
        self.download_manifest_path = self.manifest_root / f"{self.snapshot_id}.downloads.json"
        self.build_manifest_path = self.manifest_root / f"{self.snapshot_id}.build.json"
        self.benchmark_path = self.result_root / "benchmark.json"
        self.release_path = self.result_root / "target-discovery-v1.json"

    def ensure_layout(self) -> None:
        for path in (self.raw_root, self.processed_root, self.manifest_root, self.result_root, self.work_root):
            path.mkdir(parents=True, exist_ok=True)

    def doctor(self) -> dict:
        self.ensure_layout()
        probe = self.work_root / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        disk = shutil.disk_usage(self.root)
        report = {
            "status": "passed",
            "snapshot_id": self.snapshot_id,
            "root": str(self.root),
            "python": sys.version.split()[0],
            "duckdb": duckdb.__version__,
            "platform": platform.platform(),
            "free_bytes": disk.free,
            "minimum_free_bytes": 5 * 1024**3,
            "checked_at": now_iso(),
        }
        if disk.free < report["minimum_free_bytes"]:
            report["status"] = "failed"
            raise RuntimeError("At least 5 GiB free space is required for the bounded v1 snapshot")
        atomic_json(self.manifest_root / f"{self.snapshot_id}.doctor.json", report)
        return report

    @staticmethod
    def _directory_urls(url: str) -> list[str]:
        request = urllib.request.Request(url, headers={"User-Agent": "AI-for-Science-target-discovery-v1"})
        html = urllib.request.urlopen(request, timeout=60).read().decode("utf-8", "replace")
        names = sorted(set(re.findall(r'href="([^"/]+\.parquet)"', html)))
        if not names:
            raise RuntimeError(f"No parquet files found at {url}")
        return [url + name for name in names]

    @staticmethod
    def _download(url: str, destination: Path) -> dict:
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_suffix(destination.suffix + ".part")
        existing = destination.stat().st_size if destination.exists() else 0
        if existing:
            return {"url": url, "path": str(destination), "bytes": existing, "sha256": sha256_file(destination), "reused": True}
        partial_bytes = partial.stat().st_size if partial.exists() else 0
        headers = {"User-Agent": "AI-for-Science-target-discovery-v1"}
        if partial_bytes:
            headers["Range"] = f"bytes={partial_bytes}-"
        request = urllib.request.Request(url, headers=headers)
        response = urllib.request.urlopen(request, timeout=180)
        append = partial_bytes > 0 and response.status == 206
        if partial_bytes and not append:
            partial_bytes = 0
        with response, partial.open("ab" if append else "wb") as handle:
            shutil.copyfileobj(response, handle, length=8 * 1024 * 1024)
        partial.replace(destination)
        return {
            "url": url,
            "path": str(destination),
            "bytes": destination.stat().st_size,
            "sha256": sha256_file(destination),
            "reused": False,
        }

    def download(self) -> dict:
        self.ensure_layout()
        requests = []
        for source in self.registry["sources"]:
            urls = self._directory_urls(source["url"]) if source["kind"] == "parquet_directory" else [source["url"]]
            source_root = self.raw_root / source["id"] / source["version"]
            for url in urls:
                filename = source.get("filename") if len(urls) == 1 and source.get("filename") else url.rsplit("/", 1)[-1]
                requests.append((source, url, source_root / filename))
        artifacts = []
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {
                executor.submit(self._download, url, destination): source
                for source, url, destination in requests
            }
            for future in as_completed(futures):
                source = futures[future]
                item = future.result()
                artifacts.append({"source_id": source["id"], "source_version": source["version"], **item})
                print(f"downloaded {source['id']}: {Path(item['path']).name} ({item['bytes']} bytes)", flush=True)
        artifacts.sort(key=lambda item: (item["source_id"], item["path"]))
        report = {
            "manifest_version": "download-manifest-v1",
            "snapshot_id": self.snapshot_id,
            "registry_version": self.registry["registry_version"],
            "created_at": now_iso(),
            "artifact_count": len(artifacts),
            "total_bytes": sum(item["bytes"] for item in artifacts),
            "artifacts": artifacts,
        }
        atomic_json(self.download_manifest_path, report)
        return report

    def _paths(self, source_id: str) -> list[Path]:
        source = next(item for item in self.registry["sources"] if item["id"] == source_id)
        paths = sorted((self.raw_root / source_id / source["version"]).glob("*"))
        return [path for path in paths if path.is_file() and not path.name.endswith(".part")]

    @staticmethod
    def _sql_paths(paths: list[Path]) -> str:
        if not paths:
            raise RuntimeError("Required source artifact is missing; run download first")
        escaped = [str(path).replace("'", "''") for path in paths]
        return "[" + ",".join(f"'{path}'" for path in escaped) + "]"

    def _prepare_hpa(self) -> Path:
        archive = self._paths("human_protein_atlas")[0]
        output = self.work_root / "proteinatlas.tsv"
        if not output.exists():
            with zipfile.ZipFile(archive) as handle:
                members = [item for item in handle.namelist() if item.endswith((".tsv", ".txt"))]
                if len(members) != 1:
                    raise RuntimeError("Unexpected Human Protein Atlas archive layout")
                with handle.open(members[0]) as source, output.open("wb") as destination:
                    shutil.copyfileobj(source, destination)
        return output

    def build(self) -> dict:
        self.ensure_layout()
        if not self.download_manifest_path.exists():
            raise RuntimeError("Download manifest missing; run download first")
        if self.database_path.exists():
            self.database_path.unlink()
        con = duckdb.connect(str(self.database_path))
        con.execute("PRAGMA threads=8")
        con.execute("PRAGMA memory_limit='12GB'")

        disease_rows = self.disease_config["diseases"]
        con.execute("CREATE TABLE configured_diseases(disease_id VARCHAR, configured_name VARCHAR, therapeutic_area VARCHAR)")
        con.executemany(
            "INSERT INTO configured_diseases VALUES (?, ?, ?)",
            [(row["id"], row["name"], row["area"]) for row in disease_rows],
        )

        ot_target = self._sql_paths(self._paths("open_targets_target"))
        ot_disease = self._sql_paths(self._paths("open_targets_disease"))
        ot_assoc = self._sql_paths(self._paths("open_targets_association_by_datatype_direct"))
        ot_priority = self._sql_paths(self._paths("open_targets_target_prioritisation"))
        ot_clingen = self._sql_paths(self._paths("open_targets_clingen"))
        ot_panelapp = self._sql_paths(self._paths("open_targets_panelapp"))
        hgnc = str(self._paths("hgnc")[0]).replace("'", "''")
        hpa = str(self._prepare_hpa()).replace("'", "''")

        con.execute(f"""
            CREATE TABLE disease_catalog AS
            SELECT d.id AS disease_id, d.name AS source_name, c.configured_name, c.therapeutic_area,
                   d.exactSynonyms AS exact_synonyms
            FROM read_parquet({ot_disease}, union_by_name=true) d
            JOIN configured_diseases c ON c.disease_id = d.id
        """)
        con.execute(f"""
            CREATE TABLE hgnc_identity AS
            SELECT hgnc_id, symbol, name, locus_group, locus_type, status, ensembl_gene_id,
                   uniprot_ids, alias_symbol, prev_symbol, date_modified
            FROM read_csv_auto('{hgnc}', delim='\\t', header=true, all_varchar=true, quote='\"', sample_size=-1)
            WHERE status = 'Approved' AND ensembl_gene_id IS NOT NULL
        """)
        con.execute(f"""
            CREATE TABLE hpa_context AS
            SELECT Ensembl AS ensembl_gene_id, Gene AS hpa_symbol, Evidence AS protein_evidence,
                   "HPA evidence" AS hpa_evidence,
                   "RNA tissue specificity" AS rna_tissue_specificity,
                   "RNA tissue distribution" AS rna_tissue_distribution,
                   TRY_CAST("RNA tissue specificity score" AS DOUBLE) AS rna_tissue_specificity_score,
                   "Protein tissue specificity" AS protein_tissue_specificity,
                   "Protein tissue distribution" AS protein_tissue_distribution,
                   "Secretome location" AS secretome_location
            FROM read_csv_auto('{hpa}', delim='\\t', header=true, all_varchar=true, quote='\"', sample_size=-1)
            WHERE Ensembl IS NOT NULL
        """)
        con.execute(f"""
            CREATE TABLE gene_identity AS
            SELECT t.id AS target_id,
                   COALESCE(h.symbol, t.approvedSymbol) AS symbol,
                   COALESCE(h.name, t.approvedName) AS approved_name,
                   t.biotype,
                   h.hgnc_id,
                   h.uniprot_ids,
                   h.alias_symbol,
                   h.prev_symbol,
                   h.date_modified AS hgnc_date_modified,
                   hp.protein_evidence,
                   hp.hpa_evidence,
                   hp.rna_tissue_specificity,
                   hp.rna_tissue_distribution,
                   hp.rna_tissue_specificity_score,
                   hp.protein_tissue_specificity,
                   hp.protein_tissue_distribution,
                   hp.secretome_location,
                   CASE WHEN h.ensembl_gene_id IS NOT NULL THEN 'matched' ELSE 'unmatched' END AS hgnc_status,
                   CASE WHEN hp.ensembl_gene_id IS NOT NULL THEN 'matched' ELSE 'unmatched' END AS hpa_status
            FROM read_parquet({ot_target}, union_by_name=true) t
            LEFT JOIN hgnc_identity h ON h.ensembl_gene_id = t.id
            LEFT JOIN hpa_context hp ON hp.ensembl_gene_id = t.id
        """)
        con.execute(f"""
            CREATE TABLE association_features AS
            WITH selected AS (
                SELECT a.diseaseId AS disease_id, a.targetId AS target_id, a.aggregationValue AS datatype,
                       MAX(a.associationScore) AS score, SUM(a.evidenceCount) AS evidence_count
                FROM read_parquet({ot_assoc}, union_by_name=true) a
                JOIN configured_diseases c ON c.disease_id = a.diseaseId
                WHERE a.aggregationType = 'datatypeId'
                GROUP BY 1,2,3
            )
            SELECT disease_id, target_id,
                   MAX(score) FILTER (WHERE datatype='genetic_association') AS genetic_association,
                   MAX(score) FILTER (WHERE datatype='genetic_literature') AS genetic_literature,
                   MAX(score) FILTER (WHERE datatype='somatic_mutation') AS somatic_mutation,
                   MAX(score) FILTER (WHERE datatype='rna_expression') AS rna_expression,
                   MAX(score) FILTER (WHERE datatype='affected_pathway') AS affected_pathway,
                   MAX(score) FILTER (WHERE datatype='animal_model') AS animal_model,
                   MAX(score) FILTER (WHERE datatype='literature') AS literature_excluded,
                   MAX(score) FILTER (WHERE datatype='known_drug') AS known_drug_excluded,
                   SUM(evidence_count) FILTER (WHERE datatype IN ('genetic_association','genetic_literature','somatic_mutation','rna_expression','affected_pathway','animal_model')) AS nonclinical_evidence_count,
                   COUNT(*) FILTER (WHERE datatype IN ('genetic_association','genetic_literature','somatic_mutation','rna_expression','affected_pathway','animal_model')) AS nonclinical_datatype_count
            FROM selected GROUP BY 1,2
        """)
        con.execute(f"""
            CREATE TABLE feasibility_features AS
            SELECT targetId AS target_id, isInMembrane, isSecreted, hasSafetyEvent, hasPocket, hasLigand,
                   hasSmallMoleculeBinder, hasHighQualityChemicalProbes, geneticConstraint,
                   tissueSpecificity, tissueDistribution, mouseKOScore, maxClinicalStage AS clinical_stage_excluded
            FROM read_parquet({ot_priority}, union_by_name=true)
        """)
        con.execute(f"""
            CREATE TABLE curated_lineage AS
            SELECT disease_id, target_id,
                   MAX(clingen_records) AS clingen_records,
                   MAX(panelapp_records) AS panelapp_records,
                   MAX(clingen_latest) AS clingen_latest,
                   MAX(panelapp_latest) AS panelapp_latest
            FROM (
                SELECT c.diseaseId AS disease_id, c.targetId AS target_id, COUNT(*) AS clingen_records,
                       0::BIGINT AS panelapp_records, MAX(c.evidenceDate) AS clingen_latest, NULL::VARCHAR AS panelapp_latest
                FROM read_parquet({ot_clingen}, union_by_name=true) c
                JOIN configured_diseases d ON d.disease_id=c.diseaseId GROUP BY 1,2
                UNION ALL
                SELECT p.diseaseId, p.targetId, 0::BIGINT, COUNT(*), NULL::VARCHAR, MAX(p.evidenceDate)
                FROM read_parquet({ot_panelapp}, union_by_name=true) p
                JOIN configured_diseases d ON d.disease_id=p.diseaseId GROUP BY 1,2
            ) x GROUP BY 1,2
        """)
        con.execute("""
            CREATE TABLE target_scores AS
            WITH base AS (
                SELECT d.disease_id, d.source_name AS disease_name, d.therapeutic_area,
                       a.target_id, g.symbol, g.approved_name, g.biotype, g.hgnc_id, g.uniprot_ids,
                       g.hgnc_status, g.hpa_status, g.protein_evidence, g.hpa_evidence,
                       g.rna_tissue_specificity, g.rna_tissue_distribution,
                       COALESCE(a.genetic_association,0) AS genetic_association,
                       COALESCE(a.genetic_literature,0) AS genetic_literature,
                       COALESCE(a.somatic_mutation,0) AS somatic_mutation,
                       COALESCE(a.rna_expression,0) AS rna_expression,
                       COALESCE(a.affected_pathway,0) AS affected_pathway,
                       COALESCE(a.animal_model,0) AS animal_model,
                       a.literature_excluded, a.known_drug_excluded,
                       COALESCE(a.nonclinical_evidence_count,0) AS nonclinical_evidence_count,
                       COALESCE(a.nonclinical_datatype_count,0) AS nonclinical_datatype_count,
                       COALESCE(f.isInMembrane,0) AS isInMembrane,
                       COALESCE(f.isSecreted,0) AS isSecreted,
                       COALESCE(f.hasSafetyEvent,0) AS hasSafetyEvent,
                       COALESCE(f.hasPocket,0) AS hasPocket,
                       COALESCE(f.hasLigand,0) AS hasLigand,
                       COALESCE(f.hasSmallMoleculeBinder,0) AS hasSmallMoleculeBinder,
                       COALESCE(f.hasHighQualityChemicalProbes,0) AS hasHighQualityChemicalProbes,
                       f.tissueSpecificity, f.tissueDistribution,
                       f.geneticConstraint, f.mouseKOScore, f.clinical_stage_excluded,
                       COALESCE(l.clingen_records,0) AS clingen_records,
                       COALESCE(l.panelapp_records,0) AS panelapp_records,
                       l.clingen_latest, l.panelapp_latest
                FROM association_features a
                JOIN disease_catalog d ON d.disease_id=a.disease_id
                JOIN gene_identity g ON g.target_id=a.target_id
                LEFT JOIN feasibility_features f ON f.target_id=a.target_id
                LEFT JOIN curated_lineage l ON l.disease_id=a.disease_id AND l.target_id=a.target_id
                WHERE a.nonclinical_datatype_count > 0 AND g.biotype = 'protein_coding'
            ), axes AS (
                SELECT *,
                  ROUND(0.60*genetic_association + 0.25*genetic_literature + 0.15*somatic_mutation, 6) AS causal_confidence,
                  ROUND(0.45*rna_expression + 0.30*affected_pathway + 0.25*animal_model, 6) AS disease_context,
                  GREATEST(0.0, LEAST(1.0, ROUND(
                    0.25*GREATEST(hasSmallMoleculeBinder,0) + 0.15*GREATEST(hasPocket,0)
                    + 0.10*GREATEST(hasLigand,0)
                    + 0.10*GREATEST(GREATEST(isInMembrane,0),GREATEST(isSecreted,0))
                    + 0.10*GREATEST(hasHighQualityChemicalProbes,0)
                    + 0.15*COALESCE((tissueSpecificity+1.0)/2.0,0)
                    + 0.15*(1.0-COALESCE((tissueDistribution+1.0)/2.0,1.0))
                    - 0.20*GREATEST(hasSafetyEvent,0), 6))) AS development_feasibility
                FROM base
            ), target_to_hit AS (
                SELECT *,
                  ROUND(0.40*hasSmallMoleculeBinder + 0.25*hasPocket + 0.20*hasLigand + 0.15*hasHighQualityChemicalProbes, 6) AS chemistry_start_score,
                  ROUND(0.55*isInMembrane + 0.45*isSecreted, 6) AS biologic_start_score,
                  ROUND(0.50*COALESCE((tissueSpecificity+1.0)/2.0,0) + 0.50*(1.0-COALESCE((tissueDistribution+1.0)/2.0,1.0)), 6) AS tissue_selectivity_score,
                  ROUND(0.50*causal_confidence + 0.50*disease_context, 6) AS disease_evidence_score
                FROM axes
            ), readiness AS (
                SELECT *,
                  ROUND(0.55*GREATEST(chemistry_start_score, biologic_start_score) + 0.25*disease_evidence_score + 0.20*tissue_selectivity_score, 6) AS target_to_hit_score,
                  CASE
                    WHEN chemistry_start_score >= 0.45 AND biologic_start_score >= 0.45 THEN 'small_molecule_or_biologic'
                    WHEN chemistry_start_score >= biologic_start_score AND chemistry_start_score >= 0.20 THEN 'small_molecule'
                    WHEN biologic_start_score >= 0.20 THEN 'biologic'
                    ELSE 'evidence_generation'
                  END AS recommended_modality
                FROM target_to_hit
            )
            SELECT *,
                   CASE
                     WHEN target_to_hit_score >= 0.60 AND GREATEST(chemistry_start_score, biologic_start_score) >= 0.50 THEN 'ready_for_hit_search'
                     WHEN target_to_hit_score >= 0.40 THEN 'prioritise_assay_and_structure'
                     ELSE 'evidence_gap'
                   END AS target_to_hit_level,
                   CAST(hasSafetyEvent > 0 AS BOOLEAN) AS known_safety_signal,
                   ROUND(0.50*causal_confidence + 0.30*disease_context + 0.20*development_feasibility,6) AS discovery_score,
                   CAST(known_drug_excluded IS NOT NULL AND known_drug_excluded > 0 AS INTEGER) AS known_drug_label,
                   ROUND(nonclinical_datatype_count / 6.0, 6) AS evidence_coverage,
                   ROW_NUMBER() OVER (PARTITION BY disease_id ORDER BY
                     (0.50*causal_confidence + 0.30*disease_context + 0.20*development_feasibility) DESC,
                     target_id) AS rank
            FROM readiness
        """)
        counts = {
            "configured_diseases": con.execute("SELECT COUNT(*) FROM configured_diseases").fetchone()[0],
            "resolved_diseases": con.execute("SELECT COUNT(*) FROM disease_catalog").fetchone()[0],
            "genes": con.execute("SELECT COUNT(*) FROM gene_identity").fetchone()[0],
            "hgnc_matched": con.execute("SELECT COUNT(*) FROM gene_identity WHERE hgnc_status='matched'").fetchone()[0],
            "hpa_matched": con.execute("SELECT COUNT(*) FROM gene_identity WHERE hpa_status='matched'").fetchone()[0],
            "disease_target_rows": con.execute("SELECT COUNT(*) FROM target_scores").fetchone()[0],
            "candidate_hgnc_matched": con.execute("SELECT COUNT(*) FROM target_scores WHERE hgnc_status='matched'").fetchone()[0],
            "candidate_hpa_matched": con.execute("SELECT COUNT(*) FROM target_scores WHERE hpa_status='matched'").fetchone()[0],
            "known_drug_labels": con.execute("SELECT SUM(known_drug_label) FROM target_scores").fetchone()[0],
        }
        datatypes = {
            row[0]
            for row in con.execute(f"SELECT DISTINCT aggregationValue FROM read_parquet({ot_assoc}, union_by_name=true) WHERE aggregationType='datatypeId'").fetchall()
        }
        validation = {
            "all_diseases_resolved": counts["configured_diseases"] == counts["resolved_diseases"],
            "catalog_hgnc_match_rate": round(counts["hgnc_matched"] / counts["genes"], 6) if counts["genes"] else 0.0,
            "catalog_hpa_match_rate": round(counts["hpa_matched"] / counts["genes"], 6) if counts["genes"] else 0.0,
            "candidate_hgnc_match_rate": round(counts["candidate_hgnc_matched"] / counts["disease_target_rows"], 6) if counts["disease_target_rows"] else 0.0,
            "candidate_hpa_match_rate": round(counts["candidate_hpa_matched"] / counts["disease_target_rows"], 6) if counts["disease_target_rows"] else 0.0,
            "expected_datatypes_present": sorted(EXPECTED_DATATYPES - datatypes) == [],
            "missing_expected_datatypes": sorted(EXPECTED_DATATYPES - datatypes),
            "scores_in_range": con.execute("SELECT COUNT(*)=0 FROM target_scores WHERE discovery_score NOT BETWEEN 0 AND 1 OR causal_confidence NOT BETWEEN 0 AND 1 OR disease_context NOT BETWEEN 0 AND 1 OR development_feasibility NOT BETWEEN 0 AND 1").fetchone()[0],
            "clinical_features_excluded": True,
        }
        con.close()
        status = "passed" if all([
            validation["all_diseases_resolved"],
            validation["candidate_hgnc_match_rate"] >= 0.95,
            validation["candidate_hpa_match_rate"] >= 0.90,
            validation["expected_datatypes_present"],
            validation["scores_in_range"],
        ]) else "failed"
        report = {
            "manifest_version": "build-manifest-v1",
            "snapshot_id": self.snapshot_id,
            "status": status,
            "built_at": now_iso(),
            "database_path": str(self.database_path),
            "database_sha256": sha256_file(self.database_path),
            "counts": counts,
            "validation": validation,
            "score_contract": {
                "version": "three-axis-score-v1",
                "causal_confidence": {"genetic_association": 0.60, "genetic_literature": 0.25, "somatic_mutation": 0.15},
                "disease_context": {"rna_expression": 0.45, "affected_pathway": 0.30, "animal_model": 0.25},
                "discovery_score": {"causal_confidence": 0.50, "disease_context": 0.30, "development_feasibility": 0.20},
                "excluded_from_scoring": ["known_drug", "literature", "maxClinicalStage"],
                "target_to_hit_v2": {
                    "starting_point": 0.55,
                    "disease_evidence": 0.25,
                    "tissue_selectivity": 0.20,
                    "safety_policy": "known safety events are shown separately; absence is not rewarded"
                },
            },
        }
        atomic_json(self.build_manifest_path, report)
        if status != "passed":
            raise RuntimeError(f"Build quality gates failed: {validation}")
        return report

    def benchmark(self) -> dict:
        if not self.database_path.exists():
            raise RuntimeError("Built database missing; run build first")
        con = duckdb.connect(str(self.database_path), read_only=True)
        columns = [item[0] for item in con.execute("DESCRIBE target_scores").fetchall()]
        rows = [dict(zip(columns, values)) for values in con.execute("SELECT * FROM target_scores").fetchall()]
        con.close()
        report = evaluate(rows)
        report["snapshot_id"] = self.snapshot_id
        report["computed_at"] = now_iso()
        report["quality_gates"] = {
            "minimum_diseases_with_labels": 8,
            "diseases_with_labels": report["diseases_computed"],
            "passed": report["diseases_computed"] >= 8,
        }
        atomic_json(self.benchmark_path, report)
        if not report["quality_gates"]["passed"]:
            raise RuntimeError("Benchmark gate failed: fewer than 8 diseases have known-drug retrospective labels")
        return report

    def publish(self, top_n: int = 100) -> dict:
        if not self.build_manifest_path.exists() or not self.benchmark_path.exists():
            raise RuntimeError("Build and benchmark manifests are required before publish")
        build = load_json(self.build_manifest_path)
        benchmark = load_json(self.benchmark_path)
        if build["status"] != "passed" or not benchmark["quality_gates"]["passed"]:
            raise RuntimeError("Cannot publish a snapshot that failed quality gates")
        con = duckdb.connect(str(self.database_path), read_only=True)
        columns = [item[0] for item in con.execute("DESCRIBE target_scores").fetchall()]
        values = con.execute("SELECT * FROM target_scores WHERE rank <= ? ORDER BY disease_id, rank", [top_n]).fetchall()
        con.close()
        targets = []
        for row in (dict(zip(columns, item)) for item in values):
            targets.append({key: value for key, value in row.items() if key not in {"known_drug_excluded", "clinical_stage_excluded"}})
        source_summary = [
            {key: source[key] for key in ("id", "name", "version", "license", "license_url", "role")}
            for source in self.registry["sources"]
        ]
        release = {
            "schema_version": "target-discovery-product-snapshot-v1",
            "snapshot_id": self.snapshot_id,
            "created_at": now_iso(),
            "scientific_status": "computational_hypothesis",
            "source_registry_version": self.registry["registry_version"],
            "disease_set_version": self.disease_config["disease_set_version"],
            "score_contract": build["score_contract"],
            "quality": {"build": build["validation"], "benchmark": benchmark},
            "sources": source_summary,
            "diseases": self.disease_config["diseases"],
            "targets": targets,
        }
        atomic_json(self.release_path, release)
        release_manifest = {
            "snapshot_id": self.snapshot_id,
            "release_path": str(self.release_path),
            "release_sha256": sha256_file(self.release_path),
            "bytes": self.release_path.stat().st_size,
            "target_rows": len(targets),
            "top_n_per_disease": top_n,
            "published_at": now_iso(),
        }
        atomic_json(self.result_root / "release-manifest.json", release_manifest)
        return release_manifest
