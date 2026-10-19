import type { AdapterMode, Job } from "@drug-discovery/contracts";
import { api } from "./api";

/** The POST response is an acknowledgement, not a full Job resource. */
export async function submitAnalysis(projectId: string, path: string, adapterMode: AdapterMode, inputVersion: string, parameters: Record<string, unknown>): Promise<Job> {
  const accepted = await api<{ job_id: string; status: Job["status"]; cache_hit?: boolean; reused_from_job_id?: string }>(`/projects/${projectId}/${path}`, {
    method: "POST", body: JSON.stringify({ adapter_mode: adapterMode, input_version: inputVersion, parameters }),
  });
  if (!accepted.job_id) throw new Error("任务回执缺少编号，请先刷新任务记录，避免重复提交。");
  return {
    id: accepted.job_id, project_id: projectId,
    type: path.replace(/-jobs$/, "").replace(/-/g, "_"),
    status: accepted.status, stage: accepted.status, progress: 0,
    adapter_mode: adapterMode, parameters, created_at: new Date().toISOString(),
    cache_hit: accepted.cache_hit, reused_from_job_id: accepted.reused_from_job_id,
  };
}
