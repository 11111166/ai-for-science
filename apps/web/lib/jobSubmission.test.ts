import { afterEach, expect, it, vi } from "vitest";
import { submitAnalysis } from "./jobSubmission";
afterEach(() => vi.unstubAllGlobals());
it("turns the accepted job_id into a pollable job without resubmitting", async () => {
 const fetch = vi.fn().mockResolvedValue(Response.json({job_id:"accepted-id",status:"queued",status_url:"/jobs/accepted-id"}, {status:202}));
 vi.stubGlobal("fetch",fetch);
 const job=await submitAnalysis("p","gene-knockout-jobs","real","v1",{target_job_id:"t"});
 expect(job).toMatchObject({id:"accepted-id",type:"gene_knockout",status:"queued",project_id:"p",parameters:{target_job_id:"t"}});
 expect(fetch).toHaveBeenCalledTimes(1);
});
it("preserves a completed database cache hit in the returned job", async () => {
 const fetch = vi.fn().mockResolvedValue(Response.json({job_id:"cached-id",status:"completed",status_url:"/jobs/cached-id",cache_hit:true,reused_from_job_id:"cached-id"}, {status:202}));
 vi.stubGlobal("fetch",fetch);
 const job=await submitAnalysis("p","target-discovery-jobs","real","open-targets-live",{top_n:100});
 expect(job).toMatchObject({id:"cached-id",status:"completed",cache_hit:true,reused_from_job_id:"cached-id"});
 expect(fetch).toHaveBeenCalledTimes(1);
});
