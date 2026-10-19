// @vitest-environment jsdom
import React from "react";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { FungalTargetWorkbench } from "./FungalTargetWorkbench";
import { api } from "@/lib/api";

vi.mock("@/lib/api", () => ({ api: vi.fn(), apiBase: "http://localhost/api" }));

const completedJob = {
  id: "job-1", project_id: "project-1", type: "fungal_target", status: "completed" as const,
  stage: "completed", progress: 100, adapter_mode: "real" as const,
  parameters: { workflow: "prioritization" }, created_at: "2026-09-24T00:00:00Z",
};

const result = {
  job_id: "job-1", workflow: "prioritization", organism: "Candida albicans", compound: null,
  input: { asset_name: "evidence.csv", row_count: 3, sha256: "a".repeat(64) },
  thresholds: { evidence: .5, human_similarity: .35, top_n: 20 },
  weights: { essentiality: .22, virulence: .16, druggability: .18 }, human_similarity_penalty: .2,
  funnel: [
    { id: "proteome", label: "输入蛋白", count: 3 },
    { id: "biology", label: "必需 / 毒力 / 感染 / 耐药证据", count: 2 },
    { id: "host", label: "低宿主同源", count: 1 },
    { id: "druggable", label: "可成药性证据", count: 1 },
    { id: "ranked", label: "进入候选榜", count: 1 },
  ],
  candidates: [{
    rank: 1, protein_id: "P10613", gene_symbol: "ERG11", protein_name: "Lanosterol 14-alpha demethylase",
    pathway: "ergosterol biosynthesis", candidate_score: .86, evidence_coverage: .9, priority_tier: "high_priority",
    human_similarity: .2, pathogen_conservation: .88, druggability: .95, pocket_score: .86,
    top_signals: [{ metric: "essentiality", value: .98, contribution: .21 }], missing_evidence: [],
  }],
  all_candidate_count: 3, warnings: [], model_version: "fungal-target-prioritisation-v1", generated_at: "2026-09-24T00:00:00Z",
  boundary: "本结果仅为计算候选靶标优先级，不等于已发现或已确认靶标；需实验验证。",
};

afterEach(() => vi.clearAllMocks());

describe("FungalTargetWorkbench", () => {
  it("renders both workflows and labels ranked proteins as candidates", async () => {
    Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
    vi.mocked(api).mockImplementation((path) => Promise.resolve(path === "/fungal-targets/data-status" ? {
      configured: true, bundle_version: "test-v1", message: "核心证据库已配置", counts: { proteins: 6041, cgd_phenotype_rows: 20000, phi_base_candida_albicans_rows: 300 },
      sources: [], algorithms: [{ name: "DIAMOND", version: "2.2.8", purpose: "同源筛选", parameters: "ultra-sensitive" }],
    } as never : result as never));
    const host = document.createElement("div"); document.body.append(host); const root = createRoot(host);
    try {
      await act(async () => { root.render(<FungalTargetWorkbench projectId="project-1" assets={[]} jobs={[completedJob]} busy="" run={vi.fn()} runExploration={vi.fn()} />); await Promise.resolve(); await Promise.resolve(); });
      expect(host.textContent).toContain("真菌靶标优选");
      expect(host.textContent).toContain("候选靶标预测 / target fishing");
      expect(host.textContent).toContain("ERG11");
      expect(host.textContent).toContain("不等于已发现或已确认靶标");
      expect(host.textContent).toContain("基因扰动 + 耐药突变");
      expect(host.textContent).toContain("DIAMOND 2.2.8");
      expect(host.textContent).not.toContain("结果口径");

      const exploreTab = [...host.querySelectorAll(".fungal-mode-tabs button")].find((button) => button.textContent?.includes("探索未知化合物"));
      await act(async () => { exploreTab?.dispatchEvent(new MouseEvent("click", { bubbles: true })); await Promise.resolve(); });
      expect(exploreTab?.classList.contains("active")).toBe(true);
      expect(exploreTab?.getAttribute("aria-selected")).toBe("true");
      expect(host.querySelectorAll('.fungal-mode-tabs button[aria-selected="true"]')).toHaveLength(1);
    } finally { await act(async () => root.unmount()); host.remove(); }
  });

  it("loads the SC5314 case in one click and keeps both workflow results available", async () => {
    Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
    const fishingResult = { ...result, job_id: "example-sc5314-target_fishing", workflow: "target_fishing", compound: { name: "Fluconazole / 氟康唑", structure: "InChIKey=test" } };
    const example = {
      case_id: "candida-albicans-sc5314", case_version: "2026-09-24", title: "Candida albicans SC5314 参考菌株完整案例",
      species: "Candida albicans", strain: "SC5314", organism: "Candida albicans SC5314", summary: "完整参考案例",
      identifiers: { bioproject: "PRJNA10701", assembly: "GCA_000182965.3", assembly_name: "ASM18296v3" },
      genome: { assembly_level: "Chromosome", chromosome_count: 8, protein_sequence_count: 6030, analysis_protein_count: 6041, annotation_context: "Assembly 22" },
      isolation: { year: 1984, source: "念珠菌血症患者血液分离株" }, why_selected: ["资料完整"], caveats: ["标准化演示分数"],
      sources: [{ id: "ncbi", title: "NCBI BioProject", url: "https://www.ncbi.nlm.nih.gov/bioproject/10701", supports: "参考基因组" }],
      workflows: { prioritization: { ...result, example_mode: true, organism: "Candida albicans SC5314" }, target_fishing: fishingResult },
    };
    vi.mocked(api).mockImplementation((path) => Promise.resolve(path === "/fungal-targets/data-status" ? {
      configured: true, bundle_version: "test-v1", message: "核心证据库已配置", counts: { proteins: 6041 }, sources: [], algorithms: [],
    } as never : example as never));
    const host = document.createElement("div"); document.body.append(host); const root = createRoot(host);
    try {
      await act(async () => { root.render(<FungalTargetWorkbench projectId="project-1" assets={[]} jobs={[]} busy="" run={vi.fn()} runExploration={vi.fn()} />); });
      const loadButton = [...host.querySelectorAll("button")].find((button) => button.textContent?.includes("一键载入所有结果"));
      await act(async () => { loadButton?.dispatchEvent(new MouseEvent("click", { bubbles: true })); await Promise.resolve(); await Promise.resolve(); });
      expect(api).toHaveBeenCalledWith("/fungal-targets/examples/sc5314");
      expect(host.textContent).toContain("GCA_000182965.3");
      expect(host.textContent).toContain("本次分析 6,041 条蛋白");
      expect(host.textContent).toContain("预置案例");
      expect(host.textContent).not.toContain("配置一次可追溯分析");

      const fishingTab = [...host.querySelectorAll(".fungal-mode-tabs button")].find((button) => button.textContent?.includes("target fishing"));
      await act(async () => { fishingTab?.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
      expect(host.textContent).toContain("Fluconazole / 氟康唑 靶标预测");
    } finally { await act(async () => root.unmount()); host.remove(); }
  });
});
