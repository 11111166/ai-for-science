// @vitest-environment jsdom
import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import { FungalDiscoveryHub } from "./FungalDiscoveryHub";

vi.mock("@/lib/api", () => ({ api: vi.fn(), apiBase: "http://localhost/api" }));
vi.mock("./FungalCompoundExploration", () => ({ FungalCompoundExploration: () => null }));
vi.mock("./KetcherStructureEditor", () => ({ KetcherStructureEditor: () => <div>结构编辑器测试替身</div> }));

const catalog = {
  configured: true,
  bundle_version: "test-v1",
  organisms: [],
  raw_sources: { configured: true, sources: [] },
};

const structureResult = {
  configured: true,
  search_mode: "structure_similarity",
  query_structure: {
    name: "输入的未知结构",
    canonical_smiles: "CCO",
    isomeric_smiles: "CCO",
    model_input_smiles: "CCO",
    inchi_key: "LFQSCWFLJHTTHZ-UHFFFAOYSA-N",
    molecular_formula: "C2H6O",
    molecular_weight: 46.069,
    cas_numbers: [],
    svg: '<ns0:svg xmlns:ns0="http://www.w3.org/2000/svg" viewBox="0 0 240 180"><ns0:path d="M20 90 L220 90" /></ns0:svg>',
    normalization: { profile: "rdkit-test", transformations: ["结构校验（无需改写）"], warnings: [] },
  },
  matches: [],
  fungi: [{
    organism: "Candida albicans SC5314", species: "Candida albicans", strain: "SC5314",
    compound_matches: 1, association_count: 244, target_count: 203,
    targets: [{
      compound: "FLUCONAZOLE", uniprot: "Q59WH0", gene_symbol: "ADA2",
      protein_name: "Transcriptional adapter 2", relationship_label: "结构相似候选（证据属于 FLUCONAZOLE）",
      evidence_source: "RDKit 结构相似 + CGD", direction: "sensitizing",
    }],
  }],
  boundary: "相似结构命中只用于候选优先级。",
  raw_sources: { configured: true, sources: [] },
};

afterEach(() => vi.clearAllMocks());

