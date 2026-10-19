#!/usr/bin/env bash
#SBATCH --job-name=ai4s-compound-v1
#SBATCH --partition=LocalQ
#SBATCH --cpus-per-task=8
#SBATCH --mem=24G
#SBATCH --time=08:00:00
#SBATCH --output=/media/hdd/lijinpu/jingmiao/ai-drug-discovery/logs/compound-evaluation-v1-%j.out
#SBATCH --error=/media/hdd/lijinpu/jingmiao/ai-drug-discovery/logs/compound-evaluation-v1-%j.err

set -euo pipefail
umask 0077
project_root=/media/hdd/lijinpu/jingmiao/ai-drug-discovery
pipeline_root="$project_root/src/compound_evaluation_v1"
runtime_packages="$project_root/envs/compound-evaluation-v1/packages"
export PYTHONPATH="$pipeline_root/src:$runtime_packages"
export OPENBLAS_NUM_THREADS=8
export OMP_NUM_THREADS=8

/usr/bin/python3 -m compound_evaluation_v1.cli all \
  --root "$project_root" \
  --target-snapshot "$project_root/results/novel-td-v2-ra-ot26.06-gwas2026-09-03-cxg2026-06-11/novel-target-discovery-v1.json" \
  --intervention-snapshot "$project_root/results/perturb-ko-v2-ra-depmap24q4/perturbation-knockout-v1.json"

