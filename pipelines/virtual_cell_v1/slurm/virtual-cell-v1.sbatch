#!/usr/bin/env bash
#SBATCH --job-name=ai4s-virtual-cell-v1
#SBATCH --partition=LocalQ
#SBATCH --cpus-per-task=8
#SBATCH --mem=24G
#SBATCH --time=04:00:00
#SBATCH --output=/media/hdd/lijinpu/jingmiao/ai-drug-discovery/logs/virtual-cell-v1-%j.out
#SBATCH --error=/media/hdd/lijinpu/jingmiao/ai-drug-discovery/logs/virtual-cell-v1-%j.err

set -euo pipefail
umask 0077

project_root=/media/hdd/lijinpu/jingmiao/ai-drug-discovery
pipeline_root="$project_root/src/virtual_cell_v1"
runtime_packages="$project_root/envs/novel-target-discovery-v1/packages"
export PYTHONPATH="$pipeline_root/src:$runtime_packages"

/usr/bin/python3 -m virtual_cell_v1.cli all \
  --root "$project_root" \
  --h5ad "$project_root/data/raw/cellxgene_ra_pbmc/dataset-revised-2026-06-11/ra-pbmc-3b751975.h5ad" \
  --target-snapshot "$project_root/results/novel-td-v2-ra-ot26.06-gwas2026-09-03-cxg2026-06-11/novel-target-discovery-v1.json" \
  --intervention-snapshot "$project_root/results/perturb-ko-v2-ra-depmap24q4/perturbation-knockout-v1.json"

