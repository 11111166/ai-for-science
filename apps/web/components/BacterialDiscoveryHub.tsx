"use client";

import { AlertTriangle, Beaker, ChevronDown, Database, Dna, FileText, FileUp, FlaskConical, LoaderCircle, PencilRuler, Search, Sparkles } from "lucide-react";
import dynamic from "next/dynamic";
import Image from "next/image";
import { Fragment, useEffect, useMemo, useState } from "react";
import { api, apiBase } from "@/lib/api";
import { BacterialCompoundExploration } from "./BacterialCompoundExploration";
import { MoleculeStructure } from "./MoleculeStructure";

type RawSource = { id: string; name: string; version?: string; status?: string; bytes?: number; license?: string; role?: string; index_status?: string; index_records?: number };
type Target = { uniprot: string; gene_symbol: string; protein_name?: string; compound_count: number };
type EvidenceRecord = {
  source?: string; record_id?: string; compound?: string; uniprot?: string; gene_symbol?: string;
  protein_name?: string; direction?: string; phenotype_text?: string; experiment_type?: string;
  mutant_type?: string; reference?: string; references?: string[]; chemical_accession?: string;
  relationship_label?: string; evidence_count?: number; species?: string; strain?: string;
};
type CompoundIdentity = {
  name: string; pref_name?: string; chembl_id?: string; chebi_id?: string; class?: string;
  canonical_smiles?: string; inchi_key?: string; molecular_formula?: string; molecular_weight?: number;
  cas_numbers?: string[]; cas_source?: string; cas_source_label?: string; pubchem_cids?: string[]; source?: string;
  structure_similarity?: number; match_type?: "exact_structure" | "similar_structure"; similarity_method?: string;
  svg?: string; registry_status?: string; cas_status?: string;
  isomeric_smiles?: string; raw_isomeric_smiles?: string; model_input_smiles?: string;
  normalization?: { profile?: string; transformations?: string[]; warnings?: string[]; input_fragment_count?: number };
};
type KnownCompound = CompoundIdentity & {
  association_count: number; target_count: number; directions: string[]; evidence_kind: string;
  evidence_records?: EvidenceRecord[];
};
type Organism = {
  id: string; species: string; strain: string; organism: string; aliases: string[]; coverage: string;
  known_compound_count: number; associated_target_count: number; known_compounds: KnownCompound[]; targets: Target[];
};
type Catalog = {
  configured: boolean; bundle_version?: string; organisms: Organism[]; boundary?: string;
  raw_sources?: { configured: boolean; bundle_version?: string; status?: string; counts?: Record<string, number>; sources: RawSource[] };
};
type Association = {
  compound: string; uniprot: string; gene_symbol?: string; protein_name?: string; direction?: string;
  phenotype_text?: string; experiment_type?: string; reference?: string; references?: string[];
  relationship_label: string; evidence_source?: string; record_id?: string; evidence_count?: number;
  chemical_accession?: string;
  matched_reference_compound?: string; structure_similarity?: number; structure_match_type?: string;
  similarity_method?: string; reference_relationship_label?: string;
};
type CompoundSearch = {
  configured: boolean; query?: string; search_mode?: "structure_similarity";
  query_structure?: CompoundIdentity & { heavy_atoms?: number };
  similarity_threshold?: number;
  matches: CompoundIdentity[];
  bacteria: { organism: string; species: string; strain: string; compound_matches: number; association_count: number; target_count: number; targets: Association[] }[];
  boundary?: string; raw_sources?: Catalog["raw_sources"];
  method?: { fingerprint: string; radius: number; bits: number; chirality: boolean; metric: string; candidate_scope: string };
};

const classLabels: Record<string, string> = {
  antibacterial_drug: "抗菌药", antibacterial_metabolite: "抗菌代谢物",
  immunosuppressant: "免疫抑制剂", research_tool: "研究化合物",
  anti_infective: "抗感染化合物", chemical_entity: "ChEBI 化学实体",
  measured_bacterial_activity: "细菌实测活性化合物",
};
const directionLabels: Record<string, string> = { sensitizing: "扰动后增敏", resistance: "扰动后耐药", neutral: "无明显变化" };
const bacteriumChineseNames: Record<string, string> = {
  "staphylococcus aureus": "金黄色葡萄球菌",
  "klebsiella pneumoniae": "肺炎克雷伯菌",
  "acinetobacter baumannii": "鲍曼不动杆菌",
  "pseudomonas aeruginosa": "铜绿假单胞菌",
  "enterococcus faecium": "屎肠球菌",
  "enterococcus faecalis": "粪肠球菌",
  "enterobacter cloacae": "阴沟肠杆菌",
  "escherichia coli": "大肠杆菌",
  "mycobacterium tuberculosis": "结核分枝杆菌",
};
const unknownStructureExample = {
  label: "环丙沙星去氟结构变体",
  smiles: "OC(=O)c1cn(C2CC2)c2cc(N3CCNCC3)ccc2c1=O",
  threshold: 40,
};
const defaultBacteriumExample = {
  query: "Escherichia coli",
  label: "大肠杆菌 K-12 案例",
};

