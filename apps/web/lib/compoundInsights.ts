import type { Compound } from "@drug-discovery/contracts";

export type CompoundMatch = { experimental_records?: { standard_type?:string; standard_value?:string; standard_relation?:string; standard_units?:string; assay_chembl_id?:string }[]; target_symbol?: string; target_name?: string; target_chembl_id?: string; intervention_direction?: string; pchembl_best?: number; pchembl_median?: number; activity_count?: number; assay_count?: number; document_count?: number; activity_types?: string[]; mechanism?: string; direction_note?: string; direction_evidence?: number; match_score?: number };
export type CompoundDetails = { molecule_type?:string; canonical_smiles?: string; max_phase?: number; first_approval?: number; oral?: boolean; black_box_warning?: boolean; withdrawn_flag?: boolean; evaluation_tier?: "locked_snapshot_full_property_baseline" | "experimental_activity_plus_chembl_properties"; limitations?: string[]; target_matches?: CompoundMatch[]; descriptors?: { molecular_weight?: number; logp?: number; tpsa?: number; h_bond_donors?: number; h_bond_acceptors?: number; rotatable_bonds?: number; qed?: number; lipinski_violations?: number; lipinski_pass?: boolean; veber_pass?: boolean; structural_alerts?: string[]; structural_alert_count?: number | null; descriptor_source?: string }; admet_predictions?: Record<string, { value?: number; applicability_similarity?: number; domain?: string }>; admet_applicability?: number; provenance?: { snapshot_id?: string; model_version?: string; benchmark?: { requested_target_count?: number; chembl_target_count?: number; chembl_mapped_targets?: number; chembl_activity_targets?: number; qualified_compounds?: number; snapshot_compound_count?: number; live_compound_count?: number; full_admet_compound_count?: number; admet?: Record<string, { label_zh?: string; auroc?: number; auprc?: number; mae?: number; spearman?: number; train_count?: number; held_out_scaffold_count?: number }>; quality_gates?: { passed?: boolean } } } };


export const endpoints = [
  { id: "hia_hou", label: "肠道吸收 HIA", unit: "分类模型分数" },
  { id: "ames", label: "致突变 Ames", unit: "分类模型分数" },
  { id: "herg", label: "hERG 通道风险", unit: "分类模型分数" },
  { id: "caco2_wang", label: "Caco-2 渗透性", unit: "log cm/s" },
  { id: "solubility_aqsoldb", label: "水溶解度", unit: "log mol/L" },
] as const;
export const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
export const numberLabel = (value: unknown) => finite(value) ? value.toFixed(2) : "未提供";
export function compoundDetails(compound: Compound): CompoundDetails { return (compound.details ?? {}) as CompoundDetails; }
export function predictionsFor(compound: Compound) {
  const evaluation = compound.evaluation?.admet as { predictions?: CompoundDetails["admet_predictions"] } | undefined;
  return { ...compoundDetails(compound).admet_predictions, ...evaluation?.predictions };
}
export function predictionCount(compound: Compound) {
  const values = predictionsFor(compound);
  return endpoints.filter(({ id }) => finite(values[id]?.value)).length;
}
export function developmentStage(compound: Compound) {
  const data = compoundDetails(compound);
  if (compound.demo) return { id: "demo", label: "演示分子" };
  if (data.max_phase === 4 || (finite(data.first_approval) && data.first_approval > 0)) return { id: "approved", label: "已有批准记录" };
  if (finite(data.max_phase) && data.max_phase > 0 && data.max_phase < 4) return { id: "clinical", label: `最高临床阶段 ${data.max_phase} 期` };
  return { id: "research", label: "研究化合物 / 阶段未明" };
}
export function matchesFor(compound: Compound, target = "") {
  return (compoundDetails(compound).target_matches ?? []).filter((m) => !target || m.target_symbol === target).slice().sort((a, b) =>
    (finite(b.match_score) ? b.match_score : -Infinity) - (finite(a.match_score) ? a.match_score : -Infinity) || (a.target_symbol ?? "").localeCompare(b.target_symbol ?? ""));
}
export function needsReview(compound: Compound) {
  const data = compoundDetails(compound);
  return predictionCount(compound) < 5 || data.descriptors?.structural_alert_count == null || Boolean(data.withdrawn_flag || data.black_box_warning || (data.descriptors?.structural_alert_count ?? 0) > 0) || endpoints.some(({ id }) => predictionsFor(compound)[id]?.domain !== "inside");
}
export type MoleculeFilters = { query: string; target: string; stage: string; filter: string; sort: string };
export function filterCompounds(compounds: Compound[], options: MoleculeFilters) {
  const query = options.query.trim().toLowerCase();
  const metric = (c: Compound) => {
    const match = matchesFor(c, options.target)[0];
    const value = options.sort === "activity" ? match?.pchembl_median : options.sort === "documents" ? match?.document_count : options.sort === "complete" ? predictionCount(c) : options.target ? match?.match_score : c.match_score;
    return finite(value) ? value : -Infinity;
  };
  return compounds.filter((c) => {
    const data = compoundDetails(c);
    const text = [c.name, c.identifier, c.mechanism, data.canonical_smiles, ...(data.target_matches ?? []).flatMap((m) => [m.target_symbol, m.target_name])].join(" ").toLowerCase();
    return text.includes(query) && (!options.target || matchesFor(c, options.target).length > 0)
      && (!options.stage || developmentStage(c).id === options.stage)
      && (options.filter !== "evaluated" || predictionCount(c) === 5)
      && (options.filter !== "attention" || needsReview(c));
  }).slice().sort((a, b) => metric(b) - metric(a) || a.identifier.localeCompare(b.identifier) || a.id.localeCompare(b.id));
}
