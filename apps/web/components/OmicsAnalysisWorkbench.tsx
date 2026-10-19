"use client";

import type { Job } from "@drug-discovery/contracts";
import { Activity, Check, CircleAlert, Database, Download, ExternalLink, FileSpreadsheet, LoaderCircle, Microscope, Play, UploadCloud } from "lucide-react";
import { Component, FormEvent, useEffect, useMemo, useState } from "react";
import { api, apiBase } from "@/lib/api";

type Modality = "bulk_transcriptomics" | "single_cell" | "proteomics" | "metabolomics";
type Asset = { id: string; name: string; kind: string; size: number; content_hash: string; format?: string };
type OmicsExample = {
  modality: Modality;
  title: string;
  accession: string;
  design: string;
  contents: string;
  expected_signal: string;
  curation: string;
  source_name: string;
  source_url: string;
  article_url: string;
  recommended_scale: "counts" | "raw" | "log2";
  zero_is_missing: boolean;
  reference_label: string;
  comparison_label: string;
  size: number;
  sha256: string;
  scope_note: string;
};
type InstallExampleResponse = { asset: Asset; example: OmicsExample; reused: boolean };
type Preview = {
  asset_id: string;
  asset_name: string;
  modality: Modality;
  orientation: string;
  identifier_column?: string;
  sample_columns?: { name: string; suggested_group: "reference" | "comparison" | "ignore" }[];
  metadata_columns?: Record<string, string>;
  feature_count_detected?: number;
  group_values?: string[];
  detected_scale: "counts" | "raw" | "log2";
  preview_rows: number;
  warnings: string[];
};
type FeatureResult = { feature: string; effect: number; statistic: number; p_value: number; adjusted_p_value: number; reference_mean: number; comparison_mean: number; missing_fraction: number };
type AdvancedMethodRow = { feature: string; base_mean?: number; log_cpm?: number; log2_fold_change: number; statistic?: number; f_statistic?: number; p_value: number; adjusted_p_value: number };
type ConsensusRow = { feature: string; log2_fold_change_deseq2: number; adjusted_p_value_deseq2: number; log2_fold_change_edger: number; adjusted_p_value_edger: number; direction_agrees: boolean; significant_both: boolean };
type EnrichmentRow = { ID: string; Description: string; ONTOLOGY?: string; GeneRatio?: string; Count?: number; NES?: number; enrichmentScore?: number; p_adjust?: number; "p.adjust"?: number; direction?: string; core_enrichment?: string; source?: string };
type AdvancedTranscriptomicsResult = {
  status: string;
  input: { species: string; requested_gene_id_type: string; detected_gene_id_type: string; input_feature_count: number; filtered_feature_count: number; sample_count: number };
  deseq2: { method: string; version: string; significant_count: number; significant_up_count: number; significant_down_count: number; size_factors: Record<string, number>; rows: AdvancedMethodRow[] };
  edger: { method: string; version: string; significant_count: number; significant_up_count: number; significant_down_count: number; normalization_factors: Record<string, number>; rows: AdvancedMethodRow[] };
  consensus: { significant_count: number; rows: ConsensusRow[] };
  enrichment: { mapping: { mapped_count: number; tested_count: number; success_fraction: number; id_type: string; species: string }; kegg_source?: string; go: EnrichmentRow[]; kegg: EnrichmentRow[]; gsea_go: EnrichmentRow[]; gsea_kegg: EnrichmentRow[] };
  warnings: string[];
};
type AdvancedGeneEnrichment = AdvancedTranscriptomicsResult["enrichment"];
type AdvancedSingleCellResult = {
  status: string;
  input: { input_cell_count: number; retained_cell_count: number; input_gene_count: number; retained_gene_count: number; sample_count: number; species: string };
  qc: { min_detected_genes: number; max_mito_fraction: number; filtered_cell_count: number; min_cells_per_gene: number };
  normalisation: { method: string; hvg_method: string; hvg_count: number };
  embedding: { method: string; edge_count: number; pcs_used: number; neighbours: number; cluster_count: number; clusters: { cluster: number; cell_count: number; dominant_annotation: string; annotation_fraction: number }[]; cells: { cell_id: string; sample_id: string; group: string; supplied_cell_type: string; cluster: number; x: number; y: number; pc1: number; pc2: number; total_counts: number; detected_genes: number; mito_fraction: number }[] };
  markers: { method: string; rows: { cluster: number; feature: string; log2_fold_change: number; pct_in: number; pct_out: number; p_value: number; adjusted_p_value: number }[] };
  pseudobulk: { method: string; version: string; significant_count: number; significant_up_count: number; significant_down_count: number; rows: AdvancedMethodRow[] };
  enrichment: AdvancedGeneEnrichment;
  warnings: string[];
};
type AdvancedProteomicsResult = {
  status: string;
  input: { input_protein_count: number; modelled_protein_count: number; sample_count: number; scale: string; zero_is_missing: boolean; species: string };
  filtering: { min_values_per_group: number; excluded_from_limma: number; group_specific_missing_count: number };
  normalisation: { method: string; offsets: Record<string, number> };
  limma: { method: string; version: string; significant_count: number; significant_up_count: number; significant_down_count: number; rows: { feature: string; log2_fold_change: number; average_log2_abundance: number; statistic: number; p_value: number; adjusted_p_value: number; b_statistic: number }[] };
  missingness: { method: string; rows: { feature: string; reference_detected: number; comparison_detected: number; direction: string }[] };
  enrichment: AdvancedGeneEnrichment;
  warnings: string[];
};
type AdvancedMetabolomicsResult = {
  status: string;
  input: { input_feature_count: number; retained_feature_count: number; sample_count: number; scale: string; zero_is_missing: boolean };
  preprocessing: { max_missing_fraction: number; excluded_feature_count: number; imputed_value_count: number; imputation: string; normalisation: string; scaling: string; pqn_factors: Record<string, number> };
  pca: { explained_variance: number[]; points: { sample: string; group: "reference" | "comparison"; pc1: number; pc2: number }[] };
  plsda: { method: string; accuracy: number; balanced_accuracy: number; q2: number; permutation_count: number; permutation_p_value: number; points: { sample: string; group: "reference" | "comparison"; component1: number; component2: number; cv_prediction: number }[]; vip: { feature: string; vip: number; coefficient: number }[] };
  limma: { method: string; version: string; significant_count: number; significant_up_count: number; significant_down_count: number; rows: { feature: string; log2_fold_change: number; average_log2_abundance: number; statistic: number; p_value: number; adjusted_p_value: number; b_statistic: number }[] };
  enrichment: { mapping: { mapped_count: number; tested_count: number; success_fraction: number; id_type: string }; kegg_source: string; ora: EnrichmentRow[]; gsea: EnrichmentRow[] };
  warnings: string[];
};
type OmicsResult = {
  job_id: string;
  modality: Modality;
  model_version: string;
  generated_at: string;
  input: { asset_id: string; asset_name: string; sha256: string; scale: string };
  comparison: { reference_label: string; comparison_label: string; reference_samples: string[]; comparison_samples: string[] };
  summary: { feature_count: number; tested_feature_count: number; sample_count: number; cell_count?: number; significant_count: number; significant_up_count?: number; significant_down_count?: number; fdr_threshold: number; effect_threshold: number; cell_type_count?: number };
  sample_qc: Record<string, string | number>[];
  top_features: FeatureResult[];
  heatmap: { samples: string[]; rows: { feature: string; values: (number | null)[] }[] };
  sample_pca?: { features_used: number; explained_variance: number[]; points: { sample: string; group: "reference" | "comparison"; pc1: number; pc2: number }[] };
  sample_correlation?: { features_used: number; samples: string[]; matrix: number[][] };
  cell_types?: { name: string; cell_count: number }[];
  advanced_transcriptomics?: AdvancedTranscriptomicsResult;
  advanced_single_cell?: AdvancedSingleCellResult;
  advanced_proteomics?: AdvancedProteomicsResult;
  advanced_metabolomics?: AdvancedMetabolomicsResult;
  warnings: string[];
};

const modalities: { id: Modality; name: string; description: string; format: string }[] = [
  { id: "bulk_transcriptomics", name: "转录组", description: "基因表达质控、标准化与双组差异分析", format: "每行一个基因、每列一个样本" },
  { id: "single_cell", name: "单细胞", description: "细胞 QC、Leiden、标记基因与 pseudobulk", format: "每行一个细胞，含 sample_id 与 group" },
  { id: "proteomics", name: "蛋白组", description: "缺失过滤、limma 与功能富集", format: "每行一个蛋白、每列一个样本" },
  { id: "metabolomics", name: "代谢组", description: "PQN、PLS-DA 验证与代谢通路", format: "每行一个代谢物、每列一个样本" },
];

const modalityNames = Object.fromEntries(modalities.map((item) => [item.id, item.name])) as Record<Modality, string>;

type WorkflowProfile = {
  title: string;
  description: string;
  currentSteps: { title: string; detail: string }[];
  advancedSteps: string[];
  boundary: string;
  reference: { label: string; url: string };
  completePathLabel?: string;
};

