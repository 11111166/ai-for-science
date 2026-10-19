"use client";

import { Activity, AlertTriangle, ChevronDown, Dna, Download, ShieldCheck, Sparkles, Syringe } from "lucide-react";
import { useMemo, useState } from "react";
import {
  evaluateFungalVaccine,
  type FungalVaccineInputs,
  type FungalVaccineResult,
  type VaccineEvidenceStage,
  type VaccinePlatform,
} from "@/lib/fungalVaccineModel";

type CandidateIdentity = {
  candidateId: string;
  name: string;
  species: string;
  strain: string;
  antigen: string;
  sequenceOrAccession: string;
};

type EvidenceProvenance = {
  biology: string;
  immunogenicity: string;
  safety: string;
  notes: string;
};

type EvaluationSnapshot = {
  schemaVersion: "fungal-vaccine-evaluation-v1";
  modelVersion: "transparent-weighted-score-v0.1";
  generatedAt: string;
  snapshotId: string;
  identity: CandidateIdentity;
  inputs: FungalVaccineInputs;
  provenance: EvidenceProvenance;
  result: FungalVaccineResult;
  boundary: string;
};

const neutralInputs: FungalVaccineInputs = {
  antigenAccessibility: 50,
  pathogenConservation: 50,
  infectionExpression: 50,
  virulenceEvidence: 50,
  bCellEpitope: 50,
  tCellEpitope: 50,
  hlaCoverage: 50,
  humanHomologyRisk: 20,
  allergenicityRisk: 20,
  toxicityRisk: 20,
  evidenceCompleteness: 35,
  evidenceStage: "computational",
  platform: "protein_alum",
};

const exampleInputs: FungalVaccineInputs = {
  antigenAccessibility: 84,
  pathogenConservation: 78,
  infectionExpression: 88,
  virulenceEvidence: 86,
  bCellEpitope: 76,
  tCellEpitope: 81,
  hlaCoverage: 72,
  humanHomologyRisk: 8,
  allergenicityRisk: 14,
  toxicityRisk: 8,
  evidenceCompleteness: 58,
  evidenceStage: "computational",
  platform: "protein_cpg",
};

const emptyIdentity: CandidateIdentity = { candidateId: "", name: "", species: "", strain: "", antigen: "", sequenceOrAccession: "" };
const exampleIdentity: CandidateIdentity = {
  candidateId: "FV-SC5314-ALS3-DEMO",
  name: "SC5314 · Als3 重组抗原候选（演示）",
  species: "Candida albicans",
  strain: "SC5314",
  antigen: "Als3 N-terminal region",
  sequenceOrAccession: "CGD: CAL00001745 / UniProt accession 待项目复核",
};

const emptyProvenance: EvidenceProvenance = { biology: "", immunogenicity: "", safety: "", notes: "" };
const exampleProvenance: EvidenceProvenance = {
  biology: "演示输入：SC5314 参考菌株与 Als3 功能背景；正式使用时请填写数据库版本、文献 DOI/PMID 或实验批次。",
  immunogenicity: "演示分值，不代表已运行 IEDB/NetMHCpan/BepiPred；正式使用时应记录算法、版本、HLA 集合与阈值。",
  safety: "演示分值，不代表已完成人同源、过敏原性或毒性验证。",
  notes: "教学演示快照；不得用于临床判断。",
};

const clinicalBoundary = "仅用于候选分流和实验优先级，不是人体保护率、临床疗效、安全性结论或接种建议。";

function stableSnapshotId(identity: CandidateIdentity, inputs: FungalVaccineInputs, provenance: EvidenceProvenance) {
  const text = JSON.stringify({ identity, inputs, provenance });
  let hash = 2166136261;
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return `FVE-${(hash >>> 0).toString(16).toUpperCase().padStart(8, "0")}`;
}

