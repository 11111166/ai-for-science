"use client";

import { ArrowRight } from "lucide-react";

export type WorkspaceGuidePage = "overview" | "data" | "omics" | "targets" | "fungal_targets" | "bacterial_targets" | "perturbation" | "gene_perturbation" | "virtual_cell" | "protein_structure" | "compounds" | "process" | "ranking" | "methodology" | "report";

const nextSteps: Record<WorkspaceGuidePage, { page: WorkspaceGuidePage; label: string; hint: string }> = {
  overview: { page: "targets", label: "进入靶点发现", hint: "从疾病开始生成候选" },
  data: { page: "omics", label: "分析上传数据", hint: "对项目矩阵执行质控与组间比较" },
  omics: { page: "targets", label: "进入靶点发现", hint: "结合组学结果继续验证候选" },
  targets: { page: "perturbation", label: "评估干预方向", hint: "使用当前已选靶点" },
  fungal_targets: { page: "protein_structure", label: "复核蛋白结构", hint: "检查候选结构、口袋与证据缺口" },
  bacterial_targets: { page: "protein_structure", label: "复核蛋白结构", hint: "检查候选结构、口袋与证据缺口" },
  perturbation: { page: "gene_perturbation", label: "检查敲除风险", hint: "排查广泛必需性" },
  gene_perturbation: { page: "virtual_cell", label: "查看虚拟细胞", hint: "比较细胞类型中的预计恢复" },
  virtual_cell: { page: "protein_structure", label: "复核蛋白质结构", hint: "检查结构依据与验证缺口" },
  protein_structure: { page: "compounds", label: "寻找匹配化合物", hint: "带着结构证据进入化合物评价" },
  compounds: { page: "ranking", label: "生成候选排序", hint: "综合活性、效果与风险" },
  process: { page: "ranking", label: "进入候选决策", hint: "结合实验结果复核候选" },
  ranking: { page: "report", label: "生成决策报告", hint: "冻结当前结果与人工判断" },
  methodology: { page: "report", label: "查看决策报告", hint: "核对结果、风险与方法版本" },
  report: { page: "overview", label: "返回项目概览", hint: "查看全流程完成状态" },
};

export function NextStepBar({ page, onNavigate }: { page: WorkspaceGuidePage; onNavigate: (page: WorkspaceGuidePage) => void }) {
  const next = nextSteps[page];
  return <aside className="next-step-bar"><div><small>下一步建议</small><strong>{next.hint}</strong></div><button type="button" className="secondary-button" onClick={() => onNavigate(next.page)}>{next.label}<ArrowRight size={15} /></button></aside>;
}
