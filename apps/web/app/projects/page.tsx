"use client";

import type { Project } from "@drug-discovery/contracts";
import { Archive, ArrowRight, Beaker, CirclePlus, RotateCcw, Search } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { api } from "@/lib/api";
import { DISEASE_CATALOG_STATS } from "@/lib/diseaseCatalog";

type ProjectView = "active" | "archived";

const TRACK_LABELS: Record<Project["research_track"], string> = {
  full: "完整候选发现流程",
  target_discovery: "靶点发现与排序",
  novel_discovery: "新靶点探索",
  compound_prioritization: "候选化合物评价",
};

export default function ProjectsPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [view, setView] = useState<ProjectView>("active");
  const [query, setQuery] = useState("");
  const [updatingId, setUpdatingId] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    api<Project[]>("/projects?limit=100")
      .then((items) => { if (!cancelled) setProjects(items); })
      .catch((e) => { if (!cancelled) setError(e instanceof Error ? e.message : "项目列表读取失败"); });
    return () => { cancelled = true; };
  }, []);

  const counts = useMemo(() => ({
    active: projects.filter((project) => project.status === "active").length,
    archived: projects.filter((project) => project.status === "archived").length,
  }), [projects]);

  const visibleProjects = useMemo(() => {
    const keyword = query.trim().toLocaleLowerCase("zh-CN");
    return projects.filter((project) => {
      if (project.status !== view) return false;
      if (!keyword) return true;
      return [project.name, project.disease, project.disease_id, project.objective]
        .filter(Boolean)
        .some((value) => value!.toLocaleLowerCase("zh-CN").includes(keyword));
    });
  }, [projects, query, view]);

  async function changeStatus(project: Project) {
    const nextStatus = project.status === "active" ? "archived" : "active";
    if (nextStatus === "archived" && !window.confirm(`归档“${project.name}”？历史任务和结果会保留，之后可以恢复。`)) return;
    setUpdatingId(project.id);
    setError("");
    try {
      const updated = await api<Project>(`/projects/${project.id}`, {
        method: "PATCH",
        body: JSON.stringify({ status: nextStatus }),
      });
      setProjects((items) => items.map((item) => item.id === updated.id ? updated : item));
      setNotice(nextStatus === "archived" ? "项目已归档，可在“已归档”中恢复。" : "项目已恢复到进行中。");
    } catch (e) {
      setError(e instanceof Error ? e.message : "项目状态更新失败");
    } finally {
      setUpdatingId("");
    }
  }

  return (
    <AppShell title="AI 药物发现与研发决策平台" subtitle="从疾病机会、靶点与干预证据走到化合物评价和候选决策，让研发团队看清进度、风险、阻塞项与下一步。">
      <section className="project-start">
        <div>
          <span>启动研发项目</span>
          <h2>选择疾病，再决定先做靶点、细胞还是化合物</h2>
          <p>系统会在创建时说明当前数据能支持到哪一步；数据不足的模块会明确提示，不会用演示结果代替真实分析。</p>
        </div>
        <Link href="/projects/new" className="primary-button"><CirclePlus size={18} /> 新建药物发现项目</Link>
      </section>

      <section className="catalog-summary" aria-label="疾病目录能力摘要">
        <div><strong>{DISEASE_CATALOG_STATS.total}</strong><span>疾病目录</span><small>可运行新靶点证据探索</small></div>
        <div><strong>{DISEASE_CATALOG_STATS.oncology}</strong><span>肿瘤目录</span><small>实体瘤与血液肿瘤</small></div>
        <div><strong>{DISEASE_CATALOG_STATS.rare}</strong><span>罕见病目录</span><small>遗传、代谢与罕见系统病</small></div>
        <div><strong>{DISEASE_CATALOG_STATS.enhanced}</strong><span>增强疾病</span><small>已配置疾病实体与重点数据源</small></div>
        <div><strong>{DISEASE_CATALOG_STATS.complete}</strong><span>完整验证</span><small>按完整数据链路逐项验收</small></div>
        <div><strong>v3</strong><span>Target-to-Hit</span><small>从靶点证据衔接分子路线与验证实验</small></div>
        <Link href="/projects/new">打开疾病目录 <ArrowRight size={16} /></Link>
      </section>

      <section className="section-block project-section">
        <div className="section-title project-list-heading">
          <div><span>项目组合</span><h2>{view === "active" ? "进行中的项目" : "已归档项目"}</h2></div>
          <div className="project-list-tools">
            <div className="project-tabs" aria-label="项目状态">
              <button className={view === "active" ? "active" : ""} onClick={() => setView("active")}>进行中 <b>{counts.active}</b></button>
              <button className={view === "archived" ? "active" : ""} onClick={() => setView("archived")}>已归档 <b>{counts.archived}</b></button>
            </div>
            <label className="project-search"><Search size={16} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索疾病或项目" aria-label="搜索疾病或项目" /></label>
          </div>
        </div>

        {notice && <div className="notice success">{notice}</div>}
        {error && <div className="notice error">暂时无法读取项目：{error}。请稍后重试。</div>}
        {!error && visibleProjects.length === 0 && (
          <div className="empty-state"><Beaker size={28} /><h3>{query ? "没有找到匹配项目" : view === "archived" ? "没有已归档项目" : "还没有药物发现项目"}</h3><p>{query ? "可以换一个疾病名称或项目关键词。" : view === "archived" ? "归档后的项目会出现在这里，并且随时可以恢复。" : "创建第一个项目，系统会根据疾病告诉你当前能力范围和建议推进路径。"}</p></div>
        )}

        <div className="project-grid">
          {visibleProjects.map((project) => (
            <article className="project-card" key={project.id}>
              <div className="project-meta"><span>{project.disease}</span><small>更新于 {new Date(project.updated_at).toLocaleDateString("zh-CN")}</small></div>
              <h3>{project.name}</h3>
              <div className="project-profile">
                <span>{TRACK_LABELS[project.research_track] ?? "靶点发现与排序"}</span>
                {project.disease_id && <code>{project.disease_id}</code>}
              </div>
              <p className="project-objective">{project.objective || "尚未填写项目目标"}</p>
              <div className="project-card-actions">
                <Link href={`/projects/${project.id}`} className="card-link">{project.status === "active" ? "进入项目" : "查看历史"} <ArrowRight size={17} /></Link>
                <button className="project-archive-button" onClick={() => void changeStatus(project)} disabled={updatingId === project.id}>
                  {project.status === "active" ? <Archive size={15} /> : <RotateCcw size={15} />}
                  {updatingId === project.id ? "处理中…" : project.status === "active" ? "归档" : "恢复"}
                </button>
              </div>
            </article>
          ))}
        </div>
      </section>
    </AppShell>
  );
}
