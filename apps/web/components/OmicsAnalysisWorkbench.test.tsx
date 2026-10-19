// @vitest-environment jsdom
import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import type { Job } from "@drug-discovery/contracts";
import { api } from "@/lib/api";
import { OmicsAnalysisWorkbench } from "./OmicsAnalysisWorkbench";

vi.mock("@/lib/api", () => ({
  api: vi.fn().mockResolvedValue([]),
  apiBase: "/api/v1/drug-discovery",
}));

const completedJob = { id: "job-1", type: "omics_analysis", status: "completed", progress: 100 } as Job;
const resultFixture = {
  job_id: "job-1", modality: "bulk_transcriptomics", model_version: "omics-analysis-v1.3", generated_at: "2026-09-20T00:00:00Z",
  input: { asset_id: "a", asset_name: "example.csv", sha256: "a".repeat(64), scale: "counts" },
  comparison: { reference_label: "Control", comparison_label: "Treated", reference_samples: ["control_1", "control_2"], comparison_samples: ["treated_1", "treated_2"] },
  summary: { feature_count: 100, tested_feature_count: 100, sample_count: 4, significant_count: 2, significant_up_count: 1, significant_down_count: 1, fdr_threshold: .05, effect_threshold: 1 },
  sample_qc: ["control_1", "control_2", "treated_1", "treated_2"].map((sample, index) => ({ sample, group: index < 2 ? "reference" : "comparison", total_signal: 1000 + index * 100, detected_features: 90 + index, missing_fraction: .01 * index })),
  top_features: [
    { feature: "GENE_UP|ENSG001", effect: 2.5, statistic: 5, p_value: .001, adjusted_p_value: .01, reference_mean: 2, comparison_mean: 4.5, missing_fraction: 0 },
    { feature: "GENE_DOWN|ENSG002", effect: -2, statistic: -4, p_value: .002, adjusted_p_value: .02, reference_mean: 4, comparison_mean: 2, missing_fraction: 0 },
  ],
  heatmap: { samples: ["control_1", "control_2", "treated_1", "treated_2"], rows: [{ feature: "GENE_UP|ENSG001", values: [-1, -.8, .8, 1] }, { feature: "GENE_DOWN|ENSG002", values: [1, .7, -.7, -1] }] },
  sample_pca: { features_used: 80, explained_variance: [.62, .2], points: [{ sample: "control_1", group: "reference", pc1: -2, pc2: .2 }, { sample: "control_2", group: "reference", pc1: -1.5, pc2: -.2 }, { sample: "treated_1", group: "comparison", pc1: 1.7, pc2: .3 }, { sample: "treated_2", group: "comparison", pc1: 2, pc2: -.3 }] },
  sample_correlation: { features_used: 80, samples: ["control_1", "control_2", "treated_1", "treated_2"], matrix: [[1, .95, .2, .1], [.95, 1, .15, .2], [.2, .15, 1, .94], [.1, .2, .94, 1]] },
  advanced_transcriptomics: {
    status: "completed",
    input: { species: "human", requested_gene_id_type: "auto", detected_gene_id_type: "ensembl", input_feature_count: 100, filtered_feature_count: 96, sample_count: 4 },
    deseq2: { method: "negative-binomial Wald", version: "1.52.0", significant_count: 12, significant_up_count: 8, significant_down_count: 4, size_factors: { control_1: 1 }, rows: [{ feature: "GENE_UP|ENSG001", base_mean: 12, log2_fold_change: 2.1, statistic: 5.2, p_value: .0001, adjusted_p_value: .002 }] },
    edger: { method: "quasi-likelihood GLM", version: "4.10.5", significant_count: 11, significant_up_count: 7, significant_down_count: 4, normalization_factors: { control_1: 1 }, rows: [{ feature: "GENE_UP|ENSG001", log_cpm: 5.2, log2_fold_change: 2, f_statistic: 18, p_value: .0002, adjusted_p_value: .003 }] },
    consensus: { significant_count: 10, rows: [{ feature: "GENE_UP|ENSG001", log2_fold_change_deseq2: 2.1, adjusted_p_value_deseq2: .002, log2_fold_change_edger: 2, adjusted_p_value_edger: .003, direction_agrees: true, significant_both: true }] },
    enrichment: {
      mapping: { mapped_count: 91, tested_count: 96, success_fraction: .9479, id_type: "ensembl", species: "human" },
      kegg_source: "versioned_KEGG_REST_cache",
      go: [{ ID: "GO:0006955", Description: "immune response", ONTOLOGY: "BP", Count: 6, "p.adjust": .01, direction: "up" }],
      kegg: [{ ID: "hsa04060", Description: "Cytokine-cytokine receptor interaction", Count: 5, "p.adjust": .02, direction: "up" }],
      gsea_go: [{ ID: "GO:0006955", Description: "immune response", NES: 2.1, "p.adjust": .01 }],
      gsea_kegg: [{ ID: "hsa04060", Description: "Cytokine-cytokine receptor interaction", NES: 1.8, "p.adjust": .03 }],
    },
    warnings: [],
  },
  warnings: ["探索性结果"],
};

