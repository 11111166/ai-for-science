"""Evaluation that does not pretend known drugs are ground truth for novelty."""

from __future__ import annotations

import math
import random


def _ndcg(rows: list[dict], score_key: str, label_key: str, k: int = 20) -> float:
    ordered = sorted(rows, key=lambda row: (-float(row.get(score_key) or 0), row["target_id"]))[:k]
    ideal = sorted((int(bool(row.get(label_key))) for row in rows), reverse=True)[:k]
    dcg = sum(int(bool(row.get(label_key))) / math.log2(i + 2) for i, row in enumerate(ordered))
    idcg = sum(label / math.log2(i + 2) for i, label in enumerate(ideal))
    return round(dcg / idcg, 6) if idcg else 0.0


def evaluate(rows: list[dict], permutations: int = 200, seed: int = 20260903) -> dict:
    ordered = sorted(rows, key=lambda row: (-float(row["novel_target_score"]), row["target_id"]))
    top20 = ordered[:20]
    multimodal = lambda row: sum(float(row.get(key) or 0) >= 0.10 for key in ("gwas_score", "eqtl_score", "single_cell_score")) >= 2
    pool_rate = sum(multimodal(row) for row in rows) / len(rows) if rows else 0.0
    top_rate = sum(multimodal(row) for row in top20) / len(top20) if top20 else 0.0
    observed = sum(multimodal(row) for row in top20)
    rng = random.Random(seed)
    null_counts = []
    sample_size = min(20, len(rows))
    for _ in range(permutations):
        # Keep the observed scoring/ranking fixed and compare its Top-k against
        # equally sized random candidate sets. Re-ranking shuffled modalities
        # would select on the same definition used by the multimodal label and
        # creates a mechanically enriched, invalid null distribution.
        null_top = rng.sample(rows, sample_size) if sample_size else []
        null_counts.append(sum(multimodal(row) for row in null_top))
    permutation_p = (1 + sum(value >= observed for value in null_counts)) / (permutations + 1)

    ablation = {}
    reference = {row["target_id"] for row in top20}
    for omitted in ("gwas_score", "eqtl_score", "single_cell_score", "network_score"):
        weights = {"gwas_score": 0.35, "eqtl_score": 0.25, "single_cell_score": 0.25, "network_score": 0.15}
        weights.pop(omitted)
        total = sum(weights.values())
        alternate = sorted(rows, key=lambda row: (-sum(float(row.get(k) or 0) * w for k, w in weights.items()) / total, row["target_id"]))[:20]
        alt_ids = {row["target_id"] for row in alternate}
        ablation[omitted] = round(len(reference & alt_ids) / len(reference | alt_ids), 6) if reference | alt_ids else 0.0

    return {
        "benchmark_version": "novel-target-discovery-benchmark-v1",
        "status": "computed" if rows else "not_computed",
        "candidate_count": len(rows),
        "top20_multimodal_count": observed,
        "top20_multimodal_rate": round(top_rate, 6),
        "pool_multimodal_rate": round(pool_rate, 6),
        "multimodal_enrichment_at_20": round(top_rate / pool_rate, 6) if pool_rate else 0.0,
        "permutation_p_value": round(permutation_p, 6),
        "permutation_null": "uniform random candidate sets of the observed Top-k size; ranking remains fixed",
        "top20_emerging_count": sum(row.get("novelty_class") == "emerging" for row in top20),
        "top20_prior_association_absent": sum(float(row.get("prior_association") or 0) == 0 for row in top20),
        "known_drug_ndcg_at_20_sanity_only": _ndcg(rows, "evidence_strength", "known_drug_label"),
        "ablation_top20_jaccard": ablation,
        "leakage_control": "known-drug, clinical stage, literature and prior association affect novelty only; they never enter evidence strength",
        "limitations": [
            "Known-drug recovery is a retrospective sanity check, not evidence of novelty.",
            "Open Targets fine-mapped GWAS L2G can incorporate colocalisation features, so GWAS and eQTL panels are complementary but not statistically independent replications.",
            "The single-cell cohort is blood-based and disease-specific; it does not represent synovial tissue.",
            "Network propagation is derived from the direct-evidence seeds, generates hypotheses and cannot establish causality.",
            "External cohort and temporal validation remain required before experimental nomination.",
        ],
    }