describe("FungalDiscoveryHub structure search", () => {
  it("offers a dedicated unknown-structure input and submits a normalized structure search", async () => {
    Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
    vi.mocked(api).mockImplementation((path) => Promise.resolve(
      path === "/fungal-discovery/structure-search" ? structureResult as never : catalog as never,
    ));
    const host = document.createElement("div"); document.body.append(host); const root = createRoot(host);
    try {
      await act(async () => { root.render(<FungalDiscoveryHub projectId="p1" jobs={[]} busy="" runExploration={vi.fn()} />); await Promise.resolve(); });
      const compoundMode = [...host.querySelectorAll("button")].find((button) => button.textContent?.includes("按化合物找真菌与靶标"));
      await act(async () => compoundMode?.click());
      const structureTab = [...host.querySelectorAll("button")].find((button) => button.textContent?.includes("未知结构式"));
      await act(async () => structureTab?.click());
      expect(host.textContent).toContain("结构绘制");
      expect(host.textContent).toContain("SMILES 粘贴");
      expect(host.textContent).toContain("上传结构文件");
      expect(host.textContent).toContain("Morgan 指纹");

      const smilesTab = [...host.querySelectorAll("button")].find((button) => button.textContent?.includes("SMILES 粘贴"));
      await act(async () => smilesTab?.click());

      const textarea = host.querySelector("textarea")!;
      const setValue = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set;
      await act(async () => { setValue?.call(textarea, "CCO"); textarea.dispatchEvent(new Event("input", { bubbles: true })); });
      const search = [...host.querySelectorAll("button")].find((button) => button.textContent?.includes("校验并搜索结构"));
      await act(async () => { search?.click(); await Promise.resolve(); await Promise.resolve(); });
      expect(api).toHaveBeenCalledWith("/fungal-discovery/structure-search", expect.objectContaining({ method: "POST" }));
      expect(host.textContent).toContain("已规范化的查询结构");
      expect(host.textContent).toContain("Canonical SMILES");
      expect(host.textContent).toContain("Isomeric SMILES");
      expect(host.textContent).toContain("CAS 尚未分配或未在已接入登记库命中");
      expect(host.querySelector('img[alt="输入的未知结构二维结构式"]')).not.toBeNull();
      const fungusGroup = host.querySelector("details.fungal-hit-group") as HTMLDetailsElement;
      expect(fungusGroup).not.toBeNull();
      expect(fungusGroup.open).toBe(false);
      expect(fungusGroup.querySelector("summary")?.textContent).toContain("展开查看");
      await act(async () => fungusGroup.querySelector("summary")?.click());
      expect(fungusGroup.open).toBe(true);
      expect(fungusGroup.querySelector("summary")?.textContent).toContain("收起详情");

      const callsBeforeFill = vi.mocked(api).mock.calls.length;
      const fillExample = [...host.querySelectorAll("button")].find((button) => button.textContent?.includes("仅填入 SMILES"));
      await act(async () => fillExample?.click());
      expect((host.querySelector("textarea") as HTMLTextAreaElement).value).toContain("c1ccc(Cl)cc1F");
      expect(vi.mocked(api).mock.calls).toHaveLength(callsBeforeFill);

      const example = [...host.querySelectorAll("button")].find((button) => button.textContent?.includes("载入并搜索"));
      await act(async () => { example?.click(); await Promise.resolve(); await Promise.resolve(); });
      expect((host.querySelector("textarea") as HTMLTextAreaElement).value).toContain("c1ccc(Cl)cc1F");
      const lastCall = vi.mocked(api).mock.calls.at(-1);
      expect(lastCall?.[0]).toBe("/fungal-discovery/structure-search");
      expect(JSON.parse(String((lastCall?.[1] as RequestInit)?.body))).toMatchObject({
        format: "smiles", similarity_threshold: 0.4,
      });
    } finally { await act(async () => root.unmount()); host.remove(); }
  });

  it("opens the vaccine evaluation and loads the auditable SC5314 example", async () => {
    Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
    vi.mocked(api).mockResolvedValue(catalog as never);
    const host = document.createElement("div"); document.body.append(host); const root = createRoot(host);
    try {
      await act(async () => { root.render(<FungalDiscoveryHub projectId="p1" jobs={[]} busy="" runExploration={vi.fn()} />); await Promise.resolve(); });
      const vaccineMode = [...host.querySelectorAll("button")].find((button) => button.textContent?.includes("疫苗虚拟评价"));
      await act(async () => vaccineMode?.click());
      expect(host.textContent).toContain("不输出人体保护率或临床结论");
      expect(host.textContent).toContain("透明加权评分 v0.1");
      const generate = [...host.querySelectorAll("button")].find((button) => button.textContent?.includes("生成并冻结评价快照"));
      await act(async () => generate?.click());
      expect(host.textContent).toContain("请先补全：候选编号、候选名称、真菌物种、抗原/构建体");
      const example = [...host.querySelectorAll("button")].find((button) => button.textContent?.includes("一键载入 SC5314 示例"));
      await act(async () => example?.click());
      expect(host.textContent).toContain("SC5314 · Als3 重组抗原候选（演示）");
      expect(host.textContent).toContain("候选保护潜力（相对评分）");
      expect(host.textContent).toContain("不是人体保护率");
      expect(host.textContent).toContain("尚无体外、动物或人体免疫原性证据");
      expect(host.textContent).toContain("证据来源与计算记录");
      expect(host.textContent).toContain("导出可追溯 JSON");
      expect(host.textContent).toContain("FVE-");
    } finally { await act(async () => root.unmount()); host.remove(); }
  });
});
