"use client";

import type { Job } from "@drug-discovery/contracts";
import { AlertTriangle, BookOpen, Check, ChevronDown, Database, Download, ExternalLink, FlaskConical, LoaderCircle, Play, Search, ServerCog, Sparkles, UploadCloud, X } from "lucide-react";
import { FormEvent, useEffect, useMemo, useState } from "react";
import { api, apiBase } from "@/lib/api";
import { FungalCompoundExploration } from "./FungalCompoundExploration";

type Workflow = "prioritization" | "target_fishing";
type ProteinWorkflow = "prioritization" | "target_fishing";
type Asset = { id: string; name: string; kind: string; size: number; content_hash: string; format?: string };
type Preview = {
  asset_id: string;
  asset_name: string;
  workflow: Workflow;
  row_count: number;
  columns: string[];
  metric_coverage: Record<string, number>;
  optional_coverage: Record<string, number>;
  warnings: string[];
  ready: boolean;
};
type Candidate = {
  rank: number;
  protein_id: string;
  gene_symbol: string;
  protein_name?: string;
  pathway?: string;
  candidate_score: number;
  evidence_coverage: number;
  priority_tier: "high_priority" | "review" | "evidence_gap";
  human_similarity?: number | null;
  pathogen_conservation?: number | null;
  druggability?: number | null;
  pocket_score?: number | null;
  top_signals: { metric: string; value: number; contribution: number }[];
  missing_evidence: string[];
  notes?: string;
};
type Result = {
  job_id: string;
  workflow: Workflow;
  organism: string;
  compound?: { name: string; structure: string } | null;
  input: { asset_name: string; row_count: number; sha256: string };
  thresholds: { evidence: number; human_similarity: number; top_n: number };
  weights: Record<string, number>;
  human_similarity_penalty: number;
  funnel: { id: string; label: string; count: number }[];
  candidates: Candidate[];
  all_candidate_count: number;
  warnings: string[];
  boundary: string;
  model_version: string;
  generated_at: string;
  example_mode?: boolean;
  data_mode?: string;
  bundle_version?: string;
  score_interpretation?: string;
};
type DataStatus = {
  configured: boolean;
  bundle_version?: string;
  generated_at?: string;
  message: string;
  counts: Record<string, number>;
  sources: { id: string; name: string; version: string; url: string; license: string }[];
  algorithms: { name: string; version: string; purpose: string; parameters: string }[];
};
type ExampleCase = {
  case_id: string;
  case_version: string;
  title: string;
  species: string;
  strain: string;
  organism: string;
  summary: string;
  identifiers: { bioproject: string; assembly: string; assembly_name: string };
  genome: { assembly_level: string; chromosome_count: number; protein_sequence_count: number; analysis_protein_count?: number; annotation_context: string };
  isolation: { year: number; source: string };
  why_selected: string[];
  caveats: string[];
  sources: { id: string; title: string; url: string; supports: string }[];
  evidence_bundle?: DataStatus;
  workflows: Record<Workflow, Result>;
};

const workflowCopy: Record<ProteinWorkflow, { title: string; eyebrow: string; description: string; steps: { title: string; detail: string }[] }> = {
  prioritization: {
    title: "从真菌蛋白组优选候选药物靶标",
    eyebrow: "Fungal Target Prioritization",
    description: "整合必需性、毒力、感染表达、宿主差异、可成药性和致病真菌保守性，形成可追溯的候选优先级。",
    steps: [
      { title: "蛋白组与身份", detail: "UniProt / RefSeq / FungiDB 标识" },
      { title: "生物学重要性", detail: "必需、毒力、感染与耐药证据" },
      { title: "宿主差异", detail: "人蛋白组相似性与排除阈值" },
      { title: "可成药性", detail: "酶、结构、口袋与已知配体" },
      { title: "病原体保守性", detail: "广谱或物种特异开发价值" },
      { title: "候选排序", detail: "透明权重、覆盖度与待补证据" },
    ],
  },
  target_fishing: {
    title: "预测已有抗真菌化合物的候选作用靶标",
    eyebrow: "Compound–Target Prediction",
    description: "融合配体相似性、AI 预测、反向对接、通路与耐药证据；多类证据收敛后再进入实验验证。",
    steps: [
      { title: "化合物身份", detail: "名称与 SMILES / InChIKey" },
      { title: "候选蛋白空间", detail: "目标真菌蛋白组或预筛集合" },
      { title: "配体与 AI 预测", detail: "相似性和蛋白–化合物得分" },
      { title: "反向对接", detail: "结构口袋与对接证据" },
      { title: "机制证据", detail: "通路、耐药突变与遗传证据" },
      { title: "靶标去卷积", detail: "证据收敛、排序与验证计划" },
    ],
  },
};

