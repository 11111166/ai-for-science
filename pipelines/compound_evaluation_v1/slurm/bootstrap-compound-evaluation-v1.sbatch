#!/usr/bin/env bash
#SBATCH --job-name=ai4s-compound-env
#SBATCH --partition=LocalQ
#SBATCH --cpus-per-task=4
#SBATCH --mem=12G
#SBATCH --time=02:00:00
#SBATCH --output=/media/hdd/lijinpu/jingmiao/ai-drug-discovery/logs/compound-env-%j.out
#SBATCH --error=/media/hdd/lijinpu/jingmiao/ai-drug-discovery/logs/compound-env-%j.err

set -euo pipefail
umask 0077
project_root=/media/hdd/lijinpu/jingmiao/ai-drug-discovery
target="$project_root/envs/compound-evaluation-v1/packages"
mkdir -p "$target"
/usr/bin/python3 -m pip install --disable-pip-version-check --no-cache-dir --target "$target" -r "$project_root/src/compound_evaluation_v1/requirements.txt"

