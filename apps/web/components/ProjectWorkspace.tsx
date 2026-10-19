"use client";
import { displayMoleculeName } from "@/lib/compoundPresentation";

import type { AdapterMode, Compound, DataSourceSpec, Job, MethodologySpec, PerturbationResult, Project, Ranking, ReportRecord, TargetCandidate, TargetCompatibility, TargetDiscoveryResult, TargetEvidence } from "@drug-discovery/contracts";
import {
  Activity, Atom, BarChart3, Check, ChevronRight, CircleAlert, ClipboardList, Database,
  BrainCircuit, Dna, FileText, FlaskConical, Layers3, LoaderCircle, Microscope, Network, Play,
  Search, ShieldCheck, Sparkles, UploadCloud, Settings2, Bug, Biohazard,
} from "lucide-react";
import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AppShell } from "./AppShell";
import { GenePerturbationView, VirtualCellView } from "./VirtualCellView";
import { DiseasePortrait } from "./DiseasePortrait";
import { QsarPanel } from "./QsarPanel";
import { MoleculeValidation } from "./MoleculeValidation";
import { TargetMolecules } from "./TargetMolecules";
import { ProcessWorkbench } from "./ProcessWorkbench";
import { ProteinStructureWorkbench } from "./ProteinStructureWorkbench";
import { MoleculeWorkbench } from "./MoleculeWorkbench";
import { OmicsAnalysisWorkbench } from "./OmicsAnalysisWorkbench";
import { FungalDiscoveryHub } from "./FungalDiscoveryHub";
import { BacterialDiscoveryHub } from "./BacterialDiscoveryHub";
import { DiseaseProjectPicker } from "./DiseaseProjectPicker";
import { useAuthUser } from "./AuthSession";
import { CompoundReadout, CompoundReasoning, useProjectQsar } from "./ResultInterpretation";
import { MoleculeComparison } from "./MoleculeComparison";
import { toggleComparison } from "@/lib/drugDossier";
import { MoleculeDetails, MoleculeTargetDetails, MoleculePredictions, MoleculeActivityChart } from "./MoleculeDetails";
import { compoundDetails, developmentStage, filterCompounds, matchesFor, predictionCount, type CompoundDetails } from "@/lib/compoundInsights";
import { TargetReviewDetail } from "./TargetReviewDetail";
import { EnterpriseDecisionCenter } from "./EnterpriseDecisionCenter";
import { NextStepBar, type WorkspaceGuidePage } from "./WorkspaceGuidance";
import { api, apiBase } from "@/lib/api";
import { submitAnalysis } from "@/lib/jobSubmission";
import { buildCandidateDecisionSupport, buildProjectDecisionBrief } from "@/lib/decisionSupport";
import { analysisQueryForDisease } from "@/lib/diseaseCatalog";
import { GENERAL_WORKSPACE_DISEASE, isGeneralWorkspace, lastWorkspaceProjectKey } from "@/lib/workspaceProjects";

type View = WorkspaceGuidePage;
type Asset = { id: string; name: string; kind: string; size: number; content_hash: string; version: number };
type DiscoveryStrategy = "established" | "novel";
type TargetToHitProfile = NonNullable<NonNullable<TargetCandidate["evidence_breakdown"]>["target_to_hit"]>;

const nav: { id: View; label: string; icon: React.ReactNode }[] = [
  { id: "overview", label: "项目总览", icon: <Layers3 size={18} /> },
  { id: "data", label: "数据管理", icon: <Database size={18} /> },
  { id: "omics", label: "测序数据分析", icon: <Microscope size={18} /> },
  { id: "targets", label: "靶点发现", icon: <Dna size={18} /> },
  { id: "fungal_targets", label: "真菌靶标", icon: <Bug size={18} /> },
  { id: "bacterial_targets", label: "细菌靶标", icon: <Biohazard size={18} /> },
  { id: "perturbation", label: "虚拟干预", icon: <Activity size={18} /> },
  { id: "gene_perturbation", label: "基因敲除", icon: <Dna size={18} /> },
  { id: "virtual_cell", label: "虚拟细胞", icon: <Network size={18} /> },
  { id: "protein_structure", label: "蛋白质结构", icon: <Atom size={18} /> },
  { id: "compounds", label: "化合物评价", icon: <FlaskConical size={18} /> },
  { id: "process", label: "工艺开发与优化", icon: <Settings2 size={18} /> },
  { id: "ranking", label: "候选决策", icon: <BarChart3 size={18} /> },
  { id: "methodology", label: "数据与方法", icon: <BrainCircuit size={18} /> },
  { id: "report", label: "决策报告", icon: <FileText size={18} /> },
];

const stages = [
  ["target_discovery", "靶点发现"], ["perturbation", "虚拟干预"], ["gene_knockout", "基因敲除"], ["compound_matching", "化合物匹配"], ["admet", "ADMET 评价"], ["ranking", "候选决策"],
];

function score(value?: number) { return value == null ? "—" : value.toFixed(2); }
function preciseScore(value?: number) { return value == null ? "—" : value.toFixed(3); }
function bytes(value: number) { return value < 1024 * 1024 ? `${Math.max(1, Math.round(value / 1024))} KB` : `${(value / 1024 / 1024).toFixed(1)} MB`; }

const jobStatusLabels: Record<Job["status"], string> = {
  queued: "排队中",
  running: "运行中",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
};

const adapterModeLabels: Record<AdapterMode, string> = {
  real: "真实数据",
  precomputed: "预计算演示",
  not_configured: "未配置",
  runtime: "真实数据",
};

const evidenceCategoryLabels: Record<string, string> = {
  causal_confidence: "因果证据",
  disease_context: "疾病情境",
  development_feasibility: "开发可行性",
  hgnc_identity: "HGNC 身份映射",
  hpa_context: "HPA 组织表达情境",
  known_drug_label: "已知药物留出标签",
  genetics: "遗传学",
  expression: "表达证据",
  pathway: "通路证据",
  functional: "功能实验",
  somatic: "体细胞变异",
  animal: "动物模型",
  known_drug: "已知药物",
  literature: "文献证据",
  safety: "安全性",
  druggability: "可成药性",
  gwas: "GWAS 遗传关联",
  eqtl: "eQTL 共定位",
  single_cell: "疾病单细胞",
  network: "网络传播",
  novelty: "创新性",
  "遗传学": "遗传学",
  "表达": "表达证据",
  "网络": "网络传播",
};

const evidenceDatatypeLabels: Record<string, string> = {
  genetic_association: "遗传关联",
  genetic_literature: "遗传学文献",
  somatic_mutation: "体细胞突变",
  rna_expression: "RNA 表达",
  affected_pathway: "受影响通路",
  animal_model: "动物模型",
  known_drug: "已知药物",
  gwas: "GWAS 遗传关联",
  eqtl: "eQTL 共定位",
  single_cell: "供体级单细胞差异",
  network: "蛋白互作网络传播",
};

const evidenceStatusLabels: Record<string, string> = {
  available: "可用",
  unavailable: "暂无数据",
  not_configured: "未配置",
};

const noveltyClassLabels: Record<string, string> = {
  emerging: "新兴候选",
  established: "已建立靶点",
  exploratory: "探索性候选",
  supported: "有证据支持",
};

const directionLabels: Record<string, string> = {
  inhibit: "建议抑制",
  activate: "建议激活",
  unknown: "方向未知",
  not_available: "暂无方向",
};

const modalityLabels: Record<string, string> = {
  small_molecule: "优先小分子",
  biologic: "优先抗体或蛋白药",
  small_molecule_or_biologic: "小分子与生物药均可评估",
  evidence_generation: "先补结构或可及性证据",
};

const hitReadinessLabels: Record<string, string> = {
  ready_for_hit_search: "可进入起始化合物搜索",
  prioritise_assay_and_structure: "先补实验或结构信息",
  evidence_gap: "关键起点证据不足",
};

const hitDecisionLabels: Record<string, string> = {
  start_hit_search: "可开始寻找起始化合物",
  validate_structure_or_assay: "先补结构或结合实验",
  validate_disease_biology: "先验证疾病作用方向",
  hold_for_evidence: "暂缓开发，优先补证据",
};

const routeStatusLabels: Record<string, string> = {
  supported: "已有较好起点",
  possible: "可以评估",
  needs_evidence: "需要补证据",
  not_preferred: "当前不优先",
};

const diseaseLabels: Record<string, string> = {
  EFO_0000768: "特发性肺纤维化",
  MONDO_0004975: "阿尔茨海默病",
  MONDO_0005180: "帕金森病",
  MONDO_0008383: "类风湿关节炎",
  MONDO_0005011: "克罗恩病",
  MONDO_0005148: "2 型糖尿病",
  HP_0001513: "肥胖症",
  MONDO_0005010: "冠状动脉疾病",
  MONDO_0005233: "非小细胞肺癌",
  MONDO_0007254: "乳腺癌",
};

function sourceLabel(value?: string) {
  if (!value) return "—";
  if (value === "AI for Science public-data snapshot") return "AI for Science 公共数据快照";
  if (value === "open_targets") return "Open Targets";
  return value
    .replace("Open Targets curated genetics", "Open Targets 整理的遗传学证据")
    .replace("Open Targets genetics", "Open Targets 遗传学证据");
}

function diseaseLabel(diseaseId?: string, canonicalName?: string) {
  const translated = diseaseId ? diseaseLabels[diseaseId] : undefined;
  return translated && canonicalName ? `${translated}（${canonicalName}）` : translated ?? canonicalName ?? "未解析疾病";
}

