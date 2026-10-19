"use client";

import { AlertTriangle, ChevronDown, Database, ExternalLink, FlaskConical, Globe, LoaderCircle, Play, Search, ServerCog, Sparkles } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { MoleculeIdentity } from "./MoleculeIdentity";
import { MoleculeStructure } from "./MoleculeStructure";

/**
 * Target -> compound exploration, bacterial edition.
 *
 * Four evidence layers are kept visually and structurally separate because they
 * are not interchangeable: curated CARD/PHI-base resistance and phenotype
 * evidence, ChEMBL measured binding, ChEMBL similarity hits (which carry no
 * activity data), and rule-derived structures (which carry no activity
 * prediction at all).
 */

type CatalogTarget = {
  uniprot: string;
  gene_symbol: string;
  protein_name: string;
  compound_count: number;
  sensitizing_compounds: string[];
  resistance_compounds: string[];
  organism?: string;
  activity_evidence_count?: number;
  direct_binding_count?: number;
  evidence_sources?: string[];
  human_similarity?: number;
  human_uniprot?: string;
};
type Catalog = {
  configured: boolean;
  message?: string;
  bundle_version?: string;
  chembl_db_version?: string;
  sha256_verified?: boolean;
  generated_at?: string;
  counts: Record<string, number>;
  compounds: { curated_name: string; chembl_id: string; class: string; pref_name: string; note: string }[];
  unresolved_compounds: { curated_name: string }[];
  targets: CatalogTarget[];
};
type Resolution = {
  status: string;
  chembl_target_id?: string | null;
  resolved_organism?: string;
  resolved_pref_name?: string;
  chembl_candidates: { target_chembl_id: string; pref_name: string; organism: string; target_type: string }[];
  activity_total?: number;
  activity_truncated?: boolean;
  organism_mismatch_count?: number;
  error?: string | null;
};
type Descriptors = {
  molecular_weight?: number | null; logp?: number | null; tpsa?: number | null;
  h_bond_donors?: number | null; h_bond_acceptors?: number | null; rotatable_bonds?: number | null;
  qed?: number | null; lipinski_violations?: number | null; aromatic_rings?: number | null;
  descriptor_source?: string;
};
type KnownCompound = {
  evidence_kind: string; curated_name: string; class: string; note: string;
  molecule_chembl_id: string; name: string; canonical_smiles: string;
  max_phase?: number | null; first_approval?: number | null;
  molecule_type?: string; black_box_warning?: boolean; withdrawn_flag?: boolean;
  descriptors?: Descriptors | null;
  mechanisms?: { mechanism_of_action: string; action_type: string }[];
  structural_alerts?: { count: number; names: string[] } | null;
};
type SimilarCompound = {
  evidence_kind: string; reference_curated_name: string; molecule_chembl_id: string; name: string;
  canonical_smiles: string; chembl_similarity?: number | null; rdkit_tanimoto?: number | null;
  inchi_key?: string; chebi_id?: string; cas_numbers?: string[]; pubchem_cids?: string[];
  molecular_formula?: string; molecular_weight?: number; cas_status?: string; registry_status?: string;
  max_phase?: number | null; descriptors?: Descriptors | null;
  structural_alerts?: { count: number; names: string[] } | null; note: string;
};
type GeneratedCompound = {
  id: string; canonical_smiles: string; inchi_key?: string; formula: string; svg: string;
  chebi_id?: string; cas_numbers?: string[]; pubchem_cids?: string[]; cas_status?: string; registry_status?: string;
  registry_lookup_scope?: string[];
  properties: Record<string, number | string | boolean>;
  constraints: Record<string, number | boolean | string | null>;
  provenance: { seed_curated_name: string; seed_molecule_chembl_id: string; seed_smiles: string; rule_id: string; rule_smarts: string; rule_rationale: string; rdkit_version: string };
  boundary: string; scientific_status: string;
};
type Generation = {
  enabled: boolean; reason?: string | null; method?: string; sa_method?: string;
  rules?: { id: string; smarts: string; rationale: string }[];
  seeds?: { curated_name: string; chembl_id: string; smiles: string }[];
  funnel?: { id: string; label: string; count: number }[];
  rejected_by_reason?: Record<string, number>;
  compounds: GeneratedCompound[];
  constraints_evaluated?: string[];
  constraints_not_evaluated?: Record<string, string>;
  novelty_scope?: string; novelty_unchecked?: boolean; warnings?: string[];
};
type TargetResult = {
  target: {
    uniprot: string; gene_symbol: string; protein_name: string;
    compound_count?: number; sensitizing_compounds?: string[]; resistance_compounds?: string[];
  };
  resolution: Resolution;
  genetic_evidence: {
    curated_name: string; direction: string; phenotype_text: string;
    experiment_type: string; mutant_type: string; reference: string; evidence_source?: string;
  }[];
  known_compounds: KnownCompound[];
  direct_binding: {
    molecule_chembl_id: string; pchembl_median: number; pchembl_best: number;
    activity_count: number; assay_count: number; document_count: number; activity_types: string[];
  }[];
  similar_compounds: SimilarCompound[];
  generated_compounds: Generation;
  warnings: string[];
};
type ExplorationResult = {
  job_id: string; model_version: string; workflow: string; generated_at: string; organism: string;
  data: {
    bundle_version?: string; artifact_sha256?: string; sha256_verified?: boolean;
    chembl_db_version?: string; chembl_release_date?: string;
    counts: Record<string, number>; unresolved_compounds: string[]; warnings: string[];
  };
  parameters: Record<string, unknown>;
  targets: TargetResult[];
  capabilities: Record<string, boolean | string | null>;
  boundary: string;
};

