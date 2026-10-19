import { describe, expect, it } from "vitest";
import { evaluateFungalVaccine, type FungalVaccineInputs } from "./fungalVaccineModel";

const strongCandidate: FungalVaccineInputs = {
  antigenAccessibility: 85,
  pathogenConservation: 80,
  infectionExpression: 88,
  virulenceEvidence: 84,
  bCellEpitope: 78,
  tCellEpitope: 82,
  hlaCoverage: 76,
  humanHomologyRisk: 8,
  allergenicityRisk: 12,
  toxicityRisk: 7,
  evidenceCompleteness: 60,
  evidenceStage: "computational",
  platform: "protein_cpg",
};

describe("fungal vaccine virtual evaluation", () => {
  it("keeps computational-only confidence capped while scoring biological potential", () => {
    const result = evaluateFungalVaccine(strongCandidate);
    expect(result.protectionPotential).toBeGreaterThan(65);
    expect(result.confidence).toBeLessThanOrEqual(35);
    expect(result.confidenceBand).toBe("低");
    expect(result.evidenceGaps.join(" ")).toContain("不能推断真实保护效果");
  });

  it("penalises host, allergenicity and toxicity risks without calling the score an adverse-event rate", () => {
    const safer = evaluateFungalVaccine(strongCandidate);
    const riskier = evaluateFungalVaccine({
      ...strongCandidate,
      humanHomologyRisk: 80,
      allergenicityRisk: 70,
      toxicityRisk: 60,
    });
    expect(riskier.safety).toBeLessThan(safer.safety);
    expect(riskier.protectionPotential).toBeLessThan(safer.protectionPotential);
    expect(riskier.effectSummary.join(" ")).toContain("不是人体不良反应发生率");
  });

  it("allows stronger evidence to raise confidence but never removes the clinical boundary", () => {
    const result = evaluateFungalVaccine({
      ...strongCandidate,
      evidenceCompleteness: 90,
      evidenceStage: "animal",
    });
    expect(result.confidence).toBeGreaterThan(50);
    expect(result.confidence).toBeLessThanOrEqual(70);
    expect(result.evidenceGaps.join(" ")).toContain("不能直接外推人体");
  });
});
