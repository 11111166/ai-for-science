// @vitest-environment jsdom
import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import { BacterialDiscoveryHub } from "./BacterialDiscoveryHub";

vi.mock("@/lib/api", () => ({ api: vi.fn(), apiBase: "http://localhost/api" }));
vi.mock("./BacterialCompoundExploration", () => ({ BacterialCompoundExploration: () => null }));
vi.mock("./KetcherStructureEditor", () => ({ KetcherStructureEditor: () => <div>结构编辑器测试替身</div> }));

const catalog = {
  configured: true,
  bundle_version: "test-v1",
  organisms: [{
    id: "saureus-nctc8325", species: "Staphylococcus aureus", strain: "NCTC 8325",
    organism: "Staphylococcus aureus NCTC 8325", aliases: [], coverage: "full_curated_case",
    known_compound_count: 12, associated_target_count: 40, known_compounds: [], targets: [],
  }],
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
  bacteria: [{
    organism: "Staphylococcus aureus NCTC 8325", species: "Staphylococcus aureus", strain: "NCTC 8325",
    compound_matches: 1, association_count: 244, target_count: 203,
    targets: [{
      compound: "CIPROFLOXACIN", uniprot: "P0A0V2", gene_symbol: "gyrA",
      protein_name: "DNA gyrase subunit A", relationship_label: "结构相似候选（证据属于 CIPROFLOXACIN）",
      evidence_source: "RDKit 结构相似 + CARD", direction: "resistance",
    }],
  }],
  boundary: "相似结构命中只用于候选优先级。",
  raw_sources: { configured: true, sources: [] },
};

afterEach(() => vi.clearAllMocks());

describe("BacterialDiscoveryHub structure search", () => {
  it("offers a dedicated unknown-structure input and submits a normalized structure search", async () => {
    Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
    vi.mocked(api).mockImplementation((path) => Promise.resolve(
      path === "/bacterial-discovery/structure-search" ? structureResult as never : catalog as never,
    ));
    const host = document.createElement("div"); document.body.append(host); const root = createRoot(host);
    try {
      await act(async () => { root.render(<BacterialDiscoveryHub projectId="p1" jobs={[]} busy="" runExploration={vi.fn()} />); await Promise.resolve(); });
      const compoundMode = [...host.querySelectorAll("button")].find((button) => button.textContent?.includes("按化合物找细菌与靶标"));
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
      expect(api).toHaveBeenCalledWith("/bacterial-discovery/structure-search", expect.objectContaining({ method: "POST" }));
      expect(host.textContent).toContain("已规范化的查询结构");
      expect(host.textContent).toContain("Canonical SMILES");
      expect(host.textContent).toContain("Isomeric SMILES");
      expect(host.textContent).toContain("CAS 尚未分配或未在已接入登记库命中");
      expect(host.querySelector('img[alt="输入的未知结构二维结构式"]')).not.toBeNull();
      const bacteriumGroup = host.querySelector("details.bacterial-hit-group") as HTMLDetailsElement;
      expect(bacteriumGroup).not.toBeNull();
      expect(bacteriumGroup.open).toBe(false);
      expect(bacteriumGroup.querySelector("summary")?.textContent).toContain("展开查看");
      await act(async () => bacteriumGroup.querySelector("summary")?.click());
      expect(bacteriumGroup.open).toBe(true);
      expect(bacteriumGroup.querySelector("summary")?.textContent).toContain("收起详情");

      const callsBeforeFill = vi.mocked(api).mock.calls.length;
      const fillExample = [...host.querySelectorAll("button")].find((button) => button.textContent?.includes("仅填入 SMILES"));
      await act(async () => fillExample?.click());
      expect((host.querySelector("textarea") as HTMLTextAreaElement).value).toContain("c2cc(N3CCNCC3)");
      expect(vi.mocked(api).mock.calls).toHaveLength(callsBeforeFill);

      const example = [...host.querySelectorAll("button")].find((button) => button.textContent?.includes("载入并搜索"));
      await act(async () => { example?.click(); await Promise.resolve(); await Promise.resolve(); });
      const lastCall = vi.mocked(api).mock.calls.at(-1);
      expect(lastCall?.[0]).toBe("/bacterial-discovery/structure-search");
      expect(JSON.parse(String((lastCall?.[1] as RequestInit)?.body))).toMatchObject({
        format: "smiles", similarity_threshold: 0.4,
      });
    } finally { await act(async () => root.unmount()); host.remove(); }
  });

  it("loads the association-rich bacterium example by default", async () => {
    Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
    vi.mocked(api).mockResolvedValue(catalog as never);
    const host = document.createElement("div"); document.body.append(host); const root = createRoot(host);
    try {
      await act(async () => { root.render(<BacterialDiscoveryHub projectId="p1" jobs={[]} busy="" runExploration={vi.fn()} />); await Promise.resolve(); });
      expect(api).toHaveBeenCalledWith("/bacterial-discovery/organisms?q=Escherichia%20coli");
      expect(host.textContent).toContain("细菌—化合物—靶标双向检索");
      expect(host.textContent).toContain("按细菌找化合物");
      expect(host.textContent).toContain("按化合物找细菌与靶标");
      expect(host.textContent).toContain("一键载入大肠杆菌 K-12 案例");
      // The bacterial module deliberately has no vaccine panel.
      expect(host.textContent).not.toContain("疫苗虚拟评价");
    } finally { await act(async () => root.unmount()); host.remove(); }
  });
});
