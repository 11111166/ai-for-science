"use client";
import { apiBase } from "@/lib/api";
import { isBiologic, moleculeTypeLabel } from '@/lib/compoundPresentation';
import { useState } from 'react';
export function MoleculeStructure({identifier,name,moleculeType}:{identifier:string;name:string;moleculeType?:string}) {
 const [failed,setFailed]=useState(false);const [attempt,setAttempt]=useState(0);
 if(!/^CHEMBL\d+$/i.test(identifier))return <p>结构身份未确认</p>;
 if(isBiologic(moleculeType))return <p>{moleculeTypeLabel(moleculeType)} · 不适用二维小分子结构图<br/><a href={`https://www.ebi.ac.uk/chembl/explore/compound/${identifier}`} target="_blank" rel="noreferrer">查看生物分子记录</a></p>;
 const url=`${apiBase}/molecules/${identifier}/structure`;
 return <figure className="dossier-structure">{failed ? <><p>结构图暂不可用</p><button className="text-button" onClick={()=>{setAttempt(a=>a+1);setFailed(false);}}>重试结构</button></> :
 // eslint-disable-next-line @next/next/no-img-element
 <img key={attempt} src={url} alt={`${name} 二维结构`} width={180} height={140} loading="lazy" onError={()=>setFailed(true)}/>}<figcaption><a href={`https://www.ebi.ac.uk/chembl/explore/compound/${identifier}`} target="_blank" rel="noreferrer">核对原始分子记录</a></figcaption></figure>;
}
