import type { DataSourceSpec, Job, Project, TargetCandidate, TargetDiscoveryResult } from "@drug-discovery/contracts";
import { ArrowRight, CheckCircle2, CircleAlert, Database, Dna, MapPinned } from "lucide-react";
import { diseaseAreaLabel, getDiseaseProfile } from "@/lib/diseaseProfile";
import { readinessCopy } from "@/lib/diseaseCatalog";
import type { WorkspaceGuidePage } from "./WorkspaceGuidance";

const readinessLabel = {
  complete: "完整链路已验证",
  enhanced: "增强靶点分析",
  catalog: "目录与实时检索",
  custom: "等待疾病解析",
};

export function DiseasePortrait({
  project, jobs, targets, result, sources, go,
}: {
  project: Project;
  jobs: Job[];
  targets: TargetCandidate[];
  result: TargetDiscoveryResult | null;
  sources: DataSourceSpec[];
  go: (view: WorkspaceGuidePage) => void;
}) {
  const profile = getDiseaseProfile(project.disease, project.disease_id);
  const readiness = profile.disease?.readiness ?? "custom";
  const copy = readinessCopy(readiness);
  const completedJobs = jobs.filter((job) => job.status === "completed").length;
  const failedJobs = jobs.filter((job) => job.status === "failed").length;
  const sourceIds = readiness === "complete"
    ? ["open_targets", "gwas_catalog", "cellxgene", "hgnc", "hpa", "depmap", "chembl", "tdc_admet"]
    : readiness === "enhanced"
      ? ["open_targets", "hgnc", "hpa"]
      : ["open_targets"];
  const relevantSources = sources.filter((source) => sourceIds.includes(source.id));
  const retrievedTargets = result?.data_quality?.targets_retrieved;
  const availableTargets = typeof retrievedTargets === "number" ? retrievedTargets : targets.length;
  const resolvedId = project.disease_id ?? result?.disease_id;

  return <section className="disease-portrait">
    <header>
      <div className="disease-portrait-title">
        <span><MapPinned size={15} /> 疾病画像 v1</span>
        <h3>{profile.disease?.nameZh ?? project.disease}</h3>
        <p>{profile.disease?.nameEn && profile.disease.nameEn !== project.disease ? profile.disease.nameEn : result?.canonical_name ?? "等待标准疾病实体解析"}</p>
      </div>
      <div className="disease-portrait-tags"><span>{diseaseAreaLabel(profile)}</span><span>{readinessLabel[readiness]}</span>{resolvedId && <code>{resolvedId}</code>}</div>
    </header>
    <p className="disease-summary">{profile.summary}</p>

    <div className="portrait-status-grid">
      <article><small>当前能力范围</small><strong>{copy.title}</strong><span>{copy.description}</span></article>
      <article><small>疾病实体</small><strong>{resolvedId ? "已确认" : "待运行确认"}</strong><span>{resolvedId ? `统一使用 ${resolvedId}` : "先解析标准名称，避免结果写入错误疾病"}</span></article>
      <article><small>当前靶点结果</small><strong>{availableTargets || "尚未运行"}</strong><span>{availableTargets ? "个候选可继续查看证据与成药路线" : "运行靶点发现后自动回填"}</span></article>
      <article><small>任务状态</small><strong>{completedJobs} 已完成{failedJobs ? ` · ${failedJobs} 失败` : ""}</strong><span>失败记录会保留，不会覆盖已完成结果</span></article>
    </div>

    <div className="portrait-context-grid">
      <article><h4>重点看哪些组织与细胞</h4><div className="portrait-chip-list">{[...profile.tissues, ...profile.cellContexts].map((item) => <span key={item}>{item}</span>)}</div></article>
      <article><h4>优先梳理的生物学主题</h4><div className="portrait-chip-list themes">{profile.biologyThemes.map((item) => <span key={item}>{item}</span>)}</div></article>
      <article><h4>进入开发前要回答</h4><ul>{profile.developmentQuestions.map((item) => <li key={item}>{item}</li>)}</ul></article>
    </div>

    <div className="portrait-data-row">
      <div><Database size={17} /><div><strong>本疾病当前可调用的数据</strong><span>按能力层级展示；“已接入”只表示系统已固定版本或接口，不表示本疾病每项都有数据。</span></div></div>
      <div className="portrait-source-list">{relevantSources.map((source) => <span className={source.status !== "queued_integration" ? "connected" : "queued"} key={source.id}>{source.name_zh}{source.status !== "queued_integration" ? " · 已接入" : " · 待接入"}</span>)}</div>
    </div>

    <div className="portrait-next">
      <div>{failedJobs ? <CircleAlert /> : <CheckCircle2 />}<p><strong>{availableTargets ? "下一步：复核高优先级靶点的证据和成药路线" : "下一步：先运行已有证据靶点分析"}</strong><span>{availableTargets ? "优先查看路线依据、缺失证据和建议实验，再选择下游分析对象。" : "系统会先确认疾病实体和数据覆盖，不会用其他疾病结果替代。"}</span></p></div>
      <button className="secondary-button" onClick={() => go("targets")}><Dna size={15} /> {availableTargets ? "查看靶点" : "开始靶点分析"}<ArrowRight size={15} /></button>
    </div>
  </section>;
}
