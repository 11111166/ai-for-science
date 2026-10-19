"""Leakage-aware retrospective ranking metrics for Target Discovery v1."""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Iterable


def _rank_metrics(rows: list[dict], score_key: str, k: int = 20) -> dict[str, float]:
    ordered = sorted(rows, key=lambda row: (-float(row.get(score_key) or 0.0), row["target_id"]))
    labels = [int(bool(row.get("known_drug_label"))) for row in ordered]
    positives = sum(labels)
    cutoff = labels[: min(k, len(labels))]
    hits = sum(cutoff)
    precision = hits / len(cutoff) if cutoff else 0.0
    recall = hits / positives if positives else 0.0
    prevalence = positives / len(labels) if labels else 0.0
    dcg = sum(label / math.log2(index + 2) for index, label in enumerate(cutoff))
    ideal = sum(1.0 / math.log2(index + 2) for index in range(min(positives, len(cutoff))))

    tp = 0
    precisions = []
    for index, label in enumerate(labels, start=1):
        if label:
            tp += 1
            precisions.append(tp / index)
    average_precision = sum(precisions) / positives if positives else 0.0
    return {
        f"recall_at_{k}": round(recall, 6),
        f"precision_at_{k}": round(precision, 6),
        f"enrichment_at_{k}": round(precision / prevalence, 6) if prevalence else 0.0,
        f"ndcg_at_{k}": round(dcg / ideal, 6) if ideal else 0.0,
        "average_precision": round(average_precision, 6),
    }


def _matched_control_win_rate(rows: list[dict], score_key: str) -> dict[str, float | int]:
    """Pair each known-drug target to the closest unlabeled target in the same biotype."""
    positives = sorted((row for row in rows if row.get("known_drug_label")), key=lambda row: row["target_id"])
    negatives = [row for row in rows if not row.get("known_drug_label")]
    wins = 0.0
    pairs = 0
    for positive in positives:
        pool = [row for row in negatives if row.get("biotype") == positive.get("biotype")] or negatives
        if not pool:
            continue
        control = min(
            pool,
            key=lambda row: (
                abs(float(row.get("development_feasibility") or 0.0) - float(positive.get("development_feasibility") or 0.0)),
                row["target_id"],
            ),
        )
        difference = float(positive.get(score_key) or 0.0) - float(control.get(score_key) or 0.0)
        wins += 1.0 if difference > 0 else 0.5 if difference == 0 else 0.0
        pairs += 1
    return {"matched_control_pairs": pairs, "matched_control_win_rate": round(wins / pairs, 6) if pairs else 0.0}


def evaluate(rows: Iterable[dict]) -> dict:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[row["disease_id"]].append(row)

    disease_results = []
    for disease_id, disease_rows in sorted(grouped.items()):
        positives = sum(bool(row.get("known_drug_label")) for row in disease_rows)
        strategies = {}
        for key in ("discovery_score", "causal_confidence", "disease_context", "development_feasibility"):
            strategies[key] = {**_rank_metrics(disease_rows, key), **_matched_control_win_rate(disease_rows, key)}
        disease_results.append(
            {
                "disease_id": disease_id,
                "disease_name": disease_rows[0]["disease_name"],
                "therapeutic_area": disease_rows[0]["therapeutic_area"],
                "candidate_count": len(disease_rows),
                "positive_count": positives,
                "status": "computed" if positives else "no_positive_reference",
                "strategies": strategies,
            }
        )

    computed = [row for row in disease_results if row["status"] == "computed"]
    metric_names = ["recall_at_20", "precision_at_20", "enrichment_at_20", "ndcg_at_20", "average_precision", "matched_control_win_rate"]
    macro = {}
    for strategy in ("discovery_score", "causal_confidence", "disease_context", "development_feasibility"):
        macro[strategy] = {
            metric: round(sum(row["strategies"][strategy][metric] for row in computed) / len(computed), 6)
            if computed else 0.0
            for metric in metric_names
        }
    return {
        "benchmark_version": "target-discovery-benchmark-v1",
        "status": "computed" if computed else "not_yet_validated",
        "reference_type": "open_targets_known_drug_held_out_retrospective_label",
        "leakage_control": "known_drug and maxClinicalStage are excluded from all score features",
        "limitations": [
            "Known-drug evidence is a retrospective sanity-check label, not complete biological ground truth.",
            "The reference and features originate from the same Open Targets release, although the label is held out of scoring.",
            "Targets without a known drug are unlabeled, not proven biological negatives.",
        ],
        "diseases_computed": len(computed),
        "macro": macro,
        "diseases": disease_results,
    }
