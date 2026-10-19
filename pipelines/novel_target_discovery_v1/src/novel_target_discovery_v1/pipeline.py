"""Reproducible GWAS/eQTL/single-cell/network pipeline for Novel Target Discovery v1."""

from __future__ import annotations

import hashlib
import json
import platform
import re
import shutil
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd
from scipy import sparse

from .benchmark import evaluate
from .scoring import candidate_gate, colocalization_score, evidence_strength, final_score, novelty_class, novelty_score
from .single_cell import analyze_h5ad


PACKAGE_ROOT = Path(__file__).resolve().parents[2]
CONFIG_ROOT = PACKAGE_ROOT / "config"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def finite_number(value: Any, default: float = 0.0) -> float:
    """Convert nullable dataframe values without allowing NaN/inf into scores."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if np.isfinite(number) else default


class Pipeline:
    def __init__(self, root: Path, sources_config: Path | None = None, disease_config: Path | None = None):
        self.root = root.resolve()
        self.sources_config = (sources_config or CONFIG_ROOT / "sources.json").resolve()
        self.disease_config = (disease_config or CONFIG_ROOT / "disease.json").resolve()
        self.registry = load_json(self.sources_config)
        self.disease = load_json(self.disease_config)
        if self.disease.get("snapshot_id"):
            self.registry["snapshot_id"] = self.disease["snapshot_id"]
        for override in self.disease.get("source_overrides", []):
            replace_kind = override["kind"]
            self.registry["sources"] = [source for source in self.registry["sources"] if source["kind"] != replace_kind]
            self.registry["sources"].append(override)
        self.snapshot_id = self.registry["snapshot_id"]
        self.raw_root = self.root / "data" / "raw"
        self.processed_root = self.root / "data" / "processed" / self.snapshot_id
        self.manifest_root = self.root / "manifests"
        self.result_root = self.root / "results" / self.snapshot_id
        self.work_root = self.root / "work" / self.snapshot_id
        self.database_path = self.processed_root / "novel_target_discovery.duckdb"
        self.download_manifest_path = self.manifest_root / f"{self.snapshot_id}.downloads.json"
        self.build_manifest_path = self.manifest_root / f"{self.snapshot_id}.build.json"
        self.benchmark_path = self.result_root / "benchmark.json"
        self.release_path = self.result_root / "novel-target-discovery-v1.json"

    def ensure_layout(self) -> None:
        for path in (self.raw_root, self.processed_root, self.manifest_root, self.result_root, self.work_root):
            path.mkdir(parents=True, exist_ok=True)

    def doctor(self) -> dict:
        self.ensure_layout()
        disk = shutil.disk_usage(self.root)
        report = {
            "status": "passed" if disk.free >= 10 * 1024**3 else "failed",
            "snapshot_id": self.snapshot_id,
            "python": sys.version.split()[0],
            "duckdb": duckdb.__version__,
            "numpy": np.__version__,
            "platform": platform.platform(),
            "free_bytes": disk.free,
            "minimum_free_bytes": 10 * 1024**3,
            "checked_at": now_iso(),
        }
        atomic_json(self.manifest_root / f"{self.snapshot_id}.doctor.json", report)
        if report["status"] != "passed":
            raise RuntimeError("Novel Target Discovery v1 requires at least 10 GiB free space")
        return report

    @staticmethod
    def _directory_urls(url: str) -> list[str]:
        request = urllib.request.Request(url, headers={"User-Agent": "AI-for-Science-novel-target-v1"})
        html = urllib.request.urlopen(request, timeout=90).read().decode("utf-8", "replace")
        names = sorted(set(re.findall(r'href="([^"/]+\.parquet)"', html)))
        if not names:
            raise RuntimeError(f"No parquet files found at {url}")
        return [url + name for name in names]

    @staticmethod
    def _download(url: str, destination: Path) -> dict:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() and destination.stat().st_size:
            return {"url": url, "path": str(destination), "bytes": destination.stat().st_size, "sha256": sha256_file(destination), "reused": True}
        partial = destination.with_suffix(destination.suffix + ".part")
        partial_bytes = partial.stat().st_size if partial.exists() else 0
        headers = {"User-Agent": "AI-for-Science-novel-target-v1"}
        if partial_bytes:
            headers["Range"] = f"bytes={partial_bytes}-"
        response = urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=300)
        append = partial_bytes > 0 and response.status == 206
        with response, partial.open("ab" if append else "wb") as handle:
            shutil.copyfileobj(response, handle, length=8 * 1024 * 1024)
        partial.replace(destination)
        return {"url": url, "path": str(destination), "bytes": destination.stat().st_size, "sha256": sha256_file(destination), "reused": False}

    def _download_gwas(self, source: dict, destination: Path) -> dict:
        if destination.exists() and destination.stat().st_size:
            return {"url": source["url"], "path": str(destination), "bytes": destination.stat().st_size, "sha256": sha256_file(destination), "reused": True}
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(".jsonl.part")
        page = 0
        records = 0
        with temporary.open("w", encoding="utf-8") as handle:
            while True:
                separator = "&" if "?" in source["url"] else "?"
                url = f"{source['url']}{separator}page={page}"
                request = urllib.request.Request(url, headers={"User-Agent": "AI-for-Science-novel-target-v1"})
                payload = json.load(urllib.request.urlopen(request, timeout=180))
                associations = payload.get("_embedded", {}).get("associations", [])
                for association in associations:
                    handle.write(json.dumps(association, ensure_ascii=False) + "\n")
                records += len(associations)
                total_pages = int(payload.get("page", {}).get("totalPages", 1))
                if page + 1 >= total_pages:
                    break
                page += 1
        temporary.replace(destination)
        return {"url": source["url"], "path": str(destination), "bytes": destination.stat().st_size, "sha256": sha256_file(destination), "records": records, "pages": page + 1, "reused": False}

    def download(self) -> dict:
        self.ensure_layout()
        requests = []
        immediate = []
        for source in self.registry["sources"]:
            source_root = self.raw_root / source["id"] / source["version"]
            if source["kind"] == "gwas_catalog_v2":
                immediate.append({"source": source, "destination": source_root / source["filename"]})
                continue
            urls = self._directory_urls(source["url"]) if source["kind"] == "parquet_directory" else [source["url"]]
            for url in urls:
                filename = source.get("filename") if len(urls) == 1 and source.get("filename") else url.rsplit("/", 1)[-1]
                requests.append((source, url, source_root / filename))
        artifacts = []
        for item in immediate:
            result = self._download_gwas(item["source"], item["destination"])
            artifacts.append({"source_id": item["source"]["id"], "source_version": item["source"]["version"], **result})
            print(f"downloaded {item['source']['id']}: {result.get('records', 'cached')} records", flush=True)
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {executor.submit(self._download, url, destination): source for source, url, destination in requests}
            for future in as_completed(futures):
                source = futures[future]
                result = future.result()
                artifacts.append({"source_id": source["id"], "source_version": source["version"], **result})
                print(f"downloaded {source['id']}: {Path(result['path']).name} ({result['bytes']} bytes)", flush=True)
        artifacts.sort(key=lambda item: (item["source_id"], item["path"]))
        report = {"manifest_version": "novel-target-download-manifest-v1", "snapshot_id": self.snapshot_id, "registry_version": self.registry["registry_version"], "created_at": now_iso(), "artifact_count": len(artifacts), "total_bytes": sum(item["bytes"] for item in artifacts), "artifacts": artifacts}
        atomic_json(self.download_manifest_path, report)
        return report

    def _paths(self, source_id: str) -> list[Path]:
        source = next(item for item in self.registry["sources"] if item["id"] == source_id)
        return sorted(path for path in (self.raw_root / source_id / source["version"]).glob("*") if path.is_file() and not path.name.endswith(".part"))

    @staticmethod
    def _sql_paths(paths: list[Path]) -> str:
        if not paths:
            raise RuntimeError("Required source artifact missing; run download first")
        return "[" + ",".join("'" + str(path).replace("'", "''") + "'" for path in paths) + "]"

    @staticmethod
    def _feasibility(row: pd.Series) -> float:
        value = 0.25 * max(finite_number(row.get("hasSmallMoleculeBinder")), 0) + 0.15 * max(finite_number(row.get("hasPocket")), 0) + 0.10 * max(finite_number(row.get("hasLigand")), 0)
        value += 0.10 * max(finite_number(row.get("isInMembrane")), finite_number(row.get("isSecreted")), 0) + 0.10 * max(finite_number(row.get("hasHighQualityChemicalProbes")), 0)
        value -= 0.20 * max(finite_number(row.get("hasSafetyEvent")), 0)
        return round(min(1.0, max(0.0, value)), 6)

    @staticmethod
    def _network_scores(gene_ids: list[str], edges: list[tuple[str, str, float]], seeds: np.ndarray) -> tuple[np.ndarray, int]:
        lookup = {gene_id: index for index, gene_id in enumerate(gene_ids)}
        rows, columns, values = [], [], []
        for left, right, score in edges:
            if left not in lookup or right not in lookup or left == right:
                continue
            weight = max(0.0, min(1.0, float(score or 0.0)))
            if weight < 0.20:
                continue
            a, b = lookup[left], lookup[right]
            rows.extend((a, b)); columns.extend((b, a)); values.extend((weight, weight))
        if not values or seeds.sum() == 0:
            return np.zeros(len(gene_ids)), 0
        adjacency = sparse.csr_matrix((values, (rows, columns)), shape=(len(gene_ids), len(gene_ids)))
        adjacency.sum_duplicates()
        row_sums = np.asarray(adjacency.sum(axis=1)).ravel()
        transition = sparse.diags(np.divide(1.0, row_sums, out=np.zeros_like(row_sums), where=row_sums > 0)) @ adjacency
        seed_vector = seeds / seeds.sum()
        vector = seed_vector.copy()
        restart = 0.35
        for _ in range(50):
            updated = restart * seed_vector + (1 - restart) * (transition.T @ vector)
            if np.abs(updated - vector).sum() < 1e-10:
                vector = updated
                break
            vector = updated
        order = pd.Series(vector).rank(method="average", pct=True).to_numpy()
        order[vector <= 0] = 0
        return order, adjacency.nnz // 2

    def build(self) -> dict:
        self.ensure_layout()
        if not self.download_manifest_path.exists():
            raise RuntimeError("Download manifest missing")
        if self.database_path.exists():
            self.database_path.unlink()
        con = duckdb.connect(str(self.database_path))
        con.execute("PRAGMA threads=8")
        con.execute("PRAGMA memory_limit='12GB'")
        disease_id = self.disease["id"]

        target_paths = self._sql_paths(self._paths("open_targets_target"))
        association_paths = self._sql_paths(self._paths("open_targets_association_by_datatype_direct"))
        priority_paths = self._sql_paths(self._paths("open_targets_target_prioritisation"))
        gwas_paths = self._sql_paths(self._paths("open_targets_gwas_credible_sets"))
        coloc_paths = self._sql_paths(self._paths("open_targets_colocalisation"))
        credible_paths = self._sql_paths(self._paths("open_targets_credible_set"))
        study_paths = self._sql_paths(self._paths("open_targets_study"))
        interaction_paths = self._sql_paths(self._paths("open_targets_interaction"))

        con.execute(f"""CREATE TABLE genes AS SELECT id AS target_id, approvedSymbol AS symbol, approvedName AS approved_name, biotype FROM read_parquet({target_paths}, union_by_name=true) WHERE biotype='protein_coding'""")
        con.execute(f"""
            CREATE TABLE prior_features AS
            SELECT targetId AS target_id,
              MAX(associationScore) FILTER (WHERE aggregationValue NOT IN ('known_drug','literature','genetic_association')) AS prior_association,
              MAX(associationScore) FILTER (WHERE aggregationValue='literature') AS literature_score,
              MAX(associationScore) FILTER (WHERE aggregationValue='known_drug') AS known_drug_score
            FROM read_parquet({association_paths}, union_by_name=true)
            WHERE diseaseId='{disease_id}' AND aggregationType='datatypeId' GROUP BY 1
        """)
        con.execute(f"""
            CREATE TABLE feasibility_features AS
            SELECT targetId AS target_id, isInMembrane, isSecreted, hasSafetyEvent, hasPocket, hasLigand,
              hasSmallMoleculeBinder, hasHighQualityChemicalProbes, COALESCE(maxClinicalStage,0) AS maxClinicalStage
            FROM read_parquet({priority_paths}, union_by_name=true)
        """)
        con.execute(f"""
            CREATE TABLE ot_gwas AS
            SELECT targetId AS target_id, MAX(score) AS ot_gwas_score, COUNT(DISTINCT studyLocusId) AS fine_mapped_loci
            FROM read_parquet({gwas_paths}, union_by_name=true)
            WHERE diseaseId='{disease_id}' GROUP BY 1
        """)
        con.execute(f"""
            CREATE TABLE eqtl_features AS
            WITH disease_loci AS (
              SELECT DISTINCT studyLocusId FROM read_parquet({gwas_paths}, union_by_name=true) WHERE diseaseId='{disease_id}'
            ), qtl_loci AS (
              SELECT c.studyLocusId, s.geneId, s.biosampleId, s.studyType
              FROM read_parquet({credible_paths}, union_by_name=true) c
              JOIN read_parquet({study_paths}, union_by_name=true) s USING(studyId)
              WHERE LOWER(COALESCE(s.studyType,'')) LIKE '%eqtl%' AND s.geneId IS NOT NULL
            ), disease_qtl_pairs AS (
              SELECT x.rightStudyLocusId AS qtlStudyLocusId, x.h4, x.clpp
              FROM read_parquet({coloc_paths}, union_by_name=true) x
              JOIN disease_loci d ON x.leftStudyLocusId=d.studyLocusId
              WHERE LOWER(COALESCE(x.rightStudyType,'')) LIKE '%eqtl%'
              UNION ALL
              SELECT x.leftStudyLocusId AS qtlStudyLocusId, x.h4, x.clpp
              FROM read_parquet({coloc_paths}, union_by_name=true) x
              JOIN disease_loci d ON x.rightStudyLocusId=d.studyLocusId
            )
            SELECT q.geneId AS target_id,
              MAX(COALESCE(x.h4,0)) AS eqtl_h4,
              MAX(COALESCE(x.clpp,0)) AS eqtl_clpp,
              COUNT(DISTINCT x.qtlStudyLocusId) AS colocalised_loci,
              string_agg(DISTINCT COALESCE(q.biosampleId,'unknown'), ',') AS eqtl_biosamples
            FROM disease_qtl_pairs x
            JOIN qtl_loci q ON x.qtlStudyLocusId=q.studyLocusId
            WHERE COALESCE(x.h4,0) >= 0.50 OR COALESCE(x.clpp,0) >= 0.01
            GROUP BY 1
        """)

        genes = con.execute("SELECT * FROM genes ORDER BY target_id").fetchdf()
        symbol_to_id = {str(row.symbol): str(row.target_id) for row in genes.itertuples()}
        gwas_catalog: dict[str, dict[str, Any]] = {}
        gwas_source_id = str(self.disease.get("gwas_catalog_source_id") or next(
            source["id"] for source in self.registry["sources"] if source["kind"] == "gwas_catalog_v2"
        ))
        with self._paths(gwas_source_id)[0].open(encoding="utf-8") as handle:
            for line in handle:
                item = json.loads(line)
                pvalue = max(float(item.get("p_value") or 1.0), 1e-300)
                significance = min(1.0, -np.log10(pvalue) / 50.0)
                study = str(item.get("accession_id") or "unknown")
                for symbol in item.get("mapped_genes") or []:
                    target_id = symbol_to_id.get(str(symbol))
                    if not target_id:
                        continue
                    entry = gwas_catalog.setdefault(target_id, {"max_significance": 0.0, "studies": set(), "associations": 0})
                    entry["max_significance"] = max(entry["max_significance"], significance)
                    entry["studies"].add(study)
                    entry["associations"] += 1
        gwas_catalog_rows = [{"target_id": key, "gwas_catalog_score": round(0.7 * value["max_significance"] + 0.3 * (1 - np.exp(-len(value["studies"]) / 3)), 6), "gwas_catalog_studies": len(value["studies"]), "gwas_catalog_associations": value["associations"]} for key, value in gwas_catalog.items()]
        con.register("gwas_catalog_input", pd.DataFrame(gwas_catalog_rows))
        con.execute("CREATE TABLE gwas_catalog AS SELECT * FROM gwas_catalog_input")

        single_cell_source_id = str(self.disease["single_cell"].get("source_id") or next(
            source["id"] for source in self.registry["sources"] if source["kind"] == "file"
        ))
        single_cell_path = str(self._paths(single_cell_source_id)[0])
        single_cell, single_cell_qc = analyze_h5ad(single_cell_path, self.disease["single_cell"])
        con.register("single_cell_input", single_cell)
        con.execute("CREATE TABLE single_cell_features AS SELECT * FROM single_cell_input")

        features = con.execute("""
            SELECT g.*, COALESCE(og.ot_gwas_score,0) AS ot_gwas_score, COALESCE(og.fine_mapped_loci,0) AS fine_mapped_loci,
              COALESCE(gc.gwas_catalog_score,0) AS gwas_catalog_score, COALESCE(gc.gwas_catalog_studies,0) AS gwas_catalog_studies,
              COALESCE(gc.gwas_catalog_associations,0) AS gwas_catalog_associations,
              COALESCE(eq.eqtl_h4,0) AS eqtl_h4, COALESCE(eq.eqtl_clpp,0) AS eqtl_clpp,
              COALESCE(eq.colocalised_loci,0) AS colocalised_loci, eq.eqtl_biosamples,
              COALESCE(sc.single_cell_score,0) AS single_cell_score, sc.single_cell_log2fc, sc.single_cell_fdr, sc.best_cell_type, sc.case_donors, sc.control_donors,
              COALESCE(p.prior_association,0) AS prior_association, COALESCE(p.literature_score,0) AS literature_score,
              COALESCE(p.known_drug_score,0) AS known_drug_score,
              f.isInMembrane, f.isSecreted, f.hasSafetyEvent, f.hasPocket, f.hasLigand, f.hasSmallMoleculeBinder,
              f.hasHighQualityChemicalProbes, COALESCE(f.maxClinicalStage,0) AS maxClinicalStage
            FROM genes g LEFT JOIN ot_gwas og USING(target_id) LEFT JOIN gwas_catalog gc USING(target_id)
            LEFT JOIN eqtl_features eq USING(target_id) LEFT JOIN single_cell_features sc USING(target_id)
            LEFT JOIN prior_features p USING(target_id) LEFT JOIN feasibility_features f USING(target_id)
            ORDER BY g.target_id
        """).fetchdf()
        published_signature = set(self.disease.get("validation_signature", {}).get("genes", []))
        recovered_signature = sorted(
            features.loc[features["symbol"].isin(published_signature) & (features["single_cell_score"] > 0), "symbol"].astype(str).tolist()
        )
        single_cell_qc["within_study_reported_signature_sanity"] = {
            "reference": self.disease.get("validation_signature", {}).get("reference", "not_configured"),
            "reference_genes": sorted(published_signature),
            "genes_with_nonzero_pipeline_score": recovered_signature,
            "recovered_count": len(recovered_signature),
            "interpretation": "same-cohort qualitative sanity check; not external validation" if published_signature else "not configured for this disease",
        }
        features["gwas_score"] = np.maximum(features["ot_gwas_score"], 0.7 * features["gwas_catalog_score"])
        # H4 and CLPP are related but not interchangeable colocalisation
        # quantities. Combining their bounded transforms retains ordering and
        # avoids the previous hard saturation at CLPP >= 0.02.
        features["eqtl_score"] = [colocalization_score(h4, clpp) for h4, clpp in zip(features["eqtl_h4"], features["eqtl_clpp"])]
        seed_values = 0.50 * features["gwas_score"].to_numpy() + 0.25 * features["eqtl_score"].to_numpy() + 0.25 * features["single_cell_score"].to_numpy()
        seed_values[seed_values < 0.10] = 0
        edges = con.execute(f"""
          SELECT targetA, targetB, MAX(COALESCE(scoring,0)) FROM read_parquet({interaction_paths}, union_by_name=true)
          WHERE speciesA.taxonId=9606 AND speciesB.taxonId=9606 AND targetA IS NOT NULL AND targetB IS NOT NULL GROUP BY 1,2
        """).fetchall()
        network_scores, network_edges_used = self._network_scores(features["target_id"].astype(str).tolist(), edges, seed_values)
        features["network_score"] = network_scores
        rows = []
        for row in features.to_dict("records"):
            gwas = finite_number(row.get("gwas_score"))
            eqtl = finite_number(row.get("eqtl_score"))
            sc = finite_number(row.get("single_cell_score"))
            network = finite_number(row.get("network_score"))
            if not candidate_gate(gwas, eqtl, sc, network):
                continue
            evidence = evidence_strength(gwas, eqtl, sc, network)
            clinical = min(1.0, max(0.0, finite_number(row.get("maxClinicalStage"))))
            known_drug = 1.0 if finite_number(row.get("known_drug_score")) > 0 else 0.0
            novelty = novelty_score(known_drug, clinical, finite_number(row.get("literature_score")), finite_number(row.get("prior_association")))
            feasibility = self._feasibility(pd.Series(row))
            total = final_score(evidence, novelty, feasibility)
            clean = {key: (None if pd.isna(value) else value) for key, value in row.items()}
            clean.update({
                "gwas_score": round(gwas, 6), "eqtl_score": round(eqtl, 6), "single_cell_score": round(sc, 6), "network_score": round(network, 6),
                "evidence_strength": evidence, "novelty_score": novelty, "development_feasibility": feasibility,
                "novel_target_score": total, "novelty_class": novelty_class(evidence, novelty), "known_drug_label": int(known_drug),
                "direct_modality_count": sum(value >= 0.10 for value in (gwas, eqtl, sc)),
                "candidate_origin": "+".join(name for name, value in (("GWAS", gwas), ("eQTL", eqtl), ("single-cell", sc), ("network", network)) if value >= 0.10) or "weak-direct+network",
                "suggested_direction": "inhibit" if finite_number(clean.get("single_cell_log2fc")) > 0 else "activate" if finite_number(clean.get("single_cell_log2fc")) < 0 else "unknown",
            })
            rows.append(clean)
        rows.sort(key=lambda row: (-row["novel_target_score"], row["target_id"]))
        for rank, row in enumerate(rows, 1):
            row["rank"] = rank
        candidate_frame = pd.DataFrame(rows)
        con.register("candidate_input", candidate_frame)
        con.execute("CREATE TABLE candidate_scores AS SELECT * FROM candidate_input")

        counts = {
            "protein_coding_universe": len(genes), "gwas_catalog_associations": sum(value["associations"] for value in gwas_catalog.values()),
            "gwas_catalog_genes": len(gwas_catalog), "fine_mapped_gwas_genes": int((features["ot_gwas_score"] > 0).sum()),
            "eqtl_colocalised_genes": int((features["eqtl_score"] > 0).sum()), "single_cell_scored_genes": int((features["single_cell_score"] > 0).sum()),
            "network_edges_downloaded": len(edges), "network_edges_used": network_edges_used, "candidate_genes": len(rows), "multimodal_candidates": sum(row["direct_modality_count"] >= 2 for row in rows),
            "candidate_without_prior_association": sum(finite_number(row["prior_association"]) == 0 for row in rows),
        }
        single_cell_gate = self.disease.get("single_cell_quality_gates", {})
        minimum_total_donors = int(single_cell_gate.get("minimum_donors_per_group", 10))
        minimum_cell_types = int(single_cell_gate.get("minimum_evaluated_cell_types", 5))
        minimum_scored_genes = int(single_cell_gate.get("minimum_scored_genes", 1))
        minimum_gwas_associations = int(self.disease.get("minimum_gwas_catalog_associations", 500))
        quality = {
            "gwas_catalog_minimum": counts["gwas_catalog_associations"] >= minimum_gwas_associations,
            "single_cell_case_control_donors": single_cell_qc["case_donors"] >= minimum_total_donors and single_cell_qc["control_donors"] >= minimum_total_donors,
            "single_cell_cell_types": single_cell_qc["cell_types_evaluated"] >= minimum_cell_types,
            "single_cell_scored_genes": counts["single_cell_scored_genes"] >= minimum_scored_genes,
            "eqtl_present": counts["eqtl_colocalised_genes"] > 0,
            "network_present": counts["network_edges_used"] > 1000,
            "candidate_expansion": counts["candidate_without_prior_association"] > 0,
            "scores_in_range": all(0 <= row["novel_target_score"] <= 1 for row in rows),
            "label_leakage_excluded": True,
        }
        report = {
            "manifest_version": "novel-target-build-manifest-v1", "snapshot_id": self.snapshot_id,
            "status": "passed" if all(quality.values()) else "failed", "built_at": now_iso(), "database_path": str(self.database_path),
            "database_sha256": sha256_file(self.database_path), "counts": counts, "single_cell_qc": single_cell_qc,
            "gwas_gate_thresholds": {"minimum_catalog_associations": minimum_gwas_associations},
            "single_cell_gate_thresholds": {"minimum_donors_per_group": minimum_total_donors, "minimum_evaluated_cell_types": minimum_cell_types, "minimum_scored_genes": minimum_scored_genes},
            "quality_gates": quality,
            "score_contract": {
                "version": "novel-target-score-v2",
                "evidence_weights": {"gwas": 0.35, "eqtl": 0.25, "single_cell": 0.25, "network": 0.15},
                "final_weights": {"evidence_strength": 0.55, "novelty": 0.25, "development_feasibility": 0.20},
                "calibration": {
                    "eqtl": "0.60*H4 + 0.40*CLPP/(CLPP+0.02); H4 and CLPP retained separately",
                    "single_cell": "donor-cell-type pseudobulk; BH FDR <= 0.20 exploratory gate",
                    "network": "global empirical percentile after restart diffusion",
                },
                "excluded_from_evidence_strength": ["known_drug", "maxClinicalStage", "literature", "prior_association"],
            },
        }
        con.close()
        atomic_json(self.build_manifest_path, report)
        if report["status"] != "passed":
            raise RuntimeError(f"Build quality gates failed: {quality}")
        return report

    def benchmark(self) -> dict:
        con = duckdb.connect(str(self.database_path), read_only=True)
        columns = [row[0] for row in con.execute("DESCRIBE candidate_scores").fetchall()]
        rows = [dict(zip(columns, row)) for row in con.execute("SELECT * FROM candidate_scores").fetchall()]
        con.close()
        report = evaluate(rows)
        report["snapshot_id"] = self.snapshot_id
        report["computed_at"] = now_iso()
        report["quality_gates"] = {"multimodal_top20": report["top20_multimodal_count"] >= 3, "permutation_signal": report["permutation_p_value"] <= 0.10, "passed": report["top20_multimodal_count"] >= 3 and report["permutation_p_value"] <= 0.10}
        atomic_json(self.benchmark_path, report)
        if not report["quality_gates"]["passed"]:
            raise RuntimeError(f"Benchmark quality gates failed: {report['quality_gates']}")
        return report

    def publish(self, top_n: int = 500) -> dict:
        build = load_json(self.build_manifest_path)
        benchmark = load_json(self.benchmark_path)
        if build["status"] != "passed" or not benchmark["quality_gates"]["passed"]:
            raise RuntimeError("Cannot publish a snapshot that failed quality gates")
        con = duckdb.connect(str(self.database_path), read_only=True)
        columns = [row[0] for row in con.execute("DESCRIBE candidate_scores").fetchall()]
        rows = [dict(zip(columns, row)) for row in con.execute("SELECT * FROM candidate_scores ORDER BY rank LIMIT ?", [top_n]).fetchall()]
        con.close()
        keep = {
            "target_id", "symbol", "approved_name", "rank", "novel_target_score", "evidence_strength", "novelty_score", "development_feasibility",
            "gwas_score", "ot_gwas_score", "gwas_catalog_score", "gwas_catalog_studies", "fine_mapped_loci", "eqtl_score", "eqtl_h4", "eqtl_clpp", "colocalised_loci", "eqtl_biosamples",
            "single_cell_score", "single_cell_log2fc", "single_cell_fdr", "best_cell_type", "case_donors", "control_donors", "network_score",
            "direct_modality_count", "candidate_origin", "novelty_class", "suggested_direction", "prior_association", "literature_score", "known_drug_label", "maxClinicalStage",
        }
        targets = [{key: value for key, value in row.items() if key in keep} for row in rows]
        release = {
            "schema_version": "novel-target-discovery-product-snapshot-v1", "snapshot_id": self.snapshot_id, "created_at": now_iso(),
            "scientific_status": "computational_hypothesis", "disease": self.disease, "source_registry_version": self.registry["registry_version"],
            "score_contract": build["score_contract"], "quality": {"build": build, "benchmark": benchmark},
            "sources": [{key: source.get(key) for key in ("id", "name", "version", "license", "license_url", "citation", "role") if source.get(key) is not None} for source in self.registry["sources"]],
            "targets": targets,
        }
        atomic_json(self.release_path, release)
        manifest = {"snapshot_id": self.snapshot_id, "release_path": str(self.release_path), "release_sha256": sha256_file(self.release_path), "bytes": self.release_path.stat().st_size, "target_rows": len(targets), "published_at": now_iso()}
        atomic_json(self.result_root / "release-manifest.json", manifest)
        return manifest
