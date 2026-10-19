"use client";
import type { Compound } from "@drug-discovery/contracts";
import { matchesFor, numberLabel } from "@/lib/compoundInsights";
import { useEffect, useState } from "react";
import { displayMoleculeName, diseaseLabel, mechanismLabel, moleculeTypeLabel, isBiologic } from "@/lib/compoundPresentation";
import { MoleculeStructure } from "./MoleculeStructure";
import { moleculeRequest } from "@/lib/moleculeRequest";
import { DrugDossier as Dossier, phaseLabel } from "@/lib/drugDossier";

export function DrugDossier({ identifier, savedSmiles, compact = false, embedded=false, compound }: { identifier: string; diseaseId?: string; savedSmiles?: string; compact?: boolean; embedded?:boolean; compound?:Compound }) {
  const [data,setData] = useState<Dossier | null>(null);
  const [busy,setBusy] = useState(compact);
  const [error,setError] = useState("");
  const [requested,setRequested] = useState(compact);
  const [attempt,setAttempt] = useState(0);
  const valid = /^CHEMBL\d+$/i.test(identifier);
  useEffect(()=>{
    if(!requested || !valid)return;
    let active=true;
    moleculeRequest<Dossier>(`/molecules/${encodeURIComponent(identifier)}/dossier`).then(value=>{if(active)setData(value);}).catch(e=>{if(active)setError(e instanceof Error ? e.message : "资料加载失败");}).finally(()=>{if(active)setBusy(false);});
    return ()=>{active=false;};
  },[identifier,requested,attempt,valid]);
  if(!valid)return null;
  const hasInfo=Boolean(data && ((data.max_phase ?? 0)>0 || data.indications?.length || data.mechanisms?.length));
  const matches = compound ? matchesFor(compound) : [];
  const moleculeType = moleculeTypeLabel(data?.molecule_type);
  const indications = data?.indications ?? [];
  const indicationItem = (r:NonNullable<Dossier['indications']>[number],i:number)=><li key={i}><strong>{diseaseLabel(r.efo_term || r.mesh_heading,r.efo_id || r.mesh_id)}</strong><span>{phaseLabel(r.max_phase_for_ind)}</span>{!!r.indication_refs?.length && <details><summary>查看依据</summary><small>{r.efo_id || r.mesh_id}</small>{r.indication_refs.map((x,j)=><p key={j}>{x.ref_type}: {x.ref_id}</p>)}</details>}</li>;
  const evidenceItems = matches.map((m,i)=><p key={`${m.target_chembl_id}-${i}`}><b>{m.target_symbol || m.target_name || m.target_chembl_id}</b>{m.mechanism ? ` · ${mechanismLabel(m.mechanism)}` : ""}<br/>{m.activity_count ?? "—"} 条活性记录 · {m.assay_count ?? "—"} 个实验 · {m.document_count ?? "—"} 篇文献{m.pchembl_median != null ? ` · pChEMBL 中位 ${numberLabel(m.pchembl_median)}` : ""}</p>);
  return <details className="drug-dossier" onToggle={e=>{if(e.target===e.currentTarget && e.currentTarget.open && !requested){setBusy(true);setRequested(true);}}}><summary>{embedded ? "药物详情" : compact ? "药物与研究资料" : data?.name ? `药物资料 · ${displayMoleculeName(identifier,data.name)}` : "药物资料"}</summary>
    {busy && <p role="status">查询中…</p>}
    {error && <p role="alert">{error}</p>}
    {data?.cache_state === "stale" && <small>显示已保存资料；后台更新中或暂未成功。</small>}
    {data?.warnings?.length ? <p>{data.warnings.join("；")}</p> : null}
    {(error || (data && data.status!=="available")) && <button className="text-button" disabled={busy} onClick={()=>{setBusy(true);setError("");setAttempt(a=>a+1);}}>重试</button>}
    {(data?.status === "available" || data?.status === "partial") && <>
      {!embedded && <p className="drug-identity-line">{!compact && identifier}{moleculeType ? `${compact ? "" : " · "}${moleculeType}` : ""} · {phaseLabel(data.max_phase)}{data.first_approval ? ` · ${data.first_approval} 年首次批准记录` : ""}</p>}
      {!compact && !embedded && !isBiologic(data.molecule_type) && <MoleculeStructure identifier={identifier} name={data.name || identifier} moleculeType={data.molecule_type}/>}
      {savedSmiles && data.canonical_smiles && savedSmiles!==data.canonical_smiles && <p className="notice warning">当前数据库结构与本批结构不同，请核对。</p>}
      {!!indications.length && <><h4>研究适应症</h4><ul className="drug-indications">{indications.slice(0,3).map(indicationItem)}</ul>{indications.length>3 && <details><summary>更多适应症 · {indications.length-3} 项</summary><ul className="drug-indications">{indications.slice(3).map(indicationItem)}</ul></details>}{(data?.indication_total ?? 0)>indications.length && <small>已载入 {indications.length}/{data?.indication_total} 项，完整记录见来源。</small>}</>}

      {!embedded && !!data.mechanisms?.length && <><h4>已记录作用机制</h4>{data.mechanisms.map((m,i)=><p key={i}><b>{mechanismLabel(m.mechanism_of_action) || m.action_type}</b></p>)}</>}
      {!!(data.black_box_warning || data.withdrawn_flag) && <p className="notice warning">{data.black_box_warning ? "有黑框警告记录。" : ""}{data.withdrawn_flag ? "有撤回记录。" : ""}请核对原始药品资料。</p>}
      {!hasInfo && data.status === "available" && <p>研究化合物 · ChEMBL 暂无临床适应症或药物机制记录。</p>}
    </>}
    {!!matches.length && <section className="molecule-research-evidence"><h4>靶点与实验依据</h4>{evidenceItems.slice(0,1)}{evidenceItems.length>1 && <details><summary>更多靶点依据 · {evidenceItems.length-1} 项</summary>{evidenceItems.slice(1)}</details>}</section>}

  </details>;
}
