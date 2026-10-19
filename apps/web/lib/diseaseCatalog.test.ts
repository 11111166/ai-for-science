import { describe, expect, it } from "vitest";
import { analysisQueryForDisease, DISEASE_CATALOG_STATS, DISEASE_OPTIONS } from "./diseaseCatalog";

describe("disease catalog milestone", () => {
  it("keeps the published readiness tiers and focus-area coverage", () => {
    expect(DISEASE_CATALOG_STATS).toMatchObject({
      total: 201,
      complete: 5,
      enhanced: 20,
      oncology: 52,
      rare: 52,
    });
  });

  it("does not list the same disease twice", () => {
    expect(new Set(DISEASE_OPTIONS.map((item) => item.nameZh)).size).toBe(DISEASE_OPTIONS.length);
    expect(new Set(DISEASE_OPTIONS.map((item) => item.nameEn.toLowerCase())).size).toBe(DISEASE_OPTIONS.length);
  });

  it("gives every enhanced and complete disease a fixed ontology id", () => {
    const fixed = DISEASE_OPTIONS.filter((item) => item.readiness !== "catalog");
    expect(fixed).toHaveLength(25);
    expect(fixed.every((item) => Boolean(item.ontologyId))).toBe(true);
  });

  it("uses an ontology id or English resolver query behind Chinese project labels", () => {
    expect(analysisQueryForDisease("胶质母细胞瘤")).toBe("MONDO_0018177");
    expect(analysisQueryForDisease("卵巢癌")).toBe("MONDO_0008170");
    expect(analysisQueryForDisease("自定义疾病")).toBe("自定义疾病");
  });
});
