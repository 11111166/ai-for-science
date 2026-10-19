import { predictionCount, compoundDetails, finite } from "./compoundInsights";
import type { Compound, Job, Ranking } from "@drug-discovery/contracts";

export type DecisionGateStatus = "complete" | "attention" | "waiting";
export type DecisionAction = "advance" | "review" | "hold" | "stop";

export type DecisionGate = {
  id: string;
  label: string;
  status: DecisionGateStatus;
  statusLabel: string;
  evidence: string;
  action: string;
  view: "targets" | "perturbation" | "gene_perturbation" | "virtual_cell" | "compounds" | "ranking" | "report";
};

export type ProjectDecisionBrief = {
  readiness: number;
  headline: string;
  recommendation: string;
  blockers: string[];
  nextView: DecisionGate["view"];
  gates: DecisionGate[];
};

export type CandidateDecisionSupport = {
  action: DecisionAction;
  actionLabel: string;
  confidence: number;
  evidenceCompleteness: number;
  topBandFrequency: number;
  riskLevel: "low" | "medium" | "high";
  riskLabel: string;
  missing: string[];
  explanation: string;
};

const terminalJobs = (jobs: Job[], type: string) => jobs.find((job) => job.type === type);
const completedJob = (jobs: Job[], type: string) => terminalJobs(jobs, type)?.status === "completed";

export function buildProjectDecisionBrief(input: {
  jobs: Job[];
  targetCount: number;
  perturbationCount: number;
  knockoutCount: number;
  virtualCellCount: number;
  compoundCount: number;
  evaluatedCompoundCount: number;
  rankingCount: number;
  decidedCount: number;
}): ProjectDecisionBrief {
  const { jobs } = input;
  const gate = (
    id: string,
    label: string,
    complete: boolean,
    attempted: boolean,
    evidence: string,
    action: string,
    view: DecisionGate["view"],
  ): DecisionGate => ({
    id,
    label,
    status: complete ? "complete" : attempted ? "attention" : "waiting",
    statusLabel: complete ? "已具备" : attempted ? "需要处理" : "等待开始",
    evidence,
    action,
    view,
  });

  const gates: DecisionGate[] = [
    gate("target", "确认可推进靶点", completedJob(jobs, "target_discovery") && input.targetCount > 0, !!terminalJobs(jobs, "target_discovery"), `${input.targetCount} 个候选靶点`, "运行靶点发现并复核证据与成药路线", "targets"),
    gate("biology", "确认干预方向与基础风险", input.perturbationCount > 0 && input.knockoutCount > 0, completedJob(jobs, "perturbation") || completedJob(jobs, "gene_knockout"), `${input.perturbationCount} 条干预结果 · ${input.knockoutCount} 条敲除结果`, "补齐干预方向和广泛必需性检查", input.perturbationCount === 0 ? "perturbation" : "gene_perturbation"),
    gate("cell", "确认细胞情境响应", input.virtualCellCount > 0, completedJob(jobs, "virtual_cell"), `${input.virtualCellCount} 条通过质量门的细胞投影`, "运行虚拟细胞或检查未通过质量门的原因", "virtual_cell"),
    gate("hit", "获得可用化合物起点", input.compoundCount > 0, completedJob(jobs, "compound_matching"), `${input.compoundCount} 个有活性依据的化合物`, "运行化合物匹配或更换可成药靶点", "compounds"),
    gate("property", "完成早期性质排查", input.evaluatedCompoundCount > 0, completedJob(jobs, "admet"), `${input.evaluatedCompoundCount} 个已完成性质评价`, "补齐 ADMET 与结构风险检查", "compounds"),
    gate("decision", "形成可审计的候选决定", input.rankingCount > 0 && input.decidedCount > 0, completedJob(jobs, "ranking"), `${input.rankingCount} 个进入比较 · ${input.decidedCount} 个已人工决定`, "生成排序并记录入选、暂缓或排除理由", input.rankingCount > 0 ? "ranking" : "ranking"),
  ];
  const completeCount = gates.filter((item) => item.status === "complete").length;
  const readiness = Math.round(completeCount / gates.length * 100);
  const next = gates.find((item) => item.status !== "complete") ?? gates[gates.length - 1];
  const blockers = gates.filter((item) => item.status === "attention").map((item) => `${item.label}：${item.action}`).slice(0, 3);
  if (!blockers.length && next.status !== "complete") blockers.push(`${next.label}：${next.action}`);
  const allDone = completeCount === gates.length;
  const hasRankings = input.rankingCount > 0;
  return {
    readiness,
    headline: allDone ? "已形成候选推进决定" : hasRankings ? "候选已进入决策，等待完成审批判断" : `当前推进到：${gates[Math.max(0, completeCount - 1)]?.label ?? "项目初始化"}`,
    recommendation: allDone ? "可生成决策报告并进入线下实验、预算与项目评审流程。" : next.action,
    blockers,
    nextView: allDone ? "report" : next.view,
    gates,
  };
}

