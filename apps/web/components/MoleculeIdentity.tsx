"use client";
import { displayMoleculeName } from "@/lib/compoundPresentation";
import { useEffect,useState } from 'react';
import { moleculeRequest } from '@/lib/moleculeRequest';
import { numberLabel } from '@/lib/compoundInsights';
type Identity={cache_state?:string;cas_source?:string;cas_source_label?:string;status:string;name?:string;molecular_formula?:string;molecular_weight?:number;smiles?:string;pubchem_cid?:number;cas_numbers?:string[];warnings?:string[]};
export function MoleculeIdentity({identifier,name,smiles,weight}:{identifier:string;name:string;smiles?:string;weight?:number}) {
 const [data,setData]=useState<Identity|null>(null);const [error,setError]=useState('');const [attempt,setAttempt]=useState(0);const [copy,setCopy]=useState('');
 useEffect(()=>{if(!/^CHEMBL\d+$/i.test(identifier))return;let active=true;moleculeRequest<Identity>(`/molecules/${identifier}/identity`).then(r=>{if(active)setData(r);}).catch(()=>{if(active)setError('标识查询暂不可用');});return()=>{active=false;};},[identifier,attempt]);
 const structure=data?.smiles || smiles;
 return <section className="compound-identity" aria-label="化合物基本信息"><table><tbody>
 <tr><th>分子名称</th><td>{displayMoleculeName(identifier,data?.name || name)}</td></tr>
 <tr><th>CAS 号</th><td>{data?.cas_numbers?.length ? data.cas_numbers.join(' / ') : data ? data.status==='available' ? '未收录' : '暂不可用' : error ? '暂不可用' : '查询中…'}{(data?.cas_numbers?.length ?? 0)>1 && <small>多个来源登记号，需核对具体形式。</small>}</td></tr>
 <tr><th>分子式</th><td>{data?.molecular_formula?.split(/(\d+)/).map((p,i)=>/^\d+$/.test(p)?<sub key={i}>{p}</sub>:p) || '—'}</td></tr>
 <tr><th>分子量</th><td>{numberLabel(data?.molecular_weight ?? weight)} g/mol</td></tr>
 <tr><th>SMILES</th><td><code>{structure || '—'}</code>{structure && <button className="text-button" onClick={async()=>{try{await navigator.clipboard.writeText(structure);setCopy('已复制');}catch{setCopy('请选中文本复制');}}}>{copy || '复制'}</button>}</td></tr>
 <tr><th>PubChem CID</th><td>{data?.pubchem_cid ? <a href={`https://pubchem.ncbi.nlm.nih.gov/compound/${data.pubchem_cid}`} target="_blank" rel="noreferrer">{data.pubchem_cid}</a> : data ? data.status==='available' ? '未匹配' : '暂未取回' : '查询中…'}</td></tr>
 </tbody></table>
 {data?.cache_state==='stale' && <small>显示已保存的分子标识，后台尝试更新。</small>}
 {data?.smiles && smiles && data.smiles!==smiles && <small>当前数据库结构与本批结果不同，请核对。</small>}
 {(error || (data && data.status!=='available')) && <p>{error || data?.warnings?.join('；')}<button className="text-button" onClick={()=>{setError('');setAttempt(a=>a+1);}}>重试</button></p>}
 {data?.cas_numbers?.length ? <small>CAS 来源：<a href={data.cas_source} target="_blank" rel="noreferrer">{data.cas_source_label || "PubChem"}</a></small> : null}
 </section>;
}
