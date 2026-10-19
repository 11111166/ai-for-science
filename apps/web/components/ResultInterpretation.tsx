"use client";
import {MoleculeValidation} from "./MoleculeValidation";
import {useEffect,useState} from 'react';
import type {Compound} from '@drug-discovery/contracts';
import {api} from '@/lib/api';
import {interpretCompound} from '@/lib/resultInterpretation';
import {displayMoleculeName} from '@/lib/compoundPresentation';
import type {QsarResult} from './QsarPanel';
export function useProjectQsar(projectId:string){
 const [state,setState]=useState<{id:string;data:QsarResult}|null>(null);const [error,setError]=useState('');const [attempt,setAttempt]=useState(0);
 useEffect(()=>{let active=true;api<QsarResult>(`/projects/${projectId}/qsar`).then(data=>{if(active){setState({id:projectId,data});setError('');}}).catch(()=>{if(active)setError('QSAR 暂不可用，以下解读仅使用现有实验与性质资料。');});return()=>{active=false;};},[projectId,attempt]);
 return {data:state?.id===projectId?state.data:null,error,retry:()=>setAttempt(x=>x+1)};
}
export function CompoundReasoning({compound,target,qsar,compact=true}:{compound:Compound;target?:string;qsar?:QsarResult|null;compact?:boolean}){
 const r=interpretCompound(compound,target,qsar);
 return <div className="result-reasoning"><strong>{r.title}</strong>{!!r.support.length&&<><b>依据</b><ul>{r.support.slice(0,compact?1:undefined).map(x=><li key={x}>{x}</li>)}</ul></>} {!!r.issues.length&&<><b>主要不足</b><ul>{r.issues.slice(0,compact?1:undefined).map(x=><li key={x}>{x}</li>)}</ul></>}<b>下一步</b><ol>{r.next.slice(0,compact?1:3).map(x=><li key={x}>{x}</li>)}</ol>{compact && (r.support.length>1 || r.issues.length>1 || r.next.length>1) && <details><summary>更多依据与建议</summary>{r.support.length>1&&<><b>补充依据</b><ul>{r.support.slice(1).map(x=><li key={x}>{x}</li>)}</ul></>}{r.issues.length>1&&<><b>其他缺口与冲突</b><ul>{r.issues.slice(1).map(x=><li key={x}>{x}</li>)}</ul></>}{r.next.length>1&&<><b>后续验证</b><ol>{r.next.slice(1,3).map(x=><li key={x}>{x}</li>)}</ol></>}</details>}<MoleculeValidation compound={compound} target={target}/></div>;
}
export function CompoundReadout({compounds,target,qsar}:{compounds:Compound[];target?:string;qsar?:QsarResult|null}){
 if(!compounds.length)return null;
 return <details className="technical-details result-readout" open><summary>本批结果解读 · 当前列表前 {Math.min(3,compounds.length)} 个</summary><p>按当前列表顺序复核，不额外生成疗效排名。切换筛选后同步更新。</p><div className="result-readout-grid">{compounds.slice(0,3).map(c=><article key={c.id}><h4><a href={`#molecule-${c.id}`}>{displayMoleculeName(c.identifier,c.name)}</a></h4><CompoundReasoning compound={c} target={target} qsar={qsar}/></article>)}</div></details>;
}