const workflowProfiles: Record<Modality, WorkflowProfile> = {
  bulk_transcriptomics: {
    title: "转录组矩阵快速探索路径",
    description: "先看样本与数据分布，再做双组差异；探索性变换和正式统计分别处理。",
    currentSteps: [
      { title: "结构与分组核验", detail: "检查基因 × 样本、重复数、负值与数据尺度" },
      { title: "样本质量概览", detail: "总计数 / 总信号、检出基因数与缺失比例" },
      { title: "双轨标准化", detail: "DESeq2 RLE、edgeR TMM；探索图使用 log2 CPM" },
      { title: "样本结构", detail: "高变基因 PCA 与 Pearson 样本相关性" },
      { title: "正式差异模型", detail: "DESeq2 Wald + edgeR QL，报告一致差异基因" },
      { title: "功能解释与图表", detail: "GO、KEGG、GSEA，以及 PCA / MA / 火山图 / 热图" },
    ],
    advancedSteps: ["FASTQ 质控", "比对 / 伪比对", "转录本定量", "基因层汇总", "原始整数计数矩阵", "进入当前一键任务"],
    completePathLabel: "原始数据上游路径 · 需计算节点",
    boundary: "当前从原始整数计数矩阵开始，真实执行 DESeq2、edgeR、GO、KEGG 和 GSEA。FASTQ 比对/定量仍需计算节点；TPM、CPM 或 log2 值不会冒充计数模型输入。",
    reference: { label: "Bioconductor RNA-seq 工作流", url: "https://bioconductor.org/packages/release/workflows/vignettes/rnaseqGene/inst/doc/rnaseqGene.html" },
  },
  single_cell: {
    title: "单细胞完整矩阵分析路径",
    description: "同时完成细胞层探索和样本级统计推断，避免把单个细胞当成生物学重复。",
    currentSteps: [
      { title: "元数据核验", detail: "检查 cell_id、sample_id、group 与基因列" },
      { title: "细胞 QC 与归一化", detail: "计数、检出基因、线粒体比例、10k 归一化与 log1p" },
      { title: "HVG 与 PCA", detail: "高变基因筛选、缩放及主成分降维" },
      { title: "邻接图与 Leiden", detail: "kNN 图、Leiden 聚类及二维嵌入" },
      { title: "标记与注释核对", detail: "Wilcoxon 标记基因，并汇总上传的 cell_type 标签" },
      { title: "pseudobulk 与富集", detail: "sample_id 汇总、edgeR QL、GO / KEGG / GSEA" },
    ],
    advancedSteps: ["FASTQ / 10X", "Cell Ranger / 定量", "原始细胞 × 基因矩阵", "双细胞识别", "批次整合（如需要）", "进入当前一键任务"],
    completePathLabel: "原始数据上游路径 · 需计算节点",
    boundary: "当前从紧凑原始 UMI 矩阵开始，真实执行 QC、HVG、PCA、kNN、Leiden、标记基因和 pseudobulk 富集；FASTQ/Cell Ranger、双细胞模型和跨批次整合仍需计算节点。",
    reference: { label: "Scanpy 单细胞预处理与聚类", url: "https://scanpy.readthedocs.io/en/stable/tutorials/basics/clustering.html" },
  },
  proteomics: {
    title: "蛋白丰度矩阵完整分析路径",
    description: "把可建模缺失、组特异缺失和差异蛋白分开处理，再进行功能解释。",
    currentSteps: [
      { title: "丰度矩阵核验", detail: "检查蛋白 × 样本、重复数与数据尺度" },
      { title: "覆盖与缺失 QC", detail: "检出蛋白、总信号与样本缺失比例" },
      { title: "强度与样本归一化", detail: "原始丰度 log2、样本中位数中心化" },
      { title: "缺失机制分层", detail: "可建模蛋白与组特异完全缺失候选分开" },
      { title: "limma 差异模型", detail: "经验贝叶斯线性模型与 BH-FDR" },
      { title: "功能解释", detail: "蛋白 ID 映射、GO / KEGG ORA 与 GSEA" },
      { title: "结果汇总", detail: "PCA、相关性、MA、火山图、热图和 QC" },
    ],
    advancedSteps: ["原始质谱", "谱图搜索与 PSM FDR", "反库 / 污染物过滤", "肽段到蛋白汇总", "缺失机制与 VSN", "DEP / limma", "通路与蛋白复合体"],
    completePathLabel: "原始质谱上游路径 · 需计算节点",
    boundary: "当前从蛋白丰度矩阵开始，真实执行过滤、归一化、limma、组特异缺失报告和富集；谱图搜索、PSM FDR 及肽段到蛋白汇总需要 RAW 文件和计算节点。",
    reference: { label: "Bioconductor DEP 工作流", url: "https://bioconductor.org/packages/release/bioc/manuals/DEP/man/DEP.pdf" },
  },
  metabolomics: {
    title: "代谢峰表完整统计分析路径",
    description: "先完成缺失与尺度处理，再把无监督结构、监督模型验证和通路结果分开呈现。",
    currentSteps: [
      { title: "峰表与分组核验", detail: "检查代谢物 / 峰 × 样本、重复数与尺度" },
      { title: "信号质量概览", detail: "总峰强度、检出特征与缺失 / 零值分布" },
      { title: "缺失过滤与插补", detail: "40% 缺失门槛；有限缺失采用半最小值并报告数量" },
      { title: "PQN / log2 / Pareto", detail: "样本稀释校正、方差稳定与多变量缩放" },
      { title: "PCA 与 PLS-DA", detail: "无监督 PCA；分层交叉验证、Q² 与 100 次置换" },
      { title: "limma 与 VIP", detail: "经验贝叶斯单变量结果和 PLS 变量重要性" },
      { title: "代谢通路", detail: "KEGG compound 映射、ORA 与排名型通路分析" },
    ],
    advancedSteps: ["原始谱图", "峰提取与对齐", "空白 / QC / 漂移过滤", "缺失值处理", "PQN 与 Pareto / Auto scaling", "单变量 + 验证后 PLS-DA", "注释与通路分析"],
    completePathLabel: "原始仪器上游路径 · 需计算节点/额外元数据",
    boundary: "当前从带代谢物名称或 KEGG Compound ID 的峰表开始，真实执行 PQN、Pareto、经验证 PLS-DA、limma 与 KEGG 通路；无 blank/QC/批次列时不声称完成空白扣除或漂移校正。",
    reference: { label: "MetaboAnalyst 统计分析流程", url: "https://www.metaboanalyst.ca/resources/vignettes/Statistical_Analysis_Module.html" },
  },
};

