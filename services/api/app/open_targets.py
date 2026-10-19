"""Open Targets disease-to-target adapter with explicit provenance boundaries."""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import re
import time
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy.orm import Session

from .config import get_settings
from .models import ExternalDataCache
from .storage import read_source_artifact, save_source_artifact


SOURCE = "Open Targets Platform"
REGISTRY_VERSION = "v1"
EVIDENCE_CATEGORY_REGISTRY: dict[str, dict[str, Any]] = {
    "genetics": {"configured": True, "datatypes": ["genetic_association", "genetic_literature"]},
    "expression": {"configured": True, "datatypes": ["rna_expression"]},
    "pathway": {"configured": True, "datatypes": ["affected_pathway"]},
    "functional": {"configured": True, "datatypes": ["animal_model", "somatic_mutation"]},
    # Current Open Targets releases expose clinical precedence as ``clinical``;
    # ``known_drug`` is retained for compatibility with earlier releases.
    "known_drug": {"configured": True, "datatypes": ["clinical", "known_drug"]},
    "literature": {"configured": True, "datatypes": ["literature"]},
    "safety": {"configured": False, "datatypes": []},
    "druggability": {"configured": False, "datatypes": []},
}
DERIVED_SCORE_CATEGORIES = ("genetics", "expression", "pathway", "functional", "literature")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_disease_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).strip().lower()
    value = re.sub(r"\b([a-z0-9]+)[’']s\b", r"\1", value)
    # Keep letters and numbers from every writing system. The previous ASCII-only
    # cleanup erased Chinese disease names completely, so a valid query such as
    # “类风湿关节炎” was incorrectly reported as too short.
    value = "".join(char if unicodedata.category(char)[:1] in {"L", "N"} else " " for char in value)
    return " ".join(value.split())


