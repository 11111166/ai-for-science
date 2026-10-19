from __future__ import annotations

import numpy as np


def roc_auc(labels: list[int] | np.ndarray, scores: list[float] | np.ndarray) -> float:
    labels = np.asarray(labels, dtype=int)
    scores = np.asarray(scores, dtype=float)
    positives = int(labels.sum())
    negatives = int(len(labels) - positives)
    if not positives or not negatives:
        return float("nan")
    order = np.argsort(scores, kind="mergesort")
    ranks = np.empty(len(scores), dtype=float)
    sorted_scores = scores[order]
    start = 0
    while start < len(scores):
        end = start + 1
        while end < len(scores) and sorted_scores[end] == sorted_scores[start]:
            end += 1
        ranks[order[start:end]] = (start + 1 + end) / 2
        start = end
    return float((ranks[labels == 1].sum() - positives * (positives + 1) / 2) / (positives * negatives))


def balanced_accuracy(labels: list[int] | np.ndarray, predictions: list[int] | np.ndarray) -> float:
    labels = np.asarray(labels, dtype=int)
    predictions = np.asarray(predictions, dtype=int)
    sensitivity = float((predictions[labels == 1] == 1).mean()) if np.any(labels == 1) else float("nan")
    specificity = float((predictions[labels == 0] == 0).mean()) if np.any(labels == 0) else float("nan")
    return (sensitivity + specificity) / 2


def percentile_interval(values: list[float] | np.ndarray, lower: float = 2.5, upper: float = 97.5) -> tuple[float, float]:
    finite = np.asarray(values, dtype=float)
    finite = finite[np.isfinite(finite)]
    if not len(finite):
        return float("nan"), float("nan")
    return float(np.percentile(finite, lower)), float(np.percentile(finite, upper))