export function buildFungalVaccineSnapshot(identity: CandidateIdentity, inputs: FungalVaccineInputs, provenance: EvidenceProvenance, generatedAt = new Date().toISOString()): EvaluationSnapshot {
  return {
    schemaVersion: "fungal-vaccine-evaluation-v1",
    modelVersion: "transparent-weighted-score-v0.1",
    generatedAt,
    snapshotId: stableSnapshotId(identity, inputs, provenance),
    identity: { ...identity },
    inputs: { ...inputs },
    provenance: { ...provenance },
    result: evaluateFungalVaccine(inputs),
    boundary: clinicalBoundary,
  };
}

const stageLabels: Record<VaccineEvidenceStage, string> = {
  computational: "仅计算证据",
  in_vitro: "已有体外证据",
  animal: "已有动物证据",
  human_immunogenicity: "已有人体免疫原性数据",
};

const platformLabels: Record<VaccinePlatform, string> = {
  protein_alum: "重组蛋白 + 铝佐剂",
  protein_cpg: "重组蛋白 + CpG 类佐剂",
  mrna_lnp: "mRNA-LNP",
  viral_vector: "病毒载体",
};

const indicatorGroups: { title: string; description: string; fields: { key: keyof FungalVaccineInputs; label: string; hint: string; risk?: boolean }[] }[] = [
  {
    title: "抗原与病原体证据",
    description: "评价抗原是否在感染时可见、重要且能覆盖不同菌株。",
    fields: [
      { key: "antigenAccessibility", label: "抗原表面可及性", hint: "分泌、表面暴露或免疫系统可接触的证据" },
      { key: "pathogenConservation", label: "致病菌株保守性", hint: "目标物种/菌株间序列或结构保守程度" },
      { key: "infectionExpression", label: "感染期表达", hint: "感染相关转录组、蛋白组或体内表达证据" },
      { key: "virulenceEvidence", label: "毒力/保护相关性", hint: "敲除、感染模型或功能研究支持程度" },
    ],
  },
  {
    title: "免疫原性与人群覆盖",
    description: "评价体液和细胞免疫信号；输入应来自同一候选与明确版本的算法。",
    fields: [
      { key: "bCellEpitope", label: "B 细胞表位潜力", hint: "表位预测与结构可及性的综合标准化分" },
      { key: "tCellEpitope", label: "T 细胞表位潜力", hint: "MHC-I/MHC-II 结合及加工呈递的综合标准化分" },
      { key: "hlaCoverage", label: "HLA 人群覆盖", hint: "按目标人群 HLA 等位基因频率估计的覆盖代理" },
    ],
  },
  {
    title: "宿主安全性风险",
    description: "此处数值越高代表风险越高，模型会从安全性代理分中扣减。",
    fields: [
      { key: "humanHomologyRisk", label: "人同源/交叉反应风险", hint: "与人蛋白序列、结构或潜在表位的相似风险", risk: true },
      { key: "allergenicityRisk", label: "过敏原性风险", hint: "已知过敏原相似性与计算预警", risk: true },
      { key: "toxicityRisk", label: "毒性风险", hint: "毒性片段、非预期功能或制剂相关预警", risk: true },
    ],
  },
];

function ScoreCard({ label, score, detail }: { label: string; score: number; detail: string }) {
  const tone = score >= 70 ? "strong" : score >= 45 ? "medium" : "weak";
  return <article className={`fungal-vaccine-score ${tone}`}>
    <div><span>{label}</span><strong>{score}<small>/100</small></strong></div>
    <div className="fungal-vaccine-score-track" aria-label={`${label} ${score} 分`}><i style={{ width: `${score}%` }} /></div>
    <p>{detail}</p>
  </article>;
}