class OpenTargetsError(RuntimeError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(code)
        self.code = code
        self.message = message
        self.details = details or {}


class OpenTargetsAdapter:
    def __init__(self, db: Session, client: httpx.Client | None = None):
        self.db = db
        self.settings = get_settings()
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(
                connect=self.settings.open_targets_connect_timeout,
                read=self.settings.open_targets_read_timeout,
                write=self.settings.open_targets_read_timeout,
                pool=self.settings.open_targets_connect_timeout,
            )
        )
        self._owns_client = client is None
        self.metadata = self.get_source_metadata()

    def close(self):
        if self._owns_client:
            self.client.close()

    def _post(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(self.settings.open_targets_retries):
            try:
                response = self.client.post(
                    self.settings.open_targets_url,
                    json={"query": query, "variables": variables},
                    headers={"accept": "application/json"},
                )
                if response.status_code == 429 or response.status_code >= 500:
                    raise httpx.HTTPStatusError("retryable Open Targets response", request=response.request, response=response)
                response.raise_for_status()
                payload = response.json()
                if payload.get("errors"):
                    raise OpenTargetsError(
                        "OPEN_TARGETS_GRAPHQL_ERROR",
                        "Open Targets returned a GraphQL error.",
                        {"errors": payload["errors"]},
                    )
                if "data" not in payload:
                    raise OpenTargetsError("OPEN_TARGETS_SCHEMA_ERROR", "Open Targets response did not contain data.")
                return payload
            except OpenTargetsError:
                raise
            except (httpx.HTTPError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt + 1 < self.settings.open_targets_retries:
                    time.sleep(0.25 * (2**attempt))
        raise OpenTargetsError(
            "OPEN_TARGETS_UNAVAILABLE",
            "Open Targets could not be reached after bounded retries.",
            {"cause": type(last_error).__name__ if last_error else "unknown"},
        )

    def get_source_metadata(self) -> dict[str, str]:
        query = """
        query SourceMetadata {
          meta {
            product name dataPrefix
            apiVersion { x y z suffix }
            dataVersion { year month iteration }
          }
        }
        """
        meta = self._post(query, {})["data"]["meta"]
        data_version = ".".join(filter(None, [meta["dataVersion"]["year"], meta["dataVersion"]["month"], meta["dataVersion"].get("iteration")]))
        api_version = ".".join([meta["apiVersion"]["x"], meta["apiVersion"]["y"], meta["apiVersion"]["z"]])
        if meta["apiVersion"].get("suffix"):
            api_version += f"-{meta['apiVersion']['suffix']}"
        return {
            "source": SOURCE,
            "source_version": data_version,
            "api_version": api_version,
            "endpoint": self.settings.open_targets_url,
            "data_prefix": meta["dataPrefix"],
        }

    def _cached_query(self, operation: str, query: str, variables: dict[str, Any]) -> tuple[dict[str, Any], str, bool]:
        key_payload = {
            "source": SOURCE,
            "source_version": self.metadata["source_version"],
            "api_version": self.metadata["api_version"],
            "operation": operation,
            "variables": variables,
        }
        cache_key = hashlib.sha256(json.dumps(key_payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        cached = self.db.get(ExternalDataCache, cache_key)
        if cached:
            try:
                payload = json.loads(gzip.decompress(read_source_artifact(cached.storage_uri)))
                return payload, cached.storage_uri, True
            except (OSError, ValueError, json.JSONDecodeError):
                self.db.delete(cached)
                self.db.flush()

        payload = self._post(query, variables)
        encoded = gzip.compress(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
        storage_uri, digest = save_source_artifact(
            "open_targets",
            self.metadata["source_version"],
            cache_key,
            f"{operation}.json.gz",
            encoded,
        )
        self.db.add(
            ExternalDataCache(
                cache_key=cache_key,
                source=SOURCE,
                source_version=self.metadata["source_version"],
                api_version=self.metadata["api_version"],
                operation=operation,
                parameters=variables,
                storage_uri=storage_uri,
                content_hash=digest,
                retrieved_at=utcnow(),
            )
        )
        self.db.flush()
        return payload, storage_uri, False

    def resolve_disease(self, query_text: str) -> dict[str, Any]:
        normalized_query = normalize_disease_text(query_text)
        if len(normalized_query) < 2:
            raise OpenTargetsError("DISEASE_NOT_RESOLVED", "Disease query is too short.")
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9]*_[0-9]+", query_text.strip()):
            prefix, identifier = query_text.strip().split("_", 1)
            disease_id = ("Orphanet" if prefix.casefold() == "orphanet" else prefix.upper()) + "_" + identifier
            payload, raw_ref, cache_hit = self._cached_query("disease_details", """
            query DiseaseDetails($id: String!) {
              disease(efoId: $id) { id name synonyms { relation terms } }
            }
            """, {"id": disease_id})
            disease = payload["data"].get("disease")
            if not disease or disease["id"] != disease_id:
                raise OpenTargetsError("DISEASE_NOT_RESOLVED", "没有找到该标准疾病实体。")
            return {"query": query_text, "canonical_name": disease["name"], "disease_id": disease_id,
                    "ontology": disease_id.split("_", 1)[0], "synonyms": [term for group in disease.get("synonyms", []) for term in group.get("terms", [])],
                    "source": SOURCE, "resolution_status": "resolved", "resolution_confidence": 1.0,
                    "resolution_method": "open_targets_exact_ontology_id", "raw_reference": raw_ref, "cache_hit": cache_hit}
        search_query = """
        query DiseaseSearch($query: String!) {
          search(queryString: $query, entityNames: ["disease"], page: {index: 0, size: 5}) {
            total
            hits { id name entity score description }
          }
        }
        """
        search_payload, search_ref, cache_hit = self._cached_query("disease_search", search_query, {"query": normalized_query})
        hits = search_payload["data"]["search"]["hits"]
        if not hits:
            raise OpenTargetsError("DISEASE_NOT_RESOLVED", "No Open Targets disease matched the query.", {"query": query_text})

        disease_query = """
        query DiseaseDetails($id: String!) {
          disease(efoId: $id) { id name synonyms { relation terms } }
        }
        """
        candidates: list[dict[str, Any]] = []
        for hit in hits:
            payload, raw_ref, hit_cache = self._cached_query("disease_details", disease_query, {"id": hit["id"]})
            disease = payload["data"].get("disease")
            if not disease:
                continue
            synonyms = sorted({term for group in (disease.get("synonyms") or []) for term in group.get("terms", []) if term})
            canonical_match = normalize_disease_text(disease["name"]) == normalized_query
            synonym_match = any(normalize_disease_text(term) == normalized_query for term in synonyms)
            candidates.append(
                {
                    "id": disease["id"],
                    "name": disease["name"],
                    "synonyms": synonyms,
                    "canonical_match": canonical_match,
                    "synonym_match": synonym_match,
                    "search_score": hit.get("score"),
                    "raw_reference": raw_ref,
                    "cache_hit": hit_cache,
                }
            )

        canonical_matches = [item for item in candidates if item["canonical_match"]]
        synonym_matches = [item for item in candidates if item["synonym_match"]]
        exact = canonical_matches if len(canonical_matches) == 1 else synonym_matches if not canonical_matches else canonical_matches
        if len(exact) != 1:
            code = "DISEASE_AMBIGUOUS" if candidates else "DISEASE_NOT_RESOLVED"
            raise OpenTargetsError(
                code,
                "Disease query did not resolve to one exact canonical name or synonym.",
                {"query": query_text, "candidates": [{"id": item["id"], "name": item["name"]} for item in candidates]},
            )
        selected = exact[0]
        raw_exact = query_text.strip().casefold() == selected["name"].strip().casefold()
        confidence = 1.0 if raw_exact else 0.95 if selected["synonym_match"] else 0.90
        return {
            "query": query_text,
            "canonical_name": selected["name"],
            "disease_id": selected["id"],
            "ontology": selected["id"].split("_", 1)[0],
            "synonyms": selected["synonyms"],
            "source": SOURCE,
            "resolution_status": "resolved",
            "resolution_confidence": confidence,
            "resolution_method": "open_targets_canonical_or_synonym_exact_v1",
            "raw_reference": search_ref,
            "cache_hit": cache_hit and selected["cache_hit"],
        }

    def get_associated_targets(self, disease_id: str, top_n: int, include_indirect: bool) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        query = """
        query AssociatedTargets($id: String!, $indirect: Boolean!, $size: Int!) {
          disease(efoId: $id) {
            id name
            associatedTargets(enableIndirect: $indirect, page: {index: 0, size: $size}) {
              count
              rows {
                score novelty
                target { id approvedSymbol approvedName biotype }
                datatypeScores { id score }
                datasourceScores { id score }
              }
            }
          }
        }
        """
        payload, raw_ref, cache_hit = self._cached_query(
            "associated_targets",
            query,
            {"id": disease_id, "indirect": include_indirect, "size": top_n},
        )
        disease = payload["data"].get("disease")
        if not disease:
            raise OpenTargetsError("DISEASE_NOT_RESOLVED", "Resolved disease was not returned by Open Targets.")
        result = disease["associatedTargets"]
        return result["rows"], {"total": result["count"], "raw_reference": raw_ref, "cache_hit": cache_hit}

    def get_target_evidence(
        self,
        disease_id: str,
        target_ids: list[str],
        include_indirect: bool,
    ) -> tuple[list[dict[str, Any]], list[str], bool]:
        if not target_ids:
            return [], [], False
        query = """
        query TargetEvidence($id: String!, $targets: [String!]!, $indirect: Boolean!, $size: Int!, $cursor: String) {
          disease(efoId: $id) {
            evidences(ensemblIds: $targets, enableIndirect: $indirect, size: $size, cursor: $cursor) {
              count cursor
              rows {
                id score resourceScore datatypeId datasourceId confidence releaseVersion
                directionOnTarget directionOnTrait literature studyId publicationYear
                target { id approvedSymbol }
                disease { id name }
                drug { id name }
              }
            }
          }
        }
        """
        page_size = self.settings.open_targets_evidence_page_size
        cursor: str | None = None
        rows: list[dict[str, Any]] = []
        raw_references: list[str] = []
        all_cached = True
        while True:
            variables = {"id": disease_id, "targets": target_ids, "indirect": include_indirect, "size": page_size, "cursor": cursor}
            payload, raw_ref, cache_hit = self._cached_query("target_evidence", query, variables)
            evidence_page = payload["data"]["disease"]["evidences"]
            if evidence_page["count"] > self.settings.open_targets_max_evidence_records:
                raise OpenTargetsError(
                    "EVIDENCE_VOLUME_LIMIT",
                    "Evidence count exceeds the configured safety limit.",
                    {"count": evidence_page["count"], "limit": self.settings.open_targets_max_evidence_records},
                )
            page_rows = evidence_page["rows"]
            rows.extend([{**row, "_raw_reference": raw_ref} for row in page_rows])
            raw_references.append(raw_ref)
            all_cached = all_cached and cache_hit
            next_cursor = evidence_page.get("cursor")
            if not page_rows or len(page_rows) < page_size or not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor
        return rows, raw_references, all_cached

    def get_directional_evidence(
        self,
        disease_id: str,
        target_ids: list[str],
        include_indirect: bool = True,
        per_target_size: int = 25,
    ) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
        """Fetch a bounded directional subset without downloading all evidence.

        Large disease-target pairs can contain tens of thousands of literature
        rows.  Direction inference only needs sources that can carry signed
        target/trait effects, so every target is queried through an alias with a
        strict per-target cap.  Results are cached like other Open Targets calls.
        """
        valid_ids = list(dict.fromkeys(item for item in target_ids if re.fullmatch(r"ENSG\d+", str(item or ""))))
        if not valid_ids:
            return {}, {"target_count": 0, "directional_target_count": 0, "cache_hit": False, "raw_references": []}
        datasources = ["clinical_precedence", "gene_burden", "orphanet", "eva", "uniprot_variants"]
        output: dict[str, list[dict[str, Any]]] = defaultdict(list)
        raw_references: list[str] = []
        all_cached = True
        for start in range(0, len(valid_ids), 50):
            chunk = valid_ids[start:start + 50]
            fields = []
            for index, target_id in enumerate(chunk):
                fields.append(
                    f'e{index}: evidences(ensemblIds:["{target_id}"], enableIndirect:$indirect, '
                    'datasourceIds:$datasources, size:$size) {'
                    ' count rows { id score datatypeId datasourceId directionOnTarget directionOnTrait '
                    'target { id approvedSymbol } } }'
                )
            query = "query DirectionEvidence($id:String!,$indirect:Boolean!,$datasources:[String!]!,$size:Int!){disease(efoId:$id){" + " ".join(fields) + "}}"
            variables = {"id": disease_id, "indirect": include_indirect, "datasources": datasources, "size": per_target_size, "target_ids": chunk}
            # ``target_ids`` participates in the cache key but is not declared
            # in GraphQL, so only pass declared variables over the wire.
            cache_variables = dict(variables)
            wire_variables = {key: value for key, value in variables.items() if key != "target_ids"}
            cache_key_payload = {
                "source": SOURCE,
                "source_version": self.metadata["source_version"],
                "api_version": self.metadata["api_version"],
                "operation": "directional_evidence_v2",
                "variables": cache_variables,
            }
            cache_key = hashlib.sha256(json.dumps(cache_key_payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            cached = self.db.get(ExternalDataCache, cache_key)
            if cached:
                try:
                    payload = json.loads(gzip.decompress(read_source_artifact(cached.storage_uri)))
                    raw_ref, cache_hit = cached.storage_uri, True
                except (OSError, ValueError, json.JSONDecodeError):
                    self.db.delete(cached)
                    self.db.flush()
                    cached = None
            if not cached:
                payload = self._post(query, wire_variables)
                encoded = gzip.compress(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
                raw_ref, digest = save_source_artifact("open_targets", self.metadata["source_version"], cache_key, "directional_evidence_v2.json.gz", encoded)
                self.db.add(ExternalDataCache(cache_key=cache_key, source=SOURCE, source_version=self.metadata["source_version"], api_version=self.metadata["api_version"], operation="directional_evidence_v2", parameters=cache_variables, storage_uri=raw_ref, content_hash=digest, retrieved_at=utcnow()))
                self.db.flush()
                cache_hit = False
            disease = payload.get("data", {}).get("disease")
            if not disease:
                raise OpenTargetsError("DIRECTION_EVIDENCE_SCHEMA_ERROR", "Open Targets 未返回疾病方向证据。")
            for index, target_id in enumerate(chunk):
                for row in (disease.get(f"e{index}") or {}).get("rows") or []:
                    if row.get("directionOnTarget") and row.get("directionOnTrait"):
                        output[target_id].append({
                            "external_evidence_id": row.get("id"),
                            "score": row.get("score"),
                            "datatype": row.get("datatypeId"),
                            "datasource": row.get("datasourceId"),
                            "direction_on_target": row.get("directionOnTarget"),
                            "direction_on_trait": row.get("directionOnTrait"),
                            "raw_reference": f"{raw_ref}#{row.get('id')}" if row.get("id") else raw_ref,
                        })
            raw_references.append(raw_ref)
            all_cached = all_cached and cache_hit
        return dict(output), {
            "target_count": len(valid_ids),
            "directional_target_count": len(output),
            "datasources": datasources,
            "per_target_limit": per_target_size,
            "cache_hit": all_cached,
            "raw_references": raw_references,
            "source_version": self.metadata["source_version"],
            "api_version": self.metadata["api_version"],
        }

    def discover(
        self,
        query_text: str,
        top_n: int = 50,
        include_indirect: bool = True,
        include_evidence_records: bool = False,
    ) -> dict[str, Any]:
        if not 1 <= top_n <= 100:
            raise OpenTargetsError("INVALID_TOP_N", "top_n must be between 1 and 100.")
        resolution = self.resolve_disease(query_text)
        associations, association_meta = self.get_associated_targets(resolution["disease_id"], top_n, include_indirect)
        target_ids = [row["target"]["id"] for row in associations]
        evidence_rows: list[dict[str, Any]] = []
        evidence_refs: list[str] = []
        evidence_cached = True
        if include_evidence_records:
            evidence_rows, evidence_refs, evidence_cached = self.get_target_evidence(
                resolution["disease_id"], target_ids, include_indirect
            )
        evidence_by_target: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in evidence_rows:
            evidence_by_target[row["target"]["id"]].append(row)

        datatype_category = {
            datatype: category
            for category, spec in EVIDENCE_CATEGORY_REGISTRY.items()
            for datatype in spec["datatypes"]
        }
        normalized_targets: list[dict[str, Any]] = []
        normalized_evidence: list[dict[str, Any]] = []
        retrieved_at = utcnow()
        for rank, association in enumerate(associations, start=1):
            target = association["target"]
            datatype_scores = {item["id"]: float(item["score"]) for item in association["datatypeScores"]}
            datasource_scores = {item["id"]: float(item["score"]) for item in association["datasourceScores"]}
            breakdown: dict[str, dict[str, Any]] = {}
            for category, spec in EVIDENCE_CATEGORY_REGISTRY.items():
                values = [datatype_scores[item] for item in spec["datatypes"] if item in datatype_scores]
                status = "not_configured" if not spec["configured"] else "available" if values else "unavailable"
                breakdown[category] = {
                    "status": status,
                    "score": max(values) if values else None,
                    "datatypes": [item for item in spec["datatypes"] if item in datatype_scores],
                }
            available_count = sum(1 for value in breakdown.values() if value["status"] == "available")
            coverage = available_count / len(EVIDENCE_CATEGORY_REGISTRY)
            derived_values = [breakdown[item]["score"] for item in DERIVED_SCORE_CATEGORIES if breakdown[item]["status"] == "available"]
            derived_score = None
            if derived_values:
                derived_score = round((sum(derived_values) / len(derived_values)) * (len(derived_values) / len(DERIVED_SCORE_CATEGORIES)), 6)
            target_evidence = evidence_by_target.get(target["id"], [])
            source_count = len({item["datasourceId"] for item in target_evidence})
            normalized_targets.append(
                {
                    "external_id": target["id"],
                    "symbol": target["approvedSymbol"],
                    "name": target["approvedName"],
                    "biotype": target["biotype"],
                    "rank": rank,
                    "score": float(association["score"]),
                    "score_origin": "open_targets_association_source_score",
                    "derived_score": derived_score,
                    "evidence_coverage": round(coverage, 6),
                    "evidence_count": len(target_evidence) if include_evidence_records else None,
                    "source_count": source_count,
                    "evidence_breakdown": {
                        "registry_version": REGISTRY_VERSION,
                        "categories": breakdown,
                        "datatype_scores": datatype_scores,
                        "datasource_scores": datasource_scores,
                    },
                    "direction": "not_available",
                    "raw_reference": association_meta["raw_reference"],
                }
            )
            if include_evidence_records:
                for evidence in target_evidence:
                    datatype = evidence["datatypeId"]
                    category = datatype_category.get(datatype, "unmapped")
                    raw_ref = evidence.get("_raw_reference")
                    normalized_evidence.append(
                        {
                            "target_external_id": target["id"],
                            "external_evidence_id": evidence["id"],
                            "disease_id": resolution["disease_id"],
                            "evidence_type": "source_evidence",
                            "datatype": datatype,
                            "datasource": evidence["datasourceId"],
                            "category": category,
                            "source": evidence["datasourceId"],
                            "summary": f"Open Targets {datatype} evidence from {evidence['datasourceId']}.",
                            "score": float(evidence["score"]),
                            "direction_on_target": evidence.get("directionOnTarget"),
                            "direction_on_trait": evidence.get("directionOnTrait"),
                            "evidence_count": 1,
                            "confidence": evidence.get("confidence"),
                            "raw_reference": f"{raw_ref}#{evidence['id']}" if raw_ref else None,
                            "details": {
                                "aggregation_level": "individual_evidence",
                                "resource_score": evidence.get("resourceScore"),
                                "release_version": evidence.get("releaseVersion"),
                                "literature": evidence.get("literature") or [],
                                "study_id": evidence.get("studyId"),
                                "publication_year": evidence.get("publicationYear"),
                                "evidence_disease": evidence.get("disease"),
                                "drug": evidence.get("drug"),
                            },
                        }
                    )
            else:
                for datatype, datatype_score in datatype_scores.items():
                    normalized_evidence.append(
                        {
                            "target_external_id": target["id"],
                            "external_evidence_id": f"{resolution['disease_id']}:{target['id']}:datatype:{datatype}",
                            "disease_id": resolution["disease_id"],
                            "evidence_type": "datatype_source_score",
                            "datatype": datatype,
                            "datasource": None,
                            "category": datatype_category.get(datatype, "unmapped"),
                            "source": SOURCE,
                            "summary": f"Open Targets association score aggregated for datatype {datatype}.",
                            "score": datatype_score,
                            "direction_on_target": None,
                            "direction_on_trait": None,
                            "evidence_count": 1,
                            "confidence": None,
                            "raw_reference": association_meta["raw_reference"],
                            "details": {
                                "aggregation_level": "datatype",
                                "datasource_scores": datasource_scores,
                                "individual_evidence_materialized": False,
                            },
                        }
                    )

        benchmark = build_benchmark(normalized_targets)
        coverage_values = [item["evidence_coverage"] for item in normalized_targets]
        genetics_count = sum(1 for item in normalized_targets if item["evidence_breakdown"]["categories"]["genetics"]["status"] == "available")
        known_drug_count = sum(1 for item in normalized_targets if item["evidence_breakdown"]["categories"]["known_drug"]["status"] == "available")
        missing_distribution = {
            category: sum(1 for item in normalized_targets if item["evidence_breakdown"]["categories"][category]["status"] != "available")
            for category in EVIDENCE_CATEGORY_REGISTRY
        }
        warnings: list[str] = []
        if not normalized_targets:
            warnings.append("NO_ASSOCIATED_TARGETS")
        if benchmark["status"] != "computed":
            warnings.append("BENCHMARK_NOT_YET_VALIDATED")
        return {
            "metadata": self.metadata,
            "resolution": resolution,
            "parameters": {
                "top_n": top_n,
                "include_indirect": include_indirect,
                "include_evidence_records": include_evidence_records,
            },
            "targets": normalized_targets,
            "evidence": normalized_evidence,
            "data_quality": {
                "disease_resolution_status": resolution["resolution_status"],
                "canonical_disease_id": resolution["disease_id"],
                "associated_target_total": association_meta["total"],
                "targets_retrieved": len(normalized_targets),
                "normalized_evidence_records": len(normalized_evidence),
                "evidence_materialization": "individual" if include_evidence_records else "datatype_aggregates",
                "targets_with_genetics": genetics_count,
                "targets_with_known_drug": known_drug_count,
                "coverage": {
                    "minimum": min(coverage_values) if coverage_values else None,
                    "maximum": max(coverage_values) if coverage_values else None,
                    "mean": round(sum(coverage_values) / len(coverage_values), 6) if coverage_values else None,
                },
                "missing_distribution": missing_distribution,
                "source": SOURCE,
                "source_version": self.metadata["source_version"],
                "retrieved_at": retrieved_at.isoformat(),
                "warnings": warnings,
                "errors": [],
                "cache_hit": resolution["cache_hit"] and association_meta["cache_hit"] and evidence_cached,
            },
            "benchmark": benchmark,
            "warnings": warnings,
            "raw_reference": association_meta["raw_reference"],
            "retrieved_at": retrieved_at,
        }


def _metrics(order: list[str], references: set[str], k: int) -> dict[str, float]:
    cutoff = order[: min(k, len(order))]
    hits = sum(1 for item in cutoff if item in references)
    recall = hits / len(references) if references else 0.0
    precision = hits / len(cutoff) if cutoff else 0.0
    prevalence = len(references) / len(order) if order else 0.0
    enrichment = precision / prevalence if prevalence else 0.0
    dcg = sum((1.0 if item in references else 0.0) / math.log2(index + 2) for index, item in enumerate(cutoff))
    ideal_hits = min(len(references), len(cutoff))
    idcg = sum(1.0 / math.log2(index + 2) for index in range(ideal_hits))
    return {
        "recall": round(recall, 6),
        "precision": round(precision, 6),
        "enrichment": round(enrichment, 6),
        "ndcg": round(dcg / idcg, 6) if idcg else 0.0,
    }


def build_benchmark(targets: list[dict[str, Any]]) -> dict[str, Any]:
    references = {
        item["external_id"]
        for item in targets
        if item["evidence_breakdown"]["categories"]["known_drug"]["status"] == "available"
    }
    if not references:
        return {
            "status": "not_yet_validated",
            "reason": "No known-drug reference targets were present in the retrieved candidate set.",
            "reference_type": "open_targets_known_drug_retrospective_sanity_check",
        }
    source_order = [item["external_id"] for item in sorted(targets, key=lambda item: item["score"], reverse=True)]
    derived_order = [
        item["external_id"]
        for item in sorted(targets, key=lambda item: item["derived_score"] if item["derived_score"] is not None else -1.0, reverse=True)
    ]
    strategies: dict[str, Any] = {}
    for name, order in (("open_targets", source_order), ("transparent_evidence", derived_order)):
        at10 = _metrics(order, references, 10)
        at20 = _metrics(order, references, 20)
        strategies[name] = {
            "recall_at_10": at10["recall"],
            "recall_at_20": at20["recall"],
            "precision_at_20": at20["precision"],
            "enrichment_at_20": at20["enrichment"],
            "ndcg_at_20": at20["ndcg"],
        }
    return {
        "status": "computed",
        "reference_type": "open_targets_known_drug_retrospective_sanity_check",
        "reference_count": len(references),
        "candidate_count": len(targets),
        "limitations": [
            "This is not complete biological ground truth.",
            "Open Targets source ranking may include known-drug evidence; metrics are a retrospective sanity check only.",
            "The transparent evidence score excludes known-drug evidence from its score.",
        ],
        "strategies": strategies,
    }
