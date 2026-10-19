const API = process.env.NEXT_PUBLIC_API_URL ?? (
  process.env.NODE_ENV === "production"
    ? "/api/v1/drug-discovery"
    : "http://localhost:8000/api/v1/drug-discovery"
);

type ValidationIssue = { loc?: unknown; msg?: unknown };

const fieldLabels: Record<string, string> = {
  name: "名称",
  molecule: "目标产物",
  target_state: "目标物态",
  target_amount: "目标数量",
  target_unit: "数量单位",
  target_specification: "目标规格",
  process_system: "反应体系",
  step: "研究步骤",
  context: "固定条件、规模与检测方法",
  factors: "考察因素",
  objectives: "结果目标",
  experiment_budget: "实验总预算",
  batch_size: "每批实验数",
  confirmation_repeats: "复测次数",
  levels: "水平",
  unit: "单位",
  kind: "类型",
  graph: "图纸",
  nodes: "单元节点",
  edges: "物流连线",
  params: "参数",
  source: "来源",
  value: "数值",
  label: "名称",
  tag: "设备位号",
  notes: "备注",
  x: "X 坐标",
  y: "Y 坐标",
  w: "宽度",
  h: "高度",
  base_revision: "图纸修订号",
  source_assessment_id: "来源评估",
  direction: "方向",
  low: "较低边界",
  high: "较高边界",
};

function validationPath(loc: unknown): string {
  if (!Array.isArray(loc)) return "输入内容";
  const parts = loc.filter((part) => part !== "body").map((part, index, all) => {
    if (typeof part === "number") {
      const parent = String(all[index - 1] ?? "");
      if (parent === "factors") return `第 ${part + 1} 个因素`;
      if (parent === "objectives") return `第 ${part + 1} 个目标`;
      return `第 ${part + 1} 项`;
    }
    return fieldLabels[String(part)] ?? String(part);
  });
  return parts.join(" · ") || "输入内容";
}

function apiErrorDetail(detail: unknown): string | null {
  if (typeof detail === "string") return detail;
  if (!Array.isArray(detail)) return null;
  const issues = detail.slice(0, 3).map((item: ValidationIssue) => {
    const message = typeof item?.msg === "string"
      ? item.msg.replace(/^Value error,\s*/i, "").replace(/^Input should be /i, "应为 ")
      : "填写内容不符合要求";
    return `${validationPath(item?.loc)}：${message}`;
  });
  return issues.length ? `请检查：${issues.join("；")}${detail.length > 3 ? "；另有其他输入错误" : ""}` : null;
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (!(init?.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const method = String(init?.method ?? "GET").toUpperCase();
  const retryDelays = [700, 1500, 3000];
  let response: Response | undefined;
  for (let attempt = 0; attempt <= retryDelays.length; attempt += 1) {
    response = await fetch(`${API}${path}`, { ...init, headers, cache: "no-store", credentials: "include" });
    if (method !== "GET" || ![502, 503, 504].includes(response.status) || attempt === retryDelays.length) break;
    await new Promise((resolve) => setTimeout(resolve, retryDelays[attempt]));
  }
  if (!response) throw new Error("项目服务暂时不可用，请稍后重试。");
  if (response.status === 204) return undefined as T;
  const isJson = /(?:application\/json|application\/[\w.+-]+\+json)/i.test(response.headers.get("Content-Type") ?? "");
  if (response.redirected || !isJson) {
    throw new Error(response.status === 401 || response.status === 403
      ? "访问身份已失效，请重新进入工作台后再试。"
      : "请求未到达 AI for Science 数据接口（入口返回了页面或不支持的响应）。请稍后重试；若持续出现，请联系管理员检查入口路由。此次操作是否完成，请以任务记录为准。");
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const fallback = [502, 503, 504].includes(response.status)
      ? "项目服务正在恢复，请稍后重试。"
      : `请求失败（${response.status}）`;
    const detail = apiErrorDetail(body?.detail ?? body?.error?.message);
    throw new Error(detail ?? fallback);
  }
  try { return await response.json() as T; }
  catch { throw new Error("项目服务返回的数据格式异常，请重新加载；请勿连续重复提交任务。"); }
}

export async function apiText(path: string): Promise<string> {
  const headers = new Headers();
  const response = await fetch(`${API}${path}`, { headers, cache: "no-store", credentials: "include" });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const detail = body?.detail ?? body?.error?.message;
    throw new Error(typeof detail === "string" ? detail : `结构坐标读取失败（${response.status}）`);
  }
  if (!/^(?:chemical\/x-pdb|text\/plain)(?:;|$)/i.test(response.headers.get("Content-Type") ?? "")) {
    throw new Error("结构坐标接口返回了不支持的格式");
  }
  return response.text();
}

export const apiBase = API;

export async function apiBlob(path: string, init?: RequestInit): Promise<Blob> {
  const headers = new Headers(init?.headers);
  if (!(init?.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const response = await fetch(`${API}${path}`, { ...init, headers, cache: "no-store", credentials: "include" });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const detail = body?.detail ?? body?.error?.message;
    throw new Error(typeof detail === "string" ? detail : `图纸生成失败（${response.status}）`);
  }
  return response.blob();
}