const metricLabels: Record<string, string> = {
  essentiality: "必需性", virulence: "毒力", infection_expression: "感染表达", resistance_relevance: "耐药关联",
  druggability: "可成药性", pathogen_conservation: "病原体保守性", pocket_score: "口袋质量", human_similarity: "人同源性",
  ligand_similarity: "配体相似性", ai_prediction: "AI 预测", inverse_docking: "反向对接", pathway_evidence: "通路证据",
  resistance_evidence: "耐药证据", genetic_evidence: "遗传证据", known_ligand_evidence: "已知配体", structure_evidence: "结构证据",
};

const algorithmLabels: Record<string, string> = {
  DIAMOND: "序列同源比对",
  "Curated phenotype exact-ID integration": "表型证据精确映射",
  "Coverage-aware hard-gate ranking": "覆盖度感知分层筛选",
};

function percent(value?: number | null) {
  return value == null ? "待补" : `${Math.round(value * 100)}%`;
}

function readableBytes(value: number) {
  return value < 1024 * 1024 ? `${Math.max(1, Math.round(value / 1024))} KB` : `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function JobState({ job }: { job?: Job }) {
  if (!job) return <span className="fungal-job neutral">尚未运行</span>;
  const active = job.status === "queued" || job.status === "running";
  return <span className={`fungal-job ${job.status}`}>{active ? <LoaderCircle className="spin" size={14} /> : job.status === "completed" ? <Check size={14} /> : <AlertTriangle size={14} />}{job.status === "completed" ? "分析完成" : active ? `正在分析 · ${job.progress}%` : "任务失败"}</span>;
}

export function FungalTargetWorkbench({ projectId, assets, jobs, busy, run, runExploration }: {
  projectId: string;
  assets: Asset[];
  jobs: Job[];
  busy: string;
  run: (parameters: Record<string, unknown>) => void;
  runExploration: (parameters: Record<string, unknown>) => void;
}) {
  const [workflow, setWorkflow] = useState<Workflow>("prioritization");
  const [uploadedAssets, setUploadedAssets] = useState<Asset[]>([]);
  const [assetId, setAssetId] = useState("");
  const [organism, setOrganism] = useState("Candida albicans");
  const [compoundName, setCompoundName] = useState("");
  const [compoundStructure, setCompoundStructure] = useState("");
  const [topN, setTopN] = useState(50);
  const [evidenceThreshold, setEvidenceThreshold] = useState(0.5);
  const [humanThreshold, setHumanThreshold] = useState(0.35);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState("");
  const [uploading, setUploading] = useState(false);
  const [loadingExample, setLoadingExample] = useState(false);
  const [exampleCase, setExampleCase] = useState<ExampleCase | null>(null);
  const [dataStatus, setDataStatus] = useState<DataStatus | null>(null);
  const [query, setQuery] = useState("");
  // Target -> compound exploration is a separate job type, so it is a mode of
  // this workbench rather than a third fungal_target workflow.
  const [mode, setMode] = useState<"protein" | "exploration">("protein");
  const [candidatePage, setCandidatePage] = useState(1);

  const latestJob = useMemo(() => jobs.find((job) => job.type === "fungal_target" && job.parameters?.workflow === workflow), [jobs, workflow]);
  const eligibleAssets = useMemo(() => {
    const map = new Map([...uploadedAssets, ...assets].map((item) => [item.id, item]));
    return [...map.values()].filter((asset) => /\.(csv|tsv|txt)$/i.test(asset.name));
  }, [assets, uploadedAssets]);

  useEffect(() => {
    let cancelled = false;
    api<DataStatus>("/fungal-targets/data-status").then((value) => { if (!cancelled) setDataStatus(value); }).catch(() => undefined);
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (!assetId) return;
    let cancelled = false;
    api<Preview>(`/projects/${projectId}/fungal-targets/preview`, {
      method: "POST", body: JSON.stringify({ asset_id: assetId, workflow }),
    }).then((value) => { if (!cancelled) setPreview(value); }).catch((reason) => { if (!cancelled) setError(reason instanceof Error ? reason.message : "证据矩阵预检失败"); });
    return () => { cancelled = true; };
  }, [assetId, projectId, workflow]);

  useEffect(() => {
    if (exampleCase) return;
    if (latestJob?.status !== "completed") return;
    let cancelled = false;
    api<Result>(`/jobs/${latestJob.id}/fungal-target-result`).then((value) => { if (!cancelled) setResult(value); }).catch((reason) => { if (!cancelled) setError(reason instanceof Error ? reason.message : "结果读取失败"); });
    return () => { cancelled = true; };
  }, [exampleCase, latestJob?.id, latestJob?.status, workflow]);

  function switchWorkflow(next: Workflow) {
    setMode("protein");
    setWorkflow(next);
    setPreview(null);
    setResult(exampleCase?.workflows[next] ?? null);
    setError("");
    setQuery("");
    setCandidatePage(1);
    if (exampleCase) {
      setOrganism(exampleCase.organism);
      setCompoundName(next === "target_fishing" ? "Fluconazole / 氟康唑" : "");
      setCompoundStructure(next === "target_fishing" ? "InChIKey=RFHAOTPXVQNOHP-UHFFFAOYSA-N" : "");
    }
  }

  async function loadExample() {
    setLoadingExample(true); setError("");
    try {
      const example = await api<ExampleCase>("/fungal-targets/examples/sc5314");
      setExampleCase(example);
      setWorkflow("prioritization");
      setResult(example.workflows.prioritization);
      setOrganism(example.organism);
      setCompoundName("");
      setCompoundStructure("");
      setAssetId("");
      setPreview(null);
      setQuery("");
      setCandidatePage(1);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "案例载入失败"); }
    finally { setLoadingExample(false); }
  }

  function closeExample() {
    setExampleCase(null);
    setResult(null);
    setOrganism("Candida albicans");
    setCompoundName("");
    setCompoundStructure("");
    setQuery("");
    setCandidatePage(1);
  }

  async function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    if (!(data.get("file") instanceof File) || !(data.get("file") as File).size) return;
    setUploading(true); setError("");
    try {
      const asset = await api<Asset>(`/projects/${projectId}/assets?kind=fungal-target-evidence`, { method: "POST", body: data });
      setUploadedAssets((current) => [asset, ...current.filter((item) => item.id !== asset.id)]);
      setPreview(null);
      setAssetId(asset.id);
      form.reset();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "上传失败"); }
    finally { setUploading(false); }
  }

  function submit() {
    setError("");
    if (!assetId || !preview) return setError("请先选择并通过预检的证据矩阵。");
    if (!organism.trim()) return setError("请填写目标真菌物种或菌株。");
    if (workflow === "target_fishing" && !compoundName.trim() && !compoundStructure.trim()) return setError("请填写化合物名称或 SMILES / InChIKey。");
    run({ workflow, asset_id: assetId, organism: organism.trim(), compound_name: compoundName.trim(), compound_structure: compoundStructure.trim(), top_n: topN, evidence_threshold: evidenceThreshold, human_similarity_threshold: humanThreshold });
  }

  const filteredCandidates = (result?.candidates ?? []).filter((candidate) => {
    const haystack = `${candidate.gene_symbol} ${candidate.protein_id} ${candidate.protein_name ?? ""} ${candidate.pathway ?? ""}`.toLowerCase();
    return haystack.includes(query.trim().toLowerCase());
  });
  const pageSize = 10;
  const candidatePages = Math.max(1, Math.ceil(filteredCandidates.length / pageSize));
  const activeCandidatePage = Math.min(candidatePage, candidatePages);
  const visibleCandidates = filteredCandidates.slice((activeCandidatePage - 1) * pageSize, activeCandidatePage * pageSize);
  const copy = workflowCopy[workflow];

  return <div className="fungal-target-workbench">
    <section className="fungal-hero">
      <div><span>{copy.eyebrow}</span><h2>{copy.title}</h2><p>{copy.description}</p></div>
    </section>

    {dataStatus && <section className={`fungal-data-readiness ${dataStatus.configured ? "ready" : "pending"}`}>
      <ServerCog size={19} />
      <div className="fungal-data-summary"><strong>{dataStatus.configured ? "SC5314 证据库已就绪" : "服务器证据库待配置"}</strong><p>{dataStatus.configured ? <>{dataStatus.counts.proteins?.toLocaleString() ?? 0} 蛋白 · {dataStatus.counts.cgd_phenotype_rows?.toLocaleString() ?? 0} CGD · {dataStatus.counts.phi_base_candida_albicans_rows?.toLocaleString() ?? 0} PHI-base</> : dataStatus.message}</p></div>
      {dataStatus.configured && <details className="fungal-data-details"><summary>数据与算法 <ChevronDown size={14} /></summary><div>{dataStatus.algorithms.map((item) => <article key={item.name}><strong>{algorithmLabels[item.name] ?? item.name}</strong><span>{item.name === "DIAMOND" ? `${item.name} ${item.version}` : item.version}</span><p>{item.purpose}</p><code>{item.parameters}</code></article>)}</div></details>}
    </section>}

    {!exampleCase ? <section className="fungal-example-launch">
      <div className="fungal-example-mark"><BookOpen size={22} /></div>
      <div><span>完整参考案例</span><h3>Candida albicans SC5314</h3><p>一键载入菌株档案、{dataStatus?.configured ? `${(dataStatus.counts.proteins ?? 0).toLocaleString()} 个蛋白的服务器证据优选` : "代表性蛋白优选"}，以及氟康唑靶标去卷积示范；无需上传文件。</p></div>
      <button className="primary-button" onClick={loadExample} disabled={loadingExample}>{loadingExample ? <LoaderCircle className="spin" size={16} /> : <Play size={16} />}{loadingExample ? "正在载入…" : "一键载入所有结果"}</button>
    </section> : <section className="fungal-case-card">
      <header><div><span>已载入 · {exampleCase.evidence_bundle?.configured ? "服务器版本化证据案例" : "文献整理案例"}</span><h3>{exampleCase.title}</h3><p>{exampleCase.summary}</p></div><button onClick={closeExample} aria-label="退出案例"><X size={16} />使用自己的数据</button></header>
      <div className="fungal-case-facts">
        <article><small>参考组装</small><strong>{exampleCase.identifiers.assembly}</strong><span>{exampleCase.identifiers.bioproject} · {exampleCase.genome.assembly_level}</span></article>
        <article><small>基因组概况</small><strong>{exampleCase.genome.chromosome_count} 条染色体</strong><span>NCBI {exampleCase.genome.protein_sequence_count.toLocaleString()} 条；本次分析 {exampleCase.genome.analysis_protein_count?.toLocaleString() ?? exampleCase.genome.protein_sequence_count.toLocaleString()} 条蛋白</span></article>
        <article><small>分离背景</small><strong>{exampleCase.isolation.year}</strong><span>{exampleCase.isolation.source}</span></article>
        <article><small>已载入分析</small><strong>2 条工作流</strong><span>靶标优选 + 氟康唑 target fishing；另可切换到未知化合物探索</span></article>
      </div>
      <details className="fungal-case-notes"><summary>案例解读与使用边界 <ChevronDown size={15} /></summary><div><ul>{exampleCase.why_selected.map((item) => <li key={item}>{item}</li>)}</ul><ul className="caveats">{exampleCase.caveats.map((item) => <li key={item}>{item}</li>)}</ul></div></details>
      <details className="fungal-case-sources"><summary>可核查来源 · {exampleCase.sources.length} 项 <ChevronDown size={14} /></summary><div>{exampleCase.sources.map((source) => <a key={source.id} href={source.url} target="_blank" rel="noreferrer" title={source.supports}>{source.title}<ExternalLink size={12} /></a>)}</div></details>
    </section>}

    <div className="fungal-mode-tabs" role="tablist" aria-label="真菌靶标任务类型">
      <button type="button" role="tab" aria-selected={mode === "protein" && workflow === "prioritization"} className={mode === "protein" && workflow === "prioritization" ? "active" : ""} onClick={() => switchWorkflow("prioritization")}><Database size={18} /><span><b>没有化合物</b><small>真菌靶标优选{exampleCase ? " · 案例结果已载入" : ""}</small></span></button>
      <button type="button" role="tab" aria-selected={mode === "protein" && workflow === "target_fishing"} className={mode === "protein" && workflow === "target_fishing" ? "active" : ""} onClick={() => switchWorkflow("target_fishing")}><FlaskConical size={18} /><span><b>已有活性化合物</b><small>候选靶标预测 / target fishing{exampleCase ? " · 案例结果已载入" : ""}</small></span></button>
      <button type="button" role="tab" aria-selected={mode === "exploration"} className={mode === "exploration" ? "active" : ""} onClick={() => { setMode("exploration"); setError(""); }}><Sparkles size={18} /><span><b>探索未知化合物</b><small>靶标 → 化合物 + 结构衍生</small></span></button>
    </div>

    {mode === "exploration" && <FungalCompoundExploration projectId={projectId} jobs={jobs} busy={busy} run={runExploration} />}
    {mode === "exploration" ? null : <>

    <section className="fungal-flow" aria-label="分析流程">
      {copy.steps.map((step, index) => <article key={step.title}><b>{String(index + 1).padStart(2, "0")}</b><span><strong>{step.title}</strong><small>{step.detail}</small></span></article>)}
    </section>

    {!exampleCase && <section className="fungal-setup">
      <header><div><span>01 · 输入与阈值</span><h3>配置一次可追溯分析</h3></div><JobState job={latestJob} /></header>
      <div className="fungal-input-grid">
        <label><span>目标真菌 / 菌株</span><input value={organism} onChange={(event) => setOrganism(event.target.value)} placeholder="如 Candida albicans SC5314" /></label>
        {workflow === "target_fishing" && <><label><span>化合物名称</span><input value={compoundName} onChange={(event) => setCompoundName(event.target.value)} placeholder="项目编号或通用名" /></label><label className="wide"><span>SMILES / InChIKey（推荐）</span><input value={compoundStructure} onChange={(event) => setCompoundStructure(event.target.value)} placeholder="用于固定化合物身份；首版不在浏览器内重新计算结构预测" /></label></>}
        <label><span>证据阈值</span><input type="number" min="0" max="1" step="0.05" value={evidenceThreshold} onChange={(event) => setEvidenceThreshold(Number(event.target.value))} /></label>
        <label><span>人同源性上限</span><input type="number" min="0" max="1" step="0.05" value={humanThreshold} onChange={(event) => setHumanThreshold(Number(event.target.value))} /></label>
        <label><span>输出候选数</span><input type="number" min="1" max="200" value={topN} onChange={(event) => setTopN(Number(event.target.value))} /></label>
      </div>
      <div className="fungal-file-row">
        <div className="fungal-file-select"><label><span>项目证据矩阵</span><select value={assetId} onChange={(event) => { setAssetId(event.target.value); setPreview(null); setError(""); }}><option value="">选择 CSV / TSV</option>{eligibleAssets.map((asset) => <option key={asset.id} value={asset.id}>{asset.name} · {readableBytes(asset.size)}</option>)}</select></label><a className="secondary-button" href={`${apiBase}/fungal-targets/templates/${workflow}`}><Download size={15} />下载字段模板</a></div>
        <form onSubmit={upload}><label className="fungal-upload"><UploadCloud size={17} /><span>{uploading ? "上传中…" : "上传证据矩阵"}</span><input name="file" type="file" accept=".csv,.tsv,.txt,text/csv,text/tab-separated-values" disabled={uploading} onChange={(event) => event.currentTarget.form?.requestSubmit()} /></label></form>
      </div>
      {preview && <div className="fungal-preview"><Check size={18} /><div><strong>预检通过 · {preview.row_count.toLocaleString()} 个蛋白</strong><p>{Object.entries(preview.metric_coverage).map(([metric, count]) => `${metricLabels[metric] ?? metric} ${count}`).join(" · ")}</p></div></div>}
      {(preview?.warnings ?? []).map((warning) => <p className="fungal-warning" key={warning}><AlertTriangle size={15} />{warning}</p>)}
      {error && <p className="fungal-error" role="alert"><AlertTriangle size={16} />{error}</p>}
      <div className="fungal-run"><p>缺失证据保留为“待补”，不会被静默当成阴性；评分同时展示证据覆盖度。</p><button className="primary-button" disabled={!preview || !!busy || latestJob?.status === "queued" || latestJob?.status === "running"} onClick={submit}><Play size={16} />{latestJob?.status === "queued" || latestJob?.status === "running" ? "分析运行中" : "生成候选靶标优先级"}</button></div>
    </section>}

    {!result && <section className="fungal-empty"><Search size={26} /><h3>等待证据矩阵</h3><p>先下载字段模板，汇总数据库、同源比对、结构和化合物预测结果，再由系统完成分层筛选与透明排序。</p></section>}

    {result && <section className="fungal-results">
      <header><div><span>02 · 候选结果{result.example_mode ? " · 预置案例" : ""}{result.data_mode === "server_evidence_bundle" ? " · 服务器真实证据库" : ""}</span><h3>{result.organism} · {workflow === "prioritization" ? "靶标优选" : `${result.compound?.name || "未命名化合物"} 靶标预测`}</h3><p>{result.input.asset_name} · 输入 {result.all_candidate_count.toLocaleString()} 个蛋白 · SHA-256 {result.input.sha256.slice(0, 12)}… · {new Date(result.generated_at).toLocaleString("zh-CN")}</p>{result.score_interpretation && <p className="fungal-score-note"><AlertTriangle size={13} />{result.score_interpretation}</p>}</div><strong>{result.candidates.length}<small>个候选进入榜单</small></strong></header>
      <div className="fungal-funnel">{result.funnel.map((stage, index) => <article key={stage.id}><span>{stage.label}</span><strong>{stage.count.toLocaleString()}</strong>{index < result.funnel.length - 1 && <i>→</i>}</article>)}</div>
      <details className="fungal-method"><summary>评分公式与边界 <ChevronDown size={15} /></summary><p>正向证据按可用字段归一化加权，覆盖度对总分做温和收缩；人同源性按 {Math.round(result.human_similarity_penalty * 100)}% 权重扣分。当前权重：{Object.entries(result.weights).map(([metric, weight]) => `${metricLabels[metric] ?? metric} ${Math.round(weight * 100)}%`).join("；")}。</p><p>{result.boundary}</p></details>
      <div className="fungal-result-tools"><label><Search size={15} /><input value={query} onChange={(event) => { setQuery(event.target.value); setCandidatePage(1); }} placeholder="搜索基因、蛋白 ID、名称或通路" /></label><span>匹配 {filteredCandidates.length} / {result.candidates.length}</span></div>
      <div className="table-card fungal-table"><table><thead><tr><th>排名 / 候选</th><th>候选分</th><th>关键证据</th><th>宿主差异 / 结构</th><th>证据缺口与验证</th></tr></thead><tbody>{visibleCandidates.map((candidate) => <tr key={`${candidate.protein_id}-${candidate.gene_symbol}`}><td><span className="fungal-rank">#{candidate.rank}</span><strong>{candidate.gene_symbol}</strong><small>{candidate.protein_name || "蛋白名称待补"}</small><code>{candidate.protein_id}</code>{candidate.pathway && <em>{candidate.pathway}</em>}</td><td><b className={`fungal-score ${candidate.priority_tier}`}>{Math.round(candidate.candidate_score * 100)}</b><small>覆盖 {percent(candidate.evidence_coverage)}</small><span className={`fungal-tier ${candidate.priority_tier}`}>{candidate.priority_tier === "high_priority" ? "优先复核" : candidate.priority_tier === "review" ? "继续评估" : "先补证据"}</span></td><td>{candidate.top_signals.map((signal) => <span className="fungal-signal" key={signal.metric}><b>{metricLabels[signal.metric] ?? signal.metric}</b>{percent(signal.value)}</span>)}</td><td><span><small>人同源性</small><b>{percent(candidate.human_similarity)}</b></span><span><small>病原体保守性</small><b>{percent(candidate.pathogen_conservation)}</b></span><span><small>可成药 / 口袋</small><b>{percent(candidate.druggability)} / {percent(candidate.pocket_score)}</b></span></td><td><p>{candidate.missing_evidence.length ? `待补：${candidate.missing_evidence.map((item) => metricLabels[item] ?? item).join("、")}` : "主要计算证据已覆盖"}</p><small>建议：基因扰动 + 耐药突变 + 直接结合或 chemoproteomics</small>{candidate.notes && <details className="fungal-candidate-evidence"><summary>查看计算证据</summary><p>{candidate.notes}</p></details>}</td></tr>)}{!visibleCandidates.length && <tr><td colSpan={5}><div className="inline-empty">没有符合搜索条件的候选。</div></td></tr>}</tbody></table></div>
      {filteredCandidates.length > pageSize && <nav className="fungal-pagination" aria-label="候选结果分页"><button disabled={activeCandidatePage === 1} onClick={() => setCandidatePage((page) => Math.max(1, page - 1))}>上一页</button><span>第 {activeCandidatePage} / {candidatePages} 页 · 每页 {pageSize} 条</span><button disabled={activeCandidatePage === candidatePages} onClick={() => setCandidatePage((page) => Math.min(candidatePages, page + 1))}>下一页</button></nav>}
      <p className="fungal-boundary"><AlertTriangle size={16} />{result.boundary}</p>
    </section>}
    </>}
  </div>;
}