type Metric = "compound_match" | "efficacy" | "safety" | "developability";
const metricLabels: Record<Metric, string> = { compound_match: "靶点活性匹配", efficacy: "预计效果", safety: "安全性", developability: "可开发性" };
const sensitivityWeights: Record<Metric, number>[] = [
  { compound_match: .35, efficacy: .30, safety: .20, developability: .15 },
  { compound_match: .35, efficacy: .40, safety: .15, developability: .10 },
  { compound_match: .25, efficacy: .20, safety: .40, developability: .15 },
  { compound_match: .25, efficacy: .20, safety: .20, developability: .35 },
];

function valuesOf(ranking: Ranking): Record<Metric, number | undefined> {
  return {
    compound_match: ranking.compound?.match_score,
    efficacy: ranking.compound?.evaluation?.efficacy,
    safety: ranking.compound?.evaluation?.safety,
    developability: ranking.compound?.evaluation?.developability,
  };
}

function weightedAvailable(values: Record<Metric, number | undefined>, weights: Record<Metric, number>) {
  const available = (Object.keys(weights) as Metric[]).filter((key) => finite(values[key]));
  const totalWeight = available.reduce((sum, key) => sum + weights[key], 0);
  if (!totalWeight) return 0;
  return available.reduce((sum, key) => sum + Number(values[key]) * weights[key], 0) / totalWeight;
}

function compoundRisk(compound?: Compound): { level: "low" | "medium" | "high"; label: string } {
  if (!compound) return { level: "high", label: "缺少化合物记录" };
  const details = (compound.details ?? {}) as { withdrawn_flag?: boolean; black_box_warning?: boolean; descriptors?: { structural_alert_count?: number }; admet_predictions?: Record<string, { domain?: string }> };
  const admet = compound.evaluation?.admet as { predictions?: Record<string, { domain?: string }> } | undefined;
  const domains = Object.values(admet?.predictions ?? details.admet_predictions ?? {}).map((item) => item.domain);
  if (details.withdrawn_flag || details.black_box_warning || domains.includes("outside")) return { level: "high", label: details.withdrawn_flag || details.black_box_warning ? "存在监管或撤市提示" : "关键预测超出适用范围" };
  if ((details.descriptors?.structural_alert_count ?? 0) > 0 || domains.includes("borderline")) return { level: "medium", label: "存在需要复核的结构或适用范围提示" };
  return { level: "low", label: "当前未见高优先级风险信号" };
}

export function buildCandidateDecisionSupport(ranking: Ranking, allRankings: Ranking[]): CandidateDecisionSupport {
  const values = valuesOf(ranking);
  const missing = (Object.keys(values) as Metric[]).filter((key) => !finite(values[key])).map((key) => metricLabels[key]);
  const evidenceCompleteness = 1 - missing.length / 4;
  const propertyMissing = !ranking.compound || predictionCount(ranking.compound) < 5 || !finite(compoundDetails(ranking.compound).descriptors?.structural_alert_count);
  if (propertyMissing) missing.push("完整 ADMET / 结构警示");
  const bandSize = Math.max(1, Math.ceil(allRankings.length * .2));
  const frequencies = sensitivityWeights.map((weights) => {
    const sorted = [...allRankings].sort((a, b) => weightedAvailable(valuesOf(b), weights) - weightedAvailable(valuesOf(a), weights));
    return sorted.findIndex((item) => item.id === ranking.id) < bandSize ? 1 : 0;
  });
  const topBandFrequency = frequencies.reduce<number>((sum, value) => sum + value, 0) / frequencies.length;
  const risk = compoundRisk(ranking.compound);
  const confidence = Math.max(0, Math.min(1, .55 * evidenceCompleteness + .45 * topBandFrequency - (risk.level === "high" ? .25 : risk.level === "medium" ? .08 : 0)));
  let action: DecisionAction = "review";
  if (ranking.decision === "excluded") action = "stop";
  else if (ranking.decision === "selected") action = "advance";
  else if (risk.level === "high" || evidenceCompleteness < .75 || propertyMissing || ranking.compound?.demo) action = "hold";
  else if (ranking.total_score >= .65 && topBandFrequency >= .75) action = "advance";
  const actionLabel = { advance: "优先进入实验评审", review: "进入评审", hold: "暂缓并补证据", stop: "停止推进" }[action];
  const explanation = ranking.decision !== "pending"
    ? `已记录人工决定${propertyMissing ? "；性质检查仍不完整" : ""}。`
    : `${propertyMissing ? "性质检查不完整，暂不以已有评分确认安全性" : risk.label}；在 ${frequencies.reduce<number>((sum, value) => sum + value, 0)}/4 种业务权重下位于前 20%。`;
  return { action, actionLabel, confidence: Number(confidence.toFixed(3)), evidenceCompleteness, topBandFrequency, riskLevel: risk.level, riskLabel: risk.label, missing, explanation };
}
