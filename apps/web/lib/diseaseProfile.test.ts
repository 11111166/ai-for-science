import { describe, expect, it } from "vitest";
import { diseaseAreaLabel, getDiseaseProfile } from "./diseaseProfile";

describe("disease portrait v1", () => {
  it("returns disease-specific context for a fully validated disease", () => {
    const profile = getDiseaseProfile("类风湿关节炎", "MONDO_0008383");
    expect(profile.disease?.readiness).toBe("complete");
    expect(profile.tissues).toContain("滑膜与关节");
    expect(profile.cellContexts).toContain("滑膜成纤维细胞");
    expect(diseaseAreaLabel(profile)).toBe("免疫与炎症");
  });

  it("uses a therapeutic-area profile for catalog diseases", () => {
    const profile = getDiseaseProfile("卵巢癌");
    expect(profile.disease?.readiness).toBe("catalog");
    expect(profile.cellContexts).toContain("肿瘤细胞");
    expect(profile.developmentQuestions).toContain("肿瘤选择性");
  });

  it("keeps an honest generic profile for an unregistered disease", () => {
    const profile = getDiseaseProfile("未登记疾病");
    expect(profile.disease).toBeNull();
    expect(profile.summary.length).toBeGreaterThan(10);
  });
});
