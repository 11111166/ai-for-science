import { describe, expect, it } from "vitest";
import type { Job, Ranking } from "@drug-discovery/contracts";
import { buildCandidateDecisionSupport, buildProjectDecisionBrief } from "./decisionSupport";

const job = (type: string, status: Job["status"] = "completed"): Job => ({ id: type, project_id: "p", type, status, stage: status, progress: status === "completed" ? 100 : 0, adapter_mode: "real", parameters: {}, created_at: "2026-09-07T00:00:00Z" });

describe("enterprise decision support", () => {
  it("points to the first incomplete decision gate", () => {
    const brief = buildProjectDecisionBrief({ jobs: [job("target_discovery")], targetCount: 20, perturbationCount: 0, knockoutCount: 0, virtualCellCount: 0, compoundCount: 0, evaluatedCompoundCount: 0, rankingCount: 0, decidedCount: 0 });
    expect(brief.readiness).toBe(17);
    expect(brief.nextView).toBe("perturbation");
    expect(brief.gates[0].status).toBe("complete");
  });

  it("recommends a stable complete candidate for advancement", () => {
    const make = (id: string, value: number): Ranking => ({ id, rank: 1, total_score: value, decision: "pending", rationale: "", weights: {}, compound: { id, name: id, identifier: id, mechanism: "", match_score: value, details: {descriptors:{structural_alert_count:0},admet_predictions:Object.fromEntries(["hia_hou","ames","herg","caco2_wang","solubility_aqsoldb"].map(id=>[id,{value:.5,domain:"inside"}]))}, demo: false, evaluation: { efficacy: value, safety: value, developability: value, admet: {} } } });
    const rankings = [make("leader", .9), make("backup", .6), make("tail", .3)];
    const support = buildCandidateDecisionSupport(rankings[0], rankings);
    expect(support.action).toBe("advance");
    expect(support.topBandFrequency).toBe(1);
    expect(support.evidenceCompleteness).toBe(1);
  });

  it("holds a candidate with missing decision inputs", () => {
    const ranking: Ranking = { id: "incomplete", rank: 1, total_score: .8, decision: "pending", rationale: "", weights: {}, compound: { id: "c", name: "c", identifier: "c", mechanism: "", match_score: .8, details: {}, demo: false, evaluation: { admet: {} } } };
    expect(buildCandidateDecisionSupport(ranking, [ranking]).action).toBe("hold");
  });
});