it("keeps four omics workflows inside the existing card interaction", async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.mocked(api).mockResolvedValueOnce([{
    modality: "bulk_transcriptomics", title: "地塞米松处理气道平滑肌", accession: "GSE52778",
    design: "4 个细胞系配对样本", contents: "原始 read counts", expected_signal: "糖皮质激素应答",
    curation: "紧凑子集", source_name: "NCBI GEO", source_url: "https://example.test/source",
    article_url: "https://example.test/article", recommended_scale: "counts", zero_is_missing: false,
    reference_label: "未处理", comparison_label: "地塞米松", size: 96000, sha256: "a".repeat(64),
    scope_note: "教学用真实数据子集。",
  }] as never);
  const host = document.createElement("div"); document.body.append(host); const root = createRoot(host);
  try {
    await act(async () => { root.render(<OmicsAnalysisWorkbench projectId="p" assets={[]} jobs={[]} busy="" run={vi.fn()} refresh={vi.fn()} />); await Promise.resolve(); });
    expect(host.textContent).toContain("上传数据，一键完成组学分析");
    expect(host.textContent).toContain("转录组");
    expect(host.textContent).toContain("单细胞");
    expect(host.textContent).toContain("蛋白组");
    expect(host.textContent).toContain("代谢组");
    expect(host.textContent).toContain("真实案例 · GSE52778");
    expect(host.textContent).toContain("教学用真实数据子集");
    expect(host.textContent).toContain("原始数据和大型矩阵请先在计算节点预处理");
    expect(host.textContent).toContain("转录组矩阵快速探索路径");
    expect(host.textContent).toContain("原始数据上游路径 · 需计算节点");
    expect(host.textContent).toContain("DESeq2 Wald + edgeR QL");
    const proteomics = [...host.querySelectorAll("button")].find((button) => button.textContent?.includes("蛋白组"));
    await act(async () => proteomics?.click());
    expect(host.textContent).toContain("上传或选择 蛋白组 矩阵");
    expect(host.textContent).toContain("蛋白丰度矩阵完整分析路径");
    expect(host.textContent).toContain("谱图搜索与 PSM FDR");
    expect(host.textContent).toContain("DEP / limma");
    const singleCell = [...host.querySelectorAll("button")].find((button) => button.textContent?.includes("单细胞"));
    await act(async () => singleCell?.click());
    expect(host.textContent).toContain("单细胞完整矩阵分析路径");
    expect(host.textContent).toContain("pseudobulk 与富集");
    const metabolomics = [...host.querySelectorAll("button")].find((button) => button.textContent?.includes("代谢组"));
    await act(async () => metabolomics?.click());
    expect(host.textContent).toContain("代谢峰表完整统计分析路径");
    expect(host.textContent).toContain("空白 / QC / 漂移过滤");
    expect(host.querySelector("button.primary-button")?.getAttribute("disabled")).not.toBeNull();
  } finally { await act(async () => root.unmount()); host.remove(); }
});

