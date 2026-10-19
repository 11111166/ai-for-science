"use client";
import { useEffect, useState } from "react";
import type { Compound } from "@drug-discovery/contracts";
import { CompoundMatch, compoundDetails, developmentStage, endpoints, finite, matchesFor, numberLabel, predictionsFor } from "@/lib/compoundInsights";
import { comparableExperiments, comparisonEndpoints } from "@/lib/moleculeComparison";
import { MoleculeValidation } from "./MoleculeValidation";
import { commonTargets } from "@/lib/drugDossier";
import { displayMoleculeName, mechanismLabel } from "@/lib/compoundPresentation";
import { moleculeRequest } from "@/lib/moleculeRequest";
import { api } from "@/lib/api";
import type { QsarResult } from "./QsarPanel";
import { MoleculeStructure } from "./MoleculeStructure";
import { DrugDossier } from "./DrugDossier";
type Identity={cas_numbers?:string[];cas_source?:string;pubchem_cid?:number;status:string};
export function MoleculeComparison({ compounds, remove, clear, diseaseId, projectId }: { compounds: Compound[]; remove: (id:string)=>void; clear:()=>void; diseaseId?:string;projectId?:string }) {
 const [endpoint,setEndpoint]=useState('');const [target,setTarget]=useState('');const [open,setOpen]=useState(false);
 const [identities,setIdentities]=useState<Record<string,Identity>>({});const [qsar,setQsar]=useState<{project:string;data:QsarResult}|null>(null);const [error,setError]=useState('');const [qsarError,setQsarError]=useState('');const [attempt,setAttempt]=useState(0);
 const [experiments,setExperiments]=useState<Record<string,{status:string;items:NonNullable<CompoundMatch['experimental_records']>;total?:number}>>({});
 const [experimentError,setExperimentError]=useState('');const [experimentBatch,setExperimentBatch]=useState('');
 const identifiers=compounds.map(c=>c.identifier).sort().join(',');
 useEffect(()=>{if(!open)return;let active=true;
 Promise.allSettled(identifiers.split(',').filter(id=>/^CHEMBL\d+$/i.test(id)).map(async id=>[id,await moleculeRequest<Identity>(`/molecules/${id}/identity`)] as const)).then(rows=>{if(!active)return;setIdentities(Object.fromEntries(rows.flatMap(r=>r.status==='fulfilled'?[r.value]:[])));setError(rows.some(r=>r.status==='rejected' || r.value[1].status!=='available')?'部分分子标识暂未完整取回，可重试。':'');});
 return()=>{active=false;};},[open,identifiers,attempt]);
 useEffect(()=>{if(!open || !projectId)return;let active=true;api<QsarResult>(`/projects/${projectId}/qsar`).then(data=>{if(active){setQsar({project:projectId,data});setQsarError('');}}).catch(()=>{if(active)setQsarError('QSAR 查询失败，可重试。');});return()=>{active=false;};},[open,projectId,attempt]);
 const shared=commonTargets(compounds);const active=shared.includes(target)?target:shared[0] || '';
 const targetsKey=compounds.map(c=>`${c.identifier}:${matchesFor(c,active)[0]?.target_chembl_id || ''}`).sort().join(',');
 useEffect(()=>{if(!open || !active)return;let mounted=true;
 Promise.allSettled(targetsKey.split(',').filter(x=>/^CHEMBL\d+:CHEMBL\d+$/.test(x)).map(async pair=>{
  const [id,tid]=pair.split(':');return [pair,await moleculeRequest<{status:string;items:NonNullable<CompoundMatch['experimental_records']>;total?:number}>(`/molecules/${id}/activities?target=${tid}`)] as const;
 })).then(results=>{if(!mounted)return;setExperimentBatch(targetsKey);setExperiments(Object.fromEntries(results.flatMap(r=>r.status==='fulfilled'?[r.value]:[])));setExperimentError(results.some(r=>r.status==='rejected' || r.value[1].status!=='available')?'部分实验记录未取回，保留本批已有记录。':'');});
 return()=>{mounted=false;};},[open,active,targetsKey,attempt]);
 const compared=compounds.map(c=>{const m=matchesFor(c,active)[0],result=experiments[`${c.identifier}:${m?.target_chembl_id || ''}`];return result?.status==='available'?{...c,details:{...compoundDetails(c),target_matches:matchesFor(c).map(row=>row.target_symbol===active?{...row,experimental_records:result.items}:row)}}:c;});
 const model=qsar?.project===projectId ? qsar?.data.models.find(m=>m.symbol===active && m.status==='validated') : undefined;
 const prediction=(c:Compound)=>model?.predictions.find(p=>p.compound_id===c.id && p.identifier===c.identifier);
 const matches=compared.map(c=>active ? matchesFor(c,active)[0] : undefined);
 const endpointOptions=comparisonEndpoints(compared,active);
 const activeEndpoint=endpointOptions.includes(endpoint)?endpoint:endpointOptions[0] || '';
 const groups=active?comparableExperiments(compared,active,activeEndpoint):[];
 const hasQsar=compounds.some(c=>finite(prediction(c)?.predicted));
 const hasCas=compounds.some(c=>identities[c.identifier]?.cas_numbers?.length);
 const hasCid=compounds.some(c=>identities[c.identifier]?.pubchem_cid);
 const hasAlerts=compounds.some(c=>finite(compoundDetails(c).descriptors?.structural_alert_count));
 const maxDocuments=Math.max(0,...matches.map(m=>m?.document_count || 0));
 return <section className="molecule-comparison" aria-label="分子对比">
 <div className="comparison-toolbar"><div><b>分子对比 · 已选 {compounds.length}/5</b><p>选择 3–5 个分子，筛选时保留已选。</p></div><button className="secondary-button" disabled={compounds.length<3} onClick={()=>setOpen(!open)}>{open?'收起对比':'展开分子对比'}</button><button className="text-button" disabled={!compounds.length} onClick={clear}>清空对比</button></div>
 <div className="comparison-selection">{compounds.map(c=><button className="text-button" key={c.id} onClick={()=>remove(c.id)}>移除 {displayMoleculeName(c.identifier,c.name)} ×</button>)}</div>
 {open && compounds.length>=3 && <><label>共同靶点<select aria-label="对比共同靶点" value={active} onChange={e=>setTarget(e.target.value)}>{!shared.length && <option value="">无共同靶点</option>}{shared.map(t=><option key={t}>{t}</option>)}</select></label>
 <p>{active?`${active}：先比较同终点、同一实验记录，再核对重复性与选择性。`:'没有共同靶点，仅比较身份、结构与开发阶段。'}</p>
 {active && <section className="molecule-target-evidence"><b>本次比较结论</b><p>{experimentBatch!==targetsKey ? '正在核对共同实验记录…' : groups.length ? `找到 ${groups.length} 组共同实验，可比较 ${activeEndpoint}；较低值表示该实验条件下更强的活性。` : '当前记录不足以作直接活性排序；先在统一条件下复测，再决定优先分子。'}</p>{endpointOptions.length>0 && <label>实验终点<select aria-label="对比实验终点" value={activeEndpoint} onChange={e=>setEndpoint(e.target.value)}>{endpointOptions.map(e=><option key={e}>{e}</option>)}</select></label>}<small>每个分子最多取回 100 条实验；同一实验中的多条记录不自动视为独立重复；活性强弱不代表选择性或疾病获益。</small></section>}

 {maxDocuments>0 && <p>文献记录最多：{compounds.filter((_,i)=>matches[i]?.document_count===maxDocuments).map(c=>displayMoleculeName(c.identifier,c.name)).join('、')}（{maxDocuments} 篇）。数量不等同于证据质量。</p>}
 {hasQsar && <p>QSAR 终点 pIC50 · 骨架测试 MAE {numberLabel(model?.metrics?.mae)}；预测与实测分开列示。</p>}
 {(error || qsarError || experimentError) && <p role="alert">{error || qsarError || experimentError}<button className="text-button" onClick={()=>{setError('');setQsarError('');setAttempt(a=>a+1);}}>重试</button></p>}
 <div className="table-card comparison-scroll"><table><thead><tr><th>比较项目</th>{compounds.map(c=><th key={c.id}>{displayMoleculeName(c.identifier,c.name)}<small>{c.identifier}</small></th>)}</tr></thead><tbody>
 <tr><th>二维结构与药物资料</th>{compounds.map(c=><td key={c.id}><MoleculeStructure identifier={c.identifier} name={c.name} moleculeType={compoundDetails(c).molecule_type}/><DrugDossier compact compound={c} identifier={c.identifier} diseaseId={diseaseId} savedSmiles={compoundDetails(c).canonical_smiles}/></td>)}</tr>
 {hasCas && <tr><th>CAS 号</th>{compounds.map(c=>{const i=identities[c.identifier];return <td key={c.id}>{i?.cas_numbers?.length?<><a href={i.cas_source} target="_blank" rel="noreferrer">{i.cas_numbers.join(' / ')}</a>{i.cas_numbers.length>1 && <small>多个来源登记号，需核对具体形式</small>}</>:'—'}</td>;})}</tr>}
 {hasCid && <tr><th>PubChem CID</th>{compounds.map(c=><td key={c.id}>{identities[c.identifier]?.pubchem_cid?<a href={`https://pubchem.ncbi.nlm.nih.gov/compound/${identities[c.identifier].pubchem_cid}`} target="_blank" rel="noreferrer">{identities[c.identifier].pubchem_cid}</a>:'—'}</td>)}</tr>}
 {groups.map(g=><tr key={g.assay}><th>{activeEndpoint} · nM<small>共同实验 {g.assay}</small></th>{g.values.map((v,i)=><td key={compounds[i].id}><b>{numberLabel(v.median)}</b><small>{v.count} 条记录，中位数</small></td>)}</tr>)}
 <tr><th>开发阶段<small>数据库最高阶段，非本病获批结论</small></th>{compounds.map(c=><td key={c.id}>{developmentStage(c).label}</td>)}</tr>
 {active && <>{matches.some(m=>finite(m?.pchembl_median)||finite(m?.pchembl_best)||m?.experimental_records?.length) && <tr><th>历史活性汇总<small>不同实验不可直接排序 · 中位 / 最佳 pChEMBL</small></th>{compounds.map((c,i)=><td key={c.id}>{(finite(matches[i]?.pchembl_median)||finite(matches[i]?.pchembl_best)) && <b>{numberLabel(matches[i]?.pchembl_median)} / {numberLabel(matches[i]?.pchembl_best)}</b>}{matches[i]?.activity_types?.length ? <small>{matches[i]?.activity_types?.join(' / ')}</small>:null}{!!matches[i]?.experimental_records?.length && <details><summary>查看取回的实验记录</summary>{matches[i]?.experimental_records?.map((r,j)=><p key={j}>{r.standard_type} {r.standard_relation} {r.standard_value} {r.standard_units}<small>{r.assay_chembl_id}</small></p>)}</details>}</td>)}</tr>}
 {matches.some(m=>finite(m?.assay_count)||finite(m?.document_count)) && <tr><th>实验依据<small>实验数 / 文献数</small></th>{compounds.map((c,i)=><td key={c.id}>{matches[i]?.assay_count??'—'} / {matches[i]?.document_count??'—'}</td>)}</tr>}
 {matches.some(m=>m?.mechanism) && <tr><th>机制</th>{compounds.map((c,i)=><td key={c.id}>{mechanismLabel(matches[i]?.mechanism)||'—'}</td>)}</tr>}</>}
 {hasQsar && <><tr><th>QSAR 预测 pIC50</th>{compounds.map(c=>{const p=prediction(c);return <td key={c.id}>{numberLabel(p?.predicted)}{finite(p?.predicted) && <small>{p?.prediction_kind==='scaffold_holdout'?'独立骨架测试':p?.prediction_kind==='scaffold_cv'?'骨架交叉验证':'新结构预测'}</small>}</td>;})}</tr><tr><th>QSAR 结构适用范围</th>{compounds.map(c=>{const p=prediction(c);return <td key={c.id}>{finite(p?.predicted)&&finite(p?.similarity)?`${p?.domain==='inside'?'域内':'域外 · 谨慎使用'} · 相似度 ${numberLabel(p.similarity)}`:'—'}</td>;})}</tr></>}
 {([['molecular_weight','分子量 g/mol'],['logp','脂溶性 logP'],['tpsa','极性表面积 Å²'],['qed','类药性 QED']] as const).filter(([id])=>compounds.some(c=>finite(compoundDetails(c).descriptors?.[id]))).map(([id,label])=><tr key={id}><th>{label}</th>{compounds.map(c=><td key={c.id}>{numberLabel(compoundDetails(c).descriptors?.[id])}</td>)}</tr>)}
 {endpoints.filter(e=>compounds.some(c=>finite(predictionsFor(c)[e.id]?.value))).map(e=><tr key={e.id}><th>{e.label}<small>{e.unit}</small></th>{compounds.map(c=>{const v=predictionsFor(c)[e.id];return <td key={c.id}>{numberLabel(v?.value)}{finite(v?.value)&&<small>{v?.domain==='inside'?'适用域内':v?.domain==='outside'?'适用域外':'适用域需核对'}</small>}</td>;})}</tr>)}
 {hasAlerts && <tr><th>结构警示</th>{compounds.map(c=><td key={c.id}>{compoundDetails(c).descriptors?.structural_alert_count??'—'}</td>)}</tr>}
 {active && <tr><th>用途与验证优先项</th>{compounds.map(c=><td key={c.id}><MoleculeValidation compound={c} target={active}/></td>)}</tr>}
 </tbody></table></div></>}
 </section>;
}
