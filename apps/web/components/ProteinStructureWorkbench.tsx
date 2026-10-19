"use client";

import { type FormEvent, type PointerEvent as ReactPointerEvent, useEffect, useMemo, useRef, useState } from "react";
import { Database, RotateCcw, Search, Sparkles, UploadCloud, ZoomIn, ZoomOut } from "lucide-react";
import { api, apiText } from "@/lib/api";

type StudySpec = {
  name: string; target: string; uniprot_accession: string; sequence: string;
  assembly: "monomer" | "multimer"; research_question: string;
};
type Structure = {
  source: string; source_id: string; file_name: string; sha256: string; residue_count: number;
  chain_count: number; chain_lengths: Record<string, number>; observed_sequence: string;
  confidence: null | { metric: string; mean: number; very_high_fraction: number; confident_fraction: number; low_fraction: number; boundary: string };
  sequence_check?: null | { chain: string; aligned_residues: number; identity: number; input_coverage: number; coordinate_coverage: number; exact: boolean };
  projection: { method: string; chains: Record<string, number[][]> };
  viewer?: { kind: string; chains: Record<string, number[][]>; sampled_residues: number; total_residues: number; boundary: string };
  provenance: Record<string, string | number | null>;
};
type Comparison = {
  id: string; created_at: string; reference: Structure;
  metrics: { model_chain: string; reference_chain: string; aligned_residues: number; sequence_identity: number; model_coverage: number; reference_coverage: number; ca_rmsd_angstrom: number; sequence_registered_tm_like: number; metric_boundary: string };
  interpretation: string;
};
type Validation = {
  id: string; method: ValidationMethod; status: "planned" | "completed" | "failed";
  criterion: string; result: string; conclusion: "pending" | "supports" | "inconclusive" | "contradicts";
  source: string; notes: string; created_at: string;
};
type ValidationMethod = "expression_purification" | "xray" | "cryo_em" | "nmr" | "cd" | "dsf" | "sec_mals" | "hdx_ms" | "crosslink_ms" | "binding" | "activity" | "other";
type Study = { id: string; revision: number; spec: StudySpec; model: Structure | null; comparisons: Comparison[]; validations: Validation[]; prediction_task: { status: string; task_type: string; message: string; sequence_sha256: string } };
type ProteinSearchItem = {
  accession: string; entry_name: string; reviewed: boolean; protein_name: string; gene: string;
  gene_names: string[]; organism: string; length: number; pdb_count: number; pdb_examples: string[]; entry_url: string;
};
type ProteinSearchResponse = {
  query: string; items: ProteinSearchItem[]; returned: number; total: number;
  source: { name: string; url: string; retrieved_at: string; boundary: string };
};
type TargetSuggestion = { symbol: string; name: string; selected?: boolean; rank?: number };

const blankSpec: StudySpec = { name: "", target: "", uniprot_accession: "", sequence: "", assembly: "monomer", research_question: "比较预测结构与可用实验结构，并规划独立功能验证。" };
const methodLabels: Record<ValidationMethod, string> = {
  expression_purification: "表达与纯化", xray: "X-ray", cryo_em: "cryo-EM", nmr: "NMR",
  cd: "圆二色谱（CD）", dsf: "差示扫描荧光（DSF）", sec_mals: "SEC-MALS", hdx_ms: "HDX-MS",
  crosslink_ms: "交联质谱", binding: "结合实验", activity: "活性实验", other: "其他",
};
const sourceLabels: Record<string, string> = {
  alphafold_db: "AlphaFold DB 预测", alphafold: "AlphaFold 预测", colabfold: "ColabFold 预测",
  rosettafold: "RoseTTAFold 预测", experimental: "实验结构", other: "其他模型",
  pdb_experimental: "PDB 实验结构", manual_experimental: "人工上传实验结构", manual_predicted: "人工上传预测结构",
};

