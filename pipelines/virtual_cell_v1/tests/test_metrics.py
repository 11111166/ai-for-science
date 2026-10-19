import numpy as np

from virtual_cell_v1.metrics import balanced_accuracy, percentile_interval, roc_auc


def test_metrics_are_deterministic_and_tie_safe():
    assert roc_auc([0, 0, 1, 1], [0.1, 0.2, 0.8, 0.9]) == 1.0
    assert roc_auc([0, 1], [0.5, 0.5]) == 0.5
    assert balanced_accuracy([0, 0, 1, 1], [0, 1, 1, 1]) == 0.75
    low, high = percentile_interval(np.arange(101))
    assert low == 2.5
    assert high == 97.5

