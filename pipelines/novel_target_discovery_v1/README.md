# Novel Target Discovery v1

> 生产状态：RA v2 已通过 RCEES Slurm Job `9081` 并上线。完整候选池 10,731 个，发布 500 个；Top 20 多模态 20/20，置换 `P=0.004975`。产品运行参数仍可只返回前 50/100/200 个。

This pipeline generates rheumatoid-arthritis target hypotheses from independent GWAS, eQTL colocalisation, donor-aware disease single-cell expression and bounded protein-network propagation. It does not require a candidate to already have an Open Targets disease association.

## Scientific contract

- Candidate universe: annotated human protein-coding genes.
- GWAS: NHGRI-EBI GWAS Catalog associations plus Open Targets fine-mapped GWAS-to-gene evidence.
- eQTL: disease GWAS loci colocalised with molecular-QTL credible sets in either storage orientation and linked to QTL genes. v2 preserves H4 and CLPP and uses `0.60 × H4 + 0.40 × CLPP/(CLPP+0.02)` for continuous calibration.
- Single cell: case/control PBMC H5AD analysed by donor × cell-type pseudobulk, log-CPM, Welch test and Benjamini-Hochberg FDR. Individual cells are not treated as independent replicates.
- Network: restart random walk on a filtered human interaction graph; network-only genes cannot pass the candidate gate.
- Evidence strength: 35% GWAS + 25% eQTL + 25% single cell + 15% network.
- Final score: 55% evidence strength + 25% novelty + 20% development feasibility.
- Known drugs, clinical stage, literature and prior association are excluded from evidence strength. They only describe novelty/maturity.

All outputs are computational hypotheses. External cohort replication and experimental validation are still required.
Open Targets L2G may itself include colocalisation-derived features, so the GWAS and
eQTL panels are complementary evidence views, not statistically independent replications.
Likewise, the network score is propagated from direct-evidence seeds and is not an
independent modality.

## Run

```bash
export PYTHONPATH="$PWD/pipelines/novel_target_discovery_v1/src"
python -m novel_target_discovery_v1.cli all --root /path/to/isolated/root --top-n 500
```

On RCEES, submit `slurm/bootstrap-novel-target-v1.sbatch` once, then submit
`slurm/novel-target-discovery-v1.sbatch`. Both jobs are isolated under this
project; do not run the full build on the login node.