export function ProteinStructureWorkbench({ projectId, projectDisease = "", targetSuggestions = [] }: { projectId: string; projectDisease?: string; targetSuggestions?: TargetSuggestion[] }) {
  const base = `/projects/${projectId}/protein-structures`;
  const [studies, setStudies] = useState<Study[]>([]), [active, setActive] = useState("");
  const [loaded, setLoaded] = useState(false), [busy, setBusy] = useState(false), [creating, setCreating] = useState(false);
  const [error, setError] = useState(""), [message, setMessage] = useState("");
  const [spec, setSpec] = useState<StudySpec>({ ...blankSpec });
  const [search, setSearch] = useState({ query: "", field: "all", organism: "9606", reviewedOnly: false });
  const [searchResult, setSearchResult] = useState<ProteinSearchResponse | null>(null);
  const [searching, setSearching] = useState(false), [searchError, setSearchError] = useState("");
  const [modelFile, setModelFile] = useState<File | null>(null), [modelSource, setModelSource] = useState("alphafold");
  const [pdbId, setPdbId] = useState(""), [referenceChain, setReferenceChain] = useState("");
  const [referenceFile, setReferenceFile] = useState<File | null>(null), [referenceKind, setReferenceKind] = useState("experimental");
  const [validation, setValidation] = useState({ method: "binding" as ValidationMethod, status: "planned" as Validation["status"], criterion: "", result: "", conclusion: "pending" as Validation["conclusion"], source: "", notes: "" });
  const study = studies.find((row) => row.id === active);

  useEffect(() => {
    let live = true;
    api<Study[]>(`${base}/studies`).then((rows) => {
      if (live) { setStudies(rows); setActive(rows[0]?.id ?? ""); setLoaded(true); }
    }).catch((reason) => { if (live) { setError(reason.message); setLoaded(true); } });
    return () => { live = false; };
  }, [base]);

  async function act(work: () => Promise<void>) {
    setBusy(true); setError(""); setMessage("");
    try { await work(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "操作失败"); }
    finally { setBusy(false); }
  }
  function updateStudy(next: Study) {
    setStudies((rows) => rows.some((row) => row.id === next.id) ? rows.map((row) => row.id === next.id ? next : row) : [next, ...rows]);
    setActive(next.id);
  }
  async function create(event: FormEvent) {
    event.preventDefault();
    await act(async () => {
      const next = await api<Study>(`${base}/studies`, { method: "POST", body: JSON.stringify(spec) });
      updateStudy(next); setSpec({ ...blankSpec }); setCreating(false);
      setMessage("结构研究已创建。可获取 AlphaFold DB 模型或上传已有模型；重计算尚未在当前服务中运行。");
    });
  }
  async function uploadModel() {
    if (!study || !modelFile) return;
    await act(async () => {
      const body = new FormData(); body.append("file", modelFile);
      updateStudy(await api<Study>(`${base}/studies/${study.id}/model/upload?revision=${study.revision}&source=${modelSource}`, { method: "POST", body }));
      setModelFile(null); setMessage("模型结构已解析；坐标哈希、链、残基和来源已保存。");
    });
  }
  async function loadAlphaFold() {
    if (!study) return;
    await act(async () => {
      updateStudy(await api<Study>(`${base}/studies/${study.id}/model/alphafold`, { method: "POST", body: JSON.stringify({ revision: study.revision }) }));
      setMessage("已从 AlphaFold DB 取得公开预测；pLDDT 仅作局部模型置信度解释。");
    });
  }
  async function comparePdb(event: FormEvent) {
    event.preventDefault(); if (!study) return;
    await act(async () => {
      updateStudy(await api<Study>(`${base}/studies/${study.id}/references/pdb`, { method: "POST", body: JSON.stringify({ revision: study.revision, pdb_id: pdbId, chain: referenceChain }) }));
      setPdbId(""); setMessage("实验参考结构比较已保存；结构一致性与功能验证保持分开。");
    });
  }
  async function compareUpload() {
    if (!study || !referenceFile) return;
    await act(async () => {
      const body = new FormData(); body.append("file", referenceFile);
      updateStudy(await api<Study>(`${base}/studies/${study.id}/references/upload?revision=${study.revision}&reference_kind=${referenceKind}&chain=${encodeURIComponent(referenceChain)}`, { method: "POST", body }));
      setReferenceFile(null); setMessage("人工参考结构比较已保存。");
    });
  }
  async function saveValidation(event: FormEvent) {
    event.preventDefault(); if (!study) return;
    await act(async () => {
      updateStudy(await api<Study>(`${base}/studies/${study.id}/validations`, { method: "POST", body: JSON.stringify({ revision: study.revision, ...validation }) }));
      setValidation({ method: "binding", status: "planned", criterion: "", result: "", conclusion: "pending", source: "", notes: "" });
      setMessage("独立验证记录已保存，不会反向改写结构模型分数。");
    });
  }

  async function searchProteins(event?: FormEvent, override?: { query: string; field?: string }) {
    event?.preventDefault();
    const query = (override?.query ?? search.query).trim();
    if (query.length < 2) { setSearchError("请至少输入 2 个字符，例如 EGFR 或 P00533。"); return; }
    setSearching(true); setSearchError(""); setSearchResult(null);
    if (override) setSearch((current) => ({ ...current, query, field: override.field ?? current.field }));
    try {
      const params = new URLSearchParams({
        q: query,
        field: override?.field ?? search.field,
        organism_id: search.organism,
        reviewed_only: String(search.reviewedOnly),
        size: "10",
      });
      setSearchResult(await api<ProteinSearchResponse>(`${base}/search?${params}`));
    } catch (reason) {
      setSearchError(reason instanceof Error ? reason.message : "蛋白质数据库检索失败");
    } finally {
      setSearching(false);
    }
  }

  async function createFromSearch(item: ProteinSearchItem) {
    const target = item.gene || item.entry_name || item.accession;
    const nextSpec: StudySpec = {
      name: `${target} 结构研究`, target, uniprot_accession: item.accession, sequence: "", assembly: "monomer",
      research_question: `核对 ${target} 的公开预测结构与可用实验参考，并规划独立功能验证。`,
    };
    await act(async () => {
      const next = await api<Study>(`${base}/studies`, { method: "POST", body: JSON.stringify(nextSpec) });
      updateStudy(next); setCreating(false);
      setMessage(`已用 ${item.accession} 建立结构研究。下一步可获取 AlphaFold DB 公开预测；实验结构和功能验证仍分别记录。`);
    });
  }

  const suggestions = [...targetSuggestions]
    .sort((left, right) => Number(Boolean(right.selected)) - Number(Boolean(left.selected)) || (left.rank ?? 9999) - (right.rank ?? 9999))
    .slice(0, 8);

  return <section className="table-card workbench protein-structure-workbench">
    <header className="protein-heading"><div><span>蛋白质结构预测与验证</span><h3>从一个蛋白开始，逐步得到可核验结论</h3><p>不熟悉专业参数也可以使用；先搜索和选择，系统自动补齐基础信息。</p></div><button disabled={busy} onClick={() => setCreating(!creating)}>{creating ? "返回推荐与检索" : "进入自定义分析"}</button></header>
    {error && <div className="notice error" role="alert">{error}<button disabled={busy} onClick={() => act(async () => { const rows = await api<Study[]>(`${base}/studies`); setStudies(rows); setActive(rows[0]?.id ?? ""); })}>重新加载</button></div>}
    {message && <p role="status">{message}</p>}
    {!loaded && <p role="status">正在加载结构研究…</p>}

    {!creating && <section className="protein-guided-start">
      <div className="protein-start-title"><div><small>推荐入口</small><h4>你想从哪里开始？</h4><p>选择常见场景即可，不需要先理解 PDB、链编号或结构算法。</p></div>{!!studies.length && <span>{studies.length} 项已有研究</span>}</div>
      <div className="protein-scenario-grid">
        <button className="active" onClick={() => document.getElementById("protein-search-query")?.focus()}><Database size={20} /><span><strong>查找已有蛋白</strong><small>按名称、基因或编号检索</small></span></button>
        <button onClick={() => { setCreating(true); setSpec({ ...blankSpec, research_question: "比较已有模型与同一蛋白的实验结构，并规划独立功能验证。" }); }}><UploadCloud size={20} /><span><strong>比较已有结构</strong><small>上传模型，选择实验结构作参考</small></span></button>
        <button onClick={() => { setCreating(true); setSpec({ ...blankSpec, research_question: "检查新序列并建立可追溯的结构预测与验证计划。" }); }}><Sparkles size={20} /><span><strong>分析新序列</strong><small>粘贴氨基酸序列，先检查输入</small></span></button>
      </div>

      {!!suggestions.length && <div className="protein-project-suggestions"><div><strong>从本项目候选靶点开始</strong><span>{projectDisease ? `${projectDisease} · ` : ""}点击候选后检索对应的 UniProt 记录</span></div><div>{suggestions.map((item) => <button key={item.symbol} title={`${item.symbol} · ${item.name}`} disabled={searching} onClick={() => searchProteins(undefined, { query: item.symbol, field: "gene" })}><b>{item.symbol}</b><small>{item.name}</small>{item.selected && <em>已选择靶点</em>}</button>)}</div></div>}

      <form className="protein-search" onSubmit={searchProteins}>
        <label className="protein-search-query"><span>搜索蛋白质数据库</span><div><Search size={18} /><input id="protein-search-query" aria-label="搜索蛋白质数据库" value={search.query} onChange={(event) => setSearch({ ...search, query: event.target.value })} placeholder="例如 EGFR、P00533 或 epidermal growth factor receptor" /><button disabled={searching || search.query.trim().length < 2}>{searching ? "正在搜索…" : "搜索"}</button></div></label>
        <details><summary>自定义搜索范围</summary><div className="protein-search-options">
          <label>匹配字段<select value={search.field} onChange={(event) => setSearch({ ...search, field: event.target.value })}><option value="all">全部字段</option><option value="gene">精确基因名</option><option value="protein">蛋白名称</option><option value="accession">UniProt accession</option></select></label>
          <label>物种<select value={search.organism} onChange={(event) => setSearch({ ...search, organism: event.target.value })}><option value="9606">人类</option><option value="0">所有物种</option></select></label>
          <label className="protein-search-check"><input type="checkbox" checked={search.reviewedOnly} onChange={(event) => setSearch({ ...search, reviewedOnly: event.target.checked })} />只看人工审阅的 Swiss-Prot 记录</label>
        </div></details>
      </form>
      {searchError && <div className="notice error" role="alert">{searchError}<button disabled={searching} onClick={() => searchProteins()}>重新搜索</button></div>}
      {searchResult && <section className="protein-search-results" aria-live="polite">
        <header><div><strong>找到 {searchResult.total} 条匹配记录</strong><span>当前展示 {searchResult.returned} 条 · {searchResult.source.name}</span></div><a href={searchResult.source.url} target="_blank" rel="noreferrer">在 UniProt 查看本次检索</a></header>
        <div>{searchResult.items.map((item) => <article key={item.accession}>
          <div className="protein-result-name"><span><b>{item.gene || item.entry_name}</b>{item.reviewed ? <em>人工审阅</em> : <em className="muted">自动注释</em>}</span><strong>{item.protein_name || "未提供蛋白名称"}</strong><small>{item.accession} · {item.organism}</small></div>
          <div className="protein-result-facts"><span><b>{item.length || "—"}</b> aa</span><span><b>{item.pdb_count}</b> 个 PDB 交叉引用</span>{!!item.pdb_examples.length && <small>示例：{item.pdb_examples.join("、")}</small>}</div>
          <div className="protein-result-actions"><a href={item.entry_url} target="_blank" rel="noreferrer">查看记录</a><button disabled={busy} onClick={() => createFromSearch(item)}>选择并建立研究</button></div>
        </article>)}</div>
        {!searchResult.items.length && <div className="inline-empty">没有找到匹配记录。可缩短关键词、切换到“全部字段”或取消“只看人工审阅”。</div>}
        <p>{searchResult.source.boundary}</p>
      </section>}
      {!searchResult && !searching && <div className="protein-search-empty"><Search size={22} /><div><strong>搜索后会先列出可选结果</strong><span>确认蛋白、物种、审阅状态和实验结构覆盖后，再建立研究。</span></div></div>}
    </section>}

    {creating && <form onSubmit={create}>
      <div className="protein-custom-heading"><small>自定义分析</small><h4>定义结构问题</h4><p>适合已有 accession、序列或结构文件的用户；不确定的字段可以留空。</p></div><div className="workbench-form">
        <label>研究名称<input required maxLength={160} value={spec.name} onChange={(event) => setSpec({ ...spec, name: event.target.value })} /></label>
        <label>靶点 / 蛋白名称<input required maxLength={160} value={spec.target} onChange={(event) => setSpec({ ...spec, target: event.target.value })} /></label>
        <label>UniProt accession（可选）<input maxLength={16} placeholder="如 P00533" value={spec.uniprot_accession} onChange={(event) => setSpec({ ...spec, uniprot_accession: event.target.value.toUpperCase() })} /></label>
        <label>任务类型<select value={spec.assembly} onChange={(event) => setSpec({ ...spec, assembly: event.target.value as StudySpec["assembly"] })}><option value="monomer">单体</option><option value="multimer">多聚体 / 复合物</option></select></label>
        <label className="wide-label">氨基酸序列（新序列任务可填）<textarea maxLength={4000} placeholder="只填写氨基酸单字母序列；首版最多 2000 aa" value={spec.sequence} onChange={(event) => setSpec({ ...spec, sequence: event.target.value })} /></label>
        <label className="wide-label">研究问题<textarea required maxLength={2000} value={spec.research_question} onChange={(event) => setSpec({ ...spec, research_question: event.target.value })} /></label>
      </div><button disabled={busy}>保存结构研究</button>
    </form>}
    {!!studies.length && <div className="protein-study-picker"><label><span>我的结构研究</span><select value={active} disabled={busy} onChange={(event) => { setActive(event.target.value); setMessage(""); setError(""); }}>{studies.map((row) => <option key={row.id} value={row.id}>{row.spec.name} · {row.spec.target}</option>)}</select></label><small>先看模型来源和置信度，再选择实验结构比较与功能验证。</small></div>}
    {study && <>
      <div className="protein-command"><div><span className="process-stage">{study.model ? "结构已导入" : "等待模型"}</span><h4>{study.spec.target}</h4><p>{study.spec.research_question}</p><small>{study.spec.uniprot_accession ? `UniProt ${study.spec.uniprot_accession}` : "未填写 UniProt"} · {study.spec.assembly === "monomer" ? "单体" : "多聚体 / 复合物"}</small></div><div><strong>重计算状态：尚未配置</strong><p>{study.prediction_task.message}</p>{study.prediction_task.sequence_sha256 && <small>输入序列 SHA-256：{study.prediction_task.sequence_sha256}</small>}</div></div>
      {!study.model && <section className="protein-actions"><article><h4>已知蛋白</h4><p>按 UniProt 从 AlphaFold DB 取得公开单体预测并保留版本与 URL。</p><button disabled={busy || !study.spec.uniprot_accession} onClick={loadAlphaFold}>获取 AlphaFold DB 预测</button></article><article><h4>已有计算结果</h4><label>模型来源<select value={modelSource} onChange={(event) => setModelSource(event.target.value)}><option value="alphafold">AlphaFold</option><option value="colabfold">ColabFold</option><option value="rosettafold">RoseTTAFold</option><option value="experimental">实验结构</option><option value="other">其他</option></select></label><input aria-label="模型PDB" type="file" accept=".pdb,text/plain" onChange={(event) => setModelFile(event.target.files?.[0] ?? null)} /><button disabled={busy || !modelFile} onClick={uploadModel}>解析并保存 PDB</button></article></section>}
      {study.model && <>
        <section className="protein-model-summary"><div><small>模型来源</small><strong>{sourceLabels[study.model.source] ?? study.model.source}</strong><span>{study.model.source_id}</span></div><div><small>结构范围</small><strong>{study.model.residue_count} aa</strong><span>{study.model.chain_count} 条链 · {Object.entries(study.model.chain_lengths).map(([chain, length]) => `${chain}:${length}`).join(" · ")}</span></div><div><small>局部置信度</small><strong>{study.model.confidence ? `${study.model.confidence.mean} pLDDT` : "不适用"}</strong><span>{study.model.confidence ? `≥70：${percent(study.model.confidence.confident_fraction)}` : "实验 B-factor 不作为 pLDDT"}</span></div></section>
        {study.model.sequence_check && <div className="protein-confidence"><span>输入序列核对：{study.model.sequence_check.exact ? "与坐标链完全一致" : `与链 ${study.model.sequence_check.chain} 部分匹配`}</span><span>输入覆盖：{percent(study.model.sequence_check.input_coverage)}</span><span>序列一致：{percent(study.model.sequence_check.identity)}</span><p>序列不完全一致时请先核对亚型、缺失残基、标签和构建体，再解释结构比较。</p></div>}
        <ProteinCartoonViewer base={base} study={study} />
        <details className="protein-trace-details"><summary>查看 Cα 主链技术视图</summary><StructureProjection structure={study.model} /></details>
        {study.model.confidence && <div className="protein-confidence"><span>pLDDT ≥90：{percent(study.model.confidence.very_high_fraction)}</span><span>pLDDT ≥70：{percent(study.model.confidence.confident_fraction)}</span><span>pLDDT &lt;50：{percent(study.model.confidence.low_fraction)}</span><p>{study.model.confidence.boundary}</p></div>}
        <details><summary>模型来源与校验信息</summary><p>文件：{study.model.file_name}</p><p>SHA-256：<code>{study.model.sha256}</code></p><p>{Object.entries(study.model.provenance).map(([key, value]) => `${key}: ${value ?? "—"}`).join(" · ")}</p>{typeof study.model.provenance.pae_image_url === "string" && study.model.provenance.pae_image_url && <a href={study.model.provenance.pae_image_url} target="_blank" rel="noreferrer">查看 AlphaFold DB PAE 图</a>}</details>
        <h4>与参考结构比较</h4><p className="process-help">优先选择同一蛋白的实验结构。系统按序列选择可比链并叠合 Cα；这不是跨折叠空间搜索。</p>
        <form onSubmit={comparePdb} className="protein-reference-form"><label>PDB ID<input required minLength={4} maxLength={4} placeholder="如 1M17" value={pdbId} onChange={(event) => setPdbId(event.target.value.toUpperCase())} /></label><label>指定参考链（可选）<input maxLength={1} value={referenceChain} onChange={(event) => setReferenceChain(event.target.value)} /></label><button disabled={busy}>从 RCSB PDB 获取并比较</button></form>
        <details><summary>上游不可用或有内部结构：人工上传参考 PDB</summary><div className="workbench-controls"><select aria-label="参考结构类型" value={referenceKind} onChange={(event) => setReferenceKind(event.target.value)}><option value="experimental">实验结构</option><option value="predicted">预测结构</option></select><input aria-label="参考PDB" type="file" accept=".pdb,text/plain" onChange={(event) => setReferenceFile(event.target.files?.[0] ?? null)} /><button disabled={busy || !referenceFile} onClick={compareUpload}>上传并比较</button></div></details>
        {!!study.comparisons.length && <div className="table-scroll"><table><thead><tr><th>参考结构</th><th>配准范围</th><th>结构一致性</th><th>解释边界</th></tr></thead><tbody>{study.comparisons.map((row) => <tr key={row.id}><td><strong>{sourceLabels[row.reference.source] ?? row.reference.source}</strong><small>{row.reference.source_id} · {row.reference.file_name}</small></td><td>模型链 {row.metrics.model_chain} / 参考链 {row.metrics.reference_chain}<small>{row.metrics.aligned_residues} aa · 序列一致 {percent(row.metrics.sequence_identity)} · 覆盖 {percent(row.metrics.reference_coverage)}</small></td><td>Cα RMSD {row.metrics.ca_rmsd_angstrom} Å<small>序列配准 TM 样式一致度 {row.metrics.sequence_registered_tm_like}</small></td><td>{row.interpretation}<details><summary>指标限制</summary>{row.metrics.metric_boundary}</details></td></tr>)}</tbody></table></div>}
      </>}
      <h4>独立实验验证</h4><p className="process-help">先写判据，再记录真实结果。结构比较不会自动填充或改变这些结论。</p>
      <form onSubmit={saveValidation}><div className="workbench-form"><label>验证方法<select value={validation.method} onChange={(event) => setValidation({ ...validation, method: event.target.value as ValidationMethod })}>{Object.entries(methodLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><label>状态<select value={validation.status} onChange={(event) => setValidation({ ...validation, status: event.target.value as Validation["status"], conclusion: event.target.value === "planned" ? "pending" : validation.conclusion })}><option value="planned">待执行</option><option value="completed">已完成</option><option value="failed">失败 / 中止</option></select></label><label>预设判据<input required maxLength={1000} placeholder="如 Kd < 10 nM" value={validation.criterion} onChange={(event) => setValidation({ ...validation, criterion: event.target.value })} /></label><label>结论<select disabled={validation.status === "planned"} value={validation.conclusion} onChange={(event) => setValidation({ ...validation, conclusion: event.target.value as Validation["conclusion"] })}><option value="pending">待判断</option><option value="supports">支持</option><option value="inconclusive">不确定</option><option value="contradicts">不支持</option></select></label><label>实测结果<textarea required={validation.status === "completed"} maxLength={2000} value={validation.result} onChange={(event) => setValidation({ ...validation, result: event.target.value })} /></label><label>来源 / 批次<input maxLength={1000} value={validation.source} onChange={(event) => setValidation({ ...validation, source: event.target.value })} /></label><label>备注<textarea maxLength={2000} value={validation.notes} onChange={(event) => setValidation({ ...validation, notes: event.target.value })} /></label></div><button disabled={busy}>保存验证记录</button></form>
      {!!study.validations.length && <div className="table-scroll"><table><thead><tr><th>方法</th><th>判据</th><th>结果</th><th>结论与来源</th></tr></thead><tbody>{study.validations.map((row) => <tr key={row.id}><td>{methodLabels[row.method]}<small>{row.status === "planned" ? "待执行" : row.status === "completed" ? "已完成" : "失败 / 中止"}</small></td><td>{row.criterion}</td><td>{row.result || "尚无实测结果"}</td><td>{conclusionLabel(row.conclusion)}<small>{row.source || "未填写来源"}</small>{row.notes && <details><summary>备注</summary>{row.notes}</details>}</td></tr>)}</tbody></table></div>}
    </>}
  </section>;
}

type MolViewer = {
  addModel: (data: string, format: string) => unknown;
  setStyle: (selection: Record<string, unknown>, style: Record<string, unknown>) => unknown;
  setBackgroundColor: (color: string, alpha?: number) => unknown;
  setViewStyle: (style: Record<string, unknown>) => unknown;
  zoomTo: () => unknown;
  zoom: (factor: number) => unknown;
  rotate: (angle: number, axis: string) => unknown;
  resize: () => unknown;
  render: () => unknown;
  clear: () => unknown;
};

function ProteinCartoonViewer({ base, study }: { base: string; study: Study }) {
  const host = useRef<HTMLDivElement>(null);
  const viewer = useRef<MolViewer | null>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [viewerError, setViewerError] = useState("");

  useEffect(() => {
    let live = true;
    let resizeObserver: ResizeObserver | null = null;
    const hostElement = host.current;
    async function load() {
      setStatus("loading"); setViewerError("");
      try {
        const [pdb, module] = await Promise.all([
          apiText(`${base}/studies/${study.id}/model/coordinates`),
          import("3dmol"),
        ]);
        if (!live || !hostElement) return;
        const createViewer = (module as unknown as { createViewer: (element: HTMLElement, options: Record<string, unknown>) => MolViewer }).createViewer;
        hostElement.replaceChildren();
        const instance = createViewer(hostElement, { backgroundColor: "#f5faf8", antialias: true });
        instance.addModel(pdb, "pdb");
        instance.setBackgroundColor("#f5faf8", 0);
        instance.setViewStyle({ style: "outline", color: "#d5e8e2", width: .11 });
        instance.setStyle({}, { cartoon: study.model?.confidence ? {
          arrows: true, style: "edged", thickness: .42,
          colorfunc: (atom: { b?: number }) => confidenceNumber(atom.b ?? 0),
        } : { arrows: true, style: "edged", thickness: .42, color: "spectrum" } });
        instance.zoomTo();
        instance.render();
        viewer.current = instance;
        resizeObserver = new ResizeObserver(() => { instance.resize(); instance.render(); });
        resizeObserver.observe(hostElement);
        setStatus("ready");
      } catch (reason) {
        if (!live) return;
        setViewerError(reason instanceof Error ? reason.message : "完整结构加载失败");
        setStatus("error");
      }
    }
    load();
    return () => {
      live = false;
      resizeObserver?.disconnect();
      viewer.current?.clear();
      viewer.current = null;
      hostElement?.replaceChildren();
    };
  }, [base, study.id, study.model?.sha256, study.model?.confidence]);

  function operate(action: (instance: MolViewer) => void) {
    if (!viewer.current) return;
    action(viewer.current);
    viewer.current.render();
  }

  return <figure className="protein-cartoon">
    <figcaption><div><strong>三维丝带结构</strong><small>拖动旋转 · 滚轮缩放 · 右键平移</small></div><span>Cartoon · 完整 PDB 坐标</span></figcaption>
    <div className="protein-cartoon-stage">
      <div ref={host} className="protein-cartoon-canvas" aria-label="蛋白质三维丝带结构查看器" />
      {status === "loading" && <div className="protein-cartoon-status" role="status">正在生成丝带结构…</div>}
      {status === "error" && <div className="protein-cartoon-status error" role="alert"><strong>丝带结构暂时无法显示</strong><span>{viewerError}</span><small>下方仍可打开 Cα 主链技术视图。</small></div>}
      {status === "ready" && <div className="protein-cartoon-controls" aria-label="丝带结构控制"><button type="button" aria-label="缩小丝带结构" onClick={() => operate((instance) => { instance.zoom(.82); })}><ZoomOut size={17} /></button><button type="button" aria-label="向左旋转丝带结构" onClick={() => operate((instance) => { instance.rotate(-15, "y"); })}>左转</button><button type="button" aria-label="重置丝带结构视角" onClick={() => operate((instance) => { instance.zoomTo(); })}><RotateCcw size={17} /><span>重置</span></button><button type="button" aria-label="向右旋转丝带结构" onClick={() => operate((instance) => { instance.rotate(15, "y"); })}>右转</button><button type="button" aria-label="放大丝带结构" onClick={() => operate((instance) => { instance.zoom(1.2); })}><ZoomIn size={17} /></button></div>}
    </div>
    <div className="protein-viewer-legend">{study.model?.confidence ? <><span><i style={{ background: "#145af2" }} />很高 ≥90</span><span><i style={{ background: "#19a7ce" }} />可信 70–89</span><span><i style={{ background: "#f0c846" }} />较低 50–69</span><span><i style={{ background: "#ef7a45" }} />很低 &lt;50</span></> : <span>颜色按蛋白链和残基顺序区分</span>}</div>
    <p className="protein-viewer-boundary">丝带形状展示 α-螺旋、β-折叠与环区；颜色仍只表达模型置信度，不代表功能或疗效。</p>
  </figure>;
}

function StructureProjection({ structure }: { structure: Structure }) {
  const colors = ["#087e6b", "#315b8a", "#b66b2f", "#7b4f9d", "#a63d57"];
  const chains = useMemo(() => structure.viewer?.chains ?? {}, [structure.viewer]);
  const [rotation, setRotation] = useState({ x: -0.38, y: 0.58 });
  const [zoom, setZoom] = useState(1);
  const drag = useRef<{ x: number; y: number } | null>(null);
  const projected = useMemo(() => projectTrace(chains, rotation, zoom), [chains, rotation, zoom]);

  function startDrag(event: ReactPointerEvent<SVGSVGElement>) {
    drag.current = { x: event.clientX, y: event.clientY };
    event.currentTarget.setPointerCapture(event.pointerId);
  }
  function moveDrag(event: ReactPointerEvent<SVGSVGElement>) {
    if (!drag.current) return;
    const previous = drag.current;
    drag.current = { x: event.clientX, y: event.clientY };
    setRotation((current) => ({ x: current.x + (event.clientY - previous.y) * .009, y: current.y + (event.clientX - previous.x) * .009 }));
  }
  function stopDrag(event: ReactPointerEvent<SVGSVGElement>) {
    drag.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  }

  if (!Object.keys(chains).length) {
    return <figure className="protein-projection protein-projection-fallback"><figcaption><div><strong>结构主链概览</strong><small>当前记录暂缺三维浏览坐标</small></div><span>{structure.projection.method}</span></figcaption><svg viewBox="0 0 400 240" role="img" aria-label="蛋白质 Cα 骨架二维投影">{Object.entries(structure.projection.chains).map(([chain, points], index) => <polyline key={chain} points={points.map(([x, y]) => `${200 + x * 330},${120 + y * 190}`).join(" ")} fill="none" stroke={colors[index % colors.length]} strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round"><title>链 {chain}</title></polyline>)}</svg></figure>;
  }
  const viewer = structure.viewer!;

  return <figure className="protein-projection protein-viewer">
    <figcaption><div><strong>交互式三维主链视图</strong><small>拖动旋转 · 滚轮或按钮缩放</small></div><span>{viewer.sampled_residues === viewer.total_residues ? `${viewer.total_residues} 个 Cα 坐标` : `显示 ${viewer.sampled_residues} / ${viewer.total_residues} 个 Cα 坐标`}</span></figcaption>
    <div className="protein-viewer-stage">
      <svg viewBox="0 0 800 460" role="img" aria-label="蛋白质 Cα 主链交互式三维视图" onPointerDown={startDrag} onPointerMove={moveDrag} onPointerUp={stopDrag} onPointerCancel={stopDrag} onWheel={(event) => { event.preventDefault(); setZoom((value) => clamp(value + (event.deltaY < 0 ? .1 : -.1), .65, 1.65)); }}>
        <defs><filter id="protein-trace-shadow" x="-20%" y="-20%" width="140%" height="140%"><feDropShadow dx="0" dy="5" stdDeviation="5" floodColor="#062d27" floodOpacity=".2" /></filter><radialGradient id="protein-viewer-halo"><stop offset="0" stopColor="#d8f3eb" stopOpacity=".7" /><stop offset="1" stopColor="#eef7f4" stopOpacity="0" /></radialGradient></defs>
        <ellipse cx="400" cy="235" rx="310" ry="185" fill="url(#protein-viewer-halo)" />
        {projected.map((chain, index) => <g key={`${chain.name}-underlay`} filter="url(#protein-trace-shadow)"><path d={smoothPath(chain.points)} fill="none" stroke="#ffffff" strokeOpacity=".9" strokeWidth="9" strokeLinecap="round" strokeLinejoin="round" /><path d={smoothPath(chain.points)} fill="none" stroke={structure.confidence ? "#a8c9c1" : colors[index % colors.length]} strokeOpacity=".42" strokeWidth="6" strokeLinecap="round" strokeLinejoin="round"><title>链 {chain.name}</title></path></g>)}
        {projected.flatMap((chain, chainIndex) => chain.points.slice(1).map((point, index) => {
          const previous = chain.points[index];
          const depth = clamp((point.z + 1) / 2, 0, 1);
          return <line key={`${chain.name}-${index}`} x1={previous.x} y1={previous.y} x2={point.x} y2={point.y} stroke={structure.confidence ? confidenceColor((previous.confidence + point.confidence) / 2) : colors[chainIndex % colors.length]} strokeWidth={3.2 + depth * 2.4} strokeOpacity={.72 + depth * .25} strokeLinecap="round" />;
        }))}
      </svg>
      <div className="protein-viewer-controls" aria-label="三维视图控制"><button type="button" aria-label="缩小结构" onClick={() => setZoom((value) => clamp(value - .15, .65, 1.65))}><ZoomOut size={17} /></button><button type="button" aria-label="重置结构视角" onClick={() => { setRotation({ x: -.38, y: .58 }); setZoom(1); }}><RotateCcw size={17} /><span>重置</span></button><button type="button" aria-label="放大结构" onClick={() => setZoom((value) => clamp(value + .15, .65, 1.65))}><ZoomIn size={17} /></button></div>
    </div>
    <div className="protein-viewer-legend">{structure.confidence ? <><span><i style={{ background: "#145af2" }} />很高 ≥90</span><span><i style={{ background: "#19a7ce" }} />可信 70–89</span><span><i style={{ background: "#f0c846" }} />较低 50–69</span><span><i style={{ background: "#ef7a45" }} />很低 &lt;50</span></> : Object.keys(chains).map((chain, index) => <span key={chain}><i style={{ background: colors[index % colors.length] }} />链 {chain}</span>)}</div>
    <p className="protein-viewer-boundary">{viewer.boundary}</p>
  </figure>;
}

type ProjectedPoint = { x: number; y: number; z: number; confidence: number };
function projectTrace(chains: Record<string, number[][]>, rotation: { x: number; y: number }, zoom: number) {
  const rows = Object.values(chains).flat();
  if (!rows.length) return [];
  const center = [0, 1, 2].map((axis) => rows.reduce((sum, point) => sum + (point[axis] ?? 0), 0) / rows.length);
  const centered = rows.map((point) => point.slice(0, 3).map((value, axis) => (value ?? 0) - center[axis]));
  const cosX = Math.cos(rotation.x), sinX = Math.sin(rotation.x), cosY = Math.cos(rotation.y), sinY = Math.sin(rotation.y);
  const rotated = centered.map(([x, y, z]) => {
    const rotatedX = x * cosY + z * sinY;
    const firstZ = -x * sinY + z * cosY;
    return [rotatedX, y * cosX - firstZ * sinX, y * sinX + firstZ * cosX];
  });
  const ranges = [0, 1, 2].map((axis) => ({ minimum: Math.min(...rotated.map((point) => point[axis])), maximum: Math.max(...rotated.map((point) => point[axis])) }));
  const scale = Math.min(650 / Math.max(ranges[0].maximum - ranges[0].minimum, 1), 350 / Math.max(ranges[1].maximum - ranges[1].minimum, 1));
  const midpointX = (ranges[0].minimum + ranges[0].maximum) / 2;
  const midpointY = (ranges[1].minimum + ranges[1].maximum) / 2;
  const depthSpan = Math.max(ranges[2].maximum - ranges[2].minimum, 1);
  let cursor = 0;
  return Object.entries(chains).map(([name, points]) => ({ name, points: smoothTrace(points.map((point) => {
    const [rotatedX, rotatedY, rotatedZ] = rotated[cursor++];
    const perspective = 1 + (rotatedZ - ranges[2].minimum - depthSpan / 2) / (depthSpan * 7);
    return { x: 400 + (rotatedX - midpointX) * scale * zoom * perspective, y: 230 - (rotatedY - midpointY) * scale * zoom * perspective, z: clamp((rotatedZ - ranges[2].minimum) / depthSpan * 2 - 1, -1, 1), confidence: point[3] ?? 0 };
  })) }));
}
function smoothTrace(points: ProjectedPoint[]) {
  const smoothed = points.map((point, index) => {
    const start = Math.max(0, index - 2), end = Math.min(points.length - 1, index + 2);
    const neighbors = points.slice(start, end + 1);
    return {
      x: neighbors.reduce((sum, row) => sum + row.x, 0) / neighbors.length,
      y: neighbors.reduce((sum, row) => sum + row.y, 0) / neighbors.length,
      z: neighbors.reduce((sum, row) => sum + row.z, 0) / neighbors.length,
      confidence: point.confidence,
    };
  });
  if (smoothed.length <= 460) return smoothed;
  return Array.from({ length: 460 }, (_, index) => smoothed[Math.round(index * (smoothed.length - 1) / 459)]);
}
function smoothPath(points: ProjectedPoint[]) {
  if (!points.length) return "";
  if (points.length === 1) return `M ${points[0].x} ${points[0].y}`;
  let path = `M ${points[0].x} ${points[0].y}`;
  for (let index = 1; index < points.length - 1; index++) {
    const point = points[index], next = points[index + 1];
    path += ` Q ${point.x} ${point.y} ${(point.x + next.x) / 2} ${(point.y + next.y) / 2}`;
  }
  const last = points[points.length - 1];
  return `${path} L ${last.x} ${last.y}`;
}
function confidenceColor(value: number) { return value >= 90 ? "#145af2" : value >= 70 ? "#19a7ce" : value >= 50 ? "#f0c846" : "#ef7a45"; }
function confidenceNumber(value: number) { return value >= 90 ? 0x145af2 : value >= 70 ? 0x19a7ce : value >= 50 ? 0xf0c846 : 0xef7a45; }
function clamp(value: number, minimum: number, maximum: number) { return Math.min(maximum, Math.max(minimum, value)); }
function percent(value: number) { return `${Math.round(value * 100)}%`; }
function conclusionLabel(value: Validation["conclusion"]) { return value === "supports" ? "支持预设判据" : value === "contradicts" ? "不支持预设判据" : value === "inconclusive" ? "结果不确定" : "尚未判断"; }
