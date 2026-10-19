#!/usr/bin/env bash
#SBATCH --job-name=ai4s-full-validation
#SBATCH --partition=LocalQ
#SBATCH --cpus-per-task=8
#SBATCH --mem=16G
#SBATCH --time=24:00:00
#SBATCH --output=/media/hdd/lijinpu/jingmiao/ai-drug-discovery/logs/full-validation-%x-%j.out
#SBATCH --error=/media/hdd/lijinpu/jingmiao/ai-drug-discovery/logs/full-validation-%x-%j.err

set -euo pipefail
umask 0077

if [[ $# -ne 2 ]]; then
  echo "usage: sbatch full-validation-v1.sbatch DISEASE_CONFIG SLUG" >&2
  exit 2
fi

project_root=/media/hdd/lijinpu/jingmiao/ai-drug-discovery
disease_config="$1"
slug="$2"
python=/usr/bin/python3
runtime_packages="$project_root/envs/novel-target-discovery-v1/packages"

json_value() {
  "$python" -c 'import json,sys; value=json.load(open(sys.argv[1]));
for key in sys.argv[2].split("."): value=value[key]
print(value)' "$disease_config" "$1"
}

source_value() {
  "$python" -c 'import json,sys; value=json.load(open(sys.argv[1])); row=next(x for x in value["source_overrides"] if x["kind"]=="file"); print(row[sys.argv[2]])' "$disease_config" "$1"
}

disease_id=$(json_value id)
novel_snapshot=$(json_value snapshot_id)
virtual_snapshot=$(json_value virtual_cell_snapshot_id)
cell_source=$(source_value id)
cell_version=$(source_value version)
cell_filename=$(source_value filename)
perturb_snapshot="perturb-ko-v2-${slug}-depmap24q4"
compound_snapshot="compound-eval-v1-${slug}-chembl37-tdc2021"

novel_root="$project_root/src/novel_target_discovery_v1"
export PYTHONPATH="$novel_root/src:$runtime_packages"
"$python" -m novel_target_discovery_v1.cli all --root "$project_root" --disease-config "$disease_config" --top-n 500

target_release="$project_root/results/$novel_snapshot/novel-target-discovery-v1.json"
perturb_root="$project_root/src/perturbation_knockout_v1"
export PYTHONPATH="$perturb_root/src:$runtime_packages"
"$python" -m perturbation_knockout_v1.cli all --root "$project_root" --target-snapshot "$target_release" --snapshot-id "$perturb_snapshot"

intervention_release="$project_root/results/$perturb_snapshot/perturbation-knockout-v1.json"
virtual_root="$project_root/src/virtual_cell_v1"
export PYTHONPATH="$virtual_root/src:$runtime_packages"
"$python" -m virtual_cell_v1.cli all --root "$project_root" \
  --h5ad "$project_root/data/raw/$cell_source/$cell_version/$cell_filename" \
  --target-snapshot "$target_release" \
  --intervention-snapshot "$intervention_release" \
  --disease-config "$disease_config"

compound_root="$project_root/src/compound_evaluation_v1"
compound_packages="$project_root/envs/compound-evaluation-v1/packages"
export PYTHONPATH="$compound_root/src:$compound_packages"
export OPENBLAS_NUM_THREADS=8
export OMP_NUM_THREADS=8
"$python" -m compound_evaluation_v1.cli all --root "$project_root" \
  --target-snapshot "$target_release" \
  --intervention-snapshot "$intervention_release" \
  --snapshot-id "$compound_snapshot"

established_release="$project_root/results/td-v2-25d-ot26.06-hgnc2026-08-07-hpa25.1/target-discovery-v1.json"
"$python" "$project_root/src/validation_milestone_v1/build_manifest.py" \
  --disease-id "$disease_id" \
  --output "$project_root/results/validation-milestone-v1/$slug/validation.json" \
  --artifact "established_target_to_hit=$established_release" \
  --artifact "novel_target=$target_release" \
  --artifact "intervention_knockout=$intervention_release" \
  --artifact "virtual_cell=$project_root/results/$virtual_snapshot/virtual-cell-v1.json" \
  --artifact "compound_evaluation=$project_root/results/$compound_snapshot/compound-evaluation-v1.json"

echo "COMPLETE_VALIDATION disease=$disease_id slug=$slug"
