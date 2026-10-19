import { describe, expect, it } from "vitest";
import type { Compound } from "@drug-discovery/contracts";
import { developmentStage, endpoints, filterCompounds, matchesFor, needsReview, predictionCount } from "./compoundInsights";
const molecule = (details: Compound["details"] = {}): Compound => ({ id: "c", identifier: "CHEMBL1", name: "Molecule", mechanism: "", match_score: .8, details, demo: false });
const options = { query: "", target: "", stage: "", filter: "all", sort: "match" };
describe("molecule evidence review", () => {
  it("does not mistake an evaluation record or unknown endpoint for five predictions", () => {
    const c = { ...molecule({ admet_predictions: { other: { value: 1 }, ames: { value: 0 }, herg: { value: null } } }), evaluation: { admet: { predictions: {} } } };
    expect(predictionCount(c)).toBe(1);
    expect(filterCompounds([c], { ...options, filter: "evaluated" })).toEqual([]);
    expect(needsReview(c)).toBe(true);
    expect(predictionCount(molecule({ admet_predictions: Object.fromEntries(endpoints.map(({ id }) => [id, { value: 0 }])) }))).toBe(5);
  });
  it("labels approval without implying an indication and keeps unknown distinct", () => {
    expect(developmentStage(molecule({ max_phase: 4 })).id).toBe("approved");
    expect(developmentStage(molecule({ max_phase: 2 })).id).toBe("clinical");
    expect(developmentStage(molecule({ max_phase: 0 })).id).toBe("research");
    expect(developmentStage({ ...molecule({ max_phase: 4 }), demo: true }).id).toBe("demo");
  });
  it("finds secondary targets and sorts by the selected target without mutating input", () => {
    const a = molecule({ target_matches: [{ target_symbol: "A", match_score: .9 }, { target_symbol: "B", match_score: .1, pchembl_median: 6 }] });
    const b = { ...molecule({ target_matches: [{ target_symbol: "B", match_score: .7, pchembl_median: 8 }] }), id: "b", identifier: "CHEMBL2" };
    expect(filterCompounds([a, b], { ...options, query: "B", target: "B", sort: "activity" }).map((c) => c.id)).toEqual(["b", "c"]);
    expect(matchesFor(a, "B")[0].target_symbol).toBe("B");
    expect((a.details.target_matches as { target_symbol: string }[])[0].target_symbol).toBe("A");
  });
  it("puts missing potency after a valid zero", () => {
    const missing = molecule({ target_matches: [{ target_symbol: "A" }] });
    const zero = { ...molecule({ target_matches: [{ target_symbol: "A", pchembl_median: 0 }] }), id: "z" };
    expect(filterCompounds([missing, zero], { ...options, sort: "activity" })[0].id).toBe("z");
  });
});