export function ProjectWorkspace({ projectId }: { projectId: string }) {
  const user = useAuthUser();
  const [view, setView] = useState<View>("overview");
  const [project, setProject] = useState<Project | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [targets, setTargets] = useState<TargetCandidate[]>([]);
  const [targetResult, setTargetResult] = useState<TargetDiscoveryResult | null>(null);
  const [virtualCompatibility, setVirtualCompatibility] = useState<TargetCompatibility | null>(null);
  const [knockoutCompatibility, setKnockoutCompatibility] = useState<TargetCompatibility | null>(null);
  const [cellCompatibility, setCellCompatibility] = useState<TargetCompatibility | null>(null);
  const [compoundCompatibility, setCompoundCompatibility] = useState<TargetCompatibility | null>(null);
  const [perturbations, setPerturbations] = useState<PerturbationResult[]>([]);
  const [knockouts, setKnockouts] = useState<PerturbationResult[]>([]);
  const [virtualCells, setVirtualCells] = useState<PerturbationResult[]>([]);
  const [compounds, setCompounds] = useState<Compound[]>([]);
  const [rankings, setRankings] = useState<Ranking[]>([]);
  const [reports, setReports] = useState<ReportRecord[]>([]);
  const [methodology, setMethodology] = useState<MethodologySpec[]>([]);
  const [dataSources, setDataSources] = useState<DataSourceSpec[]>([]);
  const [targetStrategy, setTargetStrategy] = useState<DiscoveryStrategy>("established");
  const [error, setError] = useState("");
  const [cacheMessage, setCacheMessage] = useState("");
  const [busy, setBusy] = useState("");
  const refreshInFlight = useRef(false);

  useEffect(() => {
    const requested = new URLSearchParams(window.location.search).get("view");
    const timer = nav.some((item) => item.id === requested) ? window.setTimeout(() => setView(requested as View), 0) : null;
    if (user) window.localStorage.setItem(lastWorkspaceProjectKey(user.username), projectId);
    return () => { if (timer !== null) window.clearTimeout(timer); };
  }, [projectId, user]);

  useEffect(() => {
    if (!user) return;
    // 进入项目 / 切换模块留痕；后端 10 分钟内按账号、项目和页面去重。
    void api(`/projects/${projectId}/activity`, {
      method: "POST",
      body: JSON.stringify({ view }),
    }).catch(() => undefined);
  }, [projectId, view, user]);

  const refresh = useCallback(async () => {
    if (refreshInFlight.current) return;
    refreshInFlight.current = true;
    try {
      // Let the workspace render as soon as the lightweight project record is
      // available. Large result payloads (especially compound evidence) keep
      // loading in the background instead of blocking the entire first screen.
      const projectPromise = api<Project>(`/projects/${projectId}`).then((value) => {
        setProject(value);
        return value;
      });
      const [p, j, a, pe, ko, vc, c, r, rp, methods, sources] = await Promise.all([
        projectPromise, api<Job[]>(`/projects/${projectId}/jobs`), api<Asset[]>(`/projects/${projectId}/assets`),
        api<PerturbationResult[]>(`/projects/${projectId}/perturbations?module=virtual_intervention`),
        api<PerturbationResult[]>(`/projects/${projectId}/perturbations?module=gene_knockout`),
        api<PerturbationResult[]>(`/projects/${projectId}/perturbations?module=virtual_cell`),
        api<Compound[]>(`/projects/${projectId}/compounds`), api<Ranking[]>(`/projects/${projectId}/candidates`), api<ReportRecord[]>(`/projects/${projectId}/reports`),
        api<MethodologySpec[]>("/methodology"),
        api<DataSourceSpec[]>("/data-sources"),
      ]);
      const latestTargetJob = j.find((job) => job.type === "target_discovery" && job.status === "completed" && String(job.parameters?.discovery_strategy ?? "established") === targetStrategy);
      if (latestTargetJob) {
        const [targetRows, result, virtualCheck, knockoutCheck, cellCheck, compoundCheck] = await Promise.all([
          api<TargetCandidate[]>(`/projects/${projectId}/targets?job_id=${latestTargetJob.id}&limit=200`),
          api<TargetDiscoveryResult>(`/jobs/${latestTargetJob.id}/target-discovery-result`).catch(() => null),
          latestTargetJob.adapter_mode === "real" ? api<TargetCompatibility>(`/projects/${projectId}/target-compatibility?target_job_id=${latestTargetJob.id}&module=virtual_intervention&top_n=10`) : Promise.resolve(null),
          latestTargetJob.adapter_mode === "real" ? api<TargetCompatibility>(`/projects/${projectId}/target-compatibility?target_job_id=${latestTargetJob.id}&module=gene_knockout&top_n=10`) : Promise.resolve(null),
          latestTargetJob.adapter_mode === "real" ? api<TargetCompatibility>(`/projects/${projectId}/target-compatibility?target_job_id=${latestTargetJob.id}&module=virtual_cell&top_n=10`) : Promise.resolve(null),
          latestTargetJob.adapter_mode === "real" ? api<TargetCompatibility>(`/projects/${projectId}/target-compatibility?target_job_id=${latestTargetJob.id}&module=compound_matching&top_n=10`) : Promise.resolve(null),
        ]);
        setTargets(targetRows);
        setTargetResult(result);
        setVirtualCompatibility(virtualCheck);
        setKnockoutCompatibility(knockoutCheck);
        setCellCompatibility(cellCheck);
        setCompoundCompatibility(compoundCheck);
      } else {
        setTargets([]);
        setTargetResult(null);
        setVirtualCompatibility(null);
        setKnockoutCompatibility(null);
        setCellCompatibility(null);
        setCompoundCompatibility(null);
      }
      setProject(p); setJobs(j); setAssets(a); setPerturbations(pe); setKnockouts(ko); setVirtualCells(vc); setCompounds(c); setRankings(r); setReports(rp); setMethodology(methods); setDataSources(sources); setError("");
    } catch (e) { setError(e instanceof Error ? e.message : "加载失败"); }
    finally { refreshInFlight.current = false; }
  }, [projectId, targetStrategy]);

  useEffect(() => {
    const timer = window.setTimeout(() => { void refresh(); }, 0);
    return () => window.clearTimeout(timer);
  }, [refresh]);
  useEffect(() => {
    const active = jobs.some((job) => job.status === "queued" || job.status === "running");
    if (!active) return;
    const poll = async () => {
      try {
        const nextJobs = await api<Job[]>(`/projects/${projectId}/jobs`);
        setJobs(nextJobs);
        if (!nextJobs.some((job) => job.status === "queued" || job.status === "running")) void refresh();
      } catch (e) {
        setError(e instanceof Error ? e.message : "任务状态刷新失败");
      }
    };
    const timer = window.setInterval(() => { void poll(); }, 3000); return () => window.clearInterval(timer);
  }, [jobs, projectId, refresh]);

  const latest = useMemo(() => Object.fromEntries([...stages, ["virtual_cell", "虚拟细胞"]].map(([id]) => [id, jobs.find((job) => job.type === id)])), [jobs]);
  const activeTargetJob = useMemo(() => jobs.find((job) => job.type === "target_discovery" && job.status === "completed" && String(job.parameters?.discovery_strategy ?? "established") === targetStrategy), [jobs, targetStrategy]);
  const latestTargetAttempt = useMemo(() => jobs.find((job) => job.type === "target_discovery" && String(job.parameters?.discovery_strategy ?? "established") === targetStrategy), [jobs, targetStrategy]);
  const completed = stages.filter(([id]) => latest[id]?.status === "completed").length;
  const projectDecisionBrief = useMemo(() => buildProjectDecisionBrief({
    jobs,
    targetCount: targets.length,
    perturbationCount: perturbations.length,
    knockoutCount: knockouts.length,
    virtualCellCount: virtualCells.length,
    compoundCount: compounds.length,
    evaluatedCompoundCount: compounds.filter((item) => item.evaluation).length,
    rankingCount: rankings.length,
    decidedCount: rankings.filter((item) => item.decision !== "pending").length,
  }), [jobs, targets.length, perturbations.length, knockouts.length, virtualCells.length, compounds, rankings]);

  async function run(path: string, label: string, parameters: Record<string, unknown> = {}, adapterMode: AdapterMode = "precomputed") {
    setBusy(label); setError(""); setCacheMessage("");
    try {
      const inputVersion = adapterMode === "real"
        ? path === "target-discovery-jobs" ? parameters.discovery_strategy === "novel" ? "novel-target-discovery-v2" : "open-targets-live"
          : path === "virtual-cell-jobs" ? "virtual-cell-v1"
            : path === "omics-analysis-jobs" ? "omics-analysis-v1"
            : path === "fungal-compound-jobs" ? "fungal-compound-exploration-v1"
            : path === "bacterial-compound-jobs" ? "bacterial-compound-exploration-v1"
            : path === "compound-matching-jobs" || path === "admet-jobs" ? "compound-evaluation-v1"
              : path === "ranking-jobs" ? "integrated-ranking-v1"
              : "perturbation-knockout-v2"
        : "ipf-demo-2026.1";
      const submitted = await submitAnalysis(projectId, path, adapterMode, inputVersion, parameters);
      setJobs((current) => [submitted, ...current.filter((item) => item.id !== submitted.id)]);
      if (submitted.cache_hit) setCacheMessage("已直接载入数据库中的相同分析结果，无需重新计算。");
      if (submitted.status !== "queued" && submitted.status !== "running") void refresh();
    } catch (e) { setError(e instanceof Error ? e.message : "任务提交失败"); }
    finally { setBusy(""); }
  }

  async function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = event.currentTarget; const data = new FormData(form);
    setBusy("upload");
    try { await api(`/projects/${projectId}/assets?kind=research-data`, { method: "POST", body: data }); form.reset(); await refresh(); }
    catch (e) { setError(e instanceof Error ? e.message : "上传失败"); }
    finally { setBusy(""); }
  }

  async function toggleTarget(target: TargetCandidate) {
    if (busy) return;
    setBusy(`target-${target.id}`); setError("");
    try {
      await api(`/targets/${target.id}`, { method: "PATCH", body: JSON.stringify({ selected: !target.selected }) });
      await refresh();
    } catch (e) { setError(e instanceof Error ? e.message : "靶点选择保存失败，请重试"); }
    finally { setBusy(""); }
  }

  async function selectTargets(mode: "top_n" | "all" | "clear", topN = 10) {
    if (!activeTargetJob) return;
    setBusy("target-selection"); setError("");
    try {
      await api(`/projects/${projectId}/targets/selection`, { method: "PATCH", body: JSON.stringify({ job_id: activeTargetJob.id, mode, top_n: topN }) });
      await refresh();
    } catch (e) { setError(e instanceof Error ? e.message : "批量选择失败"); }
    finally { setBusy(""); }
  }

  async function decide(item: Ranking, decision: "pending" | "selected" | "excluded", rationale: string) {
    setBusy(`decision-${item.id}`); setError("");
    try {
      await api(`/rankings/${item.id}`, { method: "PATCH", body: JSON.stringify({ decision, rationale }) });
      await refresh();
    } catch (e) { setError(e instanceof Error ? e.message : "候选决策保存失败"); }
    finally { setBusy(""); }
  }

  async function createReport() {
    setBusy("report"); setError("");
    try { await api(`/projects/${projectId}/reports`, { method: "POST", body: JSON.stringify({}) }); await refresh(); }
    catch (e) { setError(e instanceof Error ? e.message : "报告生成失败"); }
    finally { setBusy(""); }
  }

  if (!project) return <AppShell title="载入项目">{error ? <div className="notice error" role="alert"><CircleAlert size={18} />{error}<button className="secondary-button" onClick={() => { setError(""); void refresh(); }}>重新加载项目</button></div> : <div className="loading-panel"><LoaderCircle className="spin" /> 正在加载项目工作区…</div>}</AppShell>;

  return (
    <AppShell title={project.name} subtitle={view === "fungal_targets" ? "以目标真菌物种或菌株为分析对象，不依赖疾病选择。" : view === "bacterial_targets" ? "以目标细菌物种或菌株为分析对象，不依赖疾病选择。" : isGeneralWorkspace(project) ? "在当前模块搜索疾病并查看对应分析。" : `当前疾病：${project.disease} · 可在当前模块搜索或切换疾病。`}>
      {error && <div className="notice error" role="alert"><CircleAlert size={18} /> {error}<button className="text-button" onClick={() => { void refresh(); }}>刷新项目状态</button></div>}
      <div className="workspace">
        <aside className="workspace-nav">
          <div className="project-id"><small>项目 ID</small><code>{project.id.slice(0, 12)}</code></div>
          {nav.map((item) => <button key={item.id} aria-label={item.label} title={item.label} onClick={() => setView(item.id)} className={view === item.id ? "active" : ""}>{item.icon}<span>{item.label}</span><ChevronRight size={15} /></button>)}
          <div className="workflow-progress"><span><strong>{completed}/6</strong> 研发阶段完成</span><div><i style={{ width: `${completed * 100 / 6}%` }} /></div></div>
        </aside>
        <section className="workspace-content">
          {view !== "fungal_targets" && view !== "bacterial_targets" && <DiseaseProjectPicker project={project} view={view} />}
          {cacheMessage && <div className="cache-result-notice" role="status"><Database size={18} /><div><strong>已命中数据库结果</strong><span>{cacheMessage}</span></div></div>}
          {(["data", "compounds", "ranking"] as string[]).includes(view) && <div className="workbench-shortcut"><a href="#molecule-workbench">{view === "data" ? "打开自有分子库" : view === "compounds" ? "打开结构检索与比较" : "打开实验验证与反馈"} ↓</a></div>}
          {view === "overview" && <Overview project={project} jobs={jobs} latest={latest} completed={completed} targets={targets} targetResult={targetResult} sources={dataSources} decisionBrief={projectDecisionBrief} go={setView} />}
          {view === "data" && <DataView assets={assets} sources={dataSources} busy={busy} upload={upload} />}
          {view === "omics" && <OmicsAnalysisWorkbench projectId={projectId} assets={assets} jobs={jobs} busy={busy} run={(parameters) => run("omics-analysis-jobs", "omics_analysis", parameters, "real")} refresh={refresh} />}
          {view === "targets" && <TargetsView diseaseId={project.disease_id ?? undefined} projectDisease={project.disease} targets={targets} result={targetResult} job={activeTargetJob} latestAttempt={latestTargetAttempt} strategy={targetStrategy} setStrategy={setTargetStrategy} busy={busy} run={(query, mode, strategy, topN) => run("target-discovery-jobs", "targets", { disease_query: analysisQueryForDisease(query, project.disease_id), top_n: topN, include_indirect: strategy === "established", include_evidence_records: true, discovery_strategy: strategy }, mode)} toggle={toggleTarget} selectTargets={selectTargets} />}
          {view === "fungal_targets" && <FungalDiscoveryHub projectId={projectId} jobs={jobs} busy={busy} runExploration={(parameters) => run("fungal-compound-jobs", "fungal_compound", parameters, "real")} />}
          {view === "bacterial_targets" && <BacterialDiscoveryHub projectId={projectId} jobs={jobs} busy={busy} runExploration={(parameters) => run("bacterial-compound-jobs", "bacterial_compound", parameters, "real")} />}
          {view === "perturbation" && <PerturbationView targetJob={activeTargetJob} compatibility={virtualCompatibility} results={perturbations} job={latest.perturbation} busy={busy} run={() => run("perturbation-jobs", "perturbation", { top_n: 10, target_job_id: activeTargetJob?.id }, activeTargetJob?.adapter_mode === "real" ? "real" : "precomputed")} />}
          {view === "gene_perturbation" && <GenePerturbationView targetJob={activeTargetJob} compatibility={knockoutCompatibility} results={knockouts} job={latest.gene_knockout} busy={busy} run={() => run("gene-knockout-jobs", "gene_knockout", { top_n: 10, target_job_id: activeTargetJob?.id }, "real")} />}
          {view === "virtual_cell" && <VirtualCellView results={virtualCells} job={latest.virtual_cell} targetJob={activeTargetJob} compatibility={cellCompatibility} busy={busy} run={() => run("virtual-cell-jobs", "virtual_cell", { top_n: 10, target_job_id: activeTargetJob?.id }, "real")} onConfigure={() => setView("gene_perturbation")} />}
          {view === "protein_structure" && <ProteinStructureWorkbench projectId={projectId} projectDisease={project.disease} targetSuggestions={targets} />}
          {view === "compounds" && <CompoundsView projectId={projectId} diseaseId={project.disease_id ?? undefined} compounds={compounds} matchJob={latest.compound_matching} admetJob={latest.admet} targetJob={activeTargetJob} compatibility={compoundCompatibility} busy={busy} match={() => run("compound-matching-jobs", "matching", { top_n: 10, target_job_id: activeTargetJob?.id }, "real")} admet={() => run("admet-jobs", "admet", { compound_job_id: latest.compound_matching?.id }, "real")} />}
          {view === "ranking" && <RankingView projectId={projectId} rankings={rankings} job={latest.ranking} ready={compounds.some((c) => c.evaluation)} busy={busy} run={(weights) => run("ranking-jobs", "ranking", { weights, compound_job_id: latest.compound_matching?.id, admet_job_id: latest.admet?.id }, compounds.some((c) => !c.demo) ? "real" : "precomputed")} decide={decide} />}
          {view === "methodology" && <MethodologyView methods={methodology} />}
          {view === "report" && <ReportView project={project} jobs={jobs} targets={targets} perturbations={perturbations} knockouts={knockouts} virtualCells={virtualCells} compounds={compounds} rankings={rankings} reports={reports} ready={rankings.length > 0} busy={busy} create={createReport} />}
          {(["data", "compounds", "ranking"] as string[]).includes(view) && <MoleculeWorkbench key={`${projectId}-${view}`} projectId={projectId} publicMolecules={compounds} mode={view === "data" ? "library" : view === "compounds" ? "search" : "tasks"} />}
          {view === "process" && <ProcessWorkbench projectId={projectId} molecules={compounds} />}
          <NextStepBar page={view} onNavigate={setView} />
        </section>
      </div>
    </AppShell>
  );
}