const CLASS_LABELS: Record<string, string> = {
  antibacterial_drug: "抗菌药",
  antibacterial_metabolite: "抗菌代谢产物",
  immunosuppressant: "免疫抑制剂（非抗菌药）",
  research_tool: "研究工具化合物",
};
const DIRECTION_LABELS: Record<string, { label: string; tone: string }> = {
  sensitizing: { label: "扰动后增敏", tone: "down" },
  resistance: { label: "扰动后耐药", tone: "up" },
  neutral: { label: "无变化", tone: "flat" },
};
const REJECT_LABELS: Record<string, string> = {
  pains_brenk_alert: "命中 PAINS/Brenk 结构警示",
  heavy_atom_range: "重原子数超出 12–50",
  tanimoto_window: "与母体相似度不在 0.2–0.7",
  duplicate: "与已生成结构重复",
  invalid_structure: "结构无效",
  lipinski: "类药性规则违反过多",
  veber: "Veber 规则未通过",
  molecular_weight: "分子量超过 500",
  not_single_component: "不是单一组分",
  undefined_stereocentre: "存在未定义手性中心",
  synthetic_accessibility: "合成可及性 SA 超过 4.0",
  synthetic_accessibility_unavailable: "合成可及性无法计算",
};

function num(value?: number | null, digits = 2) {
  return value == null ? "待补" : value.toFixed(digits);
}

function SimilarCandidateCard({ row }: { row: SimilarCompound }) {
  const [showIdentity, setShowIdentity] = useState(false);
  return <article className="bacterial-similar-card">
    <div className="bacterial-compound-head">
      <MoleculeStructure identifier={row.molecule_chembl_id} name={row.name || row.molecule_chembl_id} />
      <div><h6>{row.name || row.molecule_chembl_id}</h6><small>{row.molecule_chembl_id}{row.chebi_id ? ` · ${row.chebi_id}` : ""}</small><p>探针：{row.reference_curated_name}</p></div>
    </div>
    <div className="bacterial-props">
      <span><small>ChEMBL 相似度</small><b>{row.chembl_similarity == null ? "—" : `${row.chembl_similarity}%`}</b></span>
      <span><small>RDKit Tanimoto</small><b>{num(row.rdkit_tanimoto, 3)}</b></span>
      <span><small>分子量</small><b>{num(row.descriptors?.molecular_weight ?? row.molecular_weight, 1)}</b></span>
      <span><small>CAS 号</small><b>{row.cas_numbers?.length ? row.cas_numbers.join(" / ") : "展开后核对"}</b></span>
    </div>
    <code className="bacterial-smiles">{row.canonical_smiles}</code>
    <button type="button" className="secondary-button compact" onClick={() => setShowIdentity((value) => !value)}>{showIdentity ? "收起结构标识" : "查看 CAS 与完整标识"}</button>
    {showIdentity && <MoleculeIdentity identifier={row.molecule_chembl_id} name={row.name || row.molecule_chembl_id} smiles={row.canonical_smiles} weight={row.descriptors?.molecular_weight ?? row.molecular_weight} />}
    <em className="bacterial-generated-boundary">结构相似候选；没有针对当前细菌靶标的实测活性。</em>
  </article>;
}