function readableBytes(value: number) {
  return value < 1024 * 1024 ? `${Math.max(1, Math.round(value / 1024))} KB` : `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function formatNumber(value: number, digits = 3) {
  if (!Number.isFinite(value)) return "—";
  if (value !== 0 && Math.abs(value) < 0.001) return value.toExponential(2);
  return value.toFixed(digits);
}

function normaliseLegacyRRows<T>(value: T): T {
  if (Array.isArray(value)) {
    const rows: unknown[] = value.length && value.every((item) => Array.isArray(item) && item.length === 1 && item[0] !== null && typeof item[0] === "object" && !Array.isArray(item[0]))
      ? value.map((item) => item[0]) : value;
    return rows.map((item) => normaliseLegacyRRows(item)) as T;
  }
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, normaliseLegacyRRows(item)])) as T;
  }
  return value;
}

class OmicsResultBoundary extends Component<{ children: React.ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch(error: Error) { console.error("组学结果展示失败", error); }
  render() {
    if (this.state.failed) return <div className="notice error" role="alert">分析结果展示遇到格式问题；上传、选择数据和重新分析仍可使用。请联系管理员核对任务结果。</div>;
    return this.props.children;
  }
}

function shortLabel(value: string, limit = 15) {
  const primary = String(value ?? "").split("|")[0].trim();
  return primary.length > limit ? `${primary.slice(0, limit - 1)}…` : primary;
}

function divergingColor(value: number | null, limit = 2.5) {
  if (value == null) return "#e9efec";
  const strength = Math.min(1, Math.abs(value) / limit);
  const base = value >= 0 ? [197, 78, 68] : [37, 126, 151];
  return `rgb(${base.map((channel) => Math.round(248 + (channel - 248) * strength)).join(",")})`;
}

function PlotLegend() {
  return <div className="omics-plot-legend"><span><i className="down" />参考组较高</span><span><i className="neutral" />未达阈值</span><span><i className="up" />比较组较高</span></div>;
}

export function VolcanoPlot({ rows, fdr, effect }: { rows: FeatureResult[]; fdr: number; effect: number }) {
  const width = 760, height = 390, margin = { left: 58, right: 24, top: 32, bottom: 55 };
  const source = rows.slice(0, 100);
  const maxAbs = Math.max(effect * 1.25, 1, ...source.map((row) => Math.abs(row.effect))) * 1.08;
  const maxY = Math.max(-Math.log10(fdr) * 1.2, 1, ...source.map((row) => -Math.log10(Math.max(row.adjusted_p_value, 1e-12)))) * 1.08;
  const x = (value: number) => margin.left + ((value + maxAbs) / (maxAbs * 2)) * (width - margin.left - margin.right);
  const y = (value: number) => height - margin.bottom - (value / maxY) * (height - margin.top - margin.bottom);
  const points = source.map((row) => ({ ...row, yValue: -Math.log10(Math.max(row.adjusted_p_value, 1e-12)), significant: row.adjusted_p_value <= fdr && Math.abs(row.effect) >= effect }));
  const labels = points.filter((point) => point.significant).sort((a, b) => a.adjusted_p_value - b.adjusted_p_value || Math.abs(b.effect) - Math.abs(a.effect)).slice(0, 6);
  const xTicks = [-maxAbs, -maxAbs / 2, 0, maxAbs / 2, maxAbs];
  const yTicks = Array.from({ length: 5 }, (_, index) => maxY * index / 4);
  return <div className="omics-chart"><header><div><small>差异特征 · TOP 100</small><h3>火山图</h3></div><span>动态坐标，不再压扁显著性差异</span></header><svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="差异特征火山图">
    {yTicks.map((tick) => <g key={tick}><line x1={margin.left} y1={y(tick)} x2={width - margin.right} y2={y(tick)} className="grid-line" /><text x={margin.left - 9} y={y(tick) + 4} textAnchor="end">{formatNumber(tick, 1)}</text></g>)}
    {xTicks.map((tick) => <g key={tick}><line x1={x(tick)} y1={margin.top} x2={x(tick)} y2={height - margin.bottom} className={tick === 0 ? "zero" : "grid-line"} /><text x={x(tick)} y={height - margin.bottom + 19} textAnchor="middle">{formatNumber(tick, 1)}</text></g>)}
    <line x1={x(-effect)} y1={margin.top} x2={x(-effect)} y2={height - margin.bottom} className="threshold" /><line x1={x(effect)} y1={margin.top} x2={x(effect)} y2={height - margin.bottom} className="threshold" /><line x1={margin.left} y1={y(-Math.log10(fdr))} x2={width - margin.right} y2={y(-Math.log10(fdr))} className="threshold" />
    {points.map((point, index) => <circle key={`${point.feature}-${index}`} cx={x(point.effect)} cy={y(point.yValue)} r={point.significant ? 5 : 3.2} className={point.significant ? point.effect > 0 ? "up" : "down" : "neutral"}><title>{point.feature}：效应 {formatNumber(point.effect)}，FDR {formatNumber(point.adjusted_p_value)}</title></circle>)}
    {labels.map((point, index) => <g key={`label-${point.feature}-${index}`}><line x1={x(point.effect)} y1={y(point.yValue) - 5} x2={x(point.effect)} y2={y(point.yValue) - 13 - index % 2 * 7} className="label-line" /><text x={x(point.effect)} y={y(point.yValue) - 16 - index % 2 * 7} textAnchor="middle" className="point-label">{shortLabel(point.feature, 13)}</text></g>)}
    <text x={width / 2} y={height - 8} textAnchor="middle" className="axis-title">组间效应（比较组 − 参考组）</text><text x={15} y={height / 2} textAnchor="middle" transform={`rotate(-90 15 ${height / 2})`} className="axis-title">−log10(FDR)</text>
  </svg><PlotLegend /><p className="omics-chart-note">虚线为 |效应| ≥ {effect} 与 FDR ≤ {fdr}；标注最显著的 6 个特征。</p></div>;
}

function MaPlot({ rows, fdr, effect }: { rows: FeatureResult[]; fdr: number; effect: number }) {
  const width = 760, height = 390, margin = { left: 58, right: 24, top: 28, bottom: 55 };
  const source = rows.slice(0, 100).map((row) => ({ ...row, abundance: (row.reference_mean + row.comparison_mean) / 2, significant: row.adjusted_p_value <= fdr && Math.abs(row.effect) >= effect }));
  const minX = Math.min(...source.map((row) => row.abundance)), maxXRaw = Math.max(...source.map((row) => row.abundance));
  const xSpan = Math.max(1, maxXRaw - minX), maxX = maxXRaw + xSpan * .06, xMin = minX - xSpan * .06;
  const maxAbs = Math.max(effect * 1.25, 1, ...source.map((row) => Math.abs(row.effect))) * 1.08;
  const x = (value: number) => margin.left + ((value - xMin) / (maxX - xMin)) * (width - margin.left - margin.right);
  const y = (value: number) => margin.top + ((maxAbs - value) / (maxAbs * 2)) * (height - margin.top - margin.bottom);
  const labels = source.filter((point) => point.significant).sort((a, b) => a.adjusted_p_value - b.adjusted_p_value).slice(0, 5);
  return <div className="omics-chart"><header><div><small>表达强度依赖性</small><h3>MA 图</h3></div><span>检查效应是否受丰度驱动</span></header><svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="MA 图">
    {[-maxAbs, -maxAbs / 2, 0, maxAbs / 2, maxAbs].map((tick) => <g key={tick}><line x1={margin.left} y1={y(tick)} x2={width - margin.right} y2={y(tick)} className={tick === 0 ? "zero" : "grid-line"} /><text x={margin.left - 9} y={y(tick) + 4} textAnchor="end">{formatNumber(tick, 1)}</text></g>)}
    <line x1={margin.left} y1={y(effect)} x2={width - margin.right} y2={y(effect)} className="threshold" /><line x1={margin.left} y1={y(-effect)} x2={width - margin.right} y2={y(-effect)} className="threshold" />
    {source.map((point, index) => <circle key={`${point.feature}-${index}`} cx={x(point.abundance)} cy={y(point.effect)} r={point.significant ? 5 : 3.2} className={point.significant ? point.effect > 0 ? "up" : "down" : "neutral"}><title>{point.feature}：平均丰度 {formatNumber(point.abundance)}，效应 {formatNumber(point.effect)}</title></circle>)}
    {labels.map((point, index) => <text key={`${point.feature}-${index}`} x={x(point.abundance)} y={y(point.effect) - 9} textAnchor="middle" className="point-label">{shortLabel(point.feature, 12)}</text>)}
    <text x={width / 2} y={height - 8} textAnchor="middle" className="axis-title">平均标准化丰度</text><text x={15} y={height / 2} textAnchor="middle" transform={`rotate(-90 15 ${height / 2})`} className="axis-title">组间效应</text>
  </svg><PlotLegend /></div>;
}

function PcaPlot({ data, comparison }: { data?: OmicsResult["sample_pca"]; comparison: OmicsResult["comparison"] }) {
  if (!data?.points.length) return null;
  const width = 760, height = 390, margin = { left: 58, right: 30, top: 30, bottom: 55 };
  const maxX = Math.max(.5, ...data.points.map((point) => Math.abs(point.pc1))) * 1.25, maxY = Math.max(.5, ...data.points.map((point) => Math.abs(point.pc2))) * 1.25;
  const x = (value: number) => margin.left + ((value + maxX) / (maxX * 2)) * (width - margin.left - margin.right), y = (value: number) => margin.top + ((maxY - value) / (maxY * 2)) * (height - margin.top - margin.bottom);
  const pc1 = Math.round((data.explained_variance[0] ?? 0) * 100), pc2 = Math.round((data.explained_variance[1] ?? 0) * 100);
  return <div className="omics-chart"><header><div><small>样本结构 · {data.features_used} 个高变特征</small><h3>样本 PCA</h3></div><span>PC1 {pc1}% · PC2 {pc2}%</span></header><svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="样本 PCA">
    <line x1={margin.left} y1={y(0)} x2={width - margin.right} y2={y(0)} className="zero" /><line x1={x(0)} y1={margin.top} x2={x(0)} y2={height - margin.bottom} className="zero" />
    {data.points.map((point) => <g key={point.sample}><circle cx={x(point.pc1)} cy={y(point.pc2)} r="8" className={point.group === "comparison" ? "up" : "down"} /><text x={x(point.pc1) + 11} y={y(point.pc2) + 4} className="sample-label">{shortLabel(point.sample, 14)}</text><title>{point.sample}：PC1 {formatNumber(point.pc1, 2)}，PC2 {formatNumber(point.pc2, 2)}</title></g>)}
    <text x={width / 2} y={height - 8} textAnchor="middle" className="axis-title">PC1（{pc1}%）</text><text x={15} y={height / 2} textAnchor="middle" transform={`rotate(-90 15 ${height / 2})`} className="axis-title">PC2（{pc2}%）</text>
  </svg><div className="omics-plot-legend"><span><i className="down" />{comparison.reference_label}</span><span><i className="up" />{comparison.comparison_label}</span></div><p className="omics-chart-note">用于观察分组分离、离群样本与潜在批次效应。</p></div>;
}

function CorrelationHeatmap({ data }: { data?: OmicsResult["sample_correlation"] }) {
  if (!data?.samples.length) return null;
  const samples = data.samples.slice(0, 12), width = 720, height = 420, left = 104, top = 88, right = 24, bottom = 34, cell = Math.min((width - left - right) / samples.length, (height - top - bottom) / samples.length);
  return <div className="omics-chart"><header><div><small>样本一致性 · {data.features_used} 个高变特征</small><h3>样本相关性</h3></div><span>Pearson r · 越接近 1 越相似</span></header><svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="样本相关性热图">
    {samples.map((sample, index) => <g key={sample}><text x={left - 8} y={top + index * cell + cell * .67} textAnchor="end" className="heat-label">{shortLabel(sample, 12)}</text><text x={left + index * cell + cell * .55} y={top - 8} textAnchor="start" transform={`rotate(-42 ${left + index * cell + cell * .55} ${top - 8})`} className="heat-label">{shortLabel(sample, 12)}</text></g>)}
    {samples.flatMap((sample, row) => samples.map((_, column) => { const value = data.matrix[row]?.[column] ?? 0; return <rect key={`${sample}-${column}`} x={left + column * cell + 1} y={top + row * cell + 1} width={Math.max(1, cell - 2)} height={Math.max(1, cell - 2)} rx="3" fill={divergingColor(value, 1)}><title>{samples[row]} × {samples[column]}：r = {formatNumber(value, 2)}</title></rect>; }))}
  </svg><div className="omics-scale"><span>−1</span><i /><span>0</span><i /><span>+1</span></div>{data.samples.length > 12 && <p className="omics-chart-note">为保证可读性，仅显示前 12 个样本。</p>}</div>;
}

function Heatmap({ data, comparison }: { data: OmicsResult["heatmap"]; comparison: OmicsResult["comparison"] }) {
  const samples = data.samples.slice(0, 16), rows = data.rows.slice(0, 12);
  if (!rows.length) return null;
  const width = 980, left = 174, right = 22, top = 92, rowHeight = 30, height = top + rows.length * rowHeight + 54, cellWidth = (width - left - right) / samples.length;
  const reference = new Set(comparison.reference_samples);
  return <div className="omics-heatmap-card omics-wide-card"><header><div><small>标准化热图 · 行 Z-score</small><h3>效应量最大的特征</h3></div><span>{rows.length} 个特征 × {samples.length} 个样本</span></header><svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="标准化特征热图">
    {samples.map((sample, index) => <g key={sample}><rect x={left + index * cellWidth + 1} y={42} width={Math.max(1, cellWidth - 2)} height="8" rx="2" className={reference.has(sample) ? "group-reference" : "group-comparison"} /><text x={left + index * cellWidth + cellWidth / 2} y={68} textAnchor="start" transform={`rotate(-36 ${left + index * cellWidth + cellWidth / 2} 68)`} className="heat-label">{shortLabel(sample, 13)}</text></g>)}
    {rows.map((row, rowIndex) => <g key={row.feature}><text x={left - 10} y={top + rowIndex * rowHeight + rowHeight * .68} textAnchor="end" className="feature-label"><title>{row.feature}</title>{shortLabel(row.feature, 22)}</text>{row.values.slice(0, samples.length).map((value, column) => <rect key={`${row.feature}-${samples[column]}`} x={left + column * cellWidth + 1} y={top + rowIndex * rowHeight + 1} width={Math.max(1, cellWidth - 2)} height={rowHeight - 2} rx="4" fill={divergingColor(value)}><title>{row.feature} · {samples[column]}：{value == null ? "缺失" : formatNumber(value, 2)}</title></rect>)}</g>)}
  </svg><div className="omics-heatmap-footer"><div className="omics-plot-legend"><span><i className="down" />{comparison.reference_label}</span><span><i className="up" />{comparison.comparison_label}</span></div><div className="omics-scale"><span>低 −2.5</span><i /><span>0</span><i /><span>+2.5 高</span></div></div>{(data.samples.length > 16 || data.rows.length > 12) && <p className="omics-chart-note">为保证标签清晰，显示前 12 个特征与前 16 个样本；完整结果保留在结果表中。</p>}</div>;
}

function QcProfile({ rows }: { rows: OmicsResult["sample_qc"] }) {
  if (!rows.length) return null;
  const candidates = ["total_signal", "detected_features", "cell_count", "median_counts_per_cell", "median_genes_per_cell", "missing_fraction", "median_mito_fraction"];
  const metrics = candidates.filter((key) => rows.some((row) => typeof row[key] === "number")).slice(0, 3);
  const names: Record<string, string> = { total_signal: "总信号", detected_features: "检出特征", cell_count: "细胞数", median_counts_per_cell: "单细胞中位计数", median_genes_per_cell: "单细胞中位基因", missing_fraction: "缺失比例", median_mito_fraction: "线粒体比例" };
  return <div className="omics-heatmap-card omics-wide-card"><header><div><small>数据质量</small><h3>样本 QC 概览</h3></div><span>条形长度按每项最大值归一化</span></header><div className="omics-qc-profile"><div className="omics-qc-head"><span>样本</span>{metrics.map((metric) => <b key={metric}>{names[metric]}</b>)}</div>{rows.slice(0, 20).map((row, index) => <div className="omics-qc-row" key={`${row.sample}-${index}`}><strong title={String(row.sample)}>{shortLabel(String(row.sample), 18)}</strong>{metrics.map((metric) => { const value = Number(row[metric] ?? 0), maximum = Math.max(...rows.map((item) => Number(item[metric] ?? 0)), 1); return <span key={metric}><i style={{ width: `${Math.max(2, value / maximum * 100)}%` }} /><em>{metric.includes("fraction") ? `${(value * 100).toFixed(1)}%` : formatNumber(value, 1)}</em></span>; })}</div>)}</div></div>;
}

function MethodTable({ method, rows }: { method: "deseq2" | "edger"; rows: AdvancedMethodRow[] }) {
  return <div className="table-card omics-advanced-table"><table><thead><tr><th>基因</th><th>log2FC</th><th>{method === "deseq2" ? "Base mean" : "logCPM"}</th><th>{method === "deseq2" ? "Wald statistic" : "QL F"}</th><th>P 值</th><th>FDR</th></tr></thead><tbody>{rows.slice(0, 30).map((row) => <tr key={row.feature}><td><strong>{shortLabel(row.feature, 28)}</strong></td><td className={row.log2_fold_change > 0 ? "effect-up" : row.log2_fold_change < 0 ? "effect-down" : ""}>{row.log2_fold_change > 0 ? "+" : ""}{formatNumber(row.log2_fold_change)}</td><td>{formatNumber(method === "deseq2" ? row.base_mean ?? 0 : row.log_cpm ?? 0)}</td><td>{formatNumber(method === "deseq2" ? row.statistic ?? 0 : row.f_statistic ?? 0)}</td><td>{formatNumber(row.p_value)}</td><td>{formatNumber(row.adjusted_p_value)}</td></tr>)}</tbody></table></div>;
}

function ConsensusTable({ rows }: { rows: ConsensusRow[] }) {
  return <div className="table-card omics-advanced-table"><table><thead><tr><th>基因</th><th>DESeq2 log2FC</th><th>DESeq2 FDR</th><th>edgeR log2FC</th><th>edgeR FDR</th><th>判定</th></tr></thead><tbody>{rows.slice(0, 30).map((row) => <tr key={row.feature}><td><strong>{shortLabel(row.feature, 28)}</strong></td><td>{formatNumber(row.log2_fold_change_deseq2)}</td><td>{formatNumber(row.adjusted_p_value_deseq2)}</td><td>{formatNumber(row.log2_fold_change_edger)}</td><td>{formatNumber(row.adjusted_p_value_edger)}</td><td><span className={`omics-consensus-badge ${row.significant_both ? "confirmed" : row.direction_agrees ? "direction" : "discordant"}`}>{row.significant_both ? "双方法显著" : row.direction_agrees ? "方向一致" : "方向不一致"}</span></td></tr>)}</tbody></table></div>;
}

function EnrichmentList({ rows, emptyText }: { rows: EnrichmentRow[]; emptyText: string }) {
  if (!rows.length) return <div className="omics-enrichment-empty"><CircleAlert size={17} />{emptyText}</div>;
  const prepared = rows.map((row) => ({ ...row, adjusted: Number(row["p.adjust"] ?? row.p_adjust ?? 1) }));
  const maxScore = Math.max(1, ...prepared.map((row) => -Math.log10(Math.max(row.adjusted, 1e-12))));
  return <div className="omics-enrichment-list">{prepared.slice(0, 24).map((row, index) => { const score = -Math.log10(Math.max(row.adjusted, 1e-12)); return <article key={`${row.source ?? "path"}-${row.ID}-${row.direction ?? "all"}-${index}`}><div><small>{[row.source, row.ONTOLOGY, row.direction === "up" ? "上调" : row.direction === "down" ? "下调" : undefined].filter(Boolean).join(" · ")}</small><strong title={row.Description}>{row.Description}</strong><span>{row.NES == null ? `命中 ${row.Count ?? "—"} · FDR ${formatNumber(row.adjusted)}` : `NES ${formatNumber(row.NES)} · FDR ${formatNumber(row.adjusted)}`}</span></div><i><b style={{ width: `${Math.max(3, score / maxScore * 100)}%` }} /></i></article>; })}</div>;
}

function AdvancedTranscriptomics({ data }: { data: AdvancedTranscriptomicsResult }) {
  const [view, setView] = useState<"deseq2" | "edger" | "consensus" | "go" | "kegg" | "gsea">("deseq2");
  const gseaRows = [...data.enrichment.gsea_go.map((row) => ({ ...row, source: "GO GSEA" })), ...data.enrichment.gsea_kegg.map((row) => ({ ...row, source: "KEGG GSEA" }))].sort((a, b) => Number(a["p.adjust"] ?? 1) - Number(b["p.adjust"] ?? 1));
  const tabs = [["deseq2", "DESeq2"], ["edger", "edgeR"], ["consensus", "一致结果"], ["go", "GO"], ["kegg", "KEGG"], ["gsea", "GSEA"]] as const;
  return <div className="omics-advanced-card">
    <header><div><small>原始整数计数 · 正式模型</small><h3>DESeq2、edgeR 与功能富集</h3><p>两套负二项模型独立运行；通路结果由基因 ID 映射后计算，并保留空结果与告警。</p></div><span>DESeq2 {data.deseq2.version} · edgeR {data.edger.version}</span></header>
    <div className="omics-advanced-kpis"><div><small>DESeq2 显著</small><strong>{data.deseq2.significant_count}</strong><span>↑ {data.deseq2.significant_up_count} · ↓ {data.deseq2.significant_down_count}</span></div><div><small>edgeR 显著</small><strong>{data.edger.significant_count}</strong><span>↑ {data.edger.significant_up_count} · ↓ {data.edger.significant_down_count}</span></div><div><small>双方法一致显著</small><strong>{data.consensus.significant_count}</strong><span>方向和阈值同时一致</span></div><div><small>基因 ID 映射</small><strong>{Math.round(data.enrichment.mapping.success_fraction * 100)}%</strong><span>{data.enrichment.mapping.mapped_count} / {data.enrichment.mapping.tested_count} · {data.enrichment.mapping.id_type}</span></div></div>
    <nav className="omics-result-tabs" aria-label="高级转录组结果">{tabs.map(([id, label]) => <button key={id} className={view === id ? "active" : ""} onClick={() => setView(id)}>{label}</button>)}</nav>
    {view === "deseq2" && <><p className="omics-method-note">RLE / median-of-ratios 标准化，负二项 Wald 检验；输入必须是原始整数计数。</p><MethodTable method="deseq2" rows={data.deseq2.rows} /></>}
    {view === "edger" && <><p className="omics-method-note">TMM 标准化，负二项 quasi-likelihood GLM；与 DESeq2 独立估计离散度。</p><MethodTable method="edger" rows={data.edger.rows} /></>}
    {view === "consensus" && <><p className="omics-method-note">“双方法显著”要求方向一致，且 DESeq2 与 edgeR 均达到当前 FDR 和效应阈值。</p><ConsensusTable rows={data.consensus.rows} /></>}
    {view === "go" && <EnrichmentList rows={data.enrichment.go} emptyText="当前阈值下没有可报告的 GO 过度富集条目。" />}
    {view === "kegg" && <><p className="omics-method-note">通路定义来源：{data.enrichment.kegg_source === "versioned_KEGG_REST_cache" ? "版本化 KEGG REST 缓存" : "KEGG REST 在线查询"}；结果为双侧分别进行的过度富集分析。</p><EnrichmentList rows={data.enrichment.kegg} emptyText="当前阈值下没有可报告的 KEGG 过度富集条目，或 KEGG 数据源暂不可用。" /></>}
    {view === "gsea" && <><p className="omics-method-note">以 DESeq2 Wald statistic 排序全基因；GO 与 KEGG 分别进行 fgsea，并报告 NES 与 BH-FDR。</p><EnrichmentList rows={gseaRows} emptyText="当前排序基因列表没有可报告的 GO/KEGG GSEA 条目。" /></>}
    {!!data.warnings.length && <div className="omics-advanced-warnings">{data.warnings.map((warning) => <p key={warning}><CircleAlert size={14} />{warning}</p>)}</div>}
  </div>;
}

type LimmaRow = { feature: string; log2_fold_change: number; average_log2_abundance: number; statistic: number; p_value: number; adjusted_p_value: number; b_statistic: number };

function LimmaTable({ rows, label = "特征" }: { rows: LimmaRow[]; label?: string }) {
  return <div className="table-card omics-advanced-table"><table><thead><tr><th>{label}</th><th>log2FC</th><th>平均 log2 丰度</th><th>moderated t</th><th>P 值</th><th>FDR</th></tr></thead><tbody>{rows.slice(0, 40).map((row) => <tr key={row.feature}><td><strong title={row.feature}>{shortLabel(row.feature, 28)}</strong></td><td className={row.log2_fold_change > 0 ? "effect-up" : row.log2_fold_change < 0 ? "effect-down" : ""}>{row.log2_fold_change > 0 ? "+" : ""}{formatNumber(row.log2_fold_change)}</td><td>{formatNumber(row.average_log2_abundance)}</td><td>{formatNumber(row.statistic)}</td><td>{formatNumber(row.p_value)}</td><td>{formatNumber(row.adjusted_p_value)}</td></tr>)}</tbody></table></div>;
}

function ClusterEmbedding({ data }: { data: AdvancedSingleCellResult["embedding"] }) {
  const width = 920, height = 480, margin = 34;
  const points = data.cells;
  const xs = points.map((point) => point.x), ys = points.map((point) => point.y);
  const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
  const x = (value: number) => margin + ((value - minX) / Math.max(maxX - minX, 1e-9)) * (width - margin * 2);
  const y = (value: number) => height - margin - ((value - minY) / Math.max(maxY - minY, 1e-9)) * (height - margin * 2);
  const color = (cluster: number) => `hsl(${(cluster * 83 + 155) % 360} 48% 45%)`;
  return <div className="omics-embedding-chart"><header><div><small>{data.pcs_used} PCs · {data.neighbours}-NN · {data.edge_count} 条边</small><h3>UMAP 与 Leiden 细胞群</h3></div><span>{data.cluster_count} 个细胞群</span></header><svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="单细胞 UMAP 与 Leiden 聚类">{points.map((point) => <circle key={point.cell_id} cx={x(point.x)} cy={y(point.y)} r="4.3" fill={color(point.cluster)} opacity=".78"><title>{point.cell_id} · Cluster {point.cluster} · {point.sample_id} · {point.supplied_cell_type}</title></circle>)}</svg><div className="omics-cluster-list">{data.clusters.map((cluster) => <span key={cluster.cluster}><i style={{ background: color(cluster.cluster) }} /><b>Cluster {cluster.cluster}</b>{cluster.cell_count} cells · {cluster.dominant_annotation} {Math.round(cluster.annotation_fraction * 100)}%</span>)}</div><p className="omics-chart-note">UMAP 只用于可视化；聚类来自真实 PCA-kNN + Leiden。标签仅汇总上传文件已有 cell_type，不冒充参考注释。</p></div>;
}

function PseudobulkTable({ rows }: { rows: AdvancedMethodRow[] }) {
  return <div className="table-card omics-advanced-table"><table><thead><tr><th>基因</th><th>log2FC</th><th>logCPM</th><th>QL F</th><th>P 值</th><th>FDR</th></tr></thead><tbody>{rows.slice(0, 40).map((row) => <tr key={row.feature}><td><strong>{shortLabel(row.feature, 28)}</strong></td><td className={row.log2_fold_change > 0 ? "effect-up" : row.log2_fold_change < 0 ? "effect-down" : ""}>{formatNumber(row.log2_fold_change)}</td><td>{formatNumber(row.log_cpm ?? 0)}</td><td>{formatNumber(row.f_statistic ?? 0)}</td><td>{formatNumber(row.p_value)}</td><td>{formatNumber(row.adjusted_p_value)}</td></tr>)}</tbody></table></div>;
}

function AdvancedSingleCell({ data }: { data: AdvancedSingleCellResult }) {
  const [view, setView] = useState<"clusters" | "markers" | "pseudobulk" | "go" | "kegg" | "gsea">("clusters");
  const gsea = [...data.enrichment.gsea_go.map((row) => ({ ...row, source: "GO GSEA" })), ...data.enrichment.gsea_kegg.map((row) => ({ ...row, source: "KEGG GSEA" }))];
  const tabs = [["clusters", "QC / Leiden"], ["markers", "标记基因"], ["pseudobulk", "pseudobulk"], ["go", "GO"], ["kegg", "KEGG"], ["gsea", "GSEA"]] as const;
  return <div className="omics-advanced-card"><header><div><small>原始 UMI counts · 细胞层 + 样本层</small><h3>单细胞完整矩阵分析</h3><p>细胞层用于 QC、HVG、邻接图、Leiden 和标记；统计推断以 sample_id pseudobulk 为重复。</p></div><span>edgeR {data.pseudobulk.version}</span></header><div className="omics-advanced-kpis"><div><small>QC 保留细胞</small><strong>{data.input.retained_cell_count}</strong><span>过滤 {data.qc.filtered_cell_count} / 输入 {data.input.input_cell_count}</span></div><div><small>高变基因</small><strong>{data.normalisation.hvg_count}</strong><span>{data.input.retained_gene_count} 个 QC 后基因</span></div><div><small>Leiden 群</small><strong>{data.embedding.cluster_count}</strong><span>{data.embedding.neighbours}-NN 邻接图</span></div><div><small>pseudobulk 显著</small><strong>{data.pseudobulk.significant_count}</strong><span>↑ {data.pseudobulk.significant_up_count} · ↓ {data.pseudobulk.significant_down_count}</span></div></div><nav className="omics-result-tabs" aria-label="单细胞完整分析结果">{tabs.map(([id, label]) => <button key={id} className={view === id ? "active" : ""} onClick={() => setView(id)}>{label}</button>)}</nav>
    {view === "clusters" && <ClusterEmbedding data={data.embedding} />}
    {view === "markers" && <><p className="omics-method-note">{data.markers.method}</p><div className="table-card omics-advanced-table"><table><thead><tr><th>Cluster</th><th>标记基因</th><th>log2FC</th><th>群内检出</th><th>群外检出</th><th>FDR</th></tr></thead><tbody>{data.markers.rows.map((row, index) => <tr key={`${row.cluster}-${row.feature}-${index}`}><td>{row.cluster}</td><td><strong>{row.feature}</strong></td><td>{formatNumber(row.log2_fold_change)}</td><td>{Math.round(row.pct_in * 100)}%</td><td>{Math.round(row.pct_out * 100)}%</td><td>{formatNumber(row.adjusted_p_value)}</td></tr>)}</tbody></table></div></>}
    {view === "pseudobulk" && <><p className="omics-method-note">{data.pseudobulk.method}</p><PseudobulkTable rows={data.pseudobulk.rows} /></>}
    {view === "go" && <EnrichmentList rows={data.enrichment.go} emptyText="当前 pseudobulk 阈值下没有 GO 过度富集条目。" />}
    {view === "kegg" && <EnrichmentList rows={data.enrichment.kegg} emptyText="当前 pseudobulk 阈值下没有 KEGG 过度富集条目。" />}
    {view === "gsea" && <EnrichmentList rows={gsea} emptyText="当前 pseudobulk 排名没有可报告的 GO/KEGG GSEA 条目。" />}
    {!!data.warnings.length && <div className="omics-advanced-warnings">{data.warnings.map((warning) => <p key={warning}><CircleAlert size={14} />{warning}</p>)}</div>}
  </div>;
}

function AdvancedProteomics({ data }: { data: AdvancedProteomicsResult }) {
  const [view, setView] = useState<"limma" | "missing" | "go" | "kegg" | "gsea">("limma");
  const gsea = [...data.enrichment.gsea_go.map((row) => ({ ...row, source: "GO GSEA" })), ...data.enrichment.gsea_kegg.map((row) => ({ ...row, source: "KEGG GSEA" }))];
  const tabs = [["limma", "limma"], ["missing", "组特异缺失"], ["go", "GO"], ["kegg", "KEGG"], ["gsea", "GSEA"]] as const;
  return <div className="omics-advanced-card"><header><div><small>蛋白丰度矩阵 · 不制造插补显著性</small><h3>蛋白组差异与功能解释</h3><p>可建模蛋白进入 limma；某组完全缺失的蛋白作为独立候选报告。</p></div><span>limma {data.limma.version}</span></header><div className="omics-advanced-kpis"><div><small>进入模型</small><strong>{data.input.modelled_protein_count}</strong><span>输入 {data.input.input_protein_count}</span></div><div><small>limma 显著</small><strong>{data.limma.significant_count}</strong><span>↑ {data.limma.significant_up_count} · ↓ {data.limma.significant_down_count}</span></div><div><small>组特异缺失</small><strong>{data.filtering.group_specific_missing_count}</strong><span>单列候选，不计算伪 P 值</span></div><div><small>ID 映射</small><strong>{Math.round(data.enrichment.mapping.success_fraction * 100)}%</strong><span>{data.enrichment.mapping.mapped_count} / {data.enrichment.mapping.tested_count}</span></div></div><nav className="omics-result-tabs" aria-label="蛋白组完整分析结果">{tabs.map(([id, label]) => <button key={id} className={view === id ? "active" : ""} onClick={() => setView(id)}>{label}</button>)}</nav>
    {view === "limma" && <><p className="omics-method-note">{data.normalisation.method}；{data.limma.method}</p><LimmaTable rows={data.limma.rows} label="蛋白" /></>}
    {view === "missing" && <><p className="omics-method-note">{data.missingness.method}</p><div className="table-card omics-advanced-table"><table><thead><tr><th>蛋白</th><th>参考组检出</th><th>比较组检出</th><th>模式</th></tr></thead><tbody>{data.missingness.rows.map((row) => <tr key={row.feature}><td><strong>{shortLabel(row.feature, 30)}</strong></td><td>{row.reference_detected}</td><td>{row.comparison_detected}</td><td>{row.direction === "comparison_only" ? "仅比较组检出" : "仅参考组检出"}</td></tr>)}</tbody></table></div></>}
    {view === "go" && <EnrichmentList rows={data.enrichment.go} emptyText="当前阈值下没有 GO 富集条目。" />}
    {view === "kegg" && <EnrichmentList rows={data.enrichment.kegg} emptyText="当前阈值下没有 KEGG 富集条目。" />}
    {view === "gsea" && <EnrichmentList rows={gsea} emptyText="当前 limma 排名没有可报告的 GO/KEGG GSEA 条目。" />}
    {!!data.warnings.length && <div className="omics-advanced-warnings">{data.warnings.map((warning) => <p key={warning}><CircleAlert size={14} />{warning}</p>)}</div>}
  </div>;
}

function PlsDaPlot({ data }: { data: AdvancedMetabolomicsResult["plsda"] }) {
  const width = 860, height = 430, margin = 55, points = data.points;
  const maxX = Math.max(.1, ...points.map((point) => Math.abs(point.component1))) * 1.2, maxY = Math.max(.1, ...points.map((point) => Math.abs(point.component2))) * 1.2;
  const x = (value: number) => margin + ((value + maxX) / (maxX * 2)) * (width - margin * 2), y = (value: number) => height - margin - ((value + maxY) / (maxY * 2)) * (height - margin * 2);
  return <div className="omics-embedding-chart"><header><div><small>交叉验证 balanced accuracy {formatNumber(data.balanced_accuracy, 2)}</small><h3>PLS-DA 得分图</h3></div><span className={data.permutation_p_value <= .05 ? "effect-up" : "effect-down"}>置换 P = {formatNumber(data.permutation_p_value)}</span></header><svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="PLS-DA 得分图"><line x1={margin} y1={y(0)} x2={width - margin} y2={y(0)} className="zero" /><line x1={x(0)} y1={margin} x2={x(0)} y2={height - margin} className="zero" />{points.map((point) => <g key={point.sample}><circle cx={x(point.component1)} cy={y(point.component2)} r="7" className={point.group === "comparison" ? "up" : "down"} /><text x={x(point.component1) + 9} y={y(point.component2) + 4} className="sample-label">{shortLabel(point.sample, 13)}</text></g>)}</svg><p className="omics-chart-note">Accuracy {formatNumber(data.accuracy, 2)} · Q² {formatNumber(data.q2, 3)} · {data.permutation_count} 次标签置换；P &gt; 0.05 时不得声称模型具有经验证的判别能力。</p></div>;
}

function AdvancedMetabolomics({ data }: { data: AdvancedMetabolomicsResult }) {
  const [view, setView] = useState<"preprocess" | "plsda" | "limma" | "pathway" | "gsea">("preprocess");
  const tabs = [["preprocess", "预处理 / PCA"], ["plsda", "PLS-DA 验证"], ["limma", "limma / VIP"], ["pathway", "KEGG ORA"], ["gsea", "排名型通路"]] as const;
  return <div className="omics-advanced-card"><header><div><small>峰强度矩阵 · 监督模型必须验证</small><h3>代谢组统计与通路分析</h3><p>缺失处理、PQN/log2、Pareto、PCA、PLS-DA 验证、limma 和 KEGG compound 通路逐层报告。</p></div><span>limma {data.limma.version}</span></header><div className="omics-advanced-kpis"><div><small>保留代谢特征</small><strong>{data.input.retained_feature_count}</strong><span>排除 {data.preprocessing.excluded_feature_count}</span></div><div><small>插补数值</small><strong>{data.preprocessing.imputed_value_count}</strong><span>{data.preprocessing.imputation}</span></div><div><small>PLS-DA 置换 P</small><strong>{formatNumber(data.plsda.permutation_p_value)}</strong><span>Q² {formatNumber(data.plsda.q2)}</span></div><div><small>KEGG 映射</small><strong>{Math.round(data.enrichment.mapping.success_fraction * 100)}%</strong><span>{data.enrichment.mapping.mapped_count} / {data.enrichment.mapping.tested_count}</span></div></div><nav className="omics-result-tabs" aria-label="代谢组完整分析结果">{tabs.map(([id, label]) => <button key={id} className={view === id ? "active" : ""} onClick={() => setView(id)}>{label}</button>)}</nav>
    {view === "preprocess" && <div className="omics-detail-grid"><article><small>归一化</small><strong>{data.preprocessing.normalisation}</strong><span>缩放：{data.preprocessing.scaling}</span></article><article><small>缺失门槛</small><strong>≤ {Math.round(data.preprocessing.max_missing_fraction * 100)}%</strong><span>有限缺失才会插补</span></article><article><small>PCA 解释率</small><strong>PC1 {Math.round((data.pca.explained_variance[0] ?? 0) * 100)}%</strong><span>PC2 {Math.round((data.pca.explained_variance[1] ?? 0) * 100)}%</span></article></div>}
    {view === "plsda" && <PlsDaPlot data={data.plsda} />}
    {view === "limma" && <div className="omics-wide-stack"><div><p className="omics-method-note">{data.limma.method}</p><LimmaTable rows={data.limma.rows} label="代谢物" /></div><div className="table-card omics-advanced-table"><table><thead><tr><th>代谢物</th><th>VIP</th><th>PLS 系数</th></tr></thead><tbody>{data.plsda.vip.slice(0, 40).map((row) => <tr key={row.feature}><td><strong>{row.feature}</strong></td><td>{formatNumber(row.vip)}</td><td>{formatNumber(row.coefficient)}</td></tr>)}</tbody></table></div></div>}
    {view === "pathway" && <EnrichmentList rows={data.enrichment.ora} emptyText="当前 FDR/效应阈值下没有 KEGG 过度富集条目；仍可查看排名型通路。" />}
    {view === "gsea" && <EnrichmentList rows={data.enrichment.gsea.map((row) => ({ ...row, source: "KEGG compound GSEA" }))} emptyText="当前代谢物排名没有可报告的 KEGG 通路。" />}
    {!!data.warnings.length && <div className="omics-advanced-warnings">{data.warnings.map((warning) => <p key={warning}><CircleAlert size={14} />{warning}</p>)}</div>}
  </div>;
}

export function OmicsAnalysisWorkbench({ projectId, assets, jobs, busy, run, refresh }: {
  projectId: string;
  assets: Asset[];
  jobs: Job[];
  busy: string;
  run: (parameters: Record<string, unknown>) => Promise<void>;
  refresh: () => Promise<void>;
}) {
  const [modality, setModality] = useState<Modality>("bulk_transcriptomics");
  const [selectedAsset, setSelectedAsset] = useState("");
  const [uploadedAsset, setUploadedAsset] = useState<Asset | null>(null);
  const [fileName, setFileName] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [assignments, setAssignments] = useState<Record<string, "reference" | "comparison" | "ignore">>({});
  const [referenceGroup, setReferenceGroup] = useState("");
  const [comparisonGroup, setComparisonGroup] = useState("");
  const [referenceLabel, setReferenceLabel] = useState("参考组");
  const [comparisonLabel, setComparisonLabel] = useState("比较组");
  const [scale, setScale] = useState<"auto" | "counts" | "raw" | "log2">("auto");
  const [zeroMissing, setZeroMissing] = useState(true);
  const [species, setSpecies] = useState<"human" | "mouse">("human");
  const [geneIdType, setGeneIdType] = useState<"auto" | "symbol" | "ensembl" | "entrez" | "uniprot">("auto");
  const [metaboliteIdType, setMetaboliteIdType] = useState<"auto" | "name" | "kegg">("auto");
  const [localBusy, setLocalBusy] = useState("");
  const [error, setError] = useState("");
  const [result, setResult] = useState<OmicsResult | null>(null);
  const [examples, setExamples] = useState<OmicsExample[]>([]);
  const availableAssets = useMemo(() => {
    const combined = uploadedAsset && !assets.some((item) => item.id === uploadedAsset.id) ? [uploadedAsset, ...assets] : assets;
    return combined.filter((item) => /\.(csv|tsv|txt)$/i.test(item.name));
  }, [assets, uploadedAsset]);
  const latestJob = jobs.find((job) => job.type === "omics_analysis");
  const latestCompletedJobId = latestJob?.status === "completed" ? latestJob.id : "";

  useEffect(() => {
    let cancelled = false;
    api<OmicsExample[]>("/omics/examples").then((value) => { if (!cancelled) setExamples(value); }).catch(() => { /* Upload remains available if example metadata cannot be loaded. */ });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (!latestCompletedJobId) return;
    let cancelled = false;
    api<OmicsResult>(`/jobs/${latestCompletedJobId}/omics-result`).then((value) => { if (!cancelled) setResult(normaliseLegacyRRows(value)); }).catch((reason) => { if (!cancelled) setError(reason instanceof Error ? reason.message : "分析结果读取失败"); });
    return () => { cancelled = true; };
  }, [latestCompletedJobId]);

  function resetForModality(next: Modality) {
    setModality(next); setPreview(null); setAssignments({}); setReferenceGroup(""); setComparisonGroup(""); setResult(null); setError("");
    setZeroMissing(next === "proteomics" || next === "metabolomics");
  }

  async function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    setLocalBusy("upload"); setError("");
    try {
      const asset = await api<Asset>(`/projects/${projectId}/assets?kind=omics-${modality}`, { method: "POST", body: new FormData(form) });
      setUploadedAsset(asset); setSelectedAsset(asset.id); setFileName(""); form.reset(); await refresh();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "文件上传失败"); }
    finally { setLocalBusy(""); }
  }

  async function inspect(assetId = selectedAsset, example?: OmicsExample) {
    if (!assetId) return;
    setLocalBusy("preview"); setError(""); setPreview(null);
    try {
      const value = await api<Preview>(`/projects/${projectId}/omics/preview`, { method: "POST", body: JSON.stringify({ asset_id: assetId, modality }) });
      setPreview(value); setScale(example?.recommended_scale ?? "auto");
      if (example) {
        setZeroMissing(example.zero_is_missing);
        setReferenceLabel(example.reference_label);
        setComparisonLabel(example.comparison_label);
      }
      if (value.sample_columns) setAssignments(Object.fromEntries(value.sample_columns.map((item) => [item.name, item.suggested_group])));
      if (value.group_values?.length) {
        const reference = value.group_values.find((item) => /control|ctrl|normal|healthy|reference/i.test(item)) ?? value.group_values[0];
        const comparison = value.group_values.find((item) => item !== reference && /case|disease|tumou?r|treated|patient/i.test(item)) ?? value.group_values.find((item) => item !== reference) ?? "";
        setReferenceGroup(reference); setComparisonGroup(comparison);
      }
    } catch (reason) { setError(reason instanceof Error ? reason.message : "文件检查失败"); }
    finally { setLocalBusy(""); }
  }

  async function loadExample() {
    setLocalBusy("example"); setError(""); setResult(null);
    try {
      const value = await api<InstallExampleResponse>(`/projects/${projectId}/omics/examples/${modality}`, { method: "POST" });
      setUploadedAsset(value.asset); setSelectedAsset(value.asset.id);
      await refresh();
      await inspect(value.asset.id, value.example);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "案例数据载入失败"); }
    finally { setLocalBusy(""); }
  }

  async function analyse() {
    if (!preview) return;
    const parameters: Record<string, unknown> = { asset_id: preview.asset_id, modality, fdr_threshold: 0.05, effect_threshold: modality === "metabolomics" ? 0.585 : 1.0 };
    if (modality === "single_cell") Object.assign(parameters, { reference_group: referenceGroup, comparison_group: comparisonGroup });
    else Object.assign(parameters, {
      reference_samples: Object.entries(assignments).filter(([, value]) => value === "reference").map(([name]) => name),
      comparison_samples: Object.entries(assignments).filter(([, value]) => value === "comparison").map(([name]) => name),
      reference_label: referenceLabel || "参考组", comparison_label: comparisonLabel || "比较组", scale, zero_is_missing: zeroMissing,
    });
    const countInput = scale === "counts" || (scale === "auto" && preview.detected_scale === "counts");
    if (modality === "bulk_transcriptomics" && countInput) Object.assign(parameters, { advanced_transcriptomics: true, species, gene_id_type: geneIdType });
    if (modality === "single_cell" && preview.detected_scale === "counts") Object.assign(parameters, { advanced_multimodal: true, species, gene_id_type: geneIdType });
    if (modality === "proteomics") Object.assign(parameters, { advanced_multimodal: true, species, gene_id_type: geneIdType });
    if (modality === "metabolomics") Object.assign(parameters, { advanced_multimodal: true, metabolite_id_type: metaboliteIdType });
    setError(""); setResult(null);
    try { await run(parameters); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "分析任务提交失败"); }
  }

  const referenceCount = Object.values(assignments).filter((value) => value === "reference").length;
  const comparisonCount = Object.values(assignments).filter((value) => value === "comparison").length;
  const canRun = preview && (modality === "single_cell" ? referenceGroup && comparisonGroup && referenceGroup !== comparisonGroup : referenceCount >= 2 && comparisonCount >= 2);
  const currentModality = modalities.find((item) => item.id === modality)!;
  const workflow = workflowProfiles[modality];
  const currentExample = examples.find((item) => item.modality === modality);
  const running = latestJob && ["queued", "running"].includes(latestJob.status);

  return <>
    <div className="panel-header"><div><span>测序数据分析</span><h2>上传数据，一键完成组学分析</h2><p>转录组、单细胞、蛋白组和代谢组共享同一套可追溯任务与结果工作台。</p></div>{latestJob && <span className={`job-badge ${latestJob.status}`}>{running ? <LoaderCircle className="spin" size={14} /> : latestJob.status === "completed" ? <Check size={14} /> : <CircleAlert size={14} />}{latestJob.status === "completed" ? "已完成" : latestJob.status === "failed" ? "失败" : "分析中"} · {latestJob.progress}%</span>}</div>

    <div className="omics-modality-grid">{modalities.map((item) => <button key={item.id} className={modality === item.id ? "active" : ""} onClick={() => resetForModality(item.id)}><Microscope size={19} /><div><strong>{item.name}</strong><span>{item.description}</span><small>{item.format}</small></div></button>)}</div>

    {error && <div className="notice error" role="alert"><CircleAlert size={18} />{error}</div>}
    {latestJob?.status === "failed" && <div className="notice error" role="alert"><CircleAlert size={18} />{latestJob.error_message || "分析未完成，请核对文件格式与分组后重试。"}</div>}

    <div className="omics-setup-grid">
      <section className="omics-card"><header><span>01</span><div><small>准备输入</small><h3>上传或选择 {currentModality.name} 矩阵</h3></div></header>
        <form className="omics-upload" onSubmit={upload}><UploadCloud /><label className="secondary-button">{fileName ? `已选择：${fileName}` : "选择 CSV / TSV"}<input hidden required name="file" type="file" accept=".csv,.tsv,.txt,text/csv,text/tab-separated-values" onChange={(event) => setFileName(event.target.files?.[0]?.name || "")} /></label><button className="primary-button" disabled={!fileName || localBusy === "upload"}>{localBusy === "upload" ? "上传中…" : "上传到项目"}</button></form>
        {currentExample && <div className="omics-example"><Database size={18} /><div><small>真实案例 · {currentExample.accession} · {readableBytes(currentExample.size)}</small><strong>{currentExample.title}</strong><span>{currentExample.design}；{currentExample.contents}。</span><em>预期信号：{currentExample.expected_signal}。</em><p>{currentExample.scope_note}</p><a href={currentExample.source_url} target="_blank" rel="noreferrer">查看公开来源 <ExternalLink size={13} /></a></div><button className="secondary-button" disabled={!!localBusy} onClick={loadExample}>{localBusy === "example" ? <LoaderCircle className="spin" size={15} /> : <Database size={15} />} 载入案例并检查</button></div>}
        <div className="omics-template"><FileSpreadsheet size={16} /><div><strong>先按模板整理数据</strong><span>在线分析读取 UTF-8 CSV/TSV，文件 ≤25 MB；原始数据和大型矩阵请先在计算节点预处理。</span></div><a href={`${apiBase}/omics/templates/${modality}`}><Download size={14} /> 下载模板</a></div>
        <label className="field-label">项目文件<select value={selectedAsset} onChange={(event) => { setSelectedAsset(event.target.value); setPreview(null); }}><option value="">请选择已上传的 CSV/TSV</option>{availableAssets.map((asset) => <option key={asset.id} value={asset.id}>{asset.name} · {readableBytes(asset.size)}</option>)}</select></label>
        <button className="secondary-button omics-inspect" disabled={!selectedAsset || !!localBusy} onClick={() => void inspect()}>{localBusy === "preview" ? <LoaderCircle className="spin" size={15} /> : <FileSpreadsheet size={15} />} 读取并检查结构</button>
      </section>

      <section className="omics-card"><header><span>02</span><div><small>确认比较</small><h3>核对分组与数据尺度</h3></div></header>
        {!preview && <div className="omics-placeholder">选择文件并完成结构检查后，在这里确认比较组。</div>}
        {preview && <><div className="omics-preview-summary"><span><b>{preview.orientation === "cells_by_genes" ? "细胞 × 基因" : "特征 × 样本"}</b><small>识别方向</small></span><span><b>{preview.detected_scale === "counts" ? "计数" : preview.detected_scale === "raw" ? "原始强度" : "已 log2"}</b><small>自动判断尺度</small></span><span><b>{preview.sample_columns?.length ?? preview.feature_count_detected ?? 0}</b><small>{preview.sample_columns ? "样本列" : "基因列"}</small></span></div>
          {preview.warnings.map((warning) => <p className="omics-warning" key={warning}><CircleAlert size={14} />{warning}</p>)}
          {modality === "single_cell" ? <><div className="omics-group-pair"><label>参考组<select value={referenceGroup} onChange={(event) => setReferenceGroup(event.target.value)}>{preview.group_values?.map((group) => <option key={group}>{group}</option>)}</select></label><label>比较组<select value={comparisonGroup} onChange={(event) => setComparisonGroup(event.target.value)}>{preview.group_values?.map((group) => <option key={group}>{group}</option>)}</select></label><p>统计单位为 sample_id 伪汇总，不把单个细胞当作独立重复。</p></div><div className={`omics-advanced-config ${preview.detected_scale === "counts" ? "ready" : "unavailable"}`}><div><strong>完整单细胞矩阵分析</strong><span>{preview.detected_scale === "counts" ? "将运行细胞 QC、HVG、PCA、kNN、Leiden、二维嵌入、标记基因、pseudobulk edgeR 与富集" : "完整流程必须使用原始非负整数 UMI counts"}</span></div><label>物种<select value={species} onChange={(event) => setSpecies(event.target.value as typeof species)}><option value="human">人（Homo sapiens）</option><option value="mouse">小鼠（Mus musculus）</option></select></label><label>基因 ID<select value={geneIdType} onChange={(event) => setGeneIdType(event.target.value as typeof geneIdType)}><option value="auto">自动识别</option><option value="symbol">Gene Symbol</option><option value="ensembl">Ensembl gene ID</option><option value="entrez">Entrez gene ID</option></select></label></div></> : <><div className="omics-label-pair"><label>参考组名称<input value={referenceLabel} onChange={(event) => setReferenceLabel(event.target.value)} /></label><label>比较组名称<input value={comparisonLabel} onChange={(event) => setComparisonLabel(event.target.value)} /></label></div><div className="omics-sample-list">{preview.sample_columns?.map((sample) => <label key={sample.name}><span title={sample.name}>{sample.name}</span><select value={assignments[sample.name] ?? "ignore"} onChange={(event) => setAssignments((current) => ({ ...current, [sample.name]: event.target.value as typeof assignments[string] }))}><option value="reference">参考组</option><option value="comparison">比较组</option><option value="ignore">不参与</option></select></label>)}</div><details className="technical-details"><summary>数据尺度与缺失值</summary><div className="omics-options"><label>输入尺度<select value={scale} onChange={(event) => setScale(event.target.value as typeof scale)}><option value="auto">自动判断（当前：{preview.detected_scale}）</option><option value="counts">非负整数计数</option><option value="raw">原始丰度/峰强度</option><option value="log2">已经 log2</option></select></label><label className="check-label"><input type="checkbox" checked={zeroMissing} onChange={(event) => setZeroMissing(event.target.checked)} /> 将 0 视为缺失值</label></div></details>{modality === "bulk_transcriptomics" && <div className={`omics-advanced-config ${scale === "counts" || (scale === "auto" && preview.detected_scale === "counts") ? "ready" : "unavailable"}`}><div><strong>正式转录组分析</strong><span>{scale === "counts" || (scale === "auto" && preview.detected_scale === "counts") ? "本次将真实运行 DESeq2、edgeR、GO、KEGG 和 GSEA" : "当前输入不是原始整数计数，仅运行快速探索；正式模型不会套用到 TPM / CPM / log2 数据"}</span></div><label>物种<select value={species} onChange={(event) => setSpecies(event.target.value as typeof species)}><option value="human">人（Homo sapiens）</option><option value="mouse">小鼠（Mus musculus）</option></select></label><label>基因 ID<select value={geneIdType} onChange={(event) => setGeneIdType(event.target.value as typeof geneIdType)}><option value="auto">自动识别</option><option value="symbol">Gene Symbol</option><option value="ensembl">Ensembl gene ID</option><option value="entrez">Entrez gene ID</option></select></label></div>}{modality === "proteomics" && <div className="omics-advanced-config ready"><div><strong>完整蛋白组矩阵分析</strong><span>将真实运行覆盖过滤、log2/中位数归一化、limma、组特异缺失报告、GO、KEGG 与 GSEA</span></div><label>物种<select value={species} onChange={(event) => setSpecies(event.target.value as typeof species)}><option value="human">人（Homo sapiens）</option><option value="mouse">小鼠（Mus musculus）</option></select></label><label>蛋白 / 基因 ID<select value={geneIdType} onChange={(event) => setGeneIdType(event.target.value as typeof geneIdType)}><option value="auto">自动识别</option><option value="uniprot">UniProt accession</option><option value="symbol">Gene Symbol</option><option value="ensembl">Ensembl gene ID</option><option value="entrez">Entrez gene ID</option></select></label></div>}{modality === "metabolomics" && <div className="omics-advanced-config ready"><div><strong>完整代谢组峰表分析</strong><span>将运行缺失过滤、PQN/log2/Pareto、PCA、经验证 PLS-DA、limma/VIP 与 KEGG compound 通路</span></div><label>代谢物 ID<select value={metaboliteIdType} onChange={(event) => setMetaboliteIdType(event.target.value as typeof metaboliteIdType)}><option value="auto">自动识别</option><option value="name">代谢物英文名称</option><option value="kegg">KEGG Compound ID</option></select></label></div>}<p className="omics-group-count">当前：参考组 {referenceCount} 个，比较组 {comparisonCount} 个；每组至少 2 个。</p></>}
        </>}
      </section>
    </div>

    <section className="omics-pipeline-card">
      <header className="omics-pipeline-header"><div><Activity /><span><small>{currentModality.name} · 当前自动执行</small><h3>{workflow.title}</h3><p>{workflow.description}</p></span></div><button className="primary-button" disabled={!canRun || !!busy || !!running} onClick={analyse}>{running ? <LoaderCircle className="spin" size={16} /> : <Play size={16} />}{running ? `正在${latestJob.stage === "initializing" ? "读取数据" : "分析"}…` : "开始一键分析"}</button></header>
      <div className="omics-pipeline-flow">{workflow.currentSteps.map((step, index) => <div key={step.title}><b>{String(index + 1).padStart(2, "0")}</b><span><strong>{step.title}</strong><small>{step.detail}</small></span></div>)}</div>
      <div className="omics-pipeline-advanced"><div><small>{workflow.completePathLabel ?? "常用完整路径 · 建议进阶"}</small><div className="omics-full-path">{workflow.advancedSteps.map((step, index) => <span key={step}>{step}{index < workflow.advancedSteps.length - 1 && <i aria-hidden="true">→</i>}</span>)}</div></div><aside><strong>当前边界</strong><p>{workflow.boundary}</p><a href={workflow.reference.url} target="_blank" rel="noreferrer">方法参考：{workflow.reference.label} <ExternalLink size={12} /></a></aside></div>
    </section>

    {result && <OmicsResultBoundary key={result.job_id}><section className="omics-results"><div className="subsection-heading"><div><small>{modalityNames[result.modality]} · {result.model_version}</small><h3>分析结果</h3></div><span>{new Date(result.generated_at).toLocaleString("zh-CN")}</span></div>
      <div className="omics-kpis"><div><small>{result.summary.cell_count ? "细胞" : "样本"}</small><strong>{result.summary.cell_count ?? result.summary.sample_count}</strong><span>{result.summary.cell_count ? `${result.summary.sample_count} 个独立样本` : "进入比较"}</span></div><div><small>输入特征</small><strong>{result.summary.feature_count.toLocaleString()}</strong><span>{result.summary.tested_feature_count.toLocaleString()} 个可检验</span></div><div><small>达到当前阈值</small><strong>{result.summary.significant_count.toLocaleString()}</strong><span><b className="effect-up">↑ {result.summary.significant_up_count ?? "—"}</b> · <b className="effect-down">↓ {result.summary.significant_down_count ?? "—"}</b></span></div><div><small>输入追溯</small><strong>{result.input.sha256.slice(0, 10)}…</strong><span>{result.input.asset_name}</span></div></div>
      {(result.sample_pca || result.sample_correlation) && <div className="omics-analysis-section"><div className="omics-section-title"><span>01</span><div><small>样本层面</small><h4>先判断样本结构与一致性</h4></div></div><div className="omics-visual-grid"><PcaPlot data={result.sample_pca} comparison={result.comparison} /><CorrelationHeatmap data={result.sample_correlation} /></div></div>}
      <div className="omics-analysis-section"><div className="omics-section-title"><span>02</span><div><small>特征层面</small><h4>再查看显著性、效应量与丰度关系</h4></div></div><div className="omics-visual-grid"><VolcanoPlot rows={result.top_features} fdr={result.summary.fdr_threshold} effect={result.summary.effect_threshold} /><MaPlot rows={result.top_features} fdr={result.summary.fdr_threshold} effect={result.summary.effect_threshold} /></div></div>
      <div className="omics-analysis-section"><div className="omics-section-title"><span>03</span><div><small>模式与质量</small><h4>定位差异模式和异常样本</h4></div></div><div className="omics-wide-stack"><Heatmap data={result.heatmap} comparison={result.comparison} /><QcProfile rows={result.sample_qc} /></div></div>
      {result.advanced_transcriptomics && <div className="omics-analysis-section"><div className="omics-section-title"><span>04</span><div><small>正式转录组模型与功能解释</small><h4>核对双方法差异结果，并查看 GO / KEGG / GSEA</h4></div></div><AdvancedTranscriptomics data={result.advanced_transcriptomics} /></div>}
      {result.advanced_single_cell && <div className="omics-analysis-section"><div className="omics-section-title"><span>04</span><div><small>细胞层结构与样本级推断</small><h4>核对 Leiden、标记基因、pseudobulk 与功能富集</h4></div></div><AdvancedSingleCell data={result.advanced_single_cell} /></div>}
      {result.advanced_proteomics && <div className="omics-analysis-section"><div className="omics-section-title"><span>04</span><div><small>蛋白差异与缺失机制</small><h4>核对 limma、组特异缺失与功能富集</h4></div></div><AdvancedProteomics data={result.advanced_proteomics} /></div>}
      {result.advanced_metabolomics && <div className="omics-analysis-section"><div className="omics-section-title"><span>04</span><div><small>多变量验证与代谢通路</small><h4>核对 PCA、PLS-DA 验证、limma/VIP 与 KEGG 通路</h4></div></div><AdvancedMetabolomics data={result.advanced_metabolomics} /></div>}
      <div className="subsection-heading omics-quick-heading"><div><small>快速探索层</small><h3>Welch t + BH-FDR 特征表</h3></div><span>{result.advanced_transcriptomics ? "正式计数模型结果请以上方 DESeq2 / edgeR 为准" : result.advanced_single_cell ? "正式推断请以上方 sample_id pseudobulk edgeR 为准" : result.advanced_proteomics ? "正式差异结果请以上方 limma 为准" : result.advanced_metabolomics ? "正式差异与监督模型请以上方 limma / 经验证 PLS-DA 为准" : "用于快速核对方向，不替代模态专属模型"}</span></div><div className="table-card omics-feature-table"><table><thead><tr><th>特征</th><th>组间效应</th><th>{result.comparison.reference_label}均值</th><th>{result.comparison.comparison_label}均值</th><th>P 值</th><th>FDR</th><th>数据缺失</th></tr></thead><tbody>{result.top_features.slice(0, 50).map((item) => <tr key={item.feature}><td><strong>{item.feature}</strong></td><td><span className={item.effect > 0 ? "effect-up" : item.effect < 0 ? "effect-down" : ""}>{item.effect > 0 ? "+" : ""}{formatNumber(item.effect)}</span></td><td>{formatNumber(item.reference_mean)}</td><td>{formatNumber(item.comparison_mean)}</td><td>{formatNumber(item.p_value)}</td><td>{formatNumber(item.adjusted_p_value)}</td><td>{Math.round(item.missing_fraction * 100)}%</td></tr>)}</tbody></table></div>
      <details className="technical-details"><summary>样本质控与方法边界</summary><div className="omics-qc"><div className="table-card"><table><thead><tr>{Object.keys(result.sample_qc[0] ?? {}).map((key) => <th key={key}>{key}</th>)}</tr></thead><tbody>{result.sample_qc.map((item, index) => <tr key={`${item.sample}-${index}`}>{Object.values(item).map((value, column) => <td key={column}>{typeof value === "number" ? formatNumber(value, 2) : value}</td>)}</tr>)}</tbody></table></div>{result.warnings.map((warning) => <p key={warning}><CircleAlert size={14} />{warning}</p>)}</div></details>
    </section></OmicsResultBoundary>}
  </>;
}
