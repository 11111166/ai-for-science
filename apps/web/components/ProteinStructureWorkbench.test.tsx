// @vitest-environment jsdom
import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import { ProteinStructureWorkbench } from "./ProteinStructureWorkbench";
import { api, apiText } from "@/lib/api";

vi.mock("@/lib/api", () => ({ api: vi.fn(), apiText: vi.fn() }));
vi.mock("3dmol", () => ({ createViewer: () => ({
  addModel: vi.fn(), setStyle: vi.fn(), setBackgroundColor: vi.fn(), setViewStyle: vi.fn(),
  zoomTo: vi.fn(), zoom: vi.fn(), rotate: vi.fn(), resize: vi.fn(), render: vi.fn(), clear: vi.fn(),
}) }));

class TestResizeObserver {
  observe() {}
  disconnect() {}
}

it("keeps structural agreement separate from functional validation", async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true, ResizeObserver: TestResizeObserver });
  vi.mocked(api).mockReset();
  vi.mocked(apiText).mockReset();
  vi.mocked(apiText).mockResolvedValue(String.raw`ATOM      1  N   ALA A   1      10.000  10.000  10.000  1.00 92.00           N`);
  const study = {
    id: "s", revision: 3,
    spec: { name: "EGFR 结构", target: "EGFR", uniprot_accession: "P00533", sequence: "", assembly: "monomer", research_question: "比较模型与实验结构" },
    model: {
      source: "alphafold_db", source_id: "AF-P00533-F1", file_name: "model.pdb", sha256: "abc", residue_count: 100,
      chain_count: 1, chain_lengths: { A: 100 }, observed_sequence: "A".repeat(100),
      confidence: { metric: "pLDDT", mean: 82, very_high_fraction: .4, confident_fraction: .8, low_fraction: .05, boundary: "只表示局部置信度" },
      projection: { method: "主成分二维投影", chains: { A: [[-.2, -.1], [.2, .1]] } }, provenance: { model_version: 6 },
      viewer: { kind: "ca_trace_3d", chains: { A: [[-1, 0, 0, 92], [0, 1, .5, 82], [1, 0, 1, 61]] }, sampled_residues: 3, total_residues: 3, boundary: "基于 Cα 坐标浏览" },
    },
    comparisons: [{
      id: "c", created_at: "2026-09-14", interpretation: "结构一致性不能证明功能或疗效。",
      reference: { source: "pdb_experimental", source_id: "1M17", file_name: "1M17.pdb", sha256: "def", residue_count: 90, chain_count: 1, chain_lengths: { A: 90 }, observed_sequence: "A".repeat(90), confidence: null, projection: { method: "", chains: {} }, provenance: {} },
      metrics: { model_chain: "A", reference_chain: "A", aligned_residues: 90, sequence_identity: 1, model_coverage: .9, reference_coverage: 1, ca_rmsd_angstrom: 1.2, sequence_registered_tm_like: .86, metric_boundary: "不是完整 TM-align，也不能证明功能或疗效。" },
    }],
    validations: [], prediction_task: { status: "not_configured", task_type: "monomer_prediction", message: "重计算尚未接入", sequence_sha256: "" },
  };
  vi.mocked(api).mockResolvedValue([study]);
  const host = document.createElement("div"); document.body.append(host); const root = createRoot(host);
  try {
    await act(async () => root.render(<ProteinStructureWorkbench projectId="p" />));
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(host.textContent).toContain("AlphaFold DB 预测");
    expect(host.textContent).toContain("PDB 实验结构");
    expect(host.textContent).toContain("Cα RMSD 1.2 Å");
    expect(host.textContent).toContain("结构一致性不能证明功能或疗效");
    expect(host.textContent).toContain("独立实验验证");
    expect(host.textContent).toContain("三维丝带结构");
    expect(host.querySelector("[aria-label='蛋白质三维丝带结构查看器']")).not.toBeNull();
    expect(host.textContent).toContain("Cartoon · 完整 PDB 坐标");
    expect(host.querySelector("svg[aria-label='蛋白质 Cα 主链交互式三维视图']")).not.toBeNull();
    expect(host.textContent).toContain("拖动旋转");
    expect(host.textContent).toContain("很高 ≥90");
  } finally { await act(async () => root.unmount()); host.remove(); }
});

it("starts with guided search and creates a study from a real database result", async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.mocked(api).mockReset();
  vi.mocked(apiText).mockReset();
  const created = {
    id: "new", revision: 1,
    spec: { name: "EGFR 结构研究", target: "EGFR", uniprot_accession: "P00533", sequence: "", assembly: "monomer", research_question: "核对 EGFR" },
    model: null, comparisons: [], validations: [],
    prediction_task: { status: "not_configured", task_type: "monomer_prediction", message: "重计算尚未接入", sequence_sha256: "" },
  };
  vi.mocked(api).mockImplementation(async (path, init) => {
    if (path.endsWith("/studies") && init?.method === "POST") return created as never;
    if (path.includes("/search?")) return {
      query: "EGFR", returned: 1, total: 1,
      source: { name: "UniProtKB", url: "https://www.uniprot.org/", retrieved_at: "2026-09-14", boundary: "数据库记录不代表功能已验证。" },
      items: [{ accession: "P00533", entry_name: "EGFR_HUMAN", reviewed: true, protein_name: "Epidermal growth factor receptor", gene: "EGFR", gene_names: ["EGFR"], organism: "Homo sapiens", length: 1210, pdb_count: 7, pdb_examples: ["1M17"], entry_url: "https://www.uniprot.org/uniprotkb/P00533/entry" }],
    } as never;
    return [] as never;
  });
  const host = document.createElement("div"); document.body.append(host); const root = createRoot(host);
  try {
    await act(async () => root.render(<ProteinStructureWorkbench projectId="p" projectDisease="肺癌" targetSuggestions={[{ symbol: "EGFR", name: "表皮生长因子受体", selected: true }]} />));
    expect(host.textContent).toContain("你想从哪里开始");
    expect(host.textContent).toContain("搜索后会先列出可选结果");
    const suggestion = host.querySelector<HTMLButtonElement>(".protein-project-suggestions button");
    await act(async () => { suggestion?.click(); await Promise.resolve(); });
    expect(host.textContent).toContain("Epidermal growth factor receptor");
    expect(host.textContent).toContain("7 个 PDB 交叉引用");
    const choose = Array.from(host.querySelectorAll("button")).find((button) => button.textContent === "选择并建立研究");
    await act(async () => { choose?.click(); await Promise.resolve(); });
    const createCall = vi.mocked(api).mock.calls.find(([, init]) => init?.method === "POST");
    expect(JSON.parse(String(createCall?.[1]?.body)).uniprot_accession).toBe("P00533");
    expect(host.textContent).toContain("已用 P00533 建立结构研究");
  } finally { await act(async () => root.unmount()); host.remove(); }
});