it("renders modality-specific complete result workflows", async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  const enrichment = { mapping: { mapped_count: 8, tested_count: 10, success_fraction: .8, id_type: "symbol", species: "human" }, go: [], kegg: [], gsea_go: [], gsea_kegg: [] };
  const multimodalResult = {
    ...resultFixture,
    modality: "single_cell",
    advanced_transcriptomics: undefined,
    advanced_single_cell: {
      status: "completed", input: { input_cell_count: 10, retained_cell_count: 9, input_gene_count: 20, retained_gene_count: 18, sample_count: 4, species: "human" },
      qc: { min_detected_genes: 10, max_mito_fraction: .2, filtered_cell_count: 1, min_cells_per_gene: 3 },
      normalisation: { method: "10k + log1p", hvg_method: "variance", hvg_count: 15 },
      embedding: { method: "PCA + kNN + Leiden + UMAP", edge_count: 12, pcs_used: 5, neighbours: 4, cluster_count: 2, clusters: [{ cluster: 1, cell_count: 5, dominant_annotation: "T cell", annotation_fraction: .8 }], cells: [{ cell_id: "c1", sample_id: "s1", group: "control", supplied_cell_type: "T cell", cluster: 1, x: -1, y: .5, pc1: -2, pc2: .4, total_counts: 100, detected_genes: 18, mito_fraction: .03 }] },
      markers: { method: "Wilcoxon cluster-vs-rest", rows: [] }, pseudobulk: { method: "edgeR QL pseudobulk", version: "4.4.0", significant_count: 3, significant_up_count: 2, significant_down_count: 1, rows: [] }, enrichment, warnings: [],
    },
    advanced_proteomics: {
      status: "completed", input: { input_protein_count: 100, modelled_protein_count: 80, sample_count: 8, scale: "raw", zero_is_missing: true, species: "human" }, filtering: { min_values_per_group: 2, excluded_from_limma: 20, group_specific_missing_count: 4 }, normalisation: { method: "log2 + median centering", offsets: {} }, limma: { method: "limma empirical Bayes", version: "3.62.1", significant_count: 5, significant_up_count: 3, significant_down_count: 2, rows: [] }, missingness: { method: "group-specific complete missingness", rows: [] }, enrichment, warnings: [],
    },
    advanced_metabolomics: {
      status: "completed", input: { input_feature_count: 63, retained_feature_count: 60, sample_count: 10, scale: "raw", zero_is_missing: true }, preprocessing: { max_missing_fraction: .4, excluded_feature_count: 3, imputed_value_count: 4, imputation: "half minimum", normalisation: "PQN + log2", scaling: "Pareto", pqn_factors: {} }, pca: { explained_variance: [.4, .2], points: [] }, plsda: { method: "2-component PLS-DA", accuracy: .7, balanced_accuracy: .7, q2: .05, permutation_count: 100, permutation_p_value: .12, points: [{ sample: "s1", group: "reference", component1: -1, component2: .2, cv_prediction: 0 }], vip: [] }, limma: { method: "limma empirical Bayes", version: "3.62.1", significant_count: 0, significant_up_count: 0, significant_down_count: 0, rows: [] }, enrichment: { mapping: { mapped_count: 50, tested_count: 60, success_fraction: .83, id_type: "KEGG compound" }, kegg_source: "versioned_KEGG_REST_cache", ora: [], gsea: [] }, warnings: [],
    },
  };
  vi.mocked(api).mockImplementation(async (path: string) => path.includes("omics-result") ? multimodalResult as never : [] as never);
  const host = document.createElement("div"); document.body.append(host); const root = createRoot(host);
  try {
    await act(async () => { root.render(<OmicsAnalysisWorkbench projectId="p" assets={[]} jobs={[completedJob]} busy="" run={vi.fn()} refresh={vi.fn()} />); await Promise.resolve(); await Promise.resolve(); });
    expect(host.textContent).toContain("单细胞完整矩阵分析");
    expect(host.textContent).toContain("蛋白组差异与功能解释");
    expect(host.textContent).toContain("代谢组统计与通路分析");
    expect(host.textContent).toContain("sample_id pseudobulk edgeR");
    expect(host.textContent).toContain("组特异缺失");
    expect(host.textContent).toContain("PLS-DA 验证");
    const plsTab = [...host.querySelectorAll("button")].find((button) => button.textContent === "PLS-DA 验证");
    await act(async () => plsTab?.click());
    expect(host.textContent).toContain("不得声称模型具有经验证的判别能力");
  } finally { await act(async () => root.unmount()); host.remove(); }
});

