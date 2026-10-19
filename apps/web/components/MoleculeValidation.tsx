"use client";
import {useState} from "react";
import {matchesFor} from "@/lib/compoundInsights";
import type { Compound } from '@drug-discovery/contracts';
import { moleculeValidation } from '@/lib/moleculeValidation';
export function MoleculeValidation({compound,target}:{compound:Compound;target?:string}) {
 const options=[...new Set(matchesFor(compound).map(m=>m.target_symbol).filter((x):x is string=>!!x))];
 const [selected,setSelected]=useState('');
 const [copied,setCopied]=useState('');
 const active=options.includes(target || '')?target:options.includes(selected)?selected:options[0];
 const p=moleculeValidation(compound,active);
 if(compound.demo)return null;
 return <section className="molecule-validation">
  <p><b>{p.purpose}</b> · {p.targetName}</p>
  <small>{p.roleBasis}</small>
  {p.flagged && <p className="notice warning">{p.blocker}</p>}
  <details><summary>验证建议 · {p.step}</summary>
   {options.length>1 && !target && <label>验证靶点<select aria-label={`验证靶点 ${compound.identifier}`} value={active} onChange={e=>{setSelected(e.target.value);setCopied('');}}>{options.map(x=><option key={x}>{x}</option>)}</select></label>}
   <p><b>依据：</b>{p.reasons.join("；")}</p>
   <p><b>主要疑点：</b>{p.doubt}</p>
   <p><b>建议：</b>{p.decision}</p>
   <p><b>要回答：</b>{p.question}</p><p>{p.effect}</p>
   {!p.flagged && <p><b>当前缺口：</b>{p.blocker}</p>}
   <p><b>模型：</b>{p.experiment.model}</p><p><b>对照：</b>{p.experiment.controls}</p>
   <p><b>读出：</b>{p.experiment.readouts}</p><p><b>推进条件：</b>{p.experiment.criteria}</p>
   <p><b>暂缓条件：</b>{p.stop}</p>
   <button className="text-button" onClick={async()=>{try{await navigator.clipboard.writeText(p.summary);setCopied("已复制");}catch{setCopied("复制失败，请选择摘要文本复制");}}}>{copied || "复制验证摘要"}</button>
   <small>基于本批记录的实验建议，需按项目确认方案。</small>
  </details>
 </section>;
}
