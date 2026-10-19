#!/usr/bin/env bash
#SBATCH --job-name=ai4s-perturb-ko-v2
#SBATCH --partition=LocalQ
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=03:00:00
#SBATCH --output=/media/hdd/lijinpu/jingmiao/ai-drug-discovery/logs/perturbation-knockout-v1-%j.out
#SBATCH --error=/media/hdd/lijinpu/jingmiao/ai-drug-discovery/logs/perturbation-knockout-v1-%j.err

set -euo pipefail
umask 0077

project_root=/media/hdd/lijinpu/jingmiao/ai-drug-discovery
pipeline_root="$project_root/src/perturbation_knockout_v1"
runtime_packages="$project_root/envs/novel-target-discovery-v1/packages"
target_snapshot="$project_root/results/novel-td-v2-ra-ot26.06-gwas2026-09-03-cxg2026-06-11/novel-target-discovery-v1.json"

export PYTHONPATH="$pipeline_root/src:$runtime_packages"
/usr/bin/python3 -m perturbation_knockout_v1.cli all --root "$project_root" --target-snapshot "$target_snapshot"
