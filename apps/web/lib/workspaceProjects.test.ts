import type { Project } from "@drug-discovery/contracts";
import { beforeEach, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import { findDiseaseProject, listAccessibleResearchProjects, openDefaultWorkspace, openDiseaseProject, resolveDisease } from "./workspaceProjects";

vi.mock("@/lib/api", () => ({ api: vi.fn() }));

const project = (id: string, disease: string, owner_id = "zhao", disease_id: string | null = null): Project => ({
  id, name: `${disease}研发空间`, disease, disease_id, objective: "", owner_id,
  status: "active", project_type: "research", research_track: "full",
  created_at: "2026-09-22T00:00:00Z", updated_at: "2026-09-22T00:00:00Z",
}) as Project;

beforeEach(() => { vi.mocked(api).mockReset(); });

it("resolves catalog names and reuses the same disease project", async () => {
  const parkinsons = project("p1", "帕金森病", "zhao", "MONDO_0005180");
  expect(resolveDisease("Parkinson disease")).toEqual({ name: "帕金森病", id: "MONDO_0005180" });
  expect(findDiseaseProject([parkinsons], resolveDisease("帕金森病"))).toEqual(parkinsons);
  expect(findDiseaseProject([project("legacy", "帕金森病")], resolveDisease("帕金森病"))?.id).toBe("legacy");
  expect(await openDiseaseProject("MONDO_0005180", [parkinsons])).toEqual(parkinsons);
  expect(api).not.toHaveBeenCalled();
});

it("creates a separate disease scope only when not already present", async () => {
  const created = project("p2", "阿尔茨海默病", "zhao", "MONDO_0004975");
  vi.mocked(api).mockResolvedValueOnce(created);
  expect(await openDiseaseProject("阿尔茨海默病", [project("p1", "帕金森病")])).toEqual(created);
  const [path, options] = vi.mocked(api).mock.calls[0];
  expect(path).toBe("/projects");
  expect(JSON.parse(String(options?.body))).toMatchObject({ disease: "阿尔茨海默病", disease_id: "MONDO_0004975", project_type: "research" });
  await expect(openDiseaseProject("未选择疾病", [])).rejects.toThrow();
});

it("lands in the user's own workspace unless a previously visited accessible project was requested", async () => {
  const own = project("own", "帕金森病");
  vi.mocked(api).mockImplementation(async (path: string) => {
    if (path === "/auth/me") return { username: "zhao" } as never;
    if (path.startsWith("/projects?")) return [project("foreign", "乳腺癌", "other"), own] as never;
    throw new Error(path);
  });
  expect(await listAccessibleResearchProjects()).toEqual([project("foreign", "乳腺癌", "other"), own]);
  expect(await openDefaultWorkspace()).toEqual(own);
  expect((await openDefaultWorkspace("foreign")).id).toBe("foreign");
});

it("reuses accessible team results while preferring the user's own disease space", async () => {
  const shared = project("shared", "帕金森病", "zrq", "MONDO_0005180");
  const own = project("own", "帕金森病", "zhao", "MONDO_0005180");
  expect(findDiseaseProject([shared, own], resolveDisease("帕金森病"), "zhao")?.id).toBe("own");
  expect(await openDiseaseProject("帕金森病", [shared], "zhao")).toEqual(shared);
  expect(api).not.toHaveBeenCalled();
});

it("does not create a new project when the account has no accessible workspace", async () => {
  vi.mocked(api).mockImplementation(async (path: string) => {
    if (path === "/auth/me") return { username: "zhao" } as never;
    if (path.startsWith("/projects?")) return [] as never;
    throw new Error(path);
  });
  await expect(openDefaultWorkspace()).rejects.toThrow("当前账号没有可访问的研发项目");
  expect(vi.mocked(api).mock.calls.some(([path]) => path === "/projects")).toBe(false);
});