export function FungalVaccineEvaluation() {
  const [identity, setIdentity] = useState<CandidateIdentity>(emptyIdentity);
  const [inputs, setInputs] = useState<FungalVaccineInputs>(neutralInputs);
  const [provenance, setProvenance] = useState<EvidenceProvenance>(emptyProvenance);
  const [snapshot, setSnapshot] = useState<EvaluationSnapshot | null>(null);
  const [formError, setFormError] = useState("");
  const currentFingerprint = useMemo(() => stableSnapshotId(identity, inputs, provenance), [identity, inputs, provenance]);
  const result = snapshot?.result;
  const dirty = Boolean(snapshot && currentFingerprint !== snapshot.snapshotId);

  function updateScore(key: keyof FungalVaccineInputs, value: number) {
    setInputs((current) => ({ ...current, [key]: Math.max(0, Math.min(100, value)) }));
  }

  function loadExample() {
    setIdentity(exampleIdentity);
    setInputs(exampleInputs);
    setProvenance(exampleProvenance);
    setFormError("");
    setSnapshot(buildFungalVaccineSnapshot(exampleIdentity, exampleInputs, exampleProvenance));
  }

  function runEvaluation() {
    const missing = [!identity.candidateId && "候选编号", !identity.name && "候选名称", !identity.species && "真菌物种", !identity.antigen && "抗原/构建体"].filter(Boolean);
    if (missing.length) {
      setFormError(`请先补全：${missing.join("、")}。`);
      return;
    }
    setFormError("");
    setSnapshot(buildFungalVaccineSnapshot(identity, inputs, provenance));
  }

  function exportSnapshot() {
    if (!snapshot) return;
    const blob = new Blob([`${JSON.stringify(snapshot, null, 2)}\n`], { type: "application/json;charset=utf-8" });
    const href = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = href;
    anchor.download = `${snapshot.identity.candidateId || "fungal-vaccine"}-${snapshot.snapshotId}.json`;
    anchor.click();
    URL.revokeObjectURL(href);
  }

  return <section className="fungal-vaccine-workbench">
    <header className="fungal-vaccine-heading">
      <div><span>01 · 候选疫苗输入</span><h3>真菌疫苗虚拟评价</h3><p>用透明指标形成可复核的免疫反应假设，帮助候选分流和实验设计；不输出人体保护率或临床结论。</p></div>
      <button type="button" className="secondary-button compact" onClick={loadExample}><Sparkles size={15} />一键载入 SC5314 示例</button>
    </header>

    <div className="fungal-vaccine-boundary"><AlertTriangle size={17} /><p><strong>使用边界：</strong>结果仅表示输入指标在当前规则模型中的相对表现，不等于人接种后的真实有效性、安全性、不良反应概率或剂量建议。人体效果必须由规范的临床研究确认。</p></div>

    <div className="fungal-vaccine-layout">
      <div className="fungal-vaccine-form">
        <section className="fungal-vaccine-identity">
          <label><span>候选编号 *</span><input value={identity.candidateId} onChange={(event) => setIdentity({ ...identity, candidateId: event.target.value })} placeholder="如 FV-SC5314-ALS3-001" /></label>
          <label><span>候选名称 *</span><input value={identity.name} onChange={(event) => setIdentity({ ...identity, name: event.target.value })} placeholder="如 SC5314 · Als3 重组抗原候选" /></label>
          <label><span>真菌物种 *</span><input value={identity.species} onChange={(event) => setIdentity({ ...identity, species: event.target.value })} placeholder="如 Candida albicans" /></label>
          <label><span>菌株</span><input value={identity.strain} onChange={(event) => setIdentity({ ...identity, strain: event.target.value })} placeholder="如 SC5314；未知可留空" /></label>
          <label><span>抗原/构建体 *</span><input value={identity.antigen} onChange={(event) => setIdentity({ ...identity, antigen: event.target.value })} placeholder="蛋白、结构域或多表位构建体" /></label>
          <label><span>序列或数据库编号</span><input value={identity.sequenceOrAccession} onChange={(event) => setIdentity({ ...identity, sequenceOrAccession: event.target.value })} placeholder="FASTA 标识、UniProt/NCBI/CGD 编号或序列版本" /></label>
          <label><span>疫苗平台</span><select value={inputs.platform} onChange={(event) => setInputs({ ...inputs, platform: event.target.value as VaccinePlatform })}>{Object.entries(platformLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
          <label><span>最高证据阶段</span><select value={inputs.evidenceStage} onChange={(event) => setInputs({ ...inputs, evidenceStage: event.target.value as VaccineEvidenceStage })}>{Object.entries(stageLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        </section>

        {indicatorGroups.map((group) => <details className="fungal-vaccine-indicators" open key={group.title}>
          <summary><span><strong>{group.title}</strong><small>{group.description}</small></span><ChevronDown size={16} /></summary>
          <div>{group.fields.map((field) => <label key={field.key} className={field.risk ? "risk" : "benefit"}>
            <span><b>{field.label}</b><em>{field.hint}</em></span>
            <div><input aria-label={field.label} type="range" min={0} max={100} step={1} value={Number(inputs[field.key])} onChange={(event) => updateScore(field.key, Number(event.target.value))} /><input aria-label={`${field.label}数值`} className="fungal-vaccine-number" type="number" min={0} max={100} step={1} value={Number(inputs[field.key])} onChange={(event) => updateScore(field.key, Number(event.target.value))} /></div>
          </label>)}</div>
        </details>)}

        <label className="fungal-vaccine-completeness"><span><b>证据完整度</b><em>已完成、可追溯且适用于该候选的指标比例</em></span><div><input aria-label="证据完整度" type="range" min={0} max={100} value={inputs.evidenceCompleteness} onChange={(event) => updateScore("evidenceCompleteness", Number(event.target.value))} /><input aria-label="证据完整度数值" className="fungal-vaccine-number" type="number" min={0} max={100} value={inputs.evidenceCompleteness} onChange={(event) => updateScore("evidenceCompleteness", Number(event.target.value))} /></div></label>
        <details className="fungal-vaccine-provenance" open>
          <summary><span><strong>证据来源与计算记录</strong><small>建议填写数据库/算法版本、阈值、PMID/DOI 或实验批次，便于复核。</small></span><ChevronDown size={16} /></summary>
          <div>
            <label><span>病原体生物学来源</span><textarea rows={2} value={provenance.biology} onChange={(event) => setProvenance({ ...provenance, biology: event.target.value })} placeholder="如 CGD 版本、感染转录组 accession、毒力实验 PMID…" /></label>
            <label><span>表位/HLA 计算来源</span><textarea rows={2} value={provenance.immunogenicity} onChange={(event) => setProvenance({ ...provenance, immunogenicity: event.target.value })} placeholder="如工具名、版本、HLA 集合、阈值与输出文件…" /></label>
            <label><span>安全性筛查来源</span><textarea rows={2} value={provenance.safety} onChange={(event) => setProvenance({ ...provenance, safety: event.target.value })} placeholder="如人蛋白组版本、比对阈值、过敏原/毒性数据库版本…" /></label>
            <label><span>补充说明</span><textarea rows={2} value={provenance.notes} onChange={(event) => setProvenance({ ...provenance, notes: event.target.value })} placeholder="记录数据缺口、人工调整理由和实验条件。" /></label>
          </div>
        </details>
        {formError && <p className="fungal-vaccine-form-error" role="alert"><AlertTriangle size={15} />{formError}</p>}
        <button type="button" className="primary-button fungal-vaccine-run" onClick={runEvaluation}><Syringe size={17} />生成并冻结评价快照</button>
      </div>

      <aside className="fungal-vaccine-method">
        <span>模型说明</span><h4>透明加权评分 v0.1</h4><p>分别计算体液应答、细胞应答、人群覆盖和安全性代理，再合成保护潜力；证据不完整时主动收缩结果。</p>
        <dl><div><dt>保护潜力</dt><dd>免疫原性 + 病原体生物学 + 覆盖 + 安全</dd></div><div><dt>置信度上限</dt><dd>{stageLabels[inputs.evidenceStage]}</dd></div><div><dt>适用场景</dt><dd>候选比较、证据缺口识别、实验优先级</dd></div><div><dt>不适用场景</dt><dd>临床疗效、接种建议、保护率或不良反应预测</dd></div></dl>
      </aside>
    </div>

    {snapshot && result && <section className="fungal-vaccine-results" aria-live="polite">
      {dirty && <p className="fungal-vaccine-dirty"><AlertTriangle size={15} />当前输入已修改；下方仍显示已冻结快照。请重新生成后再导出。</p>}
      <header><div><span>02 · 虚拟评价结果</span><h3>{snapshot.identity.name || snapshot.identity.antigen || "未命名真菌疫苗候选"}</h3><p>{[snapshot.identity.candidateId, snapshot.identity.species, snapshot.identity.strain, snapshot.identity.antigen].filter(Boolean).join(" · ")}</p></div><div className="fungal-vaccine-confidence"><small>结果置信度</small><strong>{result.confidenceBand} · {result.confidence}/100</strong><span>{stageLabels[snapshot.inputs.evidenceStage]}</span></div></header>
      <div className="fungal-vaccine-protection"><ShieldCheck size={22} /><div><small>候选保护潜力（相对评分）</small><strong>{result.protectionPotential}<span>/100</span></strong><p>用于同一模型和同一输入规范下的候选排序，不是人体保护率。</p></div></div>
      <div className="fungal-vaccine-score-grid">
        <ScoreCard label="抗体应答潜力" score={result.humoral} detail="抗原可及性、B 细胞表位、保守性与平台加权" />
        <ScoreCard label="细胞免疫潜力" score={result.cellular} detail="T 细胞表位、感染表达、毒力证据与平台加权" />
        <ScoreCard label="人群/菌株覆盖" score={result.populationCoverage} detail="病原体保守性与 HLA 覆盖的联合代理" />
        <ScoreCard label="安全性代理" score={result.safety} detail="人同源、过敏原性和毒性风险的反向评分" />
      </div>
      <div className="fungal-vaccine-interpretation">
        <article><Activity size={18} /><div><h4>推测的人体免疫反应</h4><ul>{result.effectSummary.map((item) => <li key={item}>{item}</li>)}</ul></div></article>
        <article className="gaps"><AlertTriangle size={18} /><div><h4>证据缺口与下一步</h4>{result.evidenceGaps.length ? <ul>{result.evidenceGaps.map((item) => <li key={item}>{item}</li>)}</ul> : <p>当前输入未触发主要计算预警；仍需按研发阶段完成体外、动物和人体研究。</p>}</div></article>
      </div>
      {(!snapshot.provenance.biology || !snapshot.provenance.immunogenicity || !snapshot.provenance.safety) && <p className="fungal-vaccine-source-warning"><AlertTriangle size={14} />证据来源记录不完整：结果可以用于内部草筛，但不应进入正式候选比较或报告。</p>}
      <details className="fungal-vaccine-trace"><summary><Dna size={15} />查看评分口径与可追溯输入 <ChevronDown size={14} /></summary><div><p><b>快照：</b>{snapshot.snapshotId}　<b>生成时间：</b>{new Date(snapshot.generatedAt).toLocaleString("zh-CN")}　<b>模型：</b>{snapshot.modelVersion}</p><p><b>平台：</b>{platformLabels[snapshot.inputs.platform]}　<b>证据阶段：</b>{stageLabels[snapshot.inputs.evidenceStage]}　<b>证据完整度：</b>{snapshot.inputs.evidenceCompleteness}/100</p><p>保护潜力 = 体液 22% + 细胞 25% + 覆盖 16% + 感染期表达 13% + 毒力证据 14% + 安全性 10%，再按证据完整度进行保守收缩。所有输入均保留为独立指标，不把缺失项伪装成实验数据。</p></div></details>
      <div className="fungal-vaccine-result-actions"><button type="button" className="secondary-button compact" onClick={exportSnapshot} disabled={dirty}><Download size={15} />导出可追溯 JSON</button><small>{dirty ? "输入修改后需重新生成，才能导出。" : `${snapshot.snapshotId} · ${clinicalBoundary}`}</small></div>
    </section>}
  </section>;
}
