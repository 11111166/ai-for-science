#!/usr/bin/env bash
#SBATCH --job-name=ai4s-novel-td-env
#SBATCH --partition=LocalQ
#SBATCH --cpus-per-task=2
#SBATCH --mem=4G
#SBATCH --time=01:00:00
#SBATCH --output=/media/hdd/lijinpu/jingmiao/ai-drug-discovery/logs/novel-target-bootstrap-%j.out
#SBATCH --error=/media/hdd/lijinpu/jingmiao/ai-drug-discovery/logs/novel-target-bootstrap-%j.err

set -euo pipefail
umask 0077

project_root=/media/hdd/lijinpu/jingmiao/ai-drug-discovery
pipeline_root="$project_root/src/novel_target_discovery_v1"
runtime_packages="$project_root/envs/novel-target-discovery-v1/packages"

mkdir -p "$runtime_packages" "$project_root/logs"
/usr/bin/python3 -m pip install \
  --disable-pip-version-check \
  --no-cache-dir \
  --target "$runtime_packages" \
  -r "$pipeline_root/requirements.txt"

PYTHONPATH="$pipeline_root/src:$runtime_packages" /usr/bin/python3 - <<'PY'
import anndata, duckdb, h5py, numpy, pandas, scipy
print({
    "anndata": anndata.__version__,
    "duckdb": duckdb.__version__,
    "h5py": h5py.__version__,
    "numpy": numpy.__version__,
    "pandas": pandas.__version__,
    "scipy": scipy.__version__,
})
PY