const KetcherStructureEditor = dynamic(
  () => import("./KetcherStructureEditor").then((module) => module.KetcherStructureEditor),
  { ssr: false, loading: () => <div className="bacterial-ketcher-loading"><LoaderCircle className="spin" size={18} />正在载入结构编辑器…</div> },
);

function bacteriumChineseName(species: string) {
  return bacteriumChineseNames[species.trim().toLowerCase()] || "致病细菌";
}

function bytes(value?: number) {
  if (!value) return "下载中";
  if (value >= 1024 ** 3) return `${(value / 1024 ** 3).toFixed(1)} GB`;
  return `${(value / 1024 ** 2).toFixed(0)} MB`;
}

function formulaLabel(value?: string) {
  if (!value) return "数据库未提供";
  return value.split(/(\d+)/).map((part, index) => /^\d+$/.test(part) ? <sub key={`${part}-${index}`}>{part}</sub> : part);
}

function hasVisibleSvg(svg?: string) {
  return Boolean(svg && /<(?:[\w-]+:)?(?:path|line|polyline|polygon|circle|ellipse|text)\b/i.test(svg));
}

function svgDataUrl(svg: string) {
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

function referenceLabel(record: EvidenceRecord) {
  const references = record.references?.length ? record.references : record.reference ? [record.reference] : [];
  return references.length ? references.slice(0, 3).join("；") : record.record_id || "原始记录未给出文献号";
}

function evidenceForCompound(compound: CompoundIdentity, evidence: EvidenceRecord[], singleMatch: boolean) {
  const names = new Set([compound.name, compound.pref_name].filter(Boolean).map((value) => String(value).trim().toLowerCase()));
  const matched = evidence.filter((record) => names.has(String(record.compound || "").trim().toLowerCase()));
  return matched.length ? matched : singleMatch ? evidence : [];
}

function CompoundStructureVisual({ compound }: { compound: CompoundIdentity }) {
  if (compound.chembl_id) {
    return <MoleculeStructure identifier={compound.chembl_id} name={compound.pref_name || compound.name} />;
  }
  if (compound.chebi_id && compound.canonical_smiles) {
    return <figure className="dossier-structure bacterial-chebi-structure">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={`${apiBase}/bacterial-discovery/compound-structure?chebi_id=${encodeURIComponent(compound.chebi_id)}`} alt={`${compound.pref_name || compound.name} 二维结构`} width={240} height={180} loading="lazy" />
      <figcaption>本地 ChEBI 结构标准化记录</figcaption>
    </figure>;
  }
  return <div className="bacterial-structure-missing"><FlaskConical size={23} /><span>当前记录没有可验证的二维结构</span></div>;
}

function CompoundEvidenceDetails({ compound, evidence = [] }: { compound: CompoundIdentity; evidence?: EvidenceRecord[] }) {
  const [visible, setVisible] = useState(20);
  const shown = evidence.slice(0, visible);
  return <div className="bacterial-compound-detail">
    <div className="bacterial-compound-identity-detail">
      <CompoundStructureVisual compound={compound} />
      <dl>
        <div><dt>ChEMBL ID</dt><dd>{compound.chembl_id ? <a href={`https://www.ebi.ac.uk/chembl/explore/compound/${compound.chembl_id}`} target="_blank" rel="noreferrer">{compound.chembl_id}</a> : "尚未匹配到 ChEMBL"}</dd></div>
        <div><dt>CAS 号</dt><dd>{compound.cas_numbers?.length ? compound.cas_numbers.join(" / ") : "数据库未提供"}{compound.cas_numbers?.length && compound.cas_source ? <small>来源：{compound.cas_source.startsWith("http") ? <a href={compound.cas_source} target="_blank" rel="noreferrer">{compound.cas_source_label || "核对记录"}</a> : compound.cas_source_label || compound.cas_source}</small> : null}</dd></div>
        <div><dt>ChEBI ID</dt><dd>{compound.chebi_id || "数据库未提供"}</dd></div>
        <div><dt>PubChem CID</dt><dd>{compound.pubchem_cids?.length ? compound.pubchem_cids.map((cid, index) => <Fragment key={cid}>{index ? " / " : ""}<a href={`https://pubchem.ncbi.nlm.nih.gov/compound/${cid}`} target="_blank" rel="noreferrer">{cid}</a></Fragment>) : "数据库未提供"}</dd></div>
        <div><dt>分子式</dt><dd>{formulaLabel(compound.molecular_formula)}</dd></div>
        <div><dt>分子量</dt><dd>{compound.molecular_weight ? `${compound.molecular_weight.toFixed(3)} g/mol` : "数据库未提供"}</dd></div>
        <div className="wide"><dt>InChIKey</dt><dd><code>{compound.inchi_key || "数据库未提供"}</code></dd></div>
        <div className="wide"><dt>SMILES</dt><dd><code>{compound.canonical_smiles || "数据库未提供"}</code></dd></div>
      </dl>
    </div>
    <section className="bacterial-evidence-detail">
      <header><div><strong>关联证据</strong><small>{evidence.length ? `${evidence.length} 条可追溯记录` : "当前数据库没有逐条关联记录"}</small></div><span>关联不等同于直接结合</span></header>
      {!!shown.length && <div className="table-card"><table><thead><tr><th>来源与对象</th><th>关联类型</th><th>表型方向</th><th>实验与文献</th></tr></thead><tbody>{shown.map((record, index) => <tr key={`${record.source}-${record.record_id}-${record.uniprot}-${index}`}><td><b>{record.source || "策展数据库"}</b><strong>{record.gene_symbol || record.uniprot || record.species || "未标注对象"}</strong><small>{record.species ? <i>{record.species}</i> : record.protein_name || record.uniprot || "—"}{record.strain ? ` · ${record.strain}` : ""}</small></td><td>{record.relationship_label || "策展关联"}<small>{record.evidence_count && record.evidence_count > 1 ? `${record.evidence_count} 条原始证据合并` : record.record_id || ""}</small></td><td>{directionLabels[record.direction || ""] || record.direction || "—"}<small>{record.phenotype_text || record.chemical_accession || ""}</small></td><td>{record.experiment_type || "策展实验"}<small>{referenceLabel(record)}</small></td></tr>)}</tbody></table></div>}
      {visible < evidence.length && <button type="button" className="secondary-button compact bacterial-more-evidence" onClick={() => setVisible((value) => Math.min(value + 50, evidence.length))}>再显示 {Math.min(50, evidence.length - visible)} 条</button>}
    </section>
  </div>;
}

export function BacterialDiscoveryHub({ projectId, jobs, busy, runExploration }: {
  projectId: string;
  jobs: { id: string; type: string; status: string; progress: number; parameters?: Record<string, unknown> }[];
  busy: string;
  runExploration: (parameters: Record<string, unknown>) => void;
}) {
  const [mode, setMode] = useState<"bacterium" | "compound">("bacterium");
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [bacteriumQuery, setBacteriumQuery] = useState(defaultBacteriumExample.query);
  const [compoundQuery, setCompoundQuery] = useState("");
  const [compoundInputMode, setCompoundInputMode] = useState<"identity" | "structure">("identity");
  const [structureEntryMode, setStructureEntryMode] = useState<"draw" | "smiles" | "file">("draw");
  const [structureInput, setStructureInput] = useState("");
  const [structureFormat, setStructureFormat] = useState("auto");
  const [structureFileName, setStructureFileName] = useState("");
  const [similarityThreshold, setSimilarityThreshold] = useState(55);
  const [compoundResult, setCompoundResult] = useState<CompoundSearch | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [showExploration, setShowExploration] = useState(false);

  async function findBacterium(query = bacteriumQuery) {
    setLoading(true); setError("");
    try { setCatalog(await api<Catalog>(`/bacterial-discovery/organisms?q=${encodeURIComponent(query.trim())}`)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "细菌证据库读取失败"); }
    finally { setLoading(false); }
  }

  useEffect(() => {
    let cancelled = false;
    api<Catalog>(`/bacterial-discovery/organisms?q=${encodeURIComponent(defaultBacteriumExample.query)}`)
      .then((value) => { if (!cancelled) setCatalog(value); })
      .catch((reason) => { if (!cancelled) setError(reason instanceof Error ? reason.message : "细菌证据库读取失败"); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  async function findCompound(query = compoundQuery) {
    if (query.trim().length < 2) { setError("请输入至少 2 个字符的化合物名称、ChEMBL ID、InChIKey 或 SMILES。"); return; }
    setLoading(true); setError(""); setCompoundResult(null);
    try { setCompoundResult(await api<CompoundSearch>(`/bacterial-discovery/compound-search?query=${encodeURIComponent(query.trim())}`)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "化合物反查失败"); }
    finally { setLoading(false); }
  }

  async function findStructure(structure = structureInput, threshold = similarityThreshold, format = structureFormat) {
    if (structure.trim().length < 3) { setError("请粘贴 SMILES / InChI，或上传 MOL / SDF 结构文件。"); return; }
    setLoading(true); setError(""); setCompoundResult(null);
    try {
      setCompoundResult(await api<CompoundSearch>("/bacterial-discovery/structure-search", {
        method: "POST",
        body: JSON.stringify({
          structure,
          format,
          similarity_threshold: threshold / 100,
          max_results: 12,
        }),
      }));
    } catch (reason) { setError(reason instanceof Error ? reason.message : "结构检索失败"); }
    finally { setLoading(false); }
  }

  async function loadStructureFile(file?: File) {
    if (!file) return;
    if (file.size > 256 * 1024) { setError("结构文件不能超过 256 KB，且一次只支持一个分子。"); return; }
    const suffix = file.name.split(".").pop()?.toLowerCase() || "";
    const format = suffix === "mol" ? "mol" : suffix === "sdf" ? "sdf" : suffix === "inchi" ? "inchi" : suffix === "smi" || suffix === "smiles" ? "smiles" : "auto";
    setStructureInput(await file.text()); setStructureFormat(format); setStructureFileName(file.name); setCompoundResult(null); setError("");
  }

  const organism = catalog?.organisms?.[0];
  const curatedCase = organism?.coverage?.split("+").includes("full_curated_case") ?? false;
  const raw = compoundResult?.raw_sources ?? catalog?.raw_sources;
  const sourceReady = useMemo(() => raw?.sources?.filter((item) => item.index_status === "indexed").length ?? 0, [raw]);
  const reverseEvidence = useMemo<EvidenceRecord[]>(() => compoundResult?.bacteria?.flatMap((bacterium) =>
    bacterium.targets.map((item) => ({
      ...item, source: item.evidence_source || "策展数据库", species: bacterium.species, strain: bacterium.strain,
    }))) ?? [], [compoundResult]);

  return <div className="bacterial-discovery-hub">
    <section className="bacterial-discovery-hero">
      <div><span>Bacterial compound discovery</span><h2>细菌—化合物—靶标双向检索</h2><p>从菌株出发发现已知与探索性化合物，或从化合物反查相关细菌及候选靶标。证据关联、直接结合和计算生成始终分层展示。</p></div>
      <div className="bacterial-discovery-coverage"><Database size={18} /><span><b>{catalog?.bundle_version || "证据库载入中"}</b><small>{raw?.sources?.length ?? 0} 个扩展数据源 · {sourceReady} 个已建可查询索引</small></span></div>
    </section>

    <div className="bacterial-discovery-tabs" role="tablist" aria-label="细菌发现任务">
      <button type="button" role="tab" aria-selected={mode === "bacterium"} className={mode === "bacterium" ? "active" : ""} onClick={() => { setMode("bacterium"); setError(""); }}><Dna size={18} /><span><b>按细菌找化合物</b><small>已知证据 + 未知候选</small></span></button>
      <button type="button" role="tab" aria-selected={mode === "compound"} className={mode === "compound" ? "active" : ""} onClick={() => { setMode("compound"); setError(""); }}><FlaskConical size={18} /><span><b>按化合物找细菌与靶标</b><small>物种命中 + 靶标证据</small></span></button>
    </div>

    {mode === "bacterium" ? <>
      <section className="bacterial-discovery-search">
        <div><span>01 · 输入细菌物种或菌株</span><h3>检索已接入的细菌证据</h3><p>支持学名、简称、中文名和菌株号；默认载入大肠杆菌 K-12 案例。</p></div>
        <label><Search size={16} /><input value={bacteriumQuery} onChange={(event) => setBacteriumQuery(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void findBacterium(); }} placeholder="如 Escherichia coli 或菌株号" /><button onClick={() => void findBacterium()} disabled={loading}>{loading ? <LoaderCircle className="spin" size={15} /> : "搜索"}</button></label>
      </section>

      {organism ? <section className="bacterial-organism-result">
        <header><div><span>02 · {curatedCase ? "菌株结果" : "细菌证据结果"}</span><h3><i>{organism.species}</i> {organism.strain}</h3><p>{curatedCase ? "完整策展案例" : "PHI-base 细菌表型索引"} · {organism.known_compound_count} 个已知化合物 · {organism.associated_target_count} 个关联基因/蛋白</p></div><button className="secondary-button compact" onClick={() => { setBacteriumQuery(defaultBacteriumExample.query); void findBacterium(defaultBacteriumExample.query); }}>一键载入{defaultBacteriumExample.label}</button></header>
        <div className="bacterial-result-summary"><article><Beaker size={17} /><span><small>已知化合物</small><strong>{organism.known_compound_count}</strong></span></article><article><Dna size={17} /><span><small>关联蛋白</small><strong>{organism.associated_target_count}</strong></span></article><article><Sparkles size={17} /><span><small>探索候选</small><strong>按靶标生成</strong></span></article></div>
        <div className="bacterial-known-head"><div><h4>已知化合物</h4><p>来自真实策展记录，按证据条数排序；“关联”不自动等于直接结合靶标。</p></div></div>
        {organism.known_compounds.length ? <div className="table-card bacterial-known-table"><table><thead><tr><th>化合物</th><th>类型</th><th>关联靶标</th><th>证据方向</th><th>证据层级</th></tr></thead><tbody>{organism.known_compounds.slice(0, 30).map((item) => <Fragment key={item.chembl_id || item.chebi_id || item.name}><tr><td><strong>{item.pref_name || item.name}</strong><small>{item.name}{item.chembl_id ? ` · ${item.chembl_id}` : " · ChEMBL 尚未匹配"}{item.cas_numbers?.length ? ` · CAS ${item.cas_numbers[0]}` : ""}</small></td><td>{classLabels[item.class || ""] || item.class || "待分类"}</td><td><b>{item.target_count}</b><small>{item.association_count} 条记录</small></td><td>{item.directions.map((value) => directionLabels[value] || value).join(" / ") || "—"}</td><td><span className="evidence-chip association">{item.evidence_kind === "direct_binding_activity" ? "直接结合实测" : item.evidence_kind === "measured_activity" ? "实测活性" : item.evidence_kind === "card_resistance_entry" ? "CARD 耐药条目" : "PHI-base 表型关联"}</span></td></tr><tr className="bacterial-compound-detail-row"><td colSpan={5}><details><summary><FlaskConical size={15} />结构、标识与 {item.evidence_records?.length || item.association_count} 条关联证据 <ChevronDown size={14} /></summary><CompoundEvidenceDetails compound={item} evidence={item.evidence_records} /></details></td></tr></Fragment>)}</tbody></table></div> : <p className="bacterial-index-note"><Database size={15} />该细菌已有 PHI-base 基因/毒力表型证据，但当前快照没有可标准化的抗感染化合物记录。</p>}
        {organism.known_compounds.length > 30 && <p className="bacterial-list-note">当前显示证据最多的 30 个；完整结果将在下载索引完成后支持分页与导出。</p>}
        {curatedCase ? <section className="bacterial-unknown-panel"><div><span>03 · 未知化合物探索</span><h4>选择菌株靶标，生成或扩展结构候选</h4><p>相似性命中与规则衍生结构不会被标成“已知活性化合物”；所有候选都保留母体、生成规则和未验证边界。</p></div><button className="primary-button compact" onClick={() => setShowExploration((value) => !value)}><Sparkles size={15} />{showExploration ? "收起探索器" : "打开未知候选探索"}</button></section> : <p className="bacterial-index-note"><AlertTriangle size={15} />当前为跨物种 PHI-base 表型条目；尚未配置该物种的完整蛋白组、宿主去同源与结构生成流程。</p>}
      </section> : !loading && <section className="bacterial-discovery-empty"><Search size={24} /><h3>当前索引未找到该细菌</h3><p>可一键载入大肠杆菌 K-12 案例；其他致病细菌会随 PHI-base、CARD、VFDB、ChEMBL 和参考蛋白组继续扩充索引。</p></section>}
      {showExploration && organism && curatedCase && <BacterialCompoundExploration projectId={projectId} jobs={jobs} busy={busy} run={runExploration} />}
    </> : <>
      <section className="bacterial-discovery-search">
        <div><span>01 · 输入化合物</span><h3>反查相关细菌与候选靶标</h3><p>已知化合物可按名称或编号精确查找；未知化合物可直接粘贴或上传结构，按结构相似性寻找有证据的参照分子。</p></div>
        <div className="bacterial-compound-input-tabs" role="tablist" aria-label="化合物输入方式">
          <button type="button" role="tab" aria-selected={compoundInputMode === "identity"} className={compoundInputMode === "identity" ? "active" : ""} onClick={() => { setCompoundInputMode("identity"); setCompoundResult(null); setError(""); }}><Search size={15} />名称 / 数据库编号</button>
          <button type="button" role="tab" aria-selected={compoundInputMode === "structure"} className={compoundInputMode === "structure" ? "active" : ""} onClick={() => { setCompoundInputMode("structure"); setCompoundResult(null); setError(""); }}><FlaskConical size={15} />未知结构式</button>
        </div>
        {compoundInputMode === "identity" ? <>
          <label><Search size={16} /><input value={compoundQuery} onChange={(event) => setCompoundQuery(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void findCompound(); }} placeholder="如 ciprofloxacin、CHEMBL8 或 InChIKey" /><button onClick={() => void findCompound()} disabled={loading}>{loading ? <LoaderCircle className="spin" size={15} /> : "搜索"}</button></label>
          <div className="bacterial-query-examples"><span>试一试：</span>{["ciprofloxacin", "vancomycin", "isoniazid"].map((item) => <button key={item} onClick={() => { setCompoundQuery(item); void findCompound(item); }}>{item}</button>)}</div>
        </> : <div className="bacterial-structure-query-panel">
          <div className="bacterial-structure-entry-tabs" role="tablist" aria-label="结构输入方式">
            <button type="button" role="tab" aria-selected={structureEntryMode === "draw"} className={structureEntryMode === "draw" ? "active" : ""} onClick={() => setStructureEntryMode("draw")}><PencilRuler size={15} />结构绘制</button>
            <button type="button" role="tab" aria-selected={structureEntryMode === "smiles"} className={structureEntryMode === "smiles" ? "active" : ""} onClick={() => setStructureEntryMode("smiles")}><FileText size={15} />SMILES 粘贴</button>
            <button type="button" role="tab" aria-selected={structureEntryMode === "file"} className={structureEntryMode === "file" ? "active" : ""} onClick={() => setStructureEntryMode("file")}><FileUp size={15} />上传结构文件</button>
          </div>
          {structureEntryMode === "draw" && <KetcherStructureEditor structure={structureInput} onError={setError} onSmiles={(smiles) => { setStructureInput(smiles); setStructureFormat("smiles"); setStructureFileName(""); setCompoundResult(null); setError(""); setStructureEntryMode("smiles"); }} />}
          {structureEntryMode === "file" && <div className="bacterial-structure-file-panel"><label className="bacterial-structure-upload"><FileUp size={18} /><span>{structureFileName || "选择 MOL / SDF / SMI 文件"}</span><input type="file" accept=".mol,.sdf,.smi,.smiles,.txt,.inchi" onChange={(event) => { void loadStructureFile(event.target.files?.[0]); event.currentTarget.value = ""; }} /></label><small>单个文件不超过 256 KB；SDF 只读取第一个分子。载入后仍由服务器校验和规范化。</small></div>}
          {(structureEntryMode === "smiles" || structureEntryMode === "draw") && <label className="bacterial-structure-text"><span>SMILES（核心结构输入）/ InChI</span><textarea rows={4} value={structureInput} onChange={(event) => { setStructureInput(event.target.value); setStructureFormat("auto"); setStructureFileName(""); setCompoundResult(null); }} placeholder="例如 C[C@@H](N)C(=O)O；支持带手性的 Isomeric SMILES，也可粘贴 InChI" /><small>原始输入会保留用于核对；检索使用服务器生成的标准化母体结构。</small></label>}
          <div className="bacterial-structure-query-tools">
            <label className="bacterial-similarity-threshold"><span>最低结构相似度</span><select value={similarityThreshold} onChange={(event) => setSimilarityThreshold(Number(event.target.value))}><option value={40}>40% · 扩展探索</option><option value={55}>55% · 推荐</option><option value={70}>70% · 较严格</option><option value={85}>85% · 高相似</option><option value={100}>100% · 仅精确结构</option></select></label>
            <button type="button" className="primary-button compact bacterial-structure-submit" onClick={() => void findStructure()} disabled={loading}>{loading ? <LoaderCircle className="spin" size={15} /> : <FlaskConical size={15} />}校验并搜索结构</button>
          </div>
          <p className="bacterial-structure-method-note">标准化：拆分盐/多组分 → 母体选择 → 电荷与互变异构规范化 → Canonical / Isomeric SMILES。检索：RDKit Morgan 指纹（半径 2、2048 位、含手性）与 Tanimoto 相似度。结构相似不代表活性相同。</p>
          <div className="bacterial-structure-example"><div><span>SMILES 示例 · 可关联的未知结构</span><strong>{unknownStructureExample.label}</strong><code>{unknownStructureExample.smiles}</code><small>当前登记库未精确命中；以 40% 阈值可关联到有细菌证据的参照化合物。示例不代表已有实验活性。</small></div><div className="bacterial-structure-example-actions"><button type="button" className="secondary" onClick={() => { setStructureInput(unknownStructureExample.smiles); setStructureFormat("smiles"); setStructureFileName(""); setSimilarityThreshold(unknownStructureExample.threshold); setCompoundResult(null); setError(""); }} disabled={loading}>仅填入 SMILES</button><button type="button" onClick={() => { setStructureInput(unknownStructureExample.smiles); setStructureFormat("smiles"); setStructureFileName(""); setSimilarityThreshold(unknownStructureExample.threshold); void findStructure(unknownStructureExample.smiles, unknownStructureExample.threshold, "smiles"); }} disabled={loading}><Sparkles size={15} />载入并搜索</button></div></div>
        </div>}
      </section>
      {compoundResult && <section className="bacterial-compound-result">
        <header><div><span>02 · 反查结果</span><h3>{compoundResult.matches.length ? compoundResult.search_mode === "structure_similarity" ? `${compoundResult.matches.length} 个有证据的结构参照` : `${compoundResult.matches.length} 个化合物身份命中` : "未找到已接入证据"}</h3><p>{compoundResult.bacteria.length ? `${compoundResult.bacteria.length} 个细菌条目具有可追溯关联` : compoundResult.search_mode === "structure_similarity" ? "当前证据化合物结构集中没有达到阈值的参照分子。" : "不会用相似名称或无来源预测填充空结果。"}</p></div></header>
        {compoundResult.query_structure && <article className="bacterial-query-structure-card"><figure className="bacterial-query-structure-svg">{hasVisibleSvg(compoundResult.query_structure.svg) ? <Image unoptimized width={240} height={180} src={svgDataUrl(compoundResult.query_structure.svg!)} alt={`${compoundResult.query_structure.name || "查询化合物"}二维结构式`} /> : <div className="bacterial-query-structure-empty"><FlaskConical size={26} /><span>二维结构暂未生成</span></div>}<figcaption>查询结构 · 2D</figcaption></figure><div><span>已规范化的查询结构</span><strong>{compoundResult.query_structure.chebi_id || compoundResult.query_structure.chembl_id ? "登记结构精确命中" : "未知或未登记结构"}</strong><dl className="bacterial-query-identifiers"><div><dt>Canonical SMILES</dt><dd><code>{compoundResult.query_structure.canonical_smiles}</code></dd></div><div><dt>Isomeric SMILES</dt><dd><code>{compoundResult.query_structure.isomeric_smiles || compoundResult.query_structure.canonical_smiles}</code></dd></div><div><dt>模型检索结构</dt><dd><code>{compoundResult.query_structure.model_input_smiles || compoundResult.query_structure.isomeric_smiles || compoundResult.query_structure.canonical_smiles}</code></dd></div><div><dt>InChIKey</dt><dd><code>{compoundResult.query_structure.inchi_key || "未生成"}</code></dd></div></dl><small>{formulaLabel(compoundResult.query_structure.molecular_formula)} · {compoundResult.query_structure.molecular_weight?.toFixed(3) || "—"} g/mol · CAS {compoundResult.query_structure.cas_numbers?.length ? compoundResult.query_structure.cas_numbers.join(" / ") : "尚未分配或未在已接入登记库命中"}</small>{!!compoundResult.query_structure.normalization?.transformations?.length && <small className="bacterial-normalization-note">结构处理：{compoundResult.query_structure.normalization.transformations.join(" → ")}</small>}{compoundResult.query_structure.normalization?.warnings?.map((warning) => <small className="bacterial-normalization-warning" key={warning}><AlertTriangle size={12} />{warning}</small>)}</div></article>}
        {!!compoundResult.matches.length && <div className="bacterial-identity-strip">{compoundResult.matches.map((item) => <details key={item.chembl_id || item.chebi_id || item.name} className="bacterial-identity-card"><summary><span><small>{item.match_type === "exact_structure" ? "精确结构命中" : item.structure_similarity !== undefined ? `结构相似 ${(item.structure_similarity * 100).toFixed(1)}%` : classLabels[item.class || ""] || item.class || "化合物"}</small><strong>{item.pref_name || item.name}</strong><em>{item.chembl_id || item.chebi_id || "登记编号未匹配"}{item.cas_numbers?.length ? ` · CAS ${item.cas_numbers[0]}` : ""}</em>{item.source && <i>来源：{item.source}</i>}</span><ChevronDown size={16} /></summary><CompoundEvidenceDetails compound={item} evidence={evidenceForCompound(item, reverseEvidence, compoundResult.matches.length === 1)} /></details>)}</div>}
        {!!compoundResult.bacteria.length && <div className="bacterial-species-summary"><div className="bacterial-species-summary-title"><Dna size={18} /><span><small>命中细菌</small><strong>{compoundResult.bacteria.length} 个物种 / 菌株条目</strong></span></div><div className="bacterial-species-chips">{compoundResult.bacteria.map((bacterium) => <div key={bacterium.organism}><span>{bacteriumChineseName(bacterium.species)}</span><b><i>{bacterium.species}</i></b>{bacterium.strain ? <small>菌株 {bacterium.strain}</small> : <small>物种级记录</small>}</div>)}</div></div>}
        {compoundResult.bacteria.map((bacterium) => <details className="bacterial-hit-group" key={bacterium.organism}><summary aria-label={`展开 ${bacteriumChineseName(bacterium.species)} 的候选靶标`}><div className="bacterial-hit-organism"><span>命中细菌</span><h4>{bacteriumChineseName(bacterium.species)} <i>{bacterium.species}</i></h4><p>{bacterium.strain ? `菌株 ${bacterium.strain}` : "物种级记录，未指定菌株"} · {bacterium.target_count} 个关联蛋白 · {bacterium.association_count} 类去重关系</p></div><div className="bacterial-hit-summary-actions"><span className="evidence-chip association">{compoundResult.search_mode === "structure_similarity" ? "结构映射候选" : "策展关联"}</span><span className="bacterial-hit-toggle"><b className="when-closed">展开查看</b><b className="when-open">收起详情</b><ChevronDown size={16} /></span></div></summary><div className="table-card"><table><thead><tr><th>候选靶标 / 关联基因</th><th>关系</th><th>表型方向</th><th>实验与来源</th></tr></thead><tbody>{bacterium.targets.slice(0, 60).map((item, index) => <tr key={`${item.compound}-${item.uniprot}-${item.direction}-${item.matched_reference_compound || ""}-${index}`}><td><strong>{item.gene_symbol || item.uniprot}</strong><small>{item.protein_name || "蛋白名称待补"} · {item.uniprot}</small></td><td><span className="evidence-chip association">{item.relationship_label}</span>{item.structure_similarity !== undefined && <small>参照：{item.matched_reference_compound} · 相似度 {(item.structure_similarity * 100).toFixed(1)}%</small>}<small>{item.evidence_count && item.evidence_count > 1 ? `${item.evidence_count} 条原始证据` : ""}</small></td><td>{directionLabels[item.direction || ""] || item.direction || "—"}<small>{item.phenotype_text || item.chemical_accession || ""}</small></td><td><b>{item.evidence_source || "策展数据库"}</b><small>{item.experiment_type || "策展实验"}</small><small>{item.references?.slice(0, 3).join("；") || item.reference || item.record_id || "原始记录未给出文献号"}</small></td></tr>)}</tbody></table></div></details>)}
        {compoundResult.boundary && <p className="bacterial-science-boundary"><AlertTriangle size={15} />{compoundResult.boundary}</p>}
      </section>}
    </>}

    {error && <p className="bacterial-error" role="alert"><AlertTriangle size={16} />{error}</p>}
    {raw && <details className="bacterial-source-progress"><summary><Database size={15} />扩展数据库进度 · {sourceReady}/{raw.sources.length} 已建索引 <ChevronDown size={14} /></summary><div>{raw.sources.map((source) => <article key={source.id}><span className={`source-dot ${source.index_status === "indexed" ? "ready" : source.index_status === "invalid" ? "failed" : source.status || "pending"}`} /><div><strong>{source.name}</strong><small>{source.version || "版本待登记"} · {source.license || "许可待核对"}</small><p>{source.role}</p></div><b>{source.index_status === "indexed" ? `${source.index_records?.toLocaleString() || 0} 条索引` : source.index_status === "invalid" ? "校验失败" : source.status === "ready" ? "待建索引" : source.status === "failed" ? "下载中断" : "下载中"}</b></article>)}</div></details>}
  </div>;
}