function PanelHeader({ kicker, title, description, action }: { kicker: string; title: string; description: string; action?: React.ReactNode; guide?: View }) {
  return <div className="panel-header"><div><span>{kicker}</span><h2>{title}</h2><p>{description}</p></div>{action}</div>;
}

function JobBadge({ job }: { job?: Job }) {
  if (!job) return <span className="job-badge neutral">尚未运行</span>;
  return <span className={`job-badge ${job.status}`}>{job.status === "completed" ? <Check size={14} /> : job.status === "running" || job.status === "queued" ? <LoaderCircle className="spin" size={14} /> : <CircleAlert size={14} />}{jobStatusLabels[job.status]} · {job.progress}%</span>;
}

function TargetToHitDetail({ hit }: { hit: TargetToHitProfile }) {
  return <section className="target-to-hit-detail">
    <header><div><small>TARGET-TO-HIT V3</small><h4>{hit.decision ? hitDecisionLabels[hit.decision] : modalityLabels[hit.recommended_modality]}</h4></div><b>{Math.round(hit.score * 100)} 分</b></header>
    <TargetReviewDetail hit={hit} />
    <div><span><small>小分子起点</small><strong>{Math.round(hit.chemistry_start_score * 100)}</strong></span><span><small>抗体 / 蛋白药可及性</small><strong>{Math.round(hit.biologic_start_score * 100)}</strong></span><span><small>组织选择性</small><strong>{Math.round(hit.tissue_selectivity_score * 100)}</strong></span><span><small>疾病证据</small><strong>{Math.round(hit.disease_evidence_score * 100)}</strong></span></div>
    {hit.routes && <div className="target-to-hit-routes">{hit.routes.map((route) => <article key={route.id}><span><b>{route.label}</b><em>{routeStatusLabels[route.status] ?? route.status} · {Math.round(route.support_score * 100)}</em></span><small>{route.basis.length ? route.basis.join("；") : "当前没有直接路线依据"}</small>{route.gaps.length > 0 && <small>待补：{route.gaps.join("；")}</small>}</article>)}</div>}
    <p>{hit.known_safety_signal ? "公共数据中已有安全性提示，需要优先核查具体事件和组织风险。" : hit.safety_policy}</p>
    {hit.evidence_replication && <p>独立证据类型：{hit.evidence_replication.source_count} 类（{hit.evidence_replication.level === "strong" ? "较充分" : hit.evidence_replication.level === "moderate" ? "中等" : "有限"}）；结构起点：{hit.structure_readiness?.level === "strong" ? "较完整" : hit.structure_readiness?.level === "partial" ? "部分可用" : "尚缺"}；广泛必需性需在“基因敲除”模块单独检查。</p>}
    {hit.gaps.length > 0 && <ul>{hit.gaps.map((gap) => <li key={gap}>{gap}</li>)}</ul>}
    {hit.next_steps && hit.next_steps.length > 0 && <div className="target-to-hit-next"><strong>建议的下一步验证</strong><ol>{hit.next_steps.map((step) => <li key={step}>{step}</li>)}</ol></div>}
  </section>;
}

function Overview({ project, jobs, latest, completed, targets, targetResult, sources, decisionBrief, go }: { project: Project; jobs: Job[]; latest: Record<string, Job | undefined>; completed: number; targets: TargetCandidate[]; targetResult: TargetDiscoveryResult | null; sources: DataSourceSpec[]; decisionBrief: ReturnType<typeof buildProjectDecisionBrief>; go: (view: View) => void }) {
  return <>
    <PanelHeader kicker="项目总览" title="研发决策与执行进度" description="查看推进条件、阻塞项与执行进度。" guide="overview" />
    <EnterpriseDecisionCenter brief={decisionBrief} go={go} />
    <DiseasePortrait project={project} jobs={jobs} targets={targets} result={targetResult} sources={sources} go={go} />
    <div className="summary-grid"><div><small>流程完成度</small><strong>{Math.round(completed * 100 / 6)}%</strong><span>{completed} / 6 个计算阶段</span></div><div><small>任务总数</small><strong>{jobs.length}</strong><span>包含重跑与失败记录</span></div><div><small>靶点数据模式</small><strong>{latest.target_discovery?.adapter_mode ? adapterModeLabels[latest.target_discovery.adapter_mode] : "尚未运行"}</strong><span>真实、预计算与未配置明确分离</span></div></div>
    <div className="pipeline-list">{stages.map(([id, label], index) => { const views: View[] = ["targets", "perturbation", "gene_perturbation", "compounds", "compounds", "ranking"]; const descriptions = ["疾病到候选靶点与证据", "干预方向与疾病模块衰减", "CRISPR 敲除与广泛必需性风险", "已知药物与候选匹配", "药效、安全性和可开发性", "综合评分与人工决策"]; return <button key={id} onClick={() => go(views[index])}><span className="stage-index">0{index + 1}</span><div><strong>{label}</strong><small>{descriptions[index]}</small></div><JobBadge job={latest[id]} /><ChevronRight /></button>; })}</div>
    <details className="technical-details"><summary>平台使用范围</summary><div className="guardrail"><ShieldCheck /><div><strong>使用边界</strong><p>平台用于候选分流、证据复核和实验优先级管理；系统建议不能替代实验确认、临床判断、预算审批或正式项目立项。</p></div></div></details>
  </>;
}

function DataView({ assets, sources, busy, upload }: { assets: Asset[]; sources: DataSourceSpec[]; busy: string; upload: (e: FormEvent<HTMLFormElement>) => void }) {
  const [fileName, setFileName] = useState("");
  return <><PanelHeader kicker="数据资产" title="项目用了哪些数据" description="管理数据来源、版本与项目文件。" guide="data" />
    <form className="upload-box" onSubmit={upload} onReset={() => setFileName("")}><UploadCloud /><div><strong>补充自己的研究数据</strong><p>支持表达、单细胞、靶点与化合物文件，单文件 ≤50 MB；上传仅校验登记。</p></div><label className="secondary-button" title={fileName || "选择要上传的研究文件"}>{fileName ? `已选择：${fileName}` : "选择文件"}<input type="file" name="file" required hidden onChange={e => setFileName(e.target.files?.[0]?.name || "")} /></label><button className="primary-button" disabled={busy === "upload" || !fileName}>{busy === "upload" ? "正在上传…" : "上传并检查文件"}</button></form>
    <div className="table-card"><table><thead><tr><th>文件</th><th>用途类型</th><th>大小</th><th>版本</th><th title="用于确认文件内容没有变化">文件指纹</th></tr></thead><tbody>{assets.map((a) => <tr key={a.id}><td><strong>{a.name}</strong></td><td>{a.kind}</td><td>{bytes(a.size)}</td><td>v{a.version}</td><td><code>{a.content_hash.slice(0, 14)}…</code></td></tr>)}{!assets.length && <tr><td colSpan={5}><div className="inline-empty">尚未上传项目文件。当前分析仍可使用服务器上已经锁定版本的公共数据。</div></td></tr>}</tbody></table></div>
    <div className="subsection-heading"><div><small>公共数据源登记表</small><h3>已接入数据库与扩展队列</h3></div><span>{sources.filter((item) => item.status !== "queued_integration").length} 个已接入</span></div>
    <div className="source-grid">{sources.map((item) => <article key={item.id}><header><div><strong>{item.name_zh}</strong><span>{item.name}</span></div><span className={`source-status ${item.status}`}>{item.status === "connected_snapshot" ? "快照已接入" : item.status === "connected_runtime" ? "实时已接入" : "待接入"}</span></header><dl><div><dt>版本</dt><dd>{item.version}</dd></div><div><dt>用于</dt><dd>{item.used_in.join("、")}</dd></div><div><dt>作用</dt><dd>{item.role}</dd></div><div><dt>获取方式</dt><dd>{item.access_mode}</dd></div></dl><p><CircleAlert size={14} /> {item.boundary}</p><a href={item.url} target="_blank" rel="noreferrer">查看官方数据源</a></article>)}</div>
  </>;
}