it("renders readable expanded analysis panels for completed jobs", async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.mocked(api).mockImplementation(async (path: string) => path.includes("omics-result") ? resultFixture as never : [] as never);
  const host = document.createElement("div"); document.body.append(host); const root = createRoot(host);
  try {
    await act(async () => { root.render(<OmicsAnalysisWorkbench projectId="p" assets={[]} jobs={[completedJob]} busy="" run={vi.fn()} refresh={vi.fn()} />); await Promise.resolve(); await Promise.resolve(); });
    expect(host.textContent).toContain("样本 PCA");
    expect(host.textContent).toContain("样本相关性");
    expect(host.textContent).toContain("火山图");
    expect(host.textContent).toContain("MA 图");
    expect(host.textContent).toContain("样本 QC 概览");
    expect(host.textContent).toContain("DESeq2、edgeR 与功能富集");
    expect(host.textContent).toContain("双方法一致显著");
    expect(host.textContent).toContain("基因 ID 映射");
    expect(host.textContent).toContain("Welch t + BH-FDR 特征表");
    expect(host.querySelectorAll('svg[role="img"]').length).toBeGreaterThanOrEqual(5);
    expect(host.textContent).toContain("虚线为 |效应| ≥ 1 与 FDR ≤ 0.05");
    const goTab = [...host.querySelectorAll("button")].find((button) => button.textContent === "GO");
    await act(async () => goTab?.click());
    expect(host.textContent).toContain("immune response");
    const keggTab = [...host.querySelectorAll("button")].find((button) => button.textContent === "KEGG");
    await act(async () => keggTab?.click());
    expect(host.textContent).toContain("版本化 KEGG REST 缓存");
    expect(host.textContent).toContain("Cytokine-cytokine receptor interaction");
    const gseaTab = [...host.querySelectorAll("button")].find((button) => button.textContent === "GSEA");
    await act(async () => gseaTab?.click());
    expect(host.textContent).toContain("GO GSEA");
  } finally { await act(async () => root.unmount()); host.remove(); }
});

it("opens previously saved R results with singleton-array row wrappers", async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  const legacy = structuredClone(resultFixture);
  legacy.advanced_transcriptomics.deseq2.rows = legacy.advanced_transcriptomics.deseq2.rows.map((row) => [row]) as never;
  legacy.advanced_transcriptomics.edger.rows = legacy.advanced_transcriptomics.edger.rows.map((row) => [row]) as never;
  legacy.advanced_transcriptomics.consensus.rows = legacy.advanced_transcriptomics.consensus.rows.map((row) => [row]) as never;
  legacy.advanced_transcriptomics.enrichment.go = legacy.advanced_transcriptomics.enrichment.go.map((row) => [row]) as never;
  vi.mocked(api).mockImplementation(async (path: string) => path.includes("omics-result") ? legacy as never : [] as never);
  const host = document.createElement("div"); document.body.append(host); const root = createRoot(host);
  try {
    await act(async () => { root.render(<OmicsAnalysisWorkbench projectId="p" assets={[]} jobs={[completedJob]} busy="" run={vi.fn()} refresh={vi.fn()} />); await Promise.resolve(); await Promise.resolve(); });
    expect(host.textContent).toContain("GENE_UP");
    expect(host.textContent).toContain("DESeq2、edgeR 与功能富集");
    const goTab = [...host.querySelectorAll("button")].find((button) => button.textContent === "GO");
    await act(async () => goTab?.click());
    expect(host.textContent).toContain("immune response");
    expect(host.textContent).not.toContain("分析结果展示遇到格式问题");
  } finally { await act(async () => root.unmount()); host.remove(); }
});
