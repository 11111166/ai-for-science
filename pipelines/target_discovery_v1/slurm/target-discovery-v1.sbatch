#!/usr/bin/env bash
#SBATCH --job-name=ai4s-td-v1
#SBATCH --partition=LocalQ
#SBATCH --cpus-per-task=8
#SBATCH --mem=16G
#SBATCH --time=04:00:00
#SBATCH --output=/media/hdd/lijinpu/jingmiao/ai-drug-discovery/logs/target-discovery-v1-%j.out
#SBATCH --error=/media/hdd/lijinpu/jingmiao/ai-drug-discovery/logs/target-discovery-v1-%j.err

set -euo pipefail
umask 0077

project_root=/media/hdd/lijinpu/jingmiao/ai-drug-discovery
pipeline_root="$project_root/src/target_discovery_v1"
runtime_packages="$project_root/envs/target-discovery-v1/packages"

export PYTHONPATH="$pipeline_root/src:$runtime_packages"
/usr/bin/python3 -m target_discovery_v1.cli all --root "$project_root" --top-n 100