export function TargetsView({ diseaseId, projectDisease, targets, result, job, latestAttempt, strategy, setStrategy, busy, run, toggle, selectTargets }: { diseaseId?: string; projectDisease: string; targets: TargetCandidate[]; result: TargetDiscoveryResult | null; job?: Job; latestAttempt?: Job; strategy: DiscoveryStrategy; setStrategy: (value: DiscoveryStrategy) => void; busy: string; run: (query: string, mode: AdapterMode, strategy: DiscoveryStrategy, topN: number) => void; toggle: (t: TargetCandidate) => void; selectTargets: (mode: "top_n" | "all" | "clear", topN?: number) => void }) {
  const query = projectDisease === GENERAL_WORKSPACE_DISEASE ? "" : projectDisease;
  const [mode, setMode] = useState<AdapterMode>("real");
  const [selected, setSelected] = useState<TargetCandidate | null>(null);
  const [evidence, setEvidence] = useState<TargetEvidence[]>([]);
  const [evidenceTotal, setEvidenceTotal] = useState(0);
  const [evidenceBusy, setEvidenceBusy] = useState(false);
  const [evidenceError, setEvidenceError] = useState("");
  const [resultLimit, setResultLimit] = useState(100);
  const evidenceRequest = useRef(0);
  const availabilityQuery = analysisQueryForDisease(query, diseaseId);
  const [availability, setAvailability] = useState<{ query: string; available: boolean; code: string; message: string; diseases: { id: string; name: string }[] } | null>(null);
  const [availabilityRetry, setAvailabilityRetry] = useState(0);
  useEffect(() => {
    if (mode !== "real" || strategy !== "novel") return;
    let cancelled = false;
    const timer = setTimeout(() => {
      api<NonNullable<typeof availability>>(`/target-discovery-availability?disease_query=${encodeURIComponent(availabilityQuery)}`)
        .then(value => { if (!cancelled) setAvailability(value); })
        .catch(() => { if (!cancelled) setAvailability({ query: availabilityQuery, available: false, code: "CHECK_FAILED", message: "暂时无法检查新靶点数据，请重新检查。", diseases: [] }); });
    }, 150);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [availabilityQuery, availabilityRetry, mode, strategy]);
  const novelAvailability = availability?.query === availabilityQuery ? availability : null;
  const novelBlocked = mode === "real" && strategy === "novel" && !novelAvailability?.available;


  async function inspectTarget(target: TargetCandidate) {
    const request = ++evidenceRequest.current;
    setSelected(target); setEvidence([]); setEvidenceTotal(0); setEvidenceError(""); setEvidenceBusy(true);
    try {
      const page = await api<{ items: TargetEvidence[]; total: number }>(`/targets/${target.id}/evidence?limit=50`);
      if (request !== evidenceRequest.current) return;
      setEvidence(page.items); setEvidenceTotal(page.total);
    } catch (error) {
      if (request === evidenceRequest.current) setEvidenceError(error instanceof Error ? error.message : "证据加载失败，请重试。");
    } finally { if (request === evidenceRequest.current) setEvidenceBusy(false); }
  }

  const quality = result?.data_quality as { analysis_tier?: string; conclusion?: string; limitations?: string[]; targets_retrieved?: number; published_target_count?: number; normalized_evidence_records?: number; targets_with_genetics?: number; targets_with_known_drug?: number; coverage?: { mean?: number }; cache_hit?: boolean; source_registry_version?: string; score_contract_version?: string; discovery_strategy?: DiscoveryStrategy; build_counts?: { candidate_genes?: number }; benchmark_summary?: { recall_at_20?: number; enrichment_at_20?: number; ndcg_at_20?: number; average_precision?: number; top20_multimodal_count?: number; permutation_p_value?: number } } | undefined;
  const isNovelResult = quality?.discovery_strategy === "novel";
  const selectedCount = targets.filter((target) => target.selected).length;
  const categories = selected?.evidence_breakdown?.categories ?? {};
  const selectedHit = selected?.evidence_breakdown?.target_to_hit;
  const hitProfiles = targets.map((target) => target.evidence_breakdown?.target_to_hit).filter((item): item is NonNullable<typeof item> => Boolean(item));
  const hitReady = hitProfiles.filter((item) => item.level === "ready_for_hit_search").length;
  const smallMoleculeReady = hitProfiles.filter((item) => item.recommended_modality === "small_molecule" || item.recommended_modality === "small_molecule_or_biologic").length;
  const biologicReady = hitProfiles.filter((item) => item.recommended_modality === "biologic" || item.recommended_modality === "small_molecule_or_biologic").length;
  return <><PanelHeader kicker="靶点发现" title="从疾病找到值得研究的靶点" description="综合遗传、表达与网络证据，筛选候选靶点。" action={<JobBadge job={novelBlocked ? undefined : latestAttempt ?? job} />} guide="targets" />
    <div className="strategy-tabs" role="tablist" aria-label="靶点发现类型">
      <button className={strategy === "established" ? "active" : ""} onClick={() => { setStrategy("established"); setResultLimit((value) => Math.min(value, 100)); }}><ShieldCheck size={17} /><span><b>已有证据靶点</b><small>疾病关联与已有药物证据</small></span></button>
      <button className={strategy === "novel" ? "active" : ""} onClick={() => setStrategy("novel")}><Sparkles size={17} /><span><b>新靶点探索</b><small>公共证据探索 / 专属多组学</small></span></button>
    </div>
    {latestAttempt?.status === "failed" && latestAttempt.parameters?.discovery_strategy === strategy && !["NOVEL_TARGET_DISEASE_NOT_FOUND", "NOVEL_TARGET_NOT_CONFIGURED"].includes(latestAttempt.error_code ?? "") && <div className="notice error"><CircleAlert /> 本次运行未完成：{latestAttempt.error_code === "DISEASE_NOT_RESOLVED" ? "没有识别出疾病名称，请使用常见中文名、英文名或疾病本体 ID 后重试。" : latestAttempt.error_message ?? "请检查输入后重试。"}</div>}
    {!!targets.length && job?.status === "completed" && <div className="cache-result-notice"><Database size={18} /><div><strong>数据库结果已就绪 · {targets.length} 个靶点</strong><span>结果已按疾病、数据模式、参数和版本保存；相同的 {targets.length >= 100 ? "100 靶点" : "靶点"}分析会直接复用。保存时间 {new Date(job.completed_at ?? job.created_at).toLocaleString("zh-CN")}</span></div></div>}
    <div className="target-runner compact">
      <label><span>当前疾病</span><input value={query} readOnly placeholder="请先在上方选择疾病" aria-label="当前分析疾病" /></label>
      <label><span>数据模式</span><select value={mode} onChange={(event) => setMode(event.target.value as AdapterMode)}><option value="real">真实公共数据</option><option value="precomputed">预计算演示 · IPF</option><option value="not_configured">未配置 · 空状态</option></select></label>
      <label><span>返回候选数</span><select value={resultLimit} onChange={(event) => setResultLimit(Number(event.target.value))}><option value={50}>50 个</option><option value={100}>100 个</option>{strategy === "novel" && <option value={200}>200 个</option>}</select></label>
      <button className="primary-button" onClick={() => run(query, mode, mode === "real" ? strategy : "established", strategy === "novel" ? resultLimit : Math.min(resultLimit, 100))} disabled={!!busy || query.trim().length < 2 || novelBlocked}><Play size={16} /> {busy === "targets" ? "任务运行中…" : novelBlocked ? novelAvailability ? "当前疾病暂未就绪" : "检查数据中…" : targets.length ? "重新运行" : "运行靶点发现"}</button>
    </div>
    {mode === "real" && strategy === "novel" && <div className="notice" role="status"><Sparkles /><div>{novelAvailability?.message ?? "正在检查当前疾病的新靶点数据…"}
      {novelBlocked && novelAvailability && <><div>{novelAvailability.diseases.length > 0 && `当前支持：${novelAvailability.diseases.map(d => d.name).join("、")}。`}</div><button className="text-button" onClick={() => { setStrategy("established"); setResultLimit(value => Math.min(value, 100)); }}>转到已有证据靶点</button><button className="text-button" onClick={() => { setAvailability(null); setAvailabilityRetry(value => value + 1); }}>重新检查</button></>}
    </div></div>}
    {quality?.analysis_tier === "public_evidence_exploration" && <section className="notice"><div><strong>公共证据探索 · {targets.length} 个候选</strong><p>{quality.conclusion}</p><details><summary>证据范围与局限</summary>{quality.limitations?.map(line => <p key={line}>{line}</p>)}<p>排序权重：人类遗传45%、体细胞变异20%、功能通路15%、表达10%、动物模型10%。缺失证据不增加其余权重；药物和纯文献共现分数不参与排序。</p></details></div></section>}
    {result && <div className={`source-banner ${job?.adapter_mode === "real" ? "real" : "demo"}`}><Database /><div><strong>{diseaseLabel(result.disease_id, result.canonical_name ?? result.query)}</strong><span>{result.disease_id ?? result.resolution_status} · {sourceLabel(result.source)} {result.source_version} · {new Date(result.retrieved_at).toLocaleString("zh-CN")}</span></div><span className="mode-pill">{job?.adapter_mode ? adapterModeLabels[job.adapter_mode] : "未知模式"}</span></div>}
    {job?.adapter_mode === "precomputed" && <div className="demo-banner"><Sparkles /> IPF 预计算演示数据 · 仅用于产品功能验证</div>}
    {quality && <div className="quality-grid"><div title={isNovelResult ? "页面返回数 / 实际参与计算的候选总数" : "本次返回并排序的候选数量"}><small>{isNovelResult ? "展示数 / 完整候选池" : "找到的候选靶点"}</small><strong>{isNovelResult ? `${quality.targets_retrieved ?? targets.length} / ${quality.build_counts?.candidate_genes ?? quality.published_target_count ?? "—"}` : quality.targets_retrieved ?? targets.length}</strong></div><div title="去重后的证据类型记录"><small>汇总到的证据记录</small><strong>{quality.normalized_evidence_records ?? "—"}</strong></div><div title="有遗传或其他直接因果线索的候选"><small>有因果线索的靶点</small><strong>{quality.targets_with_genetics ?? "—"}</strong></div><div title="已有药物记录只用于检查排序，不参与证据评分"><small>已有药物记录的靶点</small><strong>{quality.targets_with_known_drug ?? "—"}</strong></div><div title="每个靶点平均有多少类证据可用"><small>平均证据完整度</small><strong>{quality.coverage?.mean == null ? "—" : `${Math.round(quality.coverage.mean * 100)}%`}</strong></div><div title="本次结果的数据获取方式"><small>结果来源</small><strong>{quality.source_registry_version ? "已锁定版本的数据" : quality.cache_hit ? "缓存数据" : "实时查询"}</strong></div></div>}
    {quality?.source_registry_version && <details className="technical-details"><summary>评分规则与回顾性验证</summary><div className="score-contract"><ShieldCheck size={18} /><div><strong>{isNovelResult ? "四类证据共同排序" : "从证据、疾病关系和开发条件三个方面排序"}</strong><span>{isNovelResult ? "遗传关联、表达调控、疾病单细胞和蛋白网络分别提供独立线索；已有药物与临床阶段不参与证据强度计算。" : "已知药物和临床阶段只作为参考标签，不会把候选分数人为推高。"}</span><details><summary>查看评分版本与权重</summary><p title={`${quality.score_contract_version} · ${quality.source_registry_version}`}>{isNovelResult ? "遗传关联 35% + 表达调控 25% + 单细胞 25% + 生物网络 15%。" : "三方面评分规则 v1 · 靶点发现数据源 v1。"}</p></details></div>{quality.benchmark_summary && <div className="benchmark-mini" title={isNovelResult ? "前 20 名中至少有两类直接证据支持的靶点数 / 随机置换检验结果" : "已知阳性靶点是否集中在前 20 位；越接近 1 越好"}><small>{isNovelResult ? "前 20 名多来源支持 / 随机检验" : "已知靶点回顾性排序表现"}</small><b>{isNovelResult ? `${quality.benchmark_summary.top20_multimodal_count ?? "—"} / ${quality.benchmark_summary.permutation_p_value?.toFixed(3) ?? "—"}` : score(quality.benchmark_summary.ndcg_at_20)}</b></div>}</div></details>}
    {!isNovelResult && !!hitProfiles.length && <section className="target-to-hit-overview">
      <header><div><small>TARGET-TO-HIT V3</small><h3>从靶点证据走到可执行的分子路线</h3><p>比较四类分子路线的依据、缺口与验证建议；不预测临床成功率。</p></div><span>基于当前 {hitProfiles.length} 个候选</span></header>
      <div><article><small>可直接搜索起始化合物</small><strong>{hitReady}</strong><span>具有较完整的配体、口袋或可及性线索</span></article><article><small>小分子路线可评估</small><strong>{smallMoleculeReady}</strong><span>已有结合分子、口袋、配体或探针线索</span></article><article><small>生物药路线可评估</small><strong>{biologicReady}</strong><span>膜蛋白或分泌蛋白可及性较好</span></article><article><small>安全性处理原则</small><strong>单独复核</strong><span>未发现公开警示不等于安全，不会因此加分</span></article></div>
    </section>}
    {!!targets.length && <div className="target-selection-toolbar"><div><strong>选择下游分析靶点</strong><span>已选择 {selectedCount} 个；未选择时默认分析排名前 10 个可计算靶点</span></div><div><button className="secondary-button" disabled={!!busy} onClick={() => selectTargets("top_n", 10)}>选择前 10 名</button><button className="secondary-button" disabled={!!busy} onClick={() => selectTargets("top_n", 20)}>选择前 20 名</button><button className="text-button" disabled={!!busy} onClick={() => selectTargets("all")}>全选当前结果</button><button className="text-button" disabled={!!busy || !selectedCount} onClick={() => selectTargets("clear")}>清空选择</button></div></div>}
    <div className="target-grid">{targets.map((t, i) => {
      const isThreeAxis = t.causal_confidence != null;
      const isNovel = t.evidence_breakdown?.discovery_strategy === "novel";
      const noveltyClass = t.evidence_breakdown?.novelty_class;
      const exploration = t.evidence_breakdown?.exploration;
      const hasKnownDrug = Boolean(t.evidence_breakdown?.held_out?.known_drug_label);
      const classLabel = hasKnownDrug ? "已有药物证据" : exploration ? "待验证候选" : isNovel ? "新靶点候选" : "已有疾病关联证据";
      const hit = t.evidence_breakdown?.target_to_hit;
      return <article className={`target-card ${t.selected ? "selected" : ""}`} key={t.id}>
        <div className="target-rank">第 {t.rank ?? i + 1} 名</div><span className={`target-class ${isNovel && !hasKnownDrug ? "novel" : "known"}`}>{classLabel}</span>
        <div className="score-ring" title="综合评分（百分制显示）">{Math.round(t.score * 100)}</div><h3>{t.symbol}</h3><p>{t.name}</p>{t.external_id && <code className="target-id">{t.external_id}</code>}
        {exploration ? <><div className="target-metrics"><span><small>非药物证据分</small><b>{score(t.score)}</b></span><span><small>证据类别</small><b>{exploration.support_count} / 5</b></span></div><p>{exploration.summary}</p><small>{exploration.clinical_label}</small><details><summary>判断依据与下一步</summary><p>{exploration.channels.join("、")}</p><p>{exploration.gaps}</p><p>{exploration.next_step}</p></details></> : <div className="target-metrics"><span><small>{isNovel ? "证据支持程度" : isThreeAxis ? "因果线索强度" : "数据库关联分"}</small><b>{score(isThreeAxis ? t.causal_confidence : t.score)}</b></span><span><small>{isNovel ? "相对新颖程度" : isThreeAxis ? "与本疾病的关系" : "平台计算参考分"}</small><b>{score(isThreeAxis ? t.disease_context : t.derived_score)}</b></span><span><small>{isThreeAxis ? "后续开发条件" : "证据完整度"}</small><b>{isThreeAxis ? score(t.development_feasibility) : t.evidence_coverage == null ? "—" : `${Math.round(t.evidence_coverage * 100)}%`}</b></span></div>}
        {isThreeAxis && <div className="coverage-caption">{isNovel ? `直接证据模态 ${t.evidence_count ?? 0}/3 · ${noveltyClass ? noveltyClassLabels[noveltyClass] ?? noveltyClass : "待分类"} · ${directionLabels[t.direction] ?? t.direction}` : `证据覆盖率 ${t.evidence_coverage == null ? "—" : `${Math.round(t.evidence_coverage * 100)}%`}`}</div>}
        {hit && <div className={`hit-readiness ${hit.level}`}><div><small>进入化合物研究的准备度</small><strong>{Math.round(hit.score * 100)} · {hitReadinessLabels[hit.level]}</strong></div><span>{modalityLabels[hit.recommended_modality]}</span></div>}
        <button onClick={() => inspectTarget(t)} className="secondary-button">查看证据</button><button disabled={!!busy} onClick={() => toggle(t)} className={t.selected ? "selected-button" : "text-button"}>{busy === `target-${t.id}` ? "正在保存…" : t.selected ? <><Check size={16} /> 已选择</> : "加入干预"}</button>
      </article>;
    })}{!targets.length && <Empty icon={<Search />} title={quality?.analysis_tier === "public_evidence_exploration" ? "本批未筛出合格候选" : strategy === "novel" ? "尚无新靶点探索结果" : "尚无已有证据靶点结果"} text={quality?.analysis_tier === "public_evidence_exploration" ? "已完成证据检查；请补充独立遗传或功能数据后复核。" : strategy === "novel" ? "运行后按实际证据展示探索候选；证据不足时会说明筛选结果与待补数据。" : job?.adapter_mode === "not_configured" ? "当前任务处于未配置状态，没有生成或替代任何分析数据。" : "输入疾病并运行真实公开数据分析。"} />}</div>
    {selected && <div className="evidence-panel" role="dialog" aria-modal="true" aria-label={`${selected.symbol} 靶点证据`}><header><div><small>靶点证据卡</small><h3>{selected.symbol} · {selected.external_id}</h3><p>{selected.name}</p></div><button className="text-button" onClick={() => { evidenceRequest.current += 1; setSelected(null); }}>关闭</button></header><div className="evidence-provenance"><span>来源 <b>{sourceLabel(selected.source)}</b></span><span>版本 <b>{selected.source_version ?? "—"}</b></span><span>证据记录 <b>{evidenceTotal}</b></span><span>模式 <b>{selected.data_mode ? adapterModeLabels[selected.data_mode] : "—"}</b></span></div>{selectedHit && <TargetToHitDetail hit={selectedHit} />}{selected.evidence_breakdown?.exploration && <div className="notice"><div><strong>{selected.evidence_breakdown.exploration.summary}</strong><p>{selected.evidence_breakdown.exploration.gaps}</p><p>{selected.evidence_breakdown.exploration.next_step}</p></div></div>}<TargetMolecules key={selected.id} targetId={selected.id} symbol={selected.symbol} diseaseId={diseaseId}/><div className="coverage-list">{Object.entries(categories).map(([name, item]) => <span key={name} className={item.status}><b>{evidenceCategoryLabels[name] ?? name}</b><small>{evidenceStatusLabels[item.status] ?? item.status}{item.score == null ? "" : ` · ${item.score.toFixed(2)}`}</small></span>)}</div>{evidenceBusy ? <div className="inline-empty"><LoaderCircle className="spin" /> 正在加载证据…</div> : evidenceError ? <div className="notice error"><CircleAlert size={18} /> {evidenceError}<button className="text-button" onClick={() => inspectTarget(selected)}>重新加载</button></div> : <div className="table-card"><table><thead><tr><th>证据分类</th><th>证据类型</th><th>数据来源</th><th>证据评分</th><th>作用方向</th><th>候选类型</th></tr></thead><tbody>{evidence.map((item) => <tr key={item.id}><td>{item.category ? evidenceCategoryLabels[item.category] ?? item.category : "未分类"}</td><td>{item.datatype ? evidenceDatatypeLabels[item.datatype] ?? item.datatype : "—"}</td><td>{sourceLabel(item.datasource ?? item.source)}</td><td>{score(item.score)}</td><td>{directionLabels[item.direction_on_target ?? item.direction_on_trait ?? ""] ?? item.direction_on_target ?? item.direction_on_trait ?? "—"}</td><td>{item.confidence ? noveltyClassLabels[item.confidence] ?? item.confidence : "—"}</td></tr>)}{!evidence.length && <tr><td colSpan={6}><div className="inline-empty">暂无逐条证据，覆盖状态见上方。</div></td></tr>}</tbody></table></div>}</div>}
  </>;
}

function PerturbationView({ results, job, targetJob, compatibility, busy, run }: { results: PerturbationResult[]; job?: Job; targetJob?: Job; compatibility: TargetCompatibility | null; busy: string; run: () => void }) {
  const diseaseSpecific = results.find((item) => (item.result as { analysis_tier?: string }).analysis_tier === "disease_specific");
  const baselineCount = results.filter((item) => (item.result as { analysis_tier?: string }).analysis_tier === "evidence_baseline").length;
  const first = (diseaseSpecific ?? results[0])?.result as { analysis_tier?: string; benchmark?: { candidate_count?: number; direction_coverage?: number; top20_multimodal_count?: number; top20_high_grade_count?: number; known_drug_ndcg_at_20_sanity_only?: number; score_distributions?: Record<string, { minimum?: number; median?: number; maximum?: number; unique_values?: number }>; quality_gates?: { passed?: boolean } }; provenance?: { snapshot_id?: string; model_version?: string; source_registry_version?: string } } | undefined;
  const benchmark = first?.benchmark;
  const canRun = Boolean(targetJob) && (targetJob?.adapter_mode !== "real" || compatibility?.compatible);
  const baselinePayloads = results.map((item) => item.result as { analysis_tier?: string; direction?: string; direction_status?: string; direction_confidence?: number; direction_evidence?: { source?: string }[]; direction_lookup?: { directional_target_count?: number } }).filter((item) => item.analysis_tier === "evidence_baseline");
  const directionEvidenceCount = baselinePayloads.filter((item) => item.direction_status === "directional_evidence_supported" || item.direction_status === "directional_evidence_limited" || item.direction_status === "upstream_supported").length;
  const averageDirectionConfidence = baselinePayloads.length ? baselinePayloads.reduce((sum, item) => sum + (item.direction_confidence ?? 0), 0) / baselinePayloads.length : 0;
  const depmapContextCount = baselinePayloads.filter((item) => item.direction_evidence?.some((evidence) => evidence.source === "DepMap 24Q4")).length;
  return <><PanelHeader kicker="虚拟干预" title="判断应该抑制还是激活靶点" description="根据表达、遗传与网络证据，推测靶点干预方向。" action={<div className="action-pair"><JobBadge job={job} /><button className="primary-button" disabled={!canRun || !!busy} onClick={run}><Play size={16} /> 运行虚拟干预</button></div>} guide="perturbation" />
    {!targetJob && <div className="notice"><CircleAlert /> 请先在“已有证据靶点”或“新靶点探索”中完成一次分析。</div>}
    {targetJob?.adapter_mode === "real" && compatibility && <div className={`compatibility-card ${compatibility.compatible ? "ready" : "blocked"}`}><div><strong>本次计算范围</strong><span>{compatibility.upstream_disease_name} · {compatibility.strategy === "novel" ? "新靶点探索" : "已有证据靶点"} · {compatibility.selection_mode === "selected" ? `已选择 ${compatibility.selected_count} 个` : "默认排名前 10 个"}</span></div><div><b>{compatibility.computable_count}</b><small>个可计算</small></div><p>{compatibility.message}{typeof compatibility.disease_specific_count === "number" && typeof compatibility.baseline_count === "number" ? ` 专属模型 ${compatibility.disease_specific_count} 个，证据基线 ${compatibility.baseline_count} 个。` : ""}{compatibility.unavailable_count > 0 && compatibility.unavailable_symbols.length > 0 ? ` 暂不覆盖：${compatibility.unavailable_symbols.join("、")}` : ""}</p></div>}

    {baselineCount > 0 && <div className="notice warning"><CircleAlert /> 其中 {baselineCount} 个靶点使用“公开证据方向推测”：系统会尽量给出优先抑制或优先激活，并同时展示支持分、反对信号和验证建议；标为“模型推测”的方向尚不是实验证实结论。</div>}
    {baselineCount > 0 && <div className="quality-grid"><div><small>优先抑制</small><strong>{baselinePayloads.filter((item) => item.direction === "inhibit").length}</strong></div><div><small>优先激活 / 恢复</small><strong>{baselinePayloads.filter((item) => item.direction === "activate").length}</strong></div><div><small>有人群或临床方向记录</small><strong>{directionEvidenceCount}</strong></div><div><small>使用产品级方向先验</small><strong>{baselinePayloads.length - directionEvidenceCount}</strong></div><div><small>有 DepMap 安全情境</small><strong>{depmapContextCount}</strong></div><div><small>平均方向推测强度</small><strong>{score(averageDirectionConfidence)}</strong></div></div>}
    {first?.provenance && <div className="source-banner real"><Database /><div><strong>{first.analysis_tier === "evidence_baseline" ? "公开证据基线" : "版本化疾病专属虚拟干预快照"}</strong><span>{first.provenance.snapshot_id} · {first.provenance.model_version} · {first.provenance.source_registry_version ?? "上游靶点数据版本"}</span></div><span className="mode-pill">真实数据</span></div>}
    {benchmark && <div className="quality-grid"><div><small>方向明确的候选</small><strong>{benchmark.candidate_count ?? "—"}</strong></div><div><small>可给出方向的比例</small><strong>{typeof benchmark.direction_coverage === "number" ? `${Math.round(benchmark.direction_coverage * 100)}%` : "—"}</strong></div><div title="最小值 / 中位数 / 最大值"><small>结果可信度范围</small><strong>{benchmark.score_distributions?.confidence ? `${preciseScore(benchmark.score_distributions.confidence.minimum)} / ${preciseScore(benchmark.score_distributions.confidence.median)} / ${preciseScore(benchmark.score_distributions.confidence.maximum)}` : "—"}</strong></div><div title="数值越多说明候选之间越有区分度"><small>可区分的可信度数值</small><strong>{benchmark.score_distributions?.confidence?.unique_values ?? "—"}</strong></div><div title="前 20 名中至少有两类直接数据支持的候选"><small>多来源支持的前 20 名</small><strong>{benchmark.top20_multimodal_count ?? "—"}</strong></div><div><small>自动质量检查</small><strong>{benchmark.quality_gates?.passed ? "通过" : "未通过"}</strong></div></div>}
    {!!results.length && <details className="technical-details"><summary>查看分数的详细计算口径</summary><p>遗传支持同时考虑证据本身强度和本批候选中的位置；网络位置同时参考全蛋白组与当前候选。单细胞统计可靠性较低或供体数较少时，可信度会自动降低。</p></details>}
    <div className="result-list">{results.map((r, i) => { const detail = r.result as { analysis_tier?: string; symbol?: string; direction_label?: string; direction?: string; direction_confidence?: number; evidence_grade?: string; direct_modality_count?: number; source_count?: number }; const isBaseline = detail.analysis_tier === "evidence_baseline"; const directionText = detail.direction_label ?? (detail.direction === "inhibit" ? "建议抑制" : detail.direction === "activate" ? "建议激活" : "方向待确认"); return <article key={r.id}><div className="result-number">{String(i + 1).padStart(2, "0")}</div><div><strong>{detail.symbol ?? r.intervention}</strong><span>{directionText} · {r.cell_context} · {detail.evidence_grade === "high" ? "证据较充分" : detail.evidence_grade === "moderate" ? "证据中等" : "需要更多验证"}</span></div><div className="result-metric"><small>{isBaseline ? "验证优先级" : "预计恢复潜力"}</small><strong>{score(r.reversal_score)}</strong></div><div className="result-metric"><small>{isBaseline ? "方向推测强度" : "结果可信度"}</small><strong>{score(isBaseline ? detail.direction_confidence : r.confidence)}</strong></div><span className="demo-pill">{r.demo ? "演示" : isBaseline ? `方向推测 · ${detail.source_count ?? 0} 类来源` : `疾病专属 · ${detail.direct_modality_count ?? 0} 类证据`}</span></article>; })}{!results.length && <Empty icon={<Activity />} title="还没有干预结果" text="先完成靶点发现并选择候选，再运行本页分析。有疾病专属数据时使用完整模型；其余疾病会生成带证据等级的方向推测与验证建议。" />}</div>
    {!!results.length && <div className="table-card"><table><thead><tr><th>候选靶点</th><th>建议怎么干预</th><th title="疾病样本中该基因升高或降低的程度">疾病中的表达变化</th><th>遗传支持</th><th>与疾病基因群的接近程度</th><th>直接数据来源</th><th>病例 / 对照供体</th><th>结论、依据与下一步</th></tr></thead><tbody>{results.map((row) => { const value = row.result as { analysis_tier?: string; symbol?: string; direction?: string; direction_label?: string; direction_status?: string; direction_confidence?: number; direction_support?: { inhibit?: number; activate?: number }; direction_margin?: number; direction_evidence?: { source?: string; signal?: string; conclusion?: string; strength?: string; limitation?: string }[]; counter_signals?: { source?: string; signal?: string; conclusion?: string }[]; recommended_experiments?: string[]; boundary?: string; direction_component?: number; genetic_support?: number; genetic_support_raw?: number; genetic_support_percentile?: number; network_module_reach?: number; network_global_percentile?: number; network_candidate_percentile?: number; direct_modality_count?: number; source_count?: number; case_donors?: number; control_donors?: number; single_cell_fdr?: number; effect_interpretation?: string; decision_summary?: string }; const isBaseline = value.analysis_tier === "evidence_baseline"; const topPercent = typeof value.network_candidate_percentile === "number" ? Math.max(0, Math.round((1 - value.network_candidate_percentile) * 100)) : null; const directionText = value.direction_label ?? (value.direction === "inhibit" ? "建议抑制" : value.direction === "activate" ? "建议激活" : "方向待确认"); return <tr key={`${row.id}-detail`}><td><strong>{value.symbol ?? row.intervention}</strong><div className="metric-subline">{isBaseline ? "公开证据方向推测" : "疾病专属模型"}</div></td><td><strong>{directionText}</strong>{isBaseline && <div className="metric-subline">方向强度 {score(value.direction_confidence)} · 抑制支持 {score(value.direction_support?.inhibit)} · 激活支持 {score(value.direction_support?.activate)}</div>}</td><td>{isBaseline ? "尚无专属数据" : preciseScore(value.direction_component)}<div className="metric-subline">{isBaseline ? "已用其他证据生成首轮假设" : `统计可靠性（FDR）${preciseScore(value.single_cell_fdr)}`}</div></td><td>{preciseScore(value.genetic_support)}<div className="metric-subline">{isBaseline ? "来自上游疾病—靶点证据" : `原始值 ${preciseScore(value.genetic_support_raw)} · 超过本批 ${typeof value.genetic_support_percentile === "number" ? `${Math.round(value.genetic_support_percentile * 100)}%` : "—"} 候选`}</div></td><td>{isBaseline ? preciseScore(value.network_module_reach) : topPercent == null ? "—" : `位于候选前 ${topPercent}%`}<div className="metric-subline">{isBaseline ? "疾病情境证据代理，不是网络传播结果" : `校准值 ${preciseScore(value.network_module_reach)} · 全局超过 ${typeof value.network_global_percentile === "number" ? `${Math.round(value.network_global_percentile * 100)}%` : "—"}`}</div></td><td>{isBaseline ? value.source_count ?? "—" : `${value.direct_modality_count ?? "—"} / 3`}<div className="metric-subline">{isBaseline ? "上游独立数据类型" : "遗传关联、表达调控、单细胞"}</div></td><td>{isBaseline ? "尚无专属供体" : `${value.case_donors ?? "—"} / ${value.control_donors ?? "—"}`}</td><td><strong>{isBaseline ? value.decision_summary ?? "先做双向功能验证" : value.effect_interpretation === "attenuate_disease_upregulated_target" ? "降低疾病中异常升高的基因信号" : value.effect_interpretation === "restore_disease_downregulated_target" ? "恢复疾病中异常降低的基因信号" : "方向仍需确认"}</strong>{isBaseline && <details><summary>查看完整方向依据</summary><p>方向证据等级：{value.direction_status === "directional_evidence_supported" ? "有人群或临床方向证据支持" : value.direction_status === "upstream_supported" ? "上游模型已有明确方向" : "模型推测，需实验确认"}；支持差值 {preciseScore(value.direction_margin)}。</p><ul>{(value.direction_evidence ?? []).map((item, index) => <li key={`${item.source}-${index}`}><b>{item.source}</b>：{item.signal}；{item.conclusion}（{item.strength ?? "—"}）。{item.limitation}</li>)}</ul>{!!value.counter_signals?.length && <><p><b>需要注意的反向或安全信号</b></p><ul>{value.counter_signals.map((item, index) => <li key={`${item.source}-${index}`}>{item.source}：{item.signal}；{item.conclusion}</li>)}</ul></>}<p><b>建议验证：</b>{(value.recommended_experiments ?? []).join("；") || "双向功能实验"}</p><p>{value.boundary}</p></details>}</td></tr>; })}</tbody></table></div>}
  </>;
}

function CompoundsView({ projectId, diseaseId, compounds, matchJob, admetJob, targetJob, compatibility, busy, match, admet }: { projectId:string; diseaseId?:string; compounds: Compound[]; matchJob?: Job; admetJob?: Job; targetJob?: Job; compatibility: TargetCompatibility | null; busy: string; match: () => void; admet: () => void }) {
  const qsar = useProjectQsar(projectId);
  const [comparisonIds, setComparisonIds] = useState<string[]>([]);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<"all" | "attention" | "evaluated">("all");
  const [targetFilter, setTargetFilter] = useState("");
  const [stageFilter, setStageFilter] = useState("");
  const [sort, setSort] = useState("match");
  const first = compounds[0]?.details as CompoundDetails | undefined;
  const benchmark = first?.provenance?.benchmark;
  const canMatch = Boolean(targetJob?.adapter_mode === "real" && compatibility?.compatible);
  const targetOptions = Array.from(new Set(compounds.flatMap((c) => (compoundDetails(c).target_matches ?? []).map((m) => m.target_symbol).filter((symbol): symbol is string => Boolean(symbol))))).sort();
  const visibleCompounds = filterCompounds(compounds, { query, target: targetFilter, stage: stageFilter, filter, sort });
  const showAlerts = visibleCompounds.some(c => { const n = compoundDetails(c).descriptors?.structural_alert_count; return typeof n === "number" && Number.isFinite(n) && n >= 0; });
  const showAdmet = visibleCompounds.some(c => predictionCount(c) > 0);
  const completeCount = compounds.filter((c) => predictionCount(c) === 5).length;
  return <><PanelHeader kicker="化合物评价" title="寻找化合物并提前排查开发风险" description="依据实测活性匹配分子，评估结构、性质与开发风险。" action={<div className="stack-actions"><button className="secondary-button" disabled={!canMatch || !!busy} onClick={match}>1. 寻找匹配化合物</button><button className="primary-button" disabled={!compounds.length || !!busy} onClick={admet}>2. 评价性质与风险</button></div>} guide="compounds" />
    <div className="job-strip"><JobBadge job={matchJob} /><JobBadge job={admetJob} /></div>
    {targetJob?.adapter_mode === "real" && compatibility && <div className={`compatibility-card ${compatibility.compatible ? "ready" : "blocked"}`}><div><strong>匹配范围</strong><span>{compatibility.upstream_disease_name} · {compatibility.strategy === "novel" ? "新靶点探索" : "已有证据靶点"} · {compatibility.selection_mode === "selected" ? `已选择 ${compatibility.selected_count} 个` : "默认排名前 10 个"}</span></div><div><b>{compatibility.computable_count}</b><small>{compatibility.coverage_scope === "gene_and_molecule_level_global" ? "个靶点进入活性检索" : "个靶点有活性化合物"}</small></div><p>{compatibility.message}</p></div>}
    {benchmark && <details className="technical-details"><summary>检索范围与数据覆盖</summary><div className="quality-grid"><div><small>本批请求靶点</small><strong>{benchmark.requested_target_count ?? benchmark.chembl_target_count ?? "—"}</strong></div><div><small>实际送入 ChEMBL</small><strong>{benchmark.chembl_target_count ?? "—"}</strong></div><div><small>有合格体外活性的靶点</small><strong>{benchmark.chembl_activity_targets ?? "—"}</strong></div><div><small>通过筛选的化合物</small><strong>{benchmark.qualified_compounds ?? "—"}</strong></div><div><small>锁定快照 / 在线新增</small><strong>{benchmark.snapshot_compound_count ?? "—"} / {benchmark.live_compound_count ?? "—"}</strong></div><div><small>已有完整五项性质预测</small><strong>{completeCount}</strong></div></div></details>}
    {!!compounds.length && <><div className="compound-filter-bar"><label><Search size={15} /><input value={query} onChange={(event) => setQuery(event.target.value)} aria-label="搜索具体分子" placeholder="名称、ChEMBL ID、靶点或 SMILES" /></label><div role="group" aria-label="化合物筛选"><button className={filter === "all" ? "active" : ""} onClick={() => setFilter("all")}>全部 {compounds.length}</button><button className={filter === "evaluated" ? "active" : ""} onClick={() => setFilter("evaluated")}>五项预测齐全 {completeCount}</button><button className={filter === "attention" ? "active" : ""} onClick={() => setFilter("attention")}>需要重点复核</button></div><span aria-live="polite">当前显示 {visibleCompounds.length} / {compounds.length} 个</span></div>
      <div className="molecule-controls">
        <label>靶点<select value={targetFilter} onChange={(e) => setTargetFilter(e.target.value)}><option value="">全部靶点</option>{targetOptions.map((value) => <option key={value}>{value}</option>)}</select></label>
        <label>开发阶段<select value={stageFilter} onChange={(e) => setStageFilter(e.target.value)}><option value="">全部阶段</option><option value="approved">已有批准记录</option><option value="clinical">临床研究分子</option><option value="research">研究化合物 / 阶段未明</option><option value="demo">演示分子</option></select></label>
        <label>比较顺序<select value={sort} onChange={(e) => setSort(e.target.value)}><option value="match">靶点匹配分</option><option value="activity">中位 pChEMBL</option><option value="documents">文献数</option><option value="complete">预测完整度</option></select></label>
        <button className="text-button" onClick={() => { setQuery(""); setTargetFilter(""); setStageFilter(""); setSort("match"); setFilter("all"); }}>重置筛选</button>
      </div>
      <details className="technical-details"><summary>同一靶点的分子活性比较</summary><MoleculeActivityChart compounds={visibleCompounds} target={targetFilter} /></details><details className="technical-details"><summary>查看活性与性质分数的专业口径</summary><p>体外活性强度（pChEMBL）通常越高越强；实验数和文献数反映记录覆盖，不能保证独立复现；跨 assay 数值不应直接解释为疗效强弱。安全性与开发性分数来自结构和五项早期性质预测，不是人体安全概率。缺少相似结构时应降低模型预测权重。研发阶段为分子整体最高阶段，具体疾病阶段见药物资料。</p></details></>}
    <QsarPanel projectId={projectId}/>
    {qsar.error && <p role="status">{qsar.error}<button className="text-button" onClick={qsar.retry}>重试</button></p>}
    <CompoundReadout compounds={visibleCompounds} target={targetFilter} qsar={qsar.data}/>
    <MoleculeComparison projectId={projectId} compounds={compounds.filter(c=>comparisonIds.includes(c.id))} remove={id=>setComparisonIds(s=>s.filter(x=>x!==id))} clear={()=>setComparisonIds([])} diseaseId={diseaseId}/>
    <div className="table-card compound-table"><table style={{ minWidth: 1000 + (showAlerts ? 160 : 0) + (showAdmet ? 160 : 0) }}><thead><tr><th>化合物</th><th>靶点与体外活性</th><th>证据重复情况</th><th>开发进展</th><th title="分子量、脂溶性、极性和类药性">关键结构性质</th>{showAlerts && <th>需要注意的结构</th>}{showAdmet && <th>吸收与安全提示</th>}<th>综合评价</th></tr></thead><tbody>{visibleCompounds.map((c) => { const details = (c.details ?? {}) as CompoundDetails; const best = matchesFor(c, targetFilter)[0]; const descriptors = details.descriptors; return <tr key={c.id} id={`molecule-${c.id}`}><td><strong>{displayMoleculeName(c.identifier,c.name)}</strong><small>{c.identifier}</small><label className="compare-check"><input type="checkbox" aria-label={`对比 ${displayMoleculeName(c.identifier,c.name)}`} checked={comparisonIds.includes(c.id)} disabled={c.demo || (!comparisonIds.includes(c.id) && compounds.filter(x=>comparisonIds.includes(x.id)).length>=5)} onChange={()=>setComparisonIds(s=>toggleComparison(s.filter(id=>compounds.some(x=>x.id===id)),c.id))}/>加入对比</label><MoleculeDetails compound={c} diseaseId={diseaseId} /><MoleculeValidation compound={c} target={targetFilter}/></td><td><strong>{best?.target_symbol ?? "—"}</strong><small>典型活性 {preciseScore(best?.pchembl_median)} · 最佳活性 {preciseScore(best?.pchembl_best)}</small><small>{best?.intervention_direction === "inhibit" ? "上游建议抑制；请结合机制方向复核" : best?.intervention_direction === "activate" ? "上游建议激活；请结合机制方向复核" : "作用方向仍需确认"}</small><MoleculeTargetDetails compound={c} /></td><td><strong>{best?.assay_count ?? "—"} 个实验</strong><small>{best?.document_count ?? "—"} 篇文献 · {best?.activity_count ?? "—"} 条活性记录</small></td><td><strong>{developmentStage(c).label}</strong>{details.first_approval && <small>{details.first_approval} 年首次批准记录</small>}{(details.black_box_warning || details.withdrawn_flag) && <span className="risk-pill risk-较高风险">存在监管提示</span>}</td><td><strong>分子量 {preciseScore(descriptors?.molecular_weight)}</strong><small>脂溶性 {preciseScore(descriptors?.logp)} · 极性 {preciseScore(descriptors?.tpsa)}</small><small>类药性 {preciseScore(descriptors?.qed)} · 常用规则不符合 {descriptors?.lipinski_violations ?? "—"} 项</small></td>{showAlerts && <td>{descriptors?.structural_alert_count != null && Number.isFinite(descriptors.structural_alert_count) && descriptors.structural_alert_count >= 0 ? <><strong>{descriptors.structural_alert_count} 条提示</strong><small>{descriptors.structural_alerts?.slice(0, 2).join("；") || (descriptors.structural_alert_count === 0 ? "未命中已检查的结构警示" : "")}</small></> : "—"}</td>}{showAdmet && <td>{predictionCount(c) > 0 ? <MoleculePredictions compound={c} /> : "—"}</td>}<td><strong>靶点匹配 {score(c.match_score)}</strong><small>安全 {score(c.evaluation?.safety)} · 开发性 {score(c.evaluation?.developability)}</small><small>{predictionCount(c) < 5 ? "基础性质分，不含完整五端点评价" : "综合模型分，不是人体安全概率"}</small><span className="demo-pill">{c.demo ? "演示结果" : predictionCount(c) > 0 ? "实验活性 + 性质预测" : "实验活性 + 结构资料"}</span></td></tr>; })}{!visibleCompounds.length && <tr><td colSpan={6 + Number(showAlerts) + Number(showAdmet)}><div className="inline-empty">{compounds.length ? "没有符合当前筛选条件的分子，请调整或重置筛选。" : "尚无化合物结果。请先运行匹配；已完成但为空时，可在任务记录中查看检索范围。"}</div></td></tr>}</tbody></table></div>
    {benchmark?.admet && <details className="benchmark-details"><summary>查看五项性质模型的独立测试表现</summary><div className="admet-benchmark-grid">{Object.entries(benchmark.admet).map(([id, value]) => <article key={id}><small>{value.label_zh ?? id}</small><strong>{value.auroc != null ? `区分能力 ${value.auroc.toFixed(3)}` : `平均误差 ${value.mae?.toFixed(3) ?? "—"}`}</strong><span>训练数据 {value.train_count ?? "—"} 条 · 不同化学骨架测试 {value.held_out_scaffold_count ?? "—"} 条</span></article>)}</div></details>}
  </>;
}

type RankingMetric = "compound_match" | "efficacy" | "safety" | "developability";
const rankingMetricLabels: Record<RankingMetric, string> = { compound_match: "靶点/活性匹配", efficacy: "药效", safety: "安全性", developability: "可开发性" };
const rankingPresets: { id: string; name: string; description: string; weights: Record<RankingMetric, number> }[] = [
  { id: "balanced", name: "均衡策略", description: "同时考虑活性、药效、安全和开发性", weights: { compound_match: .35, efficacy: .30, safety: .20, developability: .15 } },
  { id: "efficacy", name: "药效优先", description: "提高活性匹配和早期药效权重", weights: { compound_match: .35, efficacy: .40, safety: .15, developability: .10 } },
  { id: "safety", name: "安全优先", description: "适合优先排查风险的早期分流", weights: { compound_match: .25, efficacy: .20, safety: .40, developability: .15 } },
  { id: "developability", name: "开发性优先", description: "提高结构与成药性质的影响", weights: { compound_match: .25, efficacy: .20, safety: .20, developability: .35 } },
];

function rankingValues(item: Ranking): Record<RankingMetric, number> {
  return {
    compound_match: item.compound?.match_score ?? 0,
    efficacy: item.compound?.evaluation?.efficacy ?? 0,
    safety: item.compound?.evaluation?.safety ?? 0,
    developability: item.compound?.evaluation?.developability ?? 0,
  };
}

function compoundRisk(compound?: Compound) {
  if (!compound) return { label: "未评估", level: "unknown", detail: "缺少化合物数据" };
  const details = (compound.details ?? {}) as CompoundDetails;
  const predictions = ((compound.evaluation?.admet ?? {}) as { predictions?: Record<string, { domain?: string }> }).predictions ?? details.admet_predictions ?? {};
  const domains = Object.values(predictions).map((item) => item.domain);
  const alertCount = details.descriptors?.structural_alert_count ?? 0;
  if (details.withdrawn_flag || details.black_box_warning || domains.includes("outside")) return { label: "需重点复核", level: "high", detail: details.withdrawn_flag || details.black_box_warning ? "存在监管警示" : "存在适用域外预测" };
  if (predictionCount(compound) < 5 || details.descriptors?.structural_alert_count == null) return { label: "评价未齐全", level: "unknown", detail: `已有 ${predictionCount(compound)}/5 项预测，结构与适用域仍需复核` };
  if (alertCount > 0 || domains.includes("borderline")) return { label: "需关注", level: "medium", detail: alertCount > 0 ? `${alertCount} 条结构警示` : "存在适用域边缘预测" };
  return { label: "暂未见高风险信号", level: "low", detail: "仍需实验确认" };
}

function RankingDecision({ item, busy, decide }: { item: Ranking; busy: string; decide: (r: Ranking, d: "pending" | "selected" | "excluded", rationale: string) => Promise<void> }) {
  const [rationale, setRationale] = useState(item.rationale ?? "");
  const saving = busy === `decision-${item.id}`;
  return <details className="ranking-decision"><summary>{item.decision === "selected" ? "已入选" : item.decision === "excluded" ? "已排除" : "待决策"}</summary><textarea aria-label={`${item.compound?.name ?? "候选"}的决策理由`} value={rationale} onChange={(event) => setRationale(event.target.value)} placeholder="填写入选、排除或暂缓判断的理由" /><div><button disabled={saving} className={item.decision === "selected" ? "selected-button" : "secondary-button"} onClick={() => decide(item, "selected", rationale || "进入后续实验与人工复核。")}>入选</button><button disabled={saving} className={item.decision === "excluded" ? "danger-button" : "text-button"} onClick={() => decide(item, "excluded", rationale || "存在风险或证据不足，暂不进入下一步。")}>排除</button><button disabled={saving} className="text-button" onClick={() => decide(item, "pending", rationale || "保留为待决策候选。")}>待定</button></div></details>;
}

function RankingView({ projectId, rankings, job, ready, busy, run, decide }: { projectId:string; rankings: Ranking[]; job?: Job; ready: boolean; busy: string; run: (weights: Record<RankingMetric, number>) => void; decide: (r: Ranking, d: "pending" | "selected" | "excluded", rationale: string) => Promise<void> }) {
  const qsar = useProjectQsar(projectId);
  const [presetId, setPresetId] = useState("balanced");
  const preset = rankingPresets.find((item) => item.id === presetId) ?? rankingPresets[0];
  const paretoIds = useMemo(() => new Set(rankings.filter((candidate) => {
    const candidateValues = Object.values(rankingValues(candidate));
    return !rankings.some((other) => {
      if (other.id === candidate.id) return false;
      const otherValues = Object.values(rankingValues(other));
      return otherValues.every((value, index) => value >= candidateValues[index]) && otherValues.some((value, index) => value > candidateValues[index]);
    });
  }).map((item) => item.id)), [rankings]);
  const selected = rankings.filter((item) => item.decision === "selected").length;
  const excluded = rankings.filter((item) => item.decision === "excluded").length;
  const actualWeights = rankings[0]?.weights;
  const decisionSupport = useMemo(() => new Map(rankings.map((item) => [item.id, buildCandidateDecisionSupport(item, rankings)])), [rankings]);
  const actionCounts = useMemo(() => Array.from(decisionSupport.values()).reduce((counts, item) => ({ ...counts, [item.action]: counts[item.action] + 1 }), { advance: 0, review: 0, hold: 0, stop: 0 }), [decisionSupport]);
  return <><PanelHeader kicker="候选决策" title="决定哪些候选推进、复核或暂缓" description="综合证据、风险与排名稳定性，记录推进决定。" action={<div className="action-pair"><JobBadge job={job} /><button className="primary-button" disabled={!ready || !!busy} onClick={() => run(preset.weights)}><BarChart3 size={16} /> {job ? "按所选策略重新排序" : "生成候选排序"}</button></div>} guide="ranking" />
    {!!rankings.length && <div className="portfolio-action-strip"><div><small>优先进入实验评审</small><strong>{actionCounts.advance}</strong><span>信息齐备或已有人工入选决定</span></div><div><small>进入项目评审</small><strong>{actionCounts.review}</strong><span>需要业务与实验团队共同判断</span></div><div><small>暂缓并补证据</small><strong>{actionCounts.hold}</strong><span>信息缺失或存在高优先级风险</span></div><div><small>人工停止</small><strong>{actionCounts.stop}</strong><span>已保存排除决定和理由</span></div></div>}
    <details className="technical-details"><summary>调整排序策略</summary><div className="ranking-strategy"><div><strong>这次更看重什么？</strong><span>选择最符合当前项目目标的方案。它只改变候选的比较顺序，不会改动任何原始实验和预测结果。</span></div><div className="ranking-preset-grid">{rankingPresets.map((item) => <button key={item.id} className={presetId === item.id ? "active" : ""} onClick={() => setPresetId(item.id)}><b>{item.name}</b><small>{item.description}</small></button>)}</div><div className="weight-breakdown">{(Object.entries(preset.weights) as [RankingMetric, number][]).map(([key, value]) => <span key={key}><b>{rankingMetricLabels[key]}</b><i style={{ width: `${value * 100}%` }} /><em>{Math.round(value * 100)}%</em></span>)}</div></div></details>
    {!!rankings.length && <><div className="ranking-summary"><div><small>参与比较</small><strong>{rankings.length}</strong><span>个已完成评价的化合物</span></div><div title="没有其他候选能在四项指标上全部超过它"><small>多维度均衡候选</small><strong>{paretoIds.size}</strong><span>不存在全面更优的替代项</span></div><div><small>人工确认入选</small><strong>{selected}</strong><span>已保存选择理由</span></div><div><small>排除 / 待定</small><strong>{excluded} / {rankings.length - selected - excluded}</strong><span>人工决定不修改模型分</span></div></div>
      <div className="score-contract"><ShieldCheck /><div><strong>当前已发布排序的实际权重</strong><span>{actualWeights ? (Object.entries(actualWeights) as [string, number][]).map(([key, value]) => `${rankingMetricLabels[key as RankingMetric] ?? key} ${Math.round(value * 100)}%`).join(" · ") : "暂无权重记录"}</span></div><div className="benchmark-mini"><small>总分范围</small><b>{score(Math.min(...rankings.map((item) => item.total_score)))}–{score(Math.max(...rankings.map((item) => item.total_score)))}</b></div></div></>}
    {qsar.error && <p role="status">{qsar.error}<button className="text-button" onClick={qsar.retry}>重试</button></p>}
    <div className="table-card ranking-table"><table style={{minWidth:1050}}><thead><tr><th>名次与候选</th><th>本批优先分</th><th>判断依据与下一步</th><th>关键风险</th><th>评分明细</th><th>人工决定</th></tr></thead><tbody>{rankings.map(item=>{const support=decisionSupport.get(item.id)!;const risk=compoundRisk(item.compound);const values=rankingValues(item);return <tr key={item.id}><td><strong>#{item.rank} {item.compound ? displayMoleculeName(item.compound.identifier,item.compound.name) : "未命名候选"}</strong><small>{item.compound?.identifier}</small>{item.compound && <MoleculeDetails compound={item.compound}/>}</td><td><strong>{preciseScore(item.total_score)}</strong><small>仅用于本批排序</small></td><td style={{minWidth:320}}><span className={`portfolio-action ${support.action}`}>{support.actionLabel}</span>{item.compound && <CompoundReasoning compound={item.compound} qsar={qsar.data}/>}</td><td><span className={`ranking-risk ${risk.level}`}>{risk.label}</span><small>{risk.detail}</small></td><td><details><summary>权重与分项依据</summary><p>靶点匹配 {preciseScore(values.compound_match)} · 效果模型分 {preciseScore(values.efficacy)}</p><p>性质风险分 {preciseScore(values.safety)} · 开发性 {preciseScore(values.developability)}</p><p>四种权重下进入前 20%：{Math.round(support.topBandFrequency*100)}%</p><p>{paretoIds.has(item.id)?"四项评分中不存在全面更优项":"存在其他分项更优的候选"}</p>{!!support.missing.length&&<p>需补：{support.missing.join("、")}</p>}<p>{support.explanation}</p></details></td><td><RankingDecision item={item} busy={busy} decide={decide}/></td></tr>;})}{!rankings.length&&<tr><td colSpan={6}><Empty icon={<BarChart3/>} title="还没有候选排序" text="先完成化合物性质评价，再生成排序。"/></td></tr>}</tbody></table></div>
  </>;
}

const evidenceLevelLabels: Record<MethodologySpec["evidence_level"], string> = { observed: "数据库/实验观察", derived: "统计派生", predicted: "模型预测", mixed: "混合证据" };

function MethodologyView({ methods }: { methods: MethodologySpec[] }) {
  const active = methods.filter((method) => method.maturity !== "planned");
  const inputs = new Set(active.flatMap((method) => method.inputs));
  return <>
    <PanelHeader kicker="分析方法" title="每个结果是怎么得到的" description="查看数据来源、计算方法、验证结果与适用范围。" guide="methodology" />
    <div className="method-overview"><div><small>已经可用的模块</small><strong>{active.length}</strong><span>都有明确的数据与方法</span></div><div><small>使用的数据与上游结果</small><strong>{inputs.size}</strong><span>项可追溯输入</span></div><div><small>仍在计划中的能力</small><strong>{methods.length - active.length}</strong><span>不会显示成已经接入</span></div><div><small>结果管理原则</small><strong>可追溯</strong><span>观察数据与模型预测分开</span></div></div>
    <div className="method-principle"><BrainCircuit /><div><strong>关键研发分数不会由大语言模型直接生成</strong><p>页面中的靶点、活性、细胞和化合物数值来自公共数据库、透明统计或经过独立测试的模型。大语言模型只帮助查找、整理和解释信息，不替代项目负责人的决策。</p></div></div>
    <div className="method-grid">{methods.map((method, index) => <article key={method.stage} className="method-card">
      <header><div><small>模块 {String(index + 1).padStart(2, "0")}</small><h3>{method.name}</h3></div><span className={`maturity ${method.maturity}`}>{method.maturity === "baseline" ? "已有可用版本" : method.maturity === "runtime" ? "已经接入" : "尚未接入"}</span></header>
      <div className="evidence-level"><span>结果主要来自</span><b>{evidenceLevelLabels[method.evidence_level]}</b></div>
      <section className="method-summary"><b>一句话说明</b><p>{method.current}</p></section>
      <details className="method-details"><summary>展开查看数据、计算、验证与边界</summary><div>
        <section><b>用了哪些数据库和上游结果</b><div className="method-tags source">{method.inputs.map((item) => <span key={item}>{item}</span>)}</div></section>
        <section><b>系统做了哪些处理</b><ol className="method-steps">{method.method_steps.map((item) => <li key={item}>{item}</li>)}</ol></section>
        <section><b>用户可以看到什么</b><div className="method-tags output">{method.outputs.map((item) => <span key={item}>{item}</span>)}</div></section>
        <section><b>当前已经做到</b><p>{method.production_baseline.join(" · ")}</p></section>
        <section><b>怎样检查结果是否可靠</b><p>{method.validation.join(" · ")}</p></section>
        <section className="method-boundary"><b>这项结果不能说明什么</b><p>{method.boundary}</p></section>
        <section><b>大语言模型在这里做什么</b><p>{method.llm_role}</p></section>
        <small className="method-version">计算版本：{method.version}</small>
      </div></details>
    </article>)}</div>
  </>;
}

function ReportView({ project, jobs, targets, perturbations, knockouts, virtualCells, compounds, rankings, reports, ready, busy, create }: { project: Project; jobs: Job[]; targets: TargetCandidate[]; perturbations: PerturbationResult[]; knockouts: PerturbationResult[]; virtualCells: PerturbationResult[]; compounds: Compound[]; rankings: Ranking[]; reports: ReportRecord[]; ready: boolean; busy: string; create: () => void }) {
  const completedTypes = new Set(jobs.filter((job) => job.status === "completed").map((job) => job.type));
  const evaluated = compounds.filter((item) => item.evaluation).length;
  const decisions = rankings.filter((item) => item.decision !== "pending").length;
  const checks = [
    ["靶点发现", completedTypes.has("target_discovery"), `${targets.length} 个当前候选`],
    ["虚拟干预 / 基因敲除", completedTypes.has("perturbation") || completedTypes.has("gene_knockout"), `${perturbations.length + knockouts.length} 条结果`],
    ["虚拟细胞", completedTypes.has("virtual_cell"), `${virtualCells.length} 条通过门禁的投影`],
    ["化合物匹配", completedTypes.has("compound_matching"), `${compounds.length} 个化合物`],
    ["ADMET 评价", completedTypes.has("admet"), `${evaluated} 个已评价`],
    ["综合排序", completedTypes.has("ranking"), `${rankings.length} 个排序候选`],
  ] as const;
  return <><PanelHeader kicker="项目决策报告" title="保存一份可追溯的推进结论" description="保存当前证据与决定，供评审、导出和追溯。" action={<button className="primary-button" disabled={!ready || !!busy} onClick={create}><FileText size={16} /> {busy === "report" ? "正在整理报告…" : "保存新的报告版本"}</button>} guide="report" />
    <div className="report-summary"><div><small>当前报告对象</small><strong>{project.disease}</strong><span>{project.name}</span></div><div><small>已完成模块</small><strong>{checks.filter((item) => item[1]).length} / 6</strong><span>按最近完成任务判断</span></div><div><small>候选决策</small><strong>{decisions} / {rankings.length}</strong><span>入选或排除已记录理由</span></div><div><small>报告版本</small><strong>{reports.length}</strong><span>历史版本保留</span></div></div>
    <div className="report-layout"><section className="report-readiness"><header><div><small>保存前检查</small><h3>哪些结果会进入报告</h3></div><span>{ready ? "现在可以保存" : "请先完成候选排序"}</span></header>{checks.map(([label, done, detail]) => <div key={label} className={done ? "done" : "pending"}><i>{done ? <Check size={14} /> : <CircleAlert size={14} />}</i><b>{label}</b><span>{detail}</span></div>)}</section><section className="report-contents"><small>报告内容</small><h3>九个方便审阅的部分</h3><ol><li>项目与结论摘要</li><li>靶点发现</li><li>虚拟干预</li><li>基因敲除</li><li>虚拟细胞</li><li>化合物与性质评价</li><li>综合排序与人工选择</li><li>方法与使用边界</li><li>任务与版本记录</li></ol><p>网页版适合在线审阅和打印；PDF 摘要适合快速归档。</p></section></div>
    <div className="subsection-heading"><div><small>历史记录</small><h3>以前保存的报告</h3></div><span>{reports.length} 个独立版本</span></div>
    <div className="report-list">{reports.map((r) => <article key={r.id}><div className="report-icon"><FileText /></div><div><strong>{r.title}</strong><span>版本 {r.version} · {new Date(r.created_at).toLocaleString("zh-CN")} · 状态 {r.status === "ready" ? "可查看" : r.status}</span><details><summary>报告标识</summary><small>{r.id}</small></details></div><div className="report-actions"><a className="secondary-button" target="_blank" rel="noreferrer" href={`${apiBase}/reports/${r.id}`}>查看完整 HTML</a><a className="text-button" href={`${apiBase}/reports/${r.id}/pdf`}>下载 PDF 摘要</a></div></article>)}{!reports.length && <Empty icon={<ClipboardList />} title="尚无项目报告" text="完成候选排序后生成首个可追溯报告版本。" />}</div>
  </>;
}

function Empty({ icon, title, text }: { icon: React.ReactNode; title: string; text: string }) { return <div className="empty-state">{icon}<h3>{title}</h3><p>{text}</p></div>; }
