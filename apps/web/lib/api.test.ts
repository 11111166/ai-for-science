import { afterEach, describe, expect, it, vi } from "vitest";
import { api, apiText } from "./api";
afterEach(() => vi.unstubAllGlobals());
describe("API routing failures", () => {
  it("rejects a successful HTML portal response instead of parsing it as JSON", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("<!doctype html><h1>Portal</h1>", { headers: { "Content-Type": "text/html" } })));
    await expect(api("/targets/t/evidence")).rejects.toThrow("请求未到达 AI for Science 数据接口");
  });
  it("does not retry an ambiguous POST that received HTML 501", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response("Unsupported method", { status: 501, headers: { "Content-Type": "text/html" } }));
    vi.stubGlobal("fetch", fetch);
    await expect(api("/projects/p/perturbation-jobs", { method: "POST", body: "{}" })).rejects.toThrow("任务记录");
    expect(fetch).toHaveBeenCalledTimes(1);
  });
  it("preserves valid JSON errors and results", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(Response.json({ items: [] })).mockResolvedValueOnce(Response.json({ detail: "无可计算靶点" }, { status: 422 })));
    expect(await api("/targets")).toEqual({ items: [] });
    await expect(api("/jobs", { method: "POST" })).rejects.toThrow("无可计算靶点");
  });
  it("shows the exact field for FastAPI validation errors", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ detail: [
      { type: "value_error", loc: ["body", "factors", 1, "levels"], msg: "Value error, 数值因素须填写有限数字" },
    ] }, { status: 422 })));
    await expect(api("/projects/p/process/studies", { method: "POST", body: "{}" }))
      .rejects.toThrow("考察因素 · 第 2 个因素 · 水平：数值因素须填写有限数字");
  });
  it("explains malformed JSON without exposing response contents", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("broken", { headers: { "Content-Type": "application/json" } })));
    await expect(api("/jobs")).rejects.toThrow("数据格式异常");
  });
  it("accepts PDB coordinate text and rejects unrelated response types", async () => {
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(new Response("ATOM      1", { headers: { "Content-Type": "chemical/x-pdb" } }))
      .mockResolvedValueOnce(new Response("<!doctype html>", { headers: { "Content-Type": "text/html" } })));
    expect(await apiText("/projects/p/protein-structures/studies/s/model/coordinates")).toContain("ATOM");
    await expect(apiText("/projects/p/protein-structures/studies/s/model/coordinates")).rejects.toThrow("不支持的格式");
  });
});
