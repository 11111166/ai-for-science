# Target Discovery v1 offline pipeline

> 生产状态：已通过 RCEES Slurm Job `9073` 构建并上线。快照 `td-v1-ot26.06-hgnc2026-08-07-hpa25.1`，覆盖 10 个疾病；详细指标见 [`../../docs/TARGET_DISCOVERY_V1.md`](../../docs/TARGET_DISCOVERY_V1.md)。

This pipeline builds the first independently evaluable scientific module of AI for Science. It is intentionally a product baseline rather than a SOTA model.

It produces:

- a versioned, checksummed source snapshot;
- HGNC/Ensembl/UniProt identity mapping;
- disease-target features from Open Targets direct associations;
- disease-context, causal-evidence and development-feasibility axes;
- a leakage-controlled retrospective benchmark in which known-drug evidence is a label, never a score input;
- a compact JSON release that the lightweight Business Cloud API can consume.

The full raw snapshot and DuckDB database belong on RCEES. Only the compact JSON release is suitable for `jingmiao`.

## Commands

```bash
python -m target_discovery_v1.cli doctor --root /media/hdd/lijinpu/jingmiao/ai-drug-discovery
python -m target_discovery_v1.cli download --root /media/hdd/lijinpu/jingmiao/ai-drug-discovery
python -m target_discovery_v1.cli build --root /media/hdd/lijinpu/jingmiao/ai-drug-discovery
python -m target_discovery_v1.cli benchmark --root /media/hdd/lijinpu/jingmiao/ai-drug-discovery
python -m target_discovery_v1.cli publish --root /media/hdd/lijinpu/jingmiao/ai-drug-discovery
```

The Slurm entry point `slurm/target-discovery-v1.sbatch` runs all five gates. Dependencies are installed into the project-owned `envs/target-discovery-v1/packages` directory because the shared server's system Python does not include `ensurepip`; no system package is changed. Four bounded download workers use resumable partial files and each completed artifact gets a SHA-256 digest. A release is published only after all validation gates pass.

## Scientific boundary

The three axes are deterministic, inspectable baselines:

1. `causal_confidence`: genetics, curated genetic literature and somatic mutation;
2. `disease_context`: disease-linked RNA expression, affected pathways and animal models;
3. `development_feasibility`: pockets, ligands, membrane/secreted status, chemical probes and expression selectivity, with a safety-event penalty.

`discovery_score = 0.50 * causal + 0.30 * context + 0.20 * feasibility`.

The v1 product universe is intentionally restricted to protein-coding targets with at least one of the six configured causal/context evidence types. Literature-only and known-drug-only rows cannot create candidates. Missing source rows are preserved in coverage fields. Clinical stage, generic literature and known-drug evidence are excluded from every score input. This release ranks hypotheses; it does not claim experimental or clinical validation.
