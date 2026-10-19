"use client";

import type { Project } from "@drug-discovery/contracts";
import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import { DISEASE_OPTIONS } from "@/lib/diseaseCatalog";
import { GENERAL_WORKSPACE_DISEASE, isGeneralWorkspace, lastWorkspaceProjectKey, listAccessibleResearchProjects, openDiseaseProject } from "@/lib/workspaceProjects";
import { useAuthUser } from "./AuthSession";
import type { WorkspaceGuidePage } from "./WorkspaceGuidance";

type DiseaseCacheSummary = {
  project_id: string;
  project_name: string;
  disease: string;
  disease_id?: string | null;
  owner_id: string;
  has_cached_results: boolean;
  completed_modules: number;
  completed_jobs: number;
  cached_modules: string[];
  cached_result_count: number;
  target_count: number;
  target_analyses: number;
  target_strategies: string[];
  latest_cached_at?: string | null;
};

type DiseaseSearchOption = {
  key: string;
  value: string;
  nameEn?: string;
  diseaseId?: string | null;
  projectId?: string;
  projectName?: string;
  ownerId?: string;
  cached: boolean;
  completedModules: number;
  cachedModules: string[];
  cachedResults: number;
  cacheProjects: {
    projectId: string;
    projectName: string;
    ownerId: string;
    cachedModules: string[];
    cachedResults: number;
    latestCachedAt?: string | null;
  }[];
  latestCachedAt?: string | null;
};

const normalize = (value?: string | null) => (value ?? "").trim().toLocaleLowerCase("zh-CN");
const DISEASE_PAGE_SIZE = 20;
const moduleLabels: Record<string, string> = {
  data: "数据", omics_analysis: "测序分析", target_discovery: "靶点发现", fungal_target: "真菌靶标",
  fungal_compound: "真菌化合物", bacterial_target: "细菌靶标", bacterial_compound: "细菌化合物",
  perturbation: "虚拟干预", gene_knockout: "基因敲除",
  virtual_cell: "虚拟细胞", protein_structure: "蛋白结构", compound_matching: "化合物匹配",
  admet: "ADMET", process: "工艺开发", ranking: "候选决策", report: "决策报告",
};
const viewModules: Partial<Record<WorkspaceGuidePage, string[]>> = {
  data: ["data"], omics: ["omics_analysis"], targets: ["target_discovery"],
  perturbation: ["perturbation"], gene_perturbation: ["gene_knockout"], virtual_cell: ["virtual_cell"],
  protein_structure: ["protein_structure"], compounds: ["compound_matching", "admet"],
  process: ["process"], ranking: ["ranking"], report: ["report", "ranking"],
};

function moduleSummary(modules: string[]) {
  const labels = modules.map((module) => moduleLabels[module] ?? module);
  if (labels.length <= 3) return labels.join("、");
  return `${labels.slice(0, 3).join("、")}等 ${labels.length} 个模块`;
}

