"use client";

import type { Project } from "@drug-discovery/contracts";
import { ArrowLeft, ArrowRight, CheckCircle2, CircleAlert, Dna, FlaskConical, Search } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useMemo, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { api } from "@/lib/api";
import { CUSTOM_DISEASE, defaultTrack, DISEASE_CATALOG_STATS, DISEASE_OPTIONS, readinessCopy, RESEARCH_TRACKS, THERAPEUTIC_AREA_LABELS, type DiseaseReadiness, type TherapeuticArea } from "@/lib/diseaseCatalog";

function objectiveFor(disease: string, track: string) {
  const goal = RESEARCH_TRACKS.find((item) => item.key === track)?.description ?? RESEARCH_TRACKS[1].description;
  return `围绕${disease}开展候选发现：${goal}`;
}

export default function NewProjectPage() {
  const router = useRouter();
  const [selectedKey, setSelectedKey] = useState("ra");
  const [diseaseQuery, setDiseaseQuery] = useState("");
  const [area, setArea] = useState<"all" | TherapeuticArea>("all");
  const [readinessFilter, setReadinessFilter] = useState<"all" | DiseaseReadiness>("all");
  const [customDisease, setCustomDisease] = useState("");
  const [track, setTrack] = useState("full");
  const [projectName, setProjectName] = useState("类风湿关节炎药物发现项目");
  const [objective, setObjective] = useState(objectiveFor("类风湿关节炎", "full"));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const selectedDisease = selectedKey === "custom" ? CUSTOM_DISEASE : DISEASE_OPTIONS.find((item) => item.key === selectedKey) ?? DISEASE_OPTIONS[0];
  const diseaseName = selectedKey === "custom" ? customDisease.trim() : selectedDisease.nameZh;
  const readiness = readinessCopy(selectedDisease.readiness);
  const recommendedTrack = defaultTrack(selectedDisease.readiness);
  const readinessItems = selectedDisease.availableModules;
  const filteredDiseases = useMemo(() => {
    const keyword = diseaseQuery.trim().toLocaleLowerCase("zh-CN");
    return DISEASE_OPTIONS.filter((item) => {
      if (area !== "all" && item.area !== area) return false;
      if (readinessFilter !== "all" && item.readiness !== readinessFilter) return false;
      return !keyword || [item.nameZh, item.nameEn, item.ontologyId].filter(Boolean).some((value) => value!.toLocaleLowerCase("zh-CN").includes(keyword));
    });
  }, [area, diseaseQuery, readinessFilter]);

  function chooseDisease(key: string) {
    const disease = key === "custom" ? CUSTOM_DISEASE : DISEASE_OPTIONS.find((item) => item.key === key) ?? DISEASE_OPTIONS[0];
    const nextTrack = defaultTrack(disease.readiness);
    setSelectedKey(key);
    setTrack(nextTrack);
    if (key !== "custom") {
      setProjectName(`${disease.nameZh}药物发现项目`);
      setObjective(objectiveFor(disease.nameZh, nextTrack));
    } else {
      setProjectName("");
      setObjective("");
    }
  }

  function chooseTrack(nextTrack: string) {
    setTrack(nextTrack);
    if (diseaseName) setObjective(objectiveFor(diseaseName, nextTrack));
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (diseaseName.length < 2) { setError("请输入至少 2 个字符的疾病名称或疾病本体 ID。"); return; }
    setSaving(true); setError("");
    try {
      const project = await api<Project>("/projects", {
        method: "POST",
        body: JSON.stringify({
          name: projectName.trim() || `${diseaseName}药物发现项目`,
          disease: diseaseName,
          disease_id: selectedDisease.ontologyId,
          objective,
          research_track: track,
          project_type: "research",
        }),
      });
      router.push(`/projects/${project.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "创建失败"); setSaving(false);
    }
  }

  return (
    <AppShell title="新建药物发现项目" subtitle="先确定疾病机会和项目目标；系统会保留统一的疾病身份、能力范围和后续决策记录。">
      <div className="form-layout project-create-layout">
        <form className="form-card project-create-form" onSubmit={submit}>
          <div className="form-step"><span>1</span><div><strong>从疾病目录中选择</strong><small>目录收录 {DISEASE_CATALOG_STATS.total} 种疾病，其中 {DISEASE_CATALOG_STATS.enhanced} 个增强项目、{DISEASE_CATALOG_STATS.complete} 个完整验证项目。</small></div></div>
          <div className="disease-catalog-tools">
            <label className="disease-catalog-search"><Search size={16} /><input value={diseaseQuery} onChange={(event) => setDiseaseQuery(event.target.value)} placeholder="搜索中文名、英文名或本体 ID" aria-label="搜索疾病目录" /></label>
            <select value={area} onChange={(event) => setArea(event.target.value as "all" | TherapeuticArea)} aria-label="按疾病领域筛选">
              <option value="all">全部疾病领域</option>
              {Object.entries(THERAPEUTIC_AREA_LABELS).map(([key, label]) => <option value={key} key={key}>{label}</option>)}
            </select>
            <select value={readinessFilter} onChange={(event) => setReadinessFilter(event.target.value as "all" | DiseaseReadiness)} aria-label="按可用能力筛选">
              <option value="all">全部可用能力</option>
              <option value="complete">完整验证（{DISEASE_CATALOG_STATS.complete}）</option>
              <option value="enhanced">增强分析（{DISEASE_CATALOG_STATS.enhanced}）</option>
              <option value="catalog">公共探索（{DISEASE_CATALOG_STATS.catalog}）</option>
            </select>
          </div>
          <div className="disease-focus-shortcuts" aria-label="常用疾病目录筛选">
            <button type="button" className={area === "all" ? "active" : ""} onClick={() => setArea("all")}>全部 {DISEASE_CATALOG_STATS.total}</button>
            <button type="button" className={area === "oncology" ? "active" : ""} onClick={() => setArea("oncology")}>肿瘤 {DISEASE_CATALOG_STATS.oncology}</button>
            <button type="button" className={area === "rare" ? "active" : ""} onClick={() => setArea("rare")}>罕见病 {DISEASE_CATALOG_STATS.rare}</button>
          </div>
          <div className="disease-catalog-resultbar"><span>找到 <b>{filteredDiseases.length}</b> 个疾病</span><small>点击疾病即可查看当前能运行到哪一步</small></div>
          <div className="disease-catalog-list" role="listbox" aria-label="疾病目录">
            {filteredDiseases.map((item) => <button type="button" role="option" aria-selected={selectedKey === item.key} className={selectedKey === item.key ? "active" : ""} key={item.key} onClick={() => chooseDisease(item.key)}>
              <span><strong>{item.nameZh}</strong><small>{item.nameEn}{item.ontologyId ? ` · ${item.ontologyId}` : ""}</small></span>
              <em className={`catalog-tier ${item.readiness}`}>{item.readiness === "complete" ? "完整验证" : item.readiness === "enhanced" ? "增强项目" : "公共探索"}</em>
            </button>)}
            {!filteredDiseases.length && <p>没有找到匹配疾病，可以使用下方“其他疾病”入口。</p>}
          </div>
          <button type="button" className={`custom-disease-button ${selectedKey === "custom" ? "active" : ""}`} onClick={() => chooseDisease("custom")}>目录里没有？输入其他疾病名称或标准本体 ID</button>
          {selectedKey === "custom" && <label>疾病名称或疾病本体 ID<input value={customDisease} onChange={(event) => { setCustomDisease(event.target.value); if (!projectName) setObjective(objectiveFor(event.target.value, track)); }} placeholder="例如：系统性红斑狼疮、Lupus 或 MONDO_0007915" required minLength={2} /></label>}

          <div className={`disease-readiness ${selectedDisease.readiness === "complete" ? "ready" : "limited"}`}>
            {selectedDisease.readiness === "complete" ? <CheckCircle2 size={20} /> : <CircleAlert size={20} />}
            <div><strong>{readiness.title}</strong><p>{readiness.description}</p><div>{readinessItems.map((item) => <span key={item}>{item}</span>)}</div></div>
          </div>

          <div className="form-step"><span>2</span><div><strong>选择本项目的重点</strong><small>这只决定项目首页的引导方式，后续仍可使用其他模块。</small></div></div>
          <div className="research-track-grid">
            {RESEARCH_TRACKS.map((item) => (
              <button type="button" key={item.key} className={track === item.key ? "active" : ""} onClick={() => chooseTrack(item.key)}>
                <strong>{item.label}{recommendedTrack === item.key && <em>建议</em>}</strong><small>{item.description}</small>
              </button>
            ))}
          </div>

          <div className="form-step"><span>3</span><div><strong>确认项目信息</strong><small>名称用于项目列表；项目目标会进入后续决策报告。</small></div></div>
          <label>项目名称<input value={projectName} onChange={(event) => setProjectName(event.target.value)} placeholder={diseaseName ? `${diseaseName}药物发现项目` : "疾病药物发现项目"} required minLength={2} /></label>
          <label>项目目标<textarea value={objective} onChange={(event) => setObjective(event.target.value)} rows={4} placeholder="用一句话说明希望筛选什么，以及结果准备怎样使用。" /></label>
          <div className="notice"><Dna size={18} />计算结果用于候选生成和实验优先级判断，不代表实验验证、临床疗效或治疗建议。</div>
          {error && <div className="notice error">{error}</div>}
          <div className="form-actions"><Link href="/projects" className="text-button"><ArrowLeft size={17} /> 返回项目</Link><button disabled={saving} className="primary-button">{saving ? "创建中…" : "创建并进入"} <ArrowRight size={17} /></button></div>
        </form>

        <aside className="form-aside project-create-aside">
          <div className="form-icon"><FlaskConical /></div>
          <h3>一个疾病，一个项目</h3>
          <p>这样更容易比较同一疾病下的靶点、细胞和化合物，也能避免不同疾病结果混在一起。</p>
          <ol><li><b>先确认疾病身份</b><span>中文名、英文名和标准 ID 会统一到同一疾病实体。</span></li><li><b>再看数据覆盖</b><span>有数据才运行，没有数据会明确说明缺什么。</span></li><li><b>最后逐步推进</b><span>结果可从靶点继续流向干预、细胞、化合物和排序。</span></li></ol>
        </aside>
      </div>
    </AppShell>
  );
}
