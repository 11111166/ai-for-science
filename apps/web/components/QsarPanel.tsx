"use client";
import { useEffect,useState } from 'react';
import { api } from '@/lib/api';
import { numberLabel } from '@/lib/compoundInsights';
type Point={id:string;observed:number;predicted:number};
type Prediction={compound_id:string;identifier:string;name:string;predicted?:number|null;observed?:number|null;similarity?:number;domain?:string;prediction_kind?:string;status:string};
type Model={symbol:string;status:string;reason?:string;structure_count?:number;scaffold_count?:number;train_count?:number;test_count?:number;raw_count?:number;source_total?:number;data_sha256?:string;metrics?:{mae:number;rmse:number;r2:number;baseline_rmse:number};test_points?:Point[];predictions:Prediction[]};
export type QsarResult={version:string;created_at?:string;method?:string;models:Model[]};
function ValidationPlot({points}:{points:Point[]}) {
 const min=Math.floor(Math.min(3,...points.flatMap(p=>[p.observed,p.predicted])));const max=Math.ceil(Math.max(10,...points.flatMap(p=>[p.observed,p.predicted])));
 const x=(v:number)=>55+((v-min)/(max-min))*400;const y=(v:number)=>310-((v-min)/(max-min))*260;
 return <svg viewBox="0 0 510 365" role="img" aria-label="独立骨架测试集的实测与预测 pIC50 对照图" className="qsar-plot"><line x1="55" y1="310" x2="455" y2="310" stroke="#69847c"/><line x1="55" y1="310" x2="55" y2="50" stroke="#69847c"/><line x1="55" y1="310" x2="455" y2="50" stroke="#9bb8ae" strokeDasharray="5 5"/>{Array.from({length:max-min+1},(_,i)=>min+i).map(t=><g key={t}><text x={x(t)} y="332" textAnchor="middle">{t}</text><text x="43" y={y(t)+4} textAnchor="end">{t}</text></g>)}{points.map((p,i)=><circle key={i} cx={x(p.observed)} cy={y(p.predicted)} r="3.5" fill="#087e6e" opacity=".55"><title>{p.id} · 实测 {p.observed.toFixed(2)} · 预测 {p.predicted.toFixed(2)}</title></circle>)}<text x="255" y="356" textAnchor="middle">实测 pIC50</text><text x="16" y="180" transform="rotate(-90 16 180)" textAnchor="middle">预测 pIC50</text></svg>;
}
export function QsarPanel({projectId}:{projectId:string}) {
 const [open,setOpen]=useState(false);const [data,setData]=useState<QsarResult|null>(null);const [error,setError]=useState('');const [attempt,setAttempt]=useState(0);const [target,setTarget]=useState('');
 useEffect(()=>{if(!open)return;let active=true;api<QsarResult>(`/projects/${projectId}/qsar`).then(r=>{if(active)setData(r);}).catch(e=>{if(active)setError(e instanceof Error?e.message:'QSAR 加载失败');});return()=>{active=false;};},[open,projectId,attempt]);
 const model=data?.models.find(m=>m.symbol===target) ?? data?.models.find(m=>m.status==='validated') ?? data?.models[0];
 return <details className="technical-details qsar-panel" onToggle={e=>setOpen(e.currentTarget.open)}><summary>QSAR · 化合物靶点活性预测</summary>
 {error && <p role="alert">{error}<button className="text-button" onClick={()=>{setError('');setAttempt(x=>x+1);}}>重试</button></p>}{!data&&!error&&<p role="status">加载模型结果…</p>}
 {data && !model && <p>当前分子尚未覆盖 QSAR 模型。</p>}
 {model && <><div className="qsar-toolbar"><label>靶点<select value={model.symbol} onChange={e=>setTarget(e.target.value)}>{data?.models.map(m=><option key={m.symbol} value={m.symbol}>{m.symbol}{m.status==='validated'?'':' · 暂不可预测'}</option>)}</select></label><strong>{model.status==='validated'?'已通过骨架留出验证':model.reason || '模型未通过验证'}</strong></div>
 <p>预测终点 pIC50：数值越高，体外抑制活性越强。仅供候选筛选。</p>
 {model.metrics && <><div className="qsar-metrics"><span>训练 / 独立测试<strong>{model.train_count} / {model.test_count}</strong></span><span>测试 MAE<strong>{numberLabel(model.metrics.mae)}</strong></span><span>测试 RMSE<strong>{numberLabel(model.metrics.rmse)}</strong></span><span>测试 R²<strong>{numberLabel(model.metrics.r2)}</strong></span></div>
 <details><summary>查看实测—预测图与验证方法</summary><ValidationPlot points={model.test_points ?? []}/><p>不同化学骨架分开训练与测试；虚线为完全一致。误差单位为 pIC50，训练中位数基线 RMSE {numberLabel(model.metrics.baseline_rmse)}。</p><p>{data?.method}</p><p>本批使用 {model.structure_count} 个结构、{model.scaffold_count} 个骨架；取回 {model.raw_count}/{model.source_total} 条 IC50 记录。混合 assay 条件仍可能影响泛化。</p><small>数据哈希 {model.data_sha256}</small></details></>}
 {model.status==='validated' && <div className="table-card"><table><thead><tr><th>分子</th><th>QSAR pIC50</th><th>实测 pIC50</th><th>预测类型</th><th>结构适用范围</th></tr></thead><tbody>{model.predictions.map(p=><tr key={p.compound_id}><td>{p.name}<small>{p.identifier}</small></td><td>{numberLabel(p.predicted)}</td><td>{numberLabel(p.observed)}</td><td>{p.status==='not_in_snapshot'?'未纳入本批计算':p.prediction_kind==='scaffold_holdout'?'独立骨架测试':p.prediction_kind==='scaffold_cv'?'骨架交叉验证':'新结构预测'}</td><td>{p.similarity==null?'—':`${p.domain==='inside'?'域内':'域外 · 谨慎使用'} · 相似度 ${p.similarity.toFixed(2)}`}</td></tr>)}</tbody></table></div>}
 <small>{data?.version} · {data?.created_at?.slice(0,10)}</small></>}
 </details>;
}