export function DiseaseProjectPicker({ project, view }: { project: Project; view: WorkspaceGuidePage }) {
  const router = useRouter();
  const user = useAuthUser();
  const [projects, setProjects] = useState<Project[]>([]);
  const [cacheIndex, setCacheIndex] = useState<DiseaseCacheSummary[]>([]);
  const [selection, setSelection] = useState(isGeneralWorkspace(project) ? "" : project.disease);
  const [menuOpen, setMenuOpen] = useState(false);
  const [visibleCount, setVisibleCount] = useState(DISEASE_PAGE_SIZE);
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const pickerRef = useRef<HTMLFormElement>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      listAccessibleResearchProjects(),
      api<DiseaseCacheSummary[]>("/disease-cache-index"),
    ]).then(([projectRows, cacheRows]) => {
      if (!cancelled) {
        setProjects(projectRows);
        setCacheIndex(cacheRows);
        setLoaded(true);
      }
    }).catch((reason) => {
      if (!cancelled) setError(reason instanceof Error ? reason.message : "疾病缓存目录读取失败");
    });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (!menuOpen) return;
    const closeOutside = (event: PointerEvent) => {
      const target = event.target;
      if (target instanceof Node && !pickerRef.current?.contains(target)) setMenuOpen(false);
    };
    const closeWithEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMenuOpen(false);
    };
    document.addEventListener("pointerdown", closeOutside);
    document.addEventListener("keydown", closeWithEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOutside);
      document.removeEventListener("keydown", closeWithEscape);
    };
  }, [menuOpen]);

  useEffect(() => setVisibleCount(DISEASE_PAGE_SIZE), [selection]);

  const options = useMemo(() => {
    const grouped = new Map<string, DiseaseSearchOption>();
    for (const row of cacheIndex) {
      if (row.disease === GENERAL_WORKSPACE_DISEASE) continue;
      const diseaseName = normalize(row.disease);
      const diseaseIdentity = normalize(row.disease_id);
      const key = [...grouped.entries()].find(([, item]) =>
        normalize(item.value) === diseaseName || Boolean(diseaseIdentity && normalize(item.diseaseId) === diseaseIdentity))?.[0] ?? diseaseName ?? diseaseIdentity;
      const next: DiseaseSearchOption = {
        key,
        value: row.disease,
        diseaseId: row.disease_id,
        projectId: row.project_id,
        projectName: row.project_name,
        ownerId: row.owner_id,
        cached: row.has_cached_results,
        completedModules: row.completed_modules,
        cachedModules: row.cached_modules ?? [],
        cachedResults: row.cached_result_count ?? row.completed_jobs,
        cacheProjects: row.has_cached_results ? [{
          projectId: row.project_id,
          projectName: row.project_name,
          ownerId: row.owner_id,
          cachedModules: row.cached_modules ?? [],
          cachedResults: row.cached_result_count ?? row.completed_jobs,
          latestCachedAt: row.latest_cached_at,
        }] : [],
        latestCachedAt: row.latest_cached_at,
      };
      const previous = grouped.get(key);
      if (!previous) {
        grouped.set(key, next);
        continue;
      }
      const modules = [...new Set([...previous.cachedModules, ...next.cachedModules])];
      const cacheProjects = [...previous.cacheProjects, ...next.cacheProjects];
      const best = [...cacheProjects].sort((a, b) =>
        b.cachedModules.length - a.cachedModules.length
        || b.cachedResults - a.cachedResults
        || Date.parse(b.latestCachedAt ?? "0") - Date.parse(a.latestCachedAt ?? "0")
        || Number(b.ownerId === user?.username) - Number(a.ownerId === user?.username))[0];
      grouped.set(key, {
        ...previous,
        projectId: best?.projectId,
        projectName: best?.projectName,
        ownerId: best?.ownerId,
        cached: previous.cached || next.cached,
        completedModules: modules.length,
        cachedModules: modules,
        cachedResults: previous.cachedResults + next.cachedResults,
        cacheProjects,
        latestCachedAt: best?.latestCachedAt,
      });
    }
    for (const item of DISEASE_OPTIONS) {
      const catalogName = normalize(item.nameZh);
      const catalogIdentity = normalize(item.ontologyId);
      const key = [...grouped.entries()].find(([, option]) =>
        normalize(option.value) === catalogName || Boolean(catalogIdentity && normalize(option.diseaseId) === catalogIdentity))?.[0] ?? catalogName ?? catalogIdentity;
      const existing = grouped.get(key);
      if (existing) {
        grouped.set(key, { ...existing, nameEn: item.nameEn, diseaseId: existing.diseaseId ?? item.ontologyId });
      } else {
        grouped.set(key, {
          key, value: item.nameZh, nameEn: item.nameEn, diseaseId: item.ontologyId,
          cached: false, completedModules: 0, cachedModules: [], cachedResults: 0, cacheProjects: [],
        });
      }
    }
    return [...grouped.values()].sort((a, b) =>
      Number(b.cached) - Number(a.cached) || b.completedModules - a.completedModules || b.cachedResults - a.cachedResults || a.value.localeCompare(b.value, "zh-CN"));
  }, [cacheIndex, user?.username]);

  const query = normalize(selection);
  const matchingOptions = useMemo(() => options.filter((item) => {
    if (!query) return true;
    return [item.value, item.nameEn, item.diseaseId, item.projectName].some((value) => normalize(value).includes(query));
  }), [options, query]);
  const visibleOptions = matchingOptions.slice(0, visibleCount);
  const cachedDiseaseCount = options.filter((item) => item.cached).length;
  const selectedOption = options.find((item) => [item.value, item.nameEn, item.diseaseId].some((value) => normalize(value) === query));
  const currentCache = options.find((item) =>
    normalize(item.value) === normalize(project.disease)
    || Boolean(project.disease_id && normalize(item.diseaseId) === normalize(project.disease_id)));

  async function open(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true); setError(""); setMenuOpen(false);
    try {
      const desiredModules = viewModules[view] ?? [];
      const allCacheProjects = selectedOption?.cacheProjects ?? [];
      const moduleCacheProjects = allCacheProjects.filter((item) =>
        !desiredModules.length || desiredModules.some((module) => item.cachedModules.includes(module)));
      const preferredCache = [...(moduleCacheProjects.length ? moduleCacheProjects : allCacheProjects)]
        .sort((a, b) =>
          desiredModules.filter((module) => b.cachedModules.includes(module)).length - desiredModules.filter((module) => a.cachedModules.includes(module)).length
          || b.cachedModules.length - a.cachedModules.length
          || b.cachedResults - a.cachedResults)[0];
      if (preferredCache) {
        if (user) window.localStorage.setItem(lastWorkspaceProjectKey(user.username), preferredCache.projectId);
        if (preferredCache.projectId !== project.id) router.push(`/projects/${preferredCache.projectId}?view=${view}`);
        return;
      }
      const selected = await openDiseaseProject(selection, projects, user?.username);
      if (user) window.localStorage.setItem(lastWorkspaceProjectKey(user.username), selected.id);
      if (selected.id !== project.id) router.push(`/projects/${selected.id}?view=${view}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法打开疾病分析");
    } finally { setBusy(false); }
  }

  const current = isGeneralWorkspace(project) ? "尚未选择" : project.disease;
  return <form ref={pickerRef} className="disease-module-picker" onSubmit={open} onBlur={(event) => {
    const next = event.relatedTarget as Node | null;
    if (!next || !event.currentTarget.contains(next)) setMenuOpen(false);
  }} aria-label="选择本模块的疾病">
    <div className="disease-current-state">
      <small>当前查看</small><strong>{current}</strong>
      {currentCache?.cached
        ? <span className="cache-status ready"><b>结果可直接加载</b> · {currentCache.completedModules} 个模块 · {currentCache.cachedResults} 项结果</span>
        : <span className="cache-status empty">当前疾病尚无已保存分析，首次运行后会自动进入数据库。</span>}
    </div>
    <label className="disease-search-field">
      <span>搜索并选择疾病</span>
      <input value={selection} onFocus={() => setMenuOpen(true)} onChange={(event) => { setSelection(event.target.value); setMenuOpen(true); }} onKeyDown={(event) => {
        if (event.key === "Escape") { setMenuOpen(false); event.currentTarget.blur(); }
      }} placeholder="输入中文名、英文名或 MONDO / EFO ID" aria-label="搜索疾病缓存" role="combobox" aria-expanded={menuOpen} aria-controls="disease-search-results" autoComplete="off" />
      {menuOpen && loaded && <div id="disease-search-results" className="disease-search-results" role="listbox">
        <div className="disease-search-summary">
          <span>{query ? `全部目录中找到 ${matchingOptions.length} 种疾病` : `疾病目录共 ${options.length} 种`}</span>
          <small>{cachedDiseaseCount} 种可直接加载完整结果</small>
          {query && <button type="button" onMouseDown={(event) => { event.preventDefault(); setSelection(""); }}>浏览全部疾病</button>}
        </div>
        {visibleOptions.map((item) => <button type="button" role="option" aria-selected={selectedOption?.key === item.key} key={item.key} onMouseDown={(event) => { event.preventDefault(); setSelection(item.value); setMenuOpen(false); }}>
          <span><strong>{item.value}</strong><small>{[item.nameEn, item.diseaseId].filter(Boolean).join(" · ") || item.projectName || "疾病研发空间"}</small></span>
          {item.cached ? <span className="disease-cache-badge"><b>结果可直接加载</b><small>{item.completedModules} 个模块 · {item.cachedResults} 项结果</small></span> : <span className="disease-cache-badge new"><b>暂无分析结果</b><small>选择后可开始分析</small></span>}
        </button>)}
        {visibleOptions.length < matchingOptions.length && <button type="button" className="disease-load-more" onMouseDown={(event) => { event.preventDefault(); setVisibleCount((count) => count + DISEASE_PAGE_SIZE); }}>
          显示更多（还有 {matchingOptions.length - visibleOptions.length} 种）
        </button>}
        {!visibleOptions.length && <div className="disease-search-empty">没有匹配的缓存记录；可以直接使用当前输入新建疾病分析。</div>}
      </div>}
      {selectedOption?.cached && <small className="selected-cache-hint">可直接加载：{moduleSummary(selectedOption.cachedModules)} · 共 {selectedOption.cachedResults} 项结果；将按当前模块打开最完整记录。</small>}
    </label>
    <button type="submit" className="secondary-button" disabled={!loaded || busy || selection.trim().length < 2}>{busy ? "正在打开…" : !loaded ? "正在读取数据库…" : selectedOption?.cached ? "打开数据库结果" : "查看此疾病"}</button>
    {error && <p className="notice error" role="alert">{error}</p>}
  </form>;
}