export function BacterialCompoundExploration({ projectId, jobs, busy, run }: {
  projectId: string;
  jobs: { id: string; type: string; status: string; progress: number; parameters?: Record<string, unknown> }[];
  busy: string;
  run: (parameters: Record<string, unknown>) => void;
}) {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [catalogError, setCatalogError] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [query, setQuery] = useState("");
  const [searchState, setSearchState] = useState<{ status: string; candidates: CatalogTarget[] } | null>(null);
  const [searching, setSearching] = useState(false);
  const [similarity, setSimilarity] = useState(70);
  const [maxGenerated, setMaxGenerated] = useState(25);
  const [includeSimilarity, setIncludeSimilarity] = useState(true);
  const [includeGeneration, setIncludeGeneration] = useState(true);
  const [result, setResult] = useState<ExplorationResult | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    api<Catalog>("/bacterial-compounds/targets")
      .then((value) => { if (!cancelled) setCatalog(value); })
      .catch((reason) => { if (!cancelled) setCatalogError(reason instanceof Error ? reason.message : "靶标清单读取失败"); });
    return () => { cancelled = true; };
  }, []);

  const latestJob = useMemo(
    () => jobs.find((job) => job.type === "bacterial_compound"),
    [jobs],
  );

  useEffect(() => {
    if (latestJob?.status !== "completed") return;
    let cancelled = false;
    api<ExplorationResult>(`/jobs/${latestJob.id}/bacterial-compound-result`)
      .then((value) => { if (!cancelled) setResult(value); })
      .catch((reason) => { if (!cancelled) setError(reason instanceof Error ? reason.message : "结果读取失败"); });
    return () => { cancelled = true; };
  }, [latestJob?.id, latestJob?.status]);

  function toggle(uniprot: string) {
    setSelected((current) => current.includes(uniprot)
      ? current.filter((item) => item !== uniprot)
      : current.length >= 12 ? current : [...current, uniprot]);
  }

  async function resolve() {
    if (!query.trim()) return;
    setSearching(true); setSearchState(null); setError("");
    try {
      const value = await api<{ status: string; matched_by?: string; candidates: CatalogTarget[] }>(
        "/bacterial-compounds/resolve-target",
        { method: "POST", body: JSON.stringify({ identifier: query.trim() }) },
      );
      setSearchState({ status: value.status, candidates: value.candidates ?? [] });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "靶标解析失败");
    } finally { setSearching(false); }
  }

  function submit() {
    setError("");
    if (!selected.length) return setError("请先选择至少一个靶标。");
    const organisms = [...new Set((catalog?.targets ?? [])
      .filter((item) => selected.includes(item.uniprot))
      .map((item) => item.organism)
      .filter((value): value is string => Boolean(value)))];
    run({
      target_ids: selected,
      organism: organisms.length === 1 ? organisms[0] : organisms.length > 1 ? `多物种（${organisms.length}）` : "Staphylococcus aureus NCTC 8325",
      similarity_threshold: similarity,
      include_similarity: includeSimilarity,
      include_generation: includeGeneration,
      max_generated: maxGenerated,
      max_targets: 12,
    });
  }

  const active = latestJob?.status === "queued" || latestJob?.status === "running";
  const capabilities = result?.capabilities;

  return <div className="bacterial-explore">
    <section className="bacterial-hero">
      <div>
        <span>Target → Compound Exploration</span>
        <h2>从细菌靶标探索未知化合物</h2>
        <p>汇总 CARD 与 PHI-base 策展的耐药/毒力证据，取回真实抗菌化合物与其结构，并在明确规则和硬门槛下衍生全新结构。四类证据严格分开，不合并成一份候选榜。</p>
      </div>
    </section>

    {catalogError && <p className="bacterial-error" role="alert"><AlertTriangle size={16} />{catalogError}</p>}

    {catalog && !catalog.configured && <section className="bacterial-data-readiness pending">
      <ServerCog size={19} />
      <div className="bacterial-data-summary"><strong>CARD/PHI-base 策展证据尚未构建</strong><p>{catalog.message}</p></div>
    </section>}

    {catalog?.configured && <section className="bacterial-data-readiness ready">
      <ServerCog size={19} />
      <div className="bacterial-data-summary">
        <strong>证据已就绪 · {(catalog.counts?.searchable_targets ?? catalog.counts?.targets_with_evidence ?? 0).toLocaleString()} 个可检索靶标</strong>
        <p>
          {(catalog.counts?.activity_targets ?? 0).toLocaleString()} 个靶标含 ChEMBL/BindingDB 实测层 · {(catalog.counts?.compounds_resolved ?? 0)} 个 CARD 化合物已解析结构 · {(catalog.counts?.gene_compound_links_resolved_only ?? 0).toLocaleString()} 条 CARD 基因–化合物证据 · ChEMBL {catalog.chembl_db_version || "—"}
          {catalog.sha256_verified ? " · 数据包校验通过" : " · 数据包未在清单中登记校验值"}
        </p>
      </div>
      <details className="bacterial-data-details"><summary>数据来源与边界 <ChevronDown size={14} /></summary><div>
        <article><strong>CARD 耐药条目</strong><span>策展证据</span><p>综合抗生素耐药数据库中的基因家族、耐药机制与药物类别，给出靶标层面的耐药证据。</p></article>
        <article><strong>PHI-base 抗感染表型</strong><span>策展证据</span><p>宿主–病原互作库中，细菌基因扰动改变抗感染化合物敏感性的记录。</p></article>
        <article><strong>ChEMBL 直接结合</strong><span>{catalog.chembl_db_version || "—"}</span><p>针对该靶标的体外实测活性。细菌靶标覆盖有限，多数靶标该层为空。</p></article>
        <article><strong>RDKit · SA_Score</strong><span>{(capabilities?.rdkit_version as string) || "2024.03.2"}</span><p>结构标准化、二维描绘、Morgan 指纹、PAINS/Brenk 过滤，以及 Ertl–Schaffenhauer 合成可及性。</p></article>
        {!!catalog.unresolved_compounds?.length && <article><strong>未纳入的化合物</strong><span>{catalog.unresolved_compounds.length} 个</span><p>{catalog.unresolved_compounds.map((item) => item.curated_name).join("、")}：未能在 ChEMBL 取到结构，已在检索与生成中被排除。</p></article>}
      </div></details>
    </section>}

    <section className="bacterial-setup">
      <header><div><span>01 · 选择靶标</span><h3>从有策展证据的细菌靶标出发</h3></div>
        <span className={`bacterial-job ${active ? "running" : latestJob?.status === "completed" ? "completed" : "neutral"}`}>
          {active ? <><LoaderCircle className="spin" size={14} />正在分析 · {latestJob?.progress ?? 0}%</> : latestJob?.status === "completed" ? "分析完成" : latestJob?.status === "failed" ? "任务失败" : "尚未运行"}
        </span>
      </header>

      <div className="bacterial-resolve-row">
        <label><Search size={15} /><input value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") resolve(); }} placeholder="输入 UniProt 编号或基因符号，如 P0A0V2 或 gyrA" /></label>
        <button className="secondary-button" onClick={resolve} disabled={searching || !catalog?.configured}>{searching ? <LoaderCircle className="spin" size={15} /> : <Globe size={15} />}解析靶标</button>
      </div>
      {searchState && <div className={`bacterial-resolve-result ${searchState.status}`}>
        {searchState.status === "resolved" && searchState.candidates.slice(0, 1).map((item) => <p key={item.uniprot}><strong>{item.gene_symbol}</strong> · {item.uniprot} · {item.compound_count} 个关联化合物
          <button className="text-button" onClick={() => { if (!selected.includes(item.uniprot)) toggle(item.uniprot); setSearchState(null); setQuery(""); }}>加入分析</button></p>)}
        {searchState.status === "ambiguous" && <><p><AlertTriangle size={14} />匹配到多个靶标，请明确选择，系统不会自动取第一个。</p>
          <ul>{searchState.candidates.map((item) => <li key={item.uniprot}><button className="text-button" onClick={() => { if (!selected.includes(item.uniprot)) toggle(item.uniprot); setSearchState(null); setQuery(""); }}>{item.gene_symbol} · {item.uniprot} · {item.protein_name?.slice(0, 40) || "—"}</button></li>)}</ul></>}
        {searchState.status === "not_found" && <p><AlertTriangle size={14} />证据库中没有匹配的靶标。该库只收录有 CARD 或 PHI-base 策展证据的细菌蛋白。</p>}
        {searchState.status === "not_configured" && <p><AlertTriangle size={14} />证据库尚未构建。</p>}
      </div>}

      <div className="bacterial-target-picker">
        <div className="bacterial-target-picker-head">
          <span>已选 {selected.length} / 12</span>
          <div>
            <button className="text-button" onClick={() => setSelected((catalog?.targets ?? []).slice(0, 12).map((item) => item.uniprot))}>按证据数选前 12 个</button>
            <button className="text-button" onClick={() => setSelected([])}>清空</button>
          </div>
        </div>
        <div className="bacterial-target-list">
          {(catalog?.targets ?? []).slice(0, 60).map((item) => <button key={`${item.uniprot}-${item.organism || "curated"}`} className={selected.includes(item.uniprot) ? "active" : ""} onClick={() => toggle(item.uniprot)}>
            <b>{item.gene_symbol || item.uniprot}</b>
            <small>{item.uniprot} · {item.compound_count} 个化合物{item.human_similarity !== undefined ? ` · 宿主同源评分 ${(item.human_similarity * 100).toFixed(1)}%` : ""}</small>
            <em>{(item.sensitizing_compounds ?? []).slice(0, 3).join("、") || item.organism || item.evidence_sources?.join(" + ") || "—"}</em>
          </button>)}
        </div>
        {(catalog?.targets?.length ?? 0) > 60 && <small>仅显示证据最多的 60 个靶标；使用上方搜索框可精确定位任意靶标。</small>}
      </div>

      <div className="bacterial-input-grid">
        <label><span>相似性阈值（%）</span><input type="number" min="40" max="100" value={similarity} onChange={(event) => setSimilarity(Number(event.target.value))} /></label>
        <label><span>最多生成结构数</span><input type="number" min="1" max="60" value={maxGenerated} onChange={(event) => setMaxGenerated(Number(event.target.value))} /></label>
        <label className="bacterial-check"><input type="checkbox" checked={includeSimilarity} onChange={(event) => setIncludeSimilarity(event.target.checked)} /><span>取回相似化合物</span></label>
        <label className="bacterial-check"><input type="checkbox" checked={includeGeneration} onChange={(event) => setIncludeGeneration(event.target.checked)} /><span>生成新结构（仅单靶标）</span></label>
      </div>

      {error && <p className="bacterial-error" role="alert"><AlertTriangle size={16} />{error}</p>}
      <div className="bacterial-run">
        <p>生成结构不携带任何活性、对接或 ADMET 预测；相似性命中不携带活性数据。四类证据分别展示，不参与同一份排序。</p>
        <button className="primary-button" disabled={!selected.length || !!busy || active} onClick={submit}><Play size={16} />探索候选化合物</button>
      </div>
    </section>

    {!result && <section className="bacterial-empty"><Sparkles size={26} /><h3>等待选择靶标</h3><p>选择靶标后，系统会汇总已有策展证据、取回真实化合物结构与性质，并给出规则衍生的新结构。</p></section>}

    {result && <section className="bacterial-results">
      <header><div>
        <span>02 · 探索结果 · 服务器真实证据库</span>
        <h3>{result.organism} · 靶标到化合物探索</h3>
        <p>{result.targets.length} 个靶标 · ChEMBL {result.data.chembl_db_version || "—"} · 数据包 {result.data.bundle_version || "—"} · {new Date(result.generated_at).toLocaleString("zh-CN")}</p>
      </div><strong>{result.targets.reduce((total, item) => total + item.known_compounds.length, 0)}<small>个已知化合物</small></strong></header>

      {(result.data.warnings ?? []).map((warning) => <p className="bacterial-warning" key={warning}><AlertTriangle size={15} />{warning}</p>)}

      {result.targets.map((item) => <TargetPanel key={item.target.uniprot} item={item} />)}

      <details className="bacterial-method"><summary>证据口径与未评估项 <ChevronDown size={15} /></summary>
        <p>{result.boundary}</p>
        <p>生成结构使用的算法：{Object.entries(result.capabilities).filter(([, value]) => value === true).map(([key]) => key).join("、")}。</p>
        <p>明确未运行：对接（Vina/GNINA）、细菌靶点活性预测、ADMET 预测。这些在本平台不存在对应模型，任何数值都不会被填充。</p>
      </details>
      <p className="bacterial-boundary"><AlertTriangle size={16} />{result.boundary}</p>
    </section>}
  </div>;
}

