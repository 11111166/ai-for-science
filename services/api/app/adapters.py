"""Traceable demonstration adapters. Values are illustrative and not scientific conclusions."""

TARGETS = [
    {"symbol": "TGFB1", "name": "Transforming growth factor beta 1", "score": 0.92, "direction": "inhibit"},
    {"symbol": "ITGB6", "name": "Integrin subunit beta 6", "score": 0.86, "direction": "inhibit"},
    {"symbol": "MUC5B", "name": "Mucin 5B", "score": 0.78, "direction": "context-dependent"},
    {"symbol": "DSP", "name": "Desmoplakin", "score": 0.71, "direction": "context-dependent"},
    {"symbol": "AKAP13", "name": "A-kinase anchoring protein 13", "score": 0.66, "direction": "investigate"},
]

COMPOUNDS = [
    {"name": "Nintedanib", "identifier": "IPF-DEMO-001", "mechanism": "Approved IPF therapy; included for workflow demonstration", "match_score": 0.88},
    {"name": "Pirfenidone", "identifier": "IPF-DEMO-002", "mechanism": "Approved IPF therapy; included for workflow demonstration", "match_score": 0.82},
    {"name": "Illustrative compound A", "identifier": "IPF-DEMO-003", "mechanism": "Synthetic candidate, not a real recommendation", "match_score": 0.64},
]

PROVENANCE = {
    "label": "Illustrative IPF precomputed demonstration dataset",
    "intended_use": "Product workflow validation only",
    "scientific_conclusion": False,
    "version": "ipf-demo-2026.1",
}

# Synthetic ranked perturbation fixtures used to exercise a real, transparent
# CMap-style rank-enrichment calculation. The gene values are still demo data.
IPF_DISEASE_SIGNATURE = {
    "up": ["COL1A1", "COL3A1", "FN1", "ACTA2", "CTGF", "SERPINE1", "TGFB1", "ITGB6"],
    "down": ["SFTPC", "SFTPB", "ABCA3", "AGER", "CAV1", "HOPX", "PDPN", "SCGB1A1"],
}

PERTURBATION_SIGNATURES = {
    "TGFB1": ["SFTPC", "SFTPB", "ABCA3", "AGER", "CAV1", "HOPX", "GAPDH", "RPLP0", "PDPN", "SCGB1A1", "MKI67", "VIM", "ITGB6", "SERPINE1", "TGFB1", "CTGF", "ACTA2", "FN1", "COL3A1", "COL1A1"],
    "ITGB6": ["SFTPC", "ABCA3", "AGER", "SFTPB", "HOPX", "GAPDH", "CAV1", "PDPN", "RPLP0", "SCGB1A1", "MKI67", "VIM", "TGFB1", "ITGB6", "SERPINE1", "ACTA2", "CTGF", "COL3A1", "FN1", "COL1A1"],
    "MUC5B": ["SFTPC", "AGER", "ABCA3", "GAPDH", "HOPX", "SFTPB", "MKI67", "CAV1", "PDPN", "RPLP0", "SCGB1A1", "VIM", "MUC5B", "ITGB6", "TGFB1", "SERPINE1", "COL3A1", "ACTA2", "FN1", "COL1A1", "CTGF"],
    "DSP": ["AGER", "SFTPC", "GAPDH", "ABCA3", "HOPX", "RPLP0", "SFTPB", "MKI67", "CAV1", "PDPN", "SCGB1A1", "VIM", "DSP", "TGFB1", "ITGB6", "SERPINE1", "COL3A1", "ACTA2", "FN1", "COL1A1", "CTGF"],
    "AKAP13": ["SFTPC", "GAPDH", "AGER", "RPLP0", "ABCA3", "HOPX", "MKI67", "SFTPB", "CAV1", "PDPN", "SCGB1A1", "VIM", "AKAP13", "TGFB1", "ITGB6", "SERPINE1", "ACTA2", "COL3A1", "FN1", "CTGF", "COL1A1"],
}
