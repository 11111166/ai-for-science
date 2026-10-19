#!/usr/bin/env bash
#SBATCH --job-name=ai4s-novel-td-v2
#SBATCH --partition=LocalQ
#SBATCH --cpus-per-task=12
#SBATCH --mem=32G
#SBATCH --time=08:00:00
#SBATCH --output=/media/hdd/lijinpu/jingmiao/ai-drug-discovery/logs/novel-target-discovery-v1-%j.out
#SBATCH --error=/media/hdd/lijinpu/jingmiao/ai-drug-discovery/logs/novel-target-discovery-v1-%j.err

set -euo pipefail
umask 0077

project_root=/media/hdd/lijinpu/jingmiao/ai-drug-discovery
pipeline_root="$project_root/src/novel_target_discovery_v1"
runtime_packages="$project_root/envs/novel-target-discovery-v1/packages"

export PYTHONPATH="$pipeline_root/src:$runtime_packages"
/usr/bin/python3 -m novel_target_discovery_v1.cli all --root "$project_root" --top-n 500
