import type { Compound } from "@drug-discovery/contracts";
import { matchesFor } from "./compoundInsights";
export type DrugDossier = { status: string; cache_state?: string; sections?: {identity?:string; indications?:string; mechanisms?:string}; warnings: string[]; retrieved_at?: string; name?: string; aliases?: string[]; molecule_type?: string; max_phase?: number | null; first_approval?: number | null; canonical_smiles?: string; black_box_warning?: number | boolean | null; withdrawn_flag?: boolean | null; indication_total?: number; mechanism_total?: number; indications?: { mesh_id?: string; mesh_heading?: string; efo_id?: string; efo_term?: string; max_phase_for_ind?: number; indication_refs?: { ref_type?: string; ref_id?: string }[] }[]; mechanisms?: { target_chembl_id?: string; action_type?: string; mechanism_of_action?: string }[] };
export const phaseLabel = (phase: unknown) => phase === 4 ? "已有批准记录" : typeof phase === "number" && phase > 0 && phase < 4 ? `最高临床阶段 ${phase} 期` : "研究化合物 / 阶段未明";
export function commonTargets(compounds: Compound[]) {
  if (!compounds.length) return [];
  return [...new Set(matchesFor(compounds[0]).map(m => m.target_symbol).filter((s): s is string => !!s))].filter(s => compounds.every(c => matchesFor(c, s).length > 0)).sort();
}
export function toggleComparison(ids: string[], id: string) { return ids.includes(id) ? ids.filter(x => x !== id) : ids.length < 5 ? [...ids, id] : ids; }
export function indicationRelation(dossier: DrugDossier, diseaseId?: string) {
  const normalize = (s: string) => s.split('/').pop()!.replace(':','_').toUpperCase();
  const matches = diseaseId ? (dossier.indications ?? []).filter(x => x.efo_id && normalize(x.efo_id) === normalize(diseaseId)) : [];
  return matches.length ? "存在与项目疾病本体 ID 一致的适应症记录；阶段见下表。" : "尚未确认与本项目疾病对应的适应症；不同本体、疾病亚型及同义词需人工复核。";
}
