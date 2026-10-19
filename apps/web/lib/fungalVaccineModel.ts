export type VaccineEvidenceStage = "computational" | "in_vitro" | "animal" | "human_immunogenicity";
export type VaccinePlatform = "protein_alum" | "protein_cpg" | "mrna_lnp" | "viral_vector";

export type FungalVaccineInputs = {
  antigenAccessibility: number;
  pathogenConservation: number;
  infectionExpression: number;
  virulenceEvidence: number;
  bCellEpitope: number;
  tCellEpitope: number;
  hlaCoverage: number;
  humanHomologyRisk: number;
  allergenicityRisk: number;
  toxicityRisk: number;
  evidenceCompleteness: number;
  evidenceStage: VaccineEvidenceStage;
  platform: VaccinePlatform;
};

export type FungalVaccineResult = {
  humoral: number;
  cellular: number;
  populationCoverage: number;
  safety: number;
  protectionPotential: number;
  confidence: number;
  confidenceBand: "低" | "中" | "较高";
  effectSummary: string[];
  evidenceGaps: string[];
};

const platformAdjustments: Record<VaccinePlatform, { humoral: number; cellular: number }> = {
  protein_alum: { humoral: 8, cellular: -2 },
  protein_cpg: { humoral: 5, cellular: 7 },
  mrna_lnp: { humoral: 6, cellular: 8 },
  viral_vector: { humoral: 3, cellular: 10 },
};

const stageCaps: Record<VaccineEvidenceStage, number> = {
  computational: 35,
  in_vitro: 50,
  animal: 70,
  human_immunogenicity: 85,
};

function clamp(value: number) {
  return Math.round(Math.max(0, Math.min(100, value)));
}

function band(value: number) {
  return value >= 70 ? "较高" : value >= 45 ? "中等" : "较低";
}

export function evaluateFungalVaccine(inputs: FungalVaccineInputs): FungalVaccineResult {
  const adjustment = platformAdjustments[inputs.platform];
  const humoral = clamp(
    inputs.antigenAccessibility * 0.34 +
    inputs.bCellEpitope * 0.30 +
    inputs.pathogenConservation * 0.18 +
    inputs.infectionExpression * 0.18 +
    adjustment.humoral,
  );
  const cellular = clamp(
    inputs.tCellEpitope * 0.34 +
    inputs.infectionExpression * 0.24 +
    inputs.virulenceEvidence * 0.22 +
    inputs.hlaCoverage * 0.20 +
    adjustment.cellular,
  );
  const populationCoverage = clamp(inputs.pathogenConservation * 0.45 + inputs.hlaCoverage * 0.55);
  const safety = clamp(100 - (
    inputs.humanHomologyRisk * 0.45 +
    inputs.allergenicityRisk * 0.35 +
    inputs.toxicityRisk * 0.20
  ));
  const biologicalPotential =
    humoral * 0.22 + cellular * 0.25 + populationCoverage * 0.16 +
    inputs.infectionExpression * 0.13 + inputs.virulenceEvidence * 0.14 + safety * 0.10;
  const evidenceShrinkage = 0.72 + inputs.evidenceCompleteness / 100 * 0.28;
  const protectionPotential = clamp(biologicalPotential * evidenceShrinkage);
  const stageSupport = stageCaps[inputs.evidenceStage];
  const confidence = clamp(Math.min(stageSupport, inputs.evidenceCompleteness * 0.75 + stageSupport * 0.25));
  const confidenceBand = confidence >= 65 ? "较高" : confidence >= 40 ? "中" : "低";

  const effectSummary = [
    `模型推测其诱导抗体应答的潜力为${band(humoral)}（${humoral}/100）。`,
    `模型推测其诱导 T 细胞相关应答的潜力为${band(cellular)}（${cellular}/100）。`,
    `跨菌株与人群覆盖的综合代理分为${band(populationCoverage)}（${populationCoverage}/100）。`,
    `安全性筛查代理分为${band(safety)}（${safety}/100）；这不是人体不良反应发生率。`,
  ];

  const evidenceGaps: string[] = [];
  if (inputs.evidenceStage === "computational") evidenceGaps.push("尚无体外、动物或人体免疫原性证据，不能推断真实保护效果。");
  if (inputs.evidenceStage === "in_vitro") evidenceGaps.push("体外证据不能替代动物攻毒或人体研究。");
  if (inputs.evidenceStage === "animal") evidenceGaps.push("动物保护信号不能直接外推人体剂量、持久性或保护率。");
  if (inputs.hlaCoverage < 60) evidenceGaps.push("HLA 人群覆盖偏低，需补充等位基因分层评估。");
  if (inputs.pathogenConservation < 60) evidenceGaps.push("跨菌株保守性不足，可能限制广谱覆盖。");
  if (inputs.humanHomologyRisk >= 35) evidenceGaps.push("人同源风险偏高，需进行序列和结构交叉反应复核。");
  if (inputs.allergenicityRisk >= 35 || inputs.toxicityRisk >= 35) evidenceGaps.push("过敏原性或毒性风险信号需要实验排查。");
  if (inputs.evidenceCompleteness < 70) evidenceGaps.push("证据完整度不足，综合分已做保守收缩。");

  return { humoral, cellular, populationCoverage, safety, protectionPotential, confidence, confidenceBand, effectSummary, evidenceGaps };
}
