"use client";
import { displayMoleculeName, mechanismLabel } from "@/lib/compoundPresentation";
import { apiBase } from "@/lib/api";

import { MoleculeIdentity } from "./MoleculeIdentity";
import { DrugDossier } from "./DrugDossier";
import { useState } from "react";
import type { Compound } from "@drug-discovery/contracts";
import { compoundDetails, endpoints, finite, matchesFor, numberLabel, predictionCount, predictionsFor } from "@/lib/compoundInsights";

export function MoleculeDetails({ compound, diseaseId }: { compound: Compound; diseaseId?: string }) {
  const [opened, setOpened] = useState(false);
  const [failed, setFailed] = useState(false);
  const details = compoundDetails(compound);
  const identifier = /^CHEMBL\d+$/i.test(compound.identifier) ? compound.identifier.toUpperCase() : null;
  const image = identifier ? `${apiBase}/molecules/${identifier}/structure` : null;
  return <details className="molecule-detail" onToggle={e=>{if(e.target===e.currentTarget)setOpened(e.currentTarget.open);}}><summary>结构与关联药物</summary>
    {image && !failed ? <figure className="compound-structure-figure">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={image} alt={`${compound.name}（${identifier}）二维分子结构`} width={400} height={300} loading="lazy" referrerPolicy="no-referrer" onError={() => setFailed(true)} />
      <figcaption>二维结构 · <a href={image} target="_blank" rel="noreferrer">打开大图</a></figcaption>
    </figure> : <div className="compound-structure-fallback">结构图暂不可用；可参考下方 SMILES。{image && <button className="text-button" onClick={() => setFailed(false)}>重试结构图</button>}</div>}
    {identifier && <a href={`https://www.ebi.ac.uk/chembl/compound_report_card/${identifier}/`} target="_blank" rel="noreferrer">查看 ChEMBL 分子记录</a>}
    {opened && <MoleculeIdentity key={`identity-${compound.identifier}`} identifier={compound.identifier} name={compound.name} smiles={details.canonical_smiles} weight={details.descriptors?.molecular_weight}/>}
    {opened && !compound.demo && <DrugDossier key={`dossier-${compound.identifier}`} compound={compound} identifier={compound.identifier} diseaseId={diseaseId} savedSmiles={details.canonical_smiles} compact />}
  </details>;
}

export function MoleculeTargetDetails({ compound }: { compound: Compound }) {
  const matches = matchesFor(compound);
  return <details><summary>全部 {matches.length} 个靶点与机制依据</summary>{matches.map((m, i) => <section className="molecule-target-evidence" key={`${m.target_chembl_id}-${i}`}>
    <b>{m.target_symbol ?? "靶点未明"} · {m.target_chembl_id ?? "未提供标识"}</b>
    <p>{m.target_name}</p>
    <p>pChEMBL 中位 / 最佳：{numberLabel(m.pchembl_median)} / {numberLabel(m.pchembl_best)}；实验类型：{m.activity_types?.join("、") || "未提供"}</p>
    <p>{m.assay_count ?? "—"} 个 assay · {m.document_count ?? "—"} 篇文献 · {m.activity_count ?? "—"} 条活性记录</p>
    {m.mechanism && <p>机制：{mechanismLabel(m.mechanism)}</p>}
    {m.direction_note && !m.direction_note.includes("未知") && !m.direction_note.includes("暂无") && <p>{m.direction_note}</p>}
  </section>)}</details>;
}

export function MoleculePredictions({ compound }: { compound: Compound }) {
  const values = predictionsFor(compound);
  const count = predictionCount(compound);
  if (!count) return null;
  return <><strong>{count === 5 ? "五项预测齐全" : count ? `已有 ${count}/5 项预测` : "五项 ADMET 待计算"}</strong>
    <details><summary>逐项数值与适用范围</summary>{endpoints.filter(({ id }) => finite(values[id]?.value)).map(({ id, label, unit }) => {
      const value = values[id];
      const available = finite(value?.value);
      return <section className="molecule-target-evidence" key={id}><b>{label}</b><p>{available ? `${numberLabel(value.value)} · ${unit}` : "待计算"}</p><p>{!available ? "" : value.domain === "inside" ? "适用域内" : value.domain === "outside" ? "适用域外 · 优先复核" : value.domain === "borderline" ? "适用域边缘 · 谨慎解释" : "适用域尚未检查"}{available && finite(value.applicability_similarity) ? ` · 最近训练结构相似度 ${numberLabel(value.applicability_similarity)}` : ""}</p></section>;
    })}<p>各端点按原尺度显示，不能合并为安全百分比；预测不代表人体安全概率。</p></details></>;
}

export function MoleculeActivityChart({ compounds, target }: { compounds: Compound[]; target: string }) {
  if (!target) return <p>先在上方选择一个靶点，再比较该靶点下分子的体外活性，避免跨靶点误比。</p>;
  const rows = compounds.filter((c) => !c.demo).map((compound) => ({ compound, match: matchesFor(compound, target)[0] })).filter((row) => finite(row.match?.pchembl_median)).sort((a, b) => b.match!.pchembl_median! - a.match!.pchembl_median! || a.compound.identifier.localeCompare(b.compound.identifier));
  const low = Math.min(0, ...rows.map((r) => r.match!.pchembl_median!));
  const high = Math.max(10, ...rows.map((r) => r.match!.pchembl_median!));
  return <section className="molecule-activity-chart" aria-label={`${target} 体外活性比较`}>
    <p>{target} · 当前筛选中有数值的 {rows.length} 个真实分子，仅展示中位 pChEMBL 最高的 10 个；条形刻度 {low}–{high}，越右数值越高。</p>
    <p>仅比较有数值的真实记录；实验条件可能不同，不代表疗效排名。</p>
    {rows.slice(0, 10).map(({ compound, match }) => <a href={`#molecule-${compound.id}`} key={compound.id} className="molecule-activity-row"><span>{displayMoleculeName(compound.identifier,compound.name)}<small>{compound.identifier}</small></span><span className="molecule-activity-track" aria-hidden="true"><i style={{ width: `${100 * (match!.pchembl_median! - low) / (high - low)}%` }} /></span><b>{numberLabel(match!.pchembl_median)}</b><small>{match?.document_count ?? "—"} 篇文献</small></a>)}
    {!rows.length && <p>当前筛选范围没有可比较的数值。</p>}
  </section>;
}
