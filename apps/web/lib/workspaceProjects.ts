import type { Project } from "@drug-discovery/contracts";
import { api } from "@/lib/api";
import { DISEASE_OPTIONS } from "@/lib/diseaseCatalog";

export const GENERAL_WORKSPACE_DISEASE = "未选择疾病";
export const lastWorkspaceProjectKey = (username: string) => `ai4science:last-workspace-project:${username}`;

export function isGeneralWorkspace(project: Project): boolean {
  return project.disease === GENERAL_WORKSPACE_DISEASE;
}

async function visibleResearchProjects(): Promise<Project[]> {
  const projects: Project[] = [];
  for (let offset = 0; ; offset += 100) {
    const page = await api<Project[]>(`/projects?limit=100&offset=${offset}`);
    projects.push(...page);
    if (page.length < 100) return projects;
  }
}

export async function listAccessibleResearchProjects(): Promise<Project[]> { return visibleResearchProjects(); }

export function resolveDisease(input: string): { name: string; id: string | null } {
  const query = input.trim();
  const matched = DISEASE_OPTIONS.find((item) =>
    [item.nameZh, item.nameEn, item.ontologyId].some((value) => value?.toLocaleLowerCase("zh-CN") === query.toLocaleLowerCase("zh-CN")));
  return matched ? { name: matched.nameZh, id: matched.ontologyId ?? null } : { name: query, id: null };
}

export function findDiseaseProject(projects: Project[], disease: { name: string; id: string | null }, preferredOwner?: string): Project | undefined {
  const matches = projects.filter((project) => project.status === "active" && !isGeneralWorkspace(project) &&
    (Boolean(disease.id && project.disease_id === disease.id) || project.disease.toLocaleLowerCase("zh-CN") === disease.name.toLocaleLowerCase("zh-CN")));
  return matches.find((project) => project.owner_id === preferredOwner) ?? matches[0];
}

export async function openDiseaseProject(input: string, projects: Project[], preferredOwner?: string): Promise<Project> {
  const disease = resolveDisease(input);
  if (disease.name.length < 2 || disease.name === GENERAL_WORKSPACE_DISEASE) throw new Error("请选择或输入至少两个字的疾病名称。");
  const existing = findDiseaseProject(projects, disease, preferredOwner);
  if (existing) return existing;
  return api<Project>("/projects", {
    method: "POST",
    body: JSON.stringify({
      name: `${disease.name}研发空间`,
      disease: disease.name,
      disease_id: disease.id,
      objective: `围绕${disease.name}查看各模块的证据与分析结果。`,
      research_track: "full",
      project_type: "research",
    }),
  });
}

/**
 * 进入工作台：交给后端决定打开哪个项目。
 * 账号一个可访问项目都没有时，后端会为它建一个个人研发工作区（workspace bootstrap），
 * 新注册的账号因此不会再卡在「没有项目权限」；团队项目由管理员在「权限管理」里授权。
 */
export async function openDefaultWorkspace(preferredId?: string | null): Promise<Project> {
  return api<Project>("/workspace/bootstrap", {
    method: "POST",
    body: JSON.stringify({ preferred_project_id: preferredId ?? null }),
  });
}
