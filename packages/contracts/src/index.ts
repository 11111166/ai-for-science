export type JobStatus = "queued" | "running" | "completed" | "failed" | "cancelled";
export type AdapterMode = "not_configured" | "precomputed" | "real" | "runtime";
export type ProjectRole = "admin" | "owner" | "member" | "viewer";

export interface DataSourceSpec {
  id: string;
  name: string;
  name_zh: string;
  version: string;
  status: "connected_snapshot" | "connected_runtime" | "queued_integration";
  used_in: string[];
  role: string;
  access_mode: string;
  license: string;
  url: string;
  boundary: string;
}

export interface Project {
  id: string;
  name: string;
  disease: string;
  disease_id?: string;
  objective: string;
  status: "active" | "archived";
  project_type: "research" | "validation";
  research_track: "full" | "target_discovery" | "novel_discovery" | "compound_prioritization";
  owner_id: string;
  created_at: string;
  updated_at: string;
}

export interface Job {
  id: string;
  project_id: string;
  type: string;
  status: JobStatus;
  stage: string;
  progress: number;
  adapter_mode: AdapterMode;
  parameters: Record<string, unknown>;
  result_status?: string;
  error_code?: string;
  error_message?: string;
  created_at: string;
  completed_at?: string;
  cache_hit?: boolean;
  reused_from_job_id?: string;
}

export interface TargetCompatibility {
  target_job_id: string;
  module: "virtual_intervention" | "gene_knockout" | "virtual_cell" | "compound_matching";
  upstream_disease_id?: string;
  upstream_disease_name: string;
  strategy: "established" | "novel";
  selection_mode: "selected" | "top_n_default";
  selected_count: number;
  requested_count: number;
  compatible: boolean;
  computable_count: number;
  unavailable_count: number;
  computable_symbols: string[];
  unavailable_symbols: string[];
  analysis_tier?: "disease_specific" | "evidence_baseline" | "mixed" | "gene_level_global" | "context_prior_baseline" | "live_database_lookup" | "snapshot_plus_live_lookup";
  coverage_scope?: "disease_specific" | "gene_level_global" | "cross_disease_context_prior" | "gene_and_molecule_level_global";
  pooled_snapshot_count?: number;
  pooled_screened_gene_count?: number;
  disease_specific_count?: number;
  baseline_count?: number;
  snapshot_covered_count?: number;
  live_lookup_count?: number;
  reason_code?: string;
  message: string;
}

export interface TargetCandidate {
  id: string;
  job_id: string;
  external_id?: string;
  symbol: string;
  name: string;
  biotype?: string;
  rank?: number;
  score: number;
  score_origin?: string;
  derived_score?: number;
  causal_confidence?: number;
  disease_context?: number;
  development_feasibility?: number;
  evidence_coverage?: number;
  evidence_count?: number;
  source_count?: number;
  evidence_breakdown?: EvidenceBreakdown;
  data_mode?: AdapterMode;
  source?: string;
  source_version?: string;
  retrieved_at?: string;
  direction: string;
  selected: boolean;
  note: string;
  demo: boolean;
}

export interface EvidenceCategory {
  status: "available" | "unavailable" | "not_configured";
  score?: number;
  datatypes: string[];
}

