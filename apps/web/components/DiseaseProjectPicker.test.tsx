// @vitest-environment jsdom
import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import { DiseaseProjectPicker } from "./DiseaseProjectPicker";
import { api } from "@/lib/api";
import { DISEASE_OPTIONS } from "@/lib/diseaseCatalog";

const { push, project } = vi.hoisted(() => ({
  push: vi.fn(),
  project: { id: "current", name: "当前项目", disease: "帕金森病", disease_id: "MONDO_0005180", objective: "", status: "active" as const, project_type: "research" as const, research_track: "full" as const, owner_id: "zhao", created_at: "2026-01-01", updated_at: "2026-01-01" },
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
vi.mock("@/lib/api", () => ({ api: vi.fn() }));
vi.mock("./AuthSession", () => ({ useAuthUser: () => ({ username: "zhao" }) }));
vi.mock("@/lib/workspaceProjects", () => ({
  GENERAL_WORKSPACE_DISEASE: "未选择疾病",
  isGeneralWorkspace: (item: { disease: string }) => item.disease === "未选择疾病",
  lastWorkspaceProjectKey: (username: string) => `last:${username}`,
  listAccessibleResearchProjects: vi.fn().mockResolvedValue([
    project,
    { ...project, id: "cached", name: "帕金森病缓存项目", owner_id: "team" },
    { ...project, id: "target-cache", name: "帕金森病靶点项目", owner_id: "team" },
  ]),
  openDiseaseProject: vi.fn(),
}));

it("shows searchable database status and opens the cached disease project", async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.mocked(api).mockResolvedValue([
    { project_id: "current", project_name: "当前项目", disease: "帕金森病", disease_id: "MONDO_0005180", owner_id: "zhao", has_cached_results: false, completed_modules: 0, completed_jobs: 0, cached_modules: [], cached_result_count: 0, target_count: 0, target_analyses: 0, target_strategies: [], latest_cached_at: null },
    { project_id: "cached", project_name: "帕金森病缓存项目", disease: "帕金森病", disease_id: "MONDO_0005180", owner_id: "team", has_cached_results: true, completed_modules: 6, completed_jobs: 8, cached_modules: ["omics_analysis", "perturbation", "gene_knockout", "virtual_cell", "compound_matching", "ranking"], cached_result_count: 8, target_count: 100, target_analyses: 1, target_strategies: ["established"], latest_cached_at: "2026-10-13T10:00:00Z" },
    { project_id: "target-cache", project_name: "帕金森病靶点项目", disease: "帕金森病", disease_id: "MONDO_0005180", owner_id: "team", has_cached_results: true, completed_modules: 1, completed_jobs: 1, cached_modules: ["target_discovery"], cached_result_count: 1, target_count: 100, target_analyses: 1, target_strategies: ["established"], latest_cached_at: "2026-10-13T09:00:00Z" },
  ] as never);
  const host = document.createElement("div"); document.body.append(host); const root = createRoot(host);
  try {
    await act(async () => root.render(<DiseaseProjectPicker project={project} view="targets" />));
    await act(async () => Promise.resolve());
    const input = host.querySelector("input")!;
    await act(async () => input.focus());
    expect(host.textContent).toContain("结果可直接加载");
    expect(host.textContent).toContain("7 个模块 · 9 项结果");
    expect(host.textContent).not.toContain("100 靶点");
    expect(host.textContent).not.toContain("个项目");
    expect(host.textContent).toContain("全部目录中找到 1 种疾病");
    const browseAll = [...host.querySelectorAll(".disease-search-summary button")][0] as HTMLButtonElement;
    await act(async () => browseAll.dispatchEvent(new MouseEvent("mousedown", { bubbles: true })));
    expect(host.textContent).toContain(`疾病目录共 ${DISEASE_OPTIONS.length} 种`);
    expect(host.querySelectorAll('.disease-search-results > button[role="option"]')).toHaveLength(20);
    expect(host.textContent).toContain(`还有 ${DISEASE_OPTIONS.length - 20} 种`);
    const loadMore = host.querySelector(".disease-load-more") as HTMLButtonElement;
    await act(async () => loadMore.dispatchEvent(new MouseEvent("mousedown", { bubbles: true })));
    expect(host.querySelectorAll('.disease-search-results > button[role="option"]')).toHaveLength(40);
    await act(async () => document.body.dispatchEvent(new Event("pointerdown", { bubbles: true })));
    expect(host.querySelector(".disease-search-results")).toBeNull();

    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
      setter?.call(input, "帕金森病");
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.focus();
    });
    const cachedOption = [...host.querySelectorAll(".disease-search-results button")].find((button) => button.textContent?.includes("9 项结果")) as HTMLButtonElement;
    await act(async () => cachedOption.dispatchEvent(new MouseEvent("mousedown", { bubbles: true })));
    const form = host.querySelector("form")!;
    await act(async () => form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
    expect(push).toHaveBeenCalledWith("/projects/target-cache?view=targets");
  } finally {
    await act(async () => root.unmount()); host.remove();
  }
});