function TargetPanel({ item }: { item: TargetResult }) {
  const generation = item.generated_compounds;
  const linkedCompoundNames = [...new Set([
    ...(item.target.sensitizing_compounds ?? []),
    ...(item.target.resistance_compounds ?? []),
  ])];
  const bindingById = useMemo(() => {
    const map = new Map<string, TargetResult["direct_binding"][number]>();
    for (const row of item.direct_binding) map.set(row.molecule_chembl_id, row);
    return map;
  }, [item.direct_binding]);

  return <div className="bacterial-explore-target">
    <header className="bacterial-target-compound-summary">
      <div className="bacterial-target-identity"><span>关联靶标</span><h4>{item.target.gene_symbol || item.target.uniprot}</h4><p>{item.target.protein_name?.split(" (")[0] || "蛋白名称待补"}</p><code>{item.target.uniprot}</code></div>
      <div className="bacterial-target-linked-compounds">
        <span>该靶标的策展化合物</span>
        <strong>{item.target.compound_count ?? Math.max(linkedCompoundNames.length, item.known_compounds.length)} 个关联 · 本次取回 {item.known_compounds.length} 个结构</strong>
        <div>{linkedCompoundNames.slice(0, 6).map((name) => <em key={name}>{name}</em>)}{linkedCompoundNames.length > 6 && <small>+{linkedCompoundNames.length - 6}</small>}</div>
      </div>
      <div className="bacterial-explore-resolution">
        <span>ChEMBL 靶标映射</span>
        {item.resolution.status === "resolved"
          ? <><b>{item.resolution.chembl_target_id}</b><small>{item.resolution.resolved_organism}</small></>
          : <b>{item.resolution.status === "not_mapped" ? "ChEMBL 无对应靶标" : item.resolution.status === "request_failed" ? "ChEMBL 请求失败" : "未解析"}</b>}
      </div>
    </header>

    {item.warnings.map((warning) => <p className="bacterial-warning" key={warning}><AlertTriangle size={14} />{warning}</p>)}

    <section className="bacterial-layer bacterial-known-integrated">
      <h5><FlaskConical size={15} />已知抗菌化合物 <small>{item.known_compounds.length} 个</small></h5>
      <p className="bacterial-layer-note">以下化合物直接归属于上方靶标；身份、理化性质与临床资料来自 ChEMBL，结构与性质均为真实数据，非本地估算。</p>
      <div className="bacterial-compound-cards">
        {item.known_compounds.map((compound) => <article key={compound.curated_name}>
          <div className="bacterial-compound-context"><span>关联 {item.target.gene_symbol || item.target.uniprot}</span><code>{item.target.uniprot}</code></div>
          <div className="bacterial-compound-head">
            <MoleculeStructure identifier={compound.molecule_chembl_id} name={compound.name} moleculeType={compound.molecule_type} />
            <div>
              <h6>{compound.name}</h6>
              <small>{compound.curated_name} · {CLASS_LABELS[compound.class] ?? compound.class} · {compound.molecule_chembl_id}</small>
              {compound.note && <p>{compound.note}</p>}
              {compound.black_box_warning && <em className="bacterial-flag">存在黑框警告记录</em>}
              {compound.withdrawn_flag && <em className="bacterial-flag">存在撤回记录</em>}
            </div>
          </div>
          {compound.descriptors && <div className="bacterial-props">
            <span><small>分子量 g/mol</small><b>{num(compound.descriptors.molecular_weight, 1)}</b></span>
            <span><small>脂溶性 logP</small><b>{num(compound.descriptors.logp)}</b></span>
            <span><small>极性表面积 Å²</small><b>{num(compound.descriptors.tpsa, 1)}</b></span>
            <span><small>类药性 QED</small><b>{num(compound.descriptors.qed, 3)}</b></span>
            <span><small>Lipinski 不符合</small><b>{compound.descriptors.lipinski_violations ?? "—"} 项</b></span>
          </div>}
          <div className="bacterial-alerts">
            {compound.structural_alerts
              ? compound.structural_alerts.count ? <span className="has">PAINS/Brenk {compound.structural_alerts.count} 条：{compound.structural_alerts.names.slice(0, 2).join("；")}</span> : <span>未命中已检查的结构警示</span>
              : <span className="unknown">结构警示未计算</span>}
          </div>
          {!!compound.mechanisms?.length && <p className="bacterial-mech">机制：{compound.mechanisms.map((row) => row.mechanism_of_action || row.action_type).filter(Boolean).join("；")}</p>}
          {bindingById.has(compound.molecule_chembl_id) && <p className="bacterial-binding">直接结合实测：pChEMBL 中位 {num(bindingById.get(compound.molecule_chembl_id)?.pchembl_median)} · 最佳 {num(bindingById.get(compound.molecule_chembl_id)?.pchembl_best)} · {bindingById.get(compound.molecule_chembl_id)?.activity_count} 条活性记录</p>}
          <details className="bacterial-compound-more"><summary>化合物基本信息与标识 <ChevronDown size={13} /></summary>
            <MoleculeIdentity identifier={compound.molecule_chembl_id} name={compound.name} smiles={compound.canonical_smiles} weight={compound.descriptors?.molecular_weight ?? undefined} />
          </details>
          <a className="bacterial-ext" href={`https://www.ebi.ac.uk/chembl/explore/compound/${compound.molecule_chembl_id}`} target="_blank" rel="noreferrer">核对 ChEMBL 记录<ExternalLink size={12} /></a>
        </article>)}
      </div>
    </section>

    <section className="bacterial-layer">
      <h5><Database size={15} />CARD / PHI-base 策展证据 <small>{item.genetic_evidence.length} 条</small></h5>
      <p className="bacterial-layer-note">细菌基因扰动改变抗感染化合物敏感性的策展记录。这是本模块中最强的证据类型：它是遗传学/耐药证据，并且直接点名了化合物。</p>
      {item.genetic_evidence.length ? <div className="table-card"><table><thead><tr><th>化合物</th><th>方向</th><th>表型原文</th><th>实验类型</th><th>来源与文献</th></tr></thead><tbody>
        {item.genetic_evidence.slice(0, 40).map((row, index) => <tr key={`${row.curated_name}-${row.direction}-${index}`}>
          <td><strong>{row.curated_name}</strong></td>
          <td><span className={`bacterial-tier ${DIRECTION_LABELS[row.direction]?.tone ?? "flat"}`}>{DIRECTION_LABELS[row.direction]?.label ?? row.direction}</span></td>
          <td><code>{row.phenotype_text}</code></td>
          <td><small>{row.experiment_type}</small></td>
          <td><small>{row.evidence_source || "策展数据库"}</small><small>{row.reference}</small></td>
        </tr>)}</tbody></table></div> : <p className="inline-empty">该靶标没有方向明确的策展记录。</p>}
      {item.genetic_evidence.length > 40 && <small>仅显示前 40 条，共 {item.genetic_evidence.length} 条。</small>}
    </section>

    <section className="bacterial-layer">
      <h5><Globe size={15} />ChEMBL 直接结合实测 <small>{item.direct_binding.length} 个化合物</small></h5>
      <p className="bacterial-layer-note">针对本靶标的体外实测活性，来源为 ChEMBL 严格过滤（结合类测定、pChEMBL 非空、标准关系为等号、非潜在重复）。细菌靶标覆盖有限，为空是真实结果。</p>
      {item.direct_binding.length ? <>
        <div className="table-card"><table><thead><tr><th>化合物</th><th>pChEMBL 中位 / 最佳</th><th>活性记录</th><th>实验 / 文献</th><th>测定类型</th></tr></thead><tbody>
          {item.direct_binding.map((row) => <tr key={row.molecule_chembl_id}>
            <td><a href={`https://www.ebi.ac.uk/chembl/explore/compound/${row.molecule_chembl_id}`} target="_blank" rel="noreferrer">{row.molecule_chembl_id}</a></td>
            <td><strong>{num(row.pchembl_median)}</strong> / {num(row.pchembl_best)}</td>
            <td>{row.activity_count} 条</td>
            <td><small>{row.assay_count} 个实验 · {row.document_count} 篇文献</small></td>
            <td><small>{row.activity_types.join("、") || "—"}</small></td>
          </tr>)}</tbody></table></div>
        {item.resolution.activity_truncated && <small>ChEMBL 命中数 {item.resolution.activity_total}，本次仅取回前 500 条并按化合物聚合。</small>}
        {!!item.resolution.organism_mismatch_count && <small>已剔除 {item.resolution.organism_mismatch_count} 条物种不匹配的活性记录。</small>}
      </> : <p className="inline-empty">该靶标在 ChEMBL 中没有符合严格过滤的直接结合实测记录。</p>}
    </section>

    <section className="bacterial-layer">
      <h5><Search size={15} />相似化合物 <small>{item.similar_compounds.length} 个</small></h5>
      <p className="bacterial-layer-note">以已知抗菌化合物为探针的 ChEMBL 相似性扩展。<strong>这些化合物没有针对本靶标的实测活性数据</strong>，仅表示结构相近，不能当作活性分子使用。</p>
      {item.similar_compounds.length ? <div className="bacterial-similar-grid">{item.similar_compounds.slice(0, 40).map((row) => <SimilarCandidateCard key={row.molecule_chembl_id} row={row} />)}</div> : <p className="inline-empty">未取回相似化合物。</p>}
      {item.similar_compounds.length > 40 && <small>仅显示前 40 条，共 {item.similar_compounds.length} 条。</small>}
    </section>

    <section className="bacterial-layer bacterial-layer-generated">
      <h5><Sparkles size={15} />规则衍生新结构 <small>{generation.compounds?.length ?? 0} 个</small></h5>
      <p className="bacterial-layer-note">{generation.enabled
        ? <>由固定 SMARTS 规则作用于上方真实抗菌化合物枚举得到，每条记录都可追溯到源化合物与具体规则。<strong>这些结构没有任何活性、对接或 ADMET 预测</strong>，仅用于探索化学空间。</>
        : generation.reason}</p>

      {generation.enabled && <>
        {!!generation.funnel?.length && <div className="bacterial-funnel">{generation.funnel.map((stage, index) => <article key={stage.id}><span>{stage.label}</span><strong>{stage.count}</strong>{index < generation.funnel!.length - 1 && <i>→</i>}</article>)}</div>}
        {!!Object.keys(generation.rejected_by_reason ?? {}).length && <div className="bacterial-rejects">
          <span>淘汰原因</span>
          {Object.entries(generation.rejected_by_reason!).map(([reason, count]) => <b key={reason}>{REJECT_LABELS[reason] ?? reason} {count}</b>)}
        </div>}

        {!!generation.compounds?.length && <div className="bacterial-generated-grid">
          {generation.compounds.map((compound) => <article key={compound.id}>
            <div className="bacterial-generated-structure">
              {compound.svg
                ? <div dangerouslySetInnerHTML={{ __html: compound.svg }} />
                : <p className="inline-empty">结构图不可用，请参考下方 SMILES。</p>}
            </div>
            <div className="bacterial-generated-body">
              <h6>{compound.id} · <code>{compound.formula}</code></h6>
              <div className="bacterial-generated-identifiers">
                <span><small>CAS 号</small><b>{compound.cas_numbers?.length ? compound.cas_numbers.join(" / ") : "尚未分配或未在已接入登记库命中"}</b></span>
                <span><small>ChEBI</small><b>{compound.chebi_id || "未命中"}</b></span>
                <span className="wide"><small>InChIKey</small><code>{compound.inchi_key || "当前 RDKit 未生成"}</code></span>
              </div>
              <code className="bacterial-smiles">{compound.canonical_smiles}</code>
              <div className="bacterial-props">
                <span><small>分子量</small><b>{num(compound.properties.molecular_weight as number, 1)}</b></span>
                <span><small>logP</small><b>{num(compound.properties.logp as number)}</b></span>
                <span><small>TPSA</small><b>{num(compound.properties.tpsa as number, 1)}</b></span>
                <span><small>QED</small><b>{num(compound.properties.qed as number, 3)}</b></span>
                <span><small>合成可及性 SA</small><b>{num(compound.constraints.sa_score as number, 2)}</b></span>
                <span><small>与母体 Tanimoto</small><b>{num(compound.constraints.tanimoto_to_seed as number, 3)}</b></span>
              </div>
              <details className="bacterial-candidate-evidence"><summary>来源与规则</summary>
                <p>母体：{compound.provenance.seed_curated_name}（{compound.provenance.seed_molecule_chembl_id}）</p>
                <p>规则：{compound.provenance.rule_id} · <code>{compound.provenance.rule_smarts}</code></p>
                <p>{compound.provenance.rule_rationale}</p>
                <p>RDKit {compound.provenance.rdkit_version} · ChEMBL 完全一致结构命中：{compound.constraints.novelty_chembl_exact_smiles === true ? "无" : compound.constraints.novelty_chembl_exact_smiles === "unchecked" ? "未检查" : "有，已排除"}</p>
                <p>CAS 检索范围：{compound.registry_lookup_scope?.join("；") || "ChEBI 与 ChEMBL 精确结构"}。未命中表示当前来源未登记或尚未分配，不代表已完成全球 CAS Registry 检索。</p>
              </details>
              <em className="bacterial-generated-boundary">{compound.boundary}</em>
            </div>
          </article>)}
        </div>}

        <details className="bacterial-method"><summary>生成方法与未评估项 <ChevronDown size={15} /></summary>
          <p>合成可及性算法：{generation.sa_method}。它是发表在 2009 年的片段贡献法，用作合成难度指示，不是实验收率预测。</p>
          <p>新颖性检查范围：{generation.novelty_scope === "chembl_exact_canonical_smiles_only" ? "仅在 ChEMBL 中检查规范 SMILES 是否完全一致；未检索专利、PubChem 或供应商目录。" : generation.novelty_scope}</p>
          {!!generation.rules?.length && <><p>使用的规则：</p><ul>{generation.rules.map((rule) => <li key={rule.id}><code>{rule.id}</code> · <code>{rule.smarts}</code> · {rule.rationale}</li>)}</ul></>}
          {!!Object.keys(generation.constraints_not_evaluated ?? {}).length && <><p>明确未评估的约束：</p><ul>{Object.entries(generation.constraints_not_evaluated!).map(([key, reason]) => <li key={key}><code>{key}</code>：{reason}</li>)}</ul></>}
        </details>
      </>}
    </section>
  </div>;
}