export interface EvidenceBreakdown {
  analysis_tier?: "public_evidence_exploration" | "disease_specific_multimodal";
  exploration?: { summary: string; gaps: string; next_step: string; support_count: number; channels: string[]; clinical_label: string };
  registry_version: string;
  score_contract_version?: string;
  discovery_strategy?: "established" | "novel";
  axes?: Record<string, number>;
  features?: Record<string, unknown>;
  target_to_hit?: {
    contract_version: "target-to-hit-v2" | "target-to-hit-v3";
    score: number;
    level: "ready_for_hit_search" | "prioritise_assay_and_structure" | "evidence_gap";
    recommended_modality: "small_molecule" | "biologic" | "small_molecule_or_biologic" | "evidence_generation";
    chemistry_start_score: number;
    biologic_start_score: number;
    tissue_selectivity_score: number;
    disease_evidence_score: number;
    has_small_molecule_binder: boolean;
    has_pocket: boolean;
    has_ligand: boolean;
    has_high_quality_probe: boolean;
    is_membrane: boolean;
    is_secreted: boolean;
    known_safety_signal: boolean;
    gaps: string[];
    safety_policy: string;
    decision?: "start_hit_search" | "validate_structure_or_assay" | "validate_disease_biology" | "hold_for_evidence";
    evidence_replication?: { source_count: number; level: "strong" | "moderate" | "limited" };
    structure_readiness?: { level: "strong" | "partial" | "missing"; basis: string[] };
    safety_review?: { status: "known_signal" | "unresolved"; essentiality_status: "not_assessed_at_target_stage" };
    routes?: Array<{
      id: "small_molecule" | "antibody_or_protein" | "targeted_degradation" | "rna_or_gene_medicine";
      label: string;
      support_score: number;
      status: "supported" | "possible" | "needs_evidence" | "not_preferred";
      basis: string[];
      gaps: string[];
    }>;
    next_steps?: string[];
  };
  candidate_origin?: string;
  novelty_class?: string;
  single_cell_context?: Record<string, unknown>;
  held_out?: Record<string, boolean>;
  categories?: Record<string, EvidenceCategory>;
  datatype_scores?: Record<string, number>;
  datasource_scores?: Record<string, number>;
  status?: string;
}

export interface TargetEvidence {
  id: string;
  external_evidence_id?: string;
  disease_id?: string;
  evidence_type: string;
  datatype?: string;
  datasource?: string;
  category?: string;
  source: string;
  summary: string;
  score: number;
  direction_on_target?: string;
  direction_on_trait?: string;
  confidence?: string;
  source_version?: string;
  retrieved_at?: string;
  details: Record<string, unknown>;
}

export interface TargetDiscoveryResult {
  id: string;
  job_id: string;
  query: string;
  resolution_status: string;
  canonical_name?: string;
  disease_id?: string;
  ontology?: string;
  synonyms: string[];
  resolution_confidence?: number;
  source: string;
  source_version: string;
  source_api_version: string;
  evidence_registry_version: string;
  retrieved_at: string;
  data_quality: Record<string, unknown>;
  benchmark: Record<string, unknown>;
  benchmark_status: string;
  warnings: string[];
}

export interface PerturbationResult {
  id: string;
  job_id: string;
  target_id?: string;
  intervention: string;
  cell_context: string;
  reversal_score?: number;
  confidence?: number;
  result: Record<string, unknown>;
  demo: boolean;
}

export interface CompoundEvaluation {
  efficacy?: number;
  safety?: number;
  developability?: number;
  admet: Record<string, unknown>;
  demo?: boolean;
}

export interface Compound {
  id: string;
  name: string;
  identifier: string;
  mechanism: string;
  match_score: number;
  details: Record<string, unknown>;
  demo: boolean;
  evaluation?: CompoundEvaluation;
}

export interface Ranking {
  id: string;
  rank: number;
  total_score: number;
  decision: "pending" | "selected" | "excluded";
  rationale: string;
  weights: Record<string, number>;
  compound?: Compound;
}

export interface ReportRecord {
  id: string;
  project_id: string;
  version: number;
  title: string;
  status: string;
  created_at: string;
}

export interface MethodologySpec {
  stage: string;
  name: string;
  version: string;
  current: string;
  inputs: string[];
  method_steps: string[];
  outputs: string[];
  production_baseline: string[];
  llm_role: string;
  validation: string[];
  boundary: string;
  evidence_level: "observed" | "derived" | "predicted" | "mixed";
  maturity: "baseline" | "planned" | "runtime";
}
