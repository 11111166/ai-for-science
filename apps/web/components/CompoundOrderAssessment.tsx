"use client";
import {FormEvent,useEffect,useState} from "react";
import {api} from "@/lib/api";
import {PatentRetrosynthesis} from "./PatentRetrosynthesis";
import {ProcessPortfolio} from "./ProcessPortfolio";

type DeliveryState="solid"|"liquid"|"gas";
type Spec={
 query:string;delivery_state:DeliveryState;quantity_kg:number;purity_pct:number;max_steps:number;chromatography_allowed:boolean;
 material_cost_cny_per_kg:number|null;required_delivery_days:number;labor_hours:number;labor_rate_cny_h:number;
 analytical_cost_cny:number;equipment_cost_cny:number;waste_cost_cny:number;contingency_pct:number;gross_margin_pct:number;
};
type Route={name:string;steps:number;starting_materials:string[];transformations:string[];purification:string[];chromatography:boolean;scale_evidence:string;route_evidence:string;hazards:string[];fit_score:number;fit:string;reason:string;estimated_overall_yield_pct:number;expected_assay_range_pct:number[];evidence_gap:string};
type Cost={material_cost_cny:number;labor_cost_cny:number;analytical_cost_cny:number;equipment_cost_cny:number;waste_cost_cny:number;direct_cost_cny:number;risk_adjusted_cost_cny:number;suggested_quote_cny:number;quote_range_cny:number[];per_product_kg_cny:number;basis:string;limitations:string};
type Result={
 status:string;identity:Record<string,string|number|boolean>;routes:Route[];recommended_route:Route|null;summary:Record<string,string>;
 decision:string;commercial_decision:{grade:string;confidence:string;why:string;stop_conditions:string[]};
 purification:{expected_assay_range_pct:number[]|null;recommendation:string;target_purity_pct?:number;improvement_actions:string[];basis?:string};
 blockers:string[];next_actions:string[];evidence_matrix:{item:string;status:string}[];
 patent_review:{status:string;conclusion:string;checklist:string[]};cost_estimate?:Cost|null;
 sources:{label:string;url:string;scope:string}[];catalog_version:string;
};
type Assessment={id:string;spec:Spec;result:Result;created_at:string};
type CatalogItem={name:string;english_name:string;cas:string};
const initial:Spec={query:"",delivery_state:"solid",quantity_kg:100,purity_pct:98,max_steps:6,chromatography_allowed:false,material_cost_cny_per_kg:null,required_delivery_days:30,labor_hours:0,labor_rate_cny_h:0,analytical_cost_cny:0,equipment_cost_cny:0,waste_cost_cny:0,contingency_pct:20,gross_margin_pct:30};
const stateLabel:Record<DeliveryState,string>={solid:"固体",liquid:"液体",gas:"气体"};

export function CompoundOrderAssessment({projectId}:{projectId:string}){
 const base=`/projects/${projectId}/process/orders`;
 const [spec,setSpec]=useState<Spec>(initial),[items,setItems]=useState<Assessment[]>([]),[catalog,setCatalog]=useState<CatalogItem[]>([]),[active,setActive]=useState(""),[busy,setBusy]=useState(false),[error,setError]=useState("");
 const item=items.find(x=>x.id===active)??items.at(-1);
 useEffect(()=>{let live=true;Promise.all([api<Assessment[]>(`${base}/assessments`),api<CatalogItem[]>(`${base}/catalog`)]).then(([a,c])=>{if(live){setItems(a);setCatalog(c);setActive(a.at(-1)?.id??"");}}).catch(e=>live&&setError(e.message));return()=>{live=false;};},[base]);
 async function submit(e:FormEvent){e.preventDefault();setBusy(true);setError("");try{const x=await api<Assessment>(`${base}/assessments`,{method:"POST",body:JSON.stringify(spec)});setItems(old=>[...old,x]);setActive(x.id);}catch(err){setError(err instanceof Error?err.message:"评估失败");}finally{setBusy(false);}}
 return <section className="process-tool-panel order-tool">
  <header className="process-subheading"><div><span>路线、证据、成本与接单</span><h3>快速判断订单是否值得接、怎样做和报多少</h3><p>身份、路线、纯化、产率、设备、安全、交期、成本和专利缺口使用同一份评估。</p></div>{items.length>0&&<label>历史评估<select value={item?.id} onChange={e=>setActive(e.target.value)}>{items.map(x=><option value={x.id} key={x.id}>{x.spec.query} · {x.spec.quantity_kg} kg</option>)}</select></label>}</header>
 {error&&<p role="alert" className="notice error">{error}</p>}
  <ProcessPortfolio projectId={projectId}/>
  <div className="order-examples"><span>可直接测试：</span>{catalog.map(x=><button type="button" key={x.cas} onClick={()=>setSpec({...spec,query:x.cas})}>{x.name} · {x.cas}</button>)}</div>
  <form onSubmit={submit}>
   <h4>交付与路线约束</h4>
   <div className="workbench-form">
    <label>名称、CAS、PubChem CID 或 SMILES<input required list={`order-catalog-${projectId}`} value={spec.query} onChange={e=>setSpec({...spec,query:e.target.value})}/><datalist id={`order-catalog-${projectId}`}>{catalog.map(x=><option key={x.cas} value={x.cas}>{x.name}</option>)}</datalist></label>
    <label>交付物态<select aria-label="订单交付物态" value={spec.delivery_state} onChange={e=>setSpec({...spec,delivery_state:e.target.value as DeliveryState})}><option value="solid">固体</option><option value="liquid">液体</option><option value="gas">气体</option></select></label>
    <label>目标数量（kg）<input required type="number" step="any" value={spec.quantity_kg} onChange={e=>setSpec({...spec,quantity_kg:+e.target.value})}/></label>
    <label>目标纯度（%）<input required type="number" step="any" value={spec.purity_pct} onChange={e=>setSpec({...spec,purity_pct:+e.target.value})}/></label>
    <label>交付周期（天）<input required type="number" value={spec.required_delivery_days} onChange={e=>setSpec({...spec,required_delivery_days:+e.target.value})}/></label>
    <label>可接受最多步骤<input required type="number" value={spec.max_steps} onChange={e=>setSpec({...spec,max_steps:+e.target.value})}/></label>
    <label className="process-check"><input type="checkbox" checked={spec.chromatography_allowed} onChange={e=>setSpec({...spec,chromatography_allowed:e.target.checked})}/>允许柱层析</label>
   </div>
   <h4>成本与报价假设</h4>
   <div className="workbench-form">
    <label>单位物料成本（元/kg，可选）<input type="number" step="any" value={spec.material_cost_cny_per_kg??""} onChange={e=>setSpec({...spec,material_cost_cny_per_kg:e.target.value?+e.target.value:null})}/></label>
    <label>预计人工（小时）<input type="number" step="any" value={spec.labor_hours} onChange={e=>setSpec({...spec,labor_hours:+e.target.value})}/></label>
    <label>人工费率（元/小时）<input type="number" step="any" value={spec.labor_rate_cny_h} onChange={e=>setSpec({...spec,labor_rate_cny_h:+e.target.value})}/></label>
    <label>分析与质量成本（元）<input type="number" step="any" value={spec.analytical_cost_cny} onChange={e=>setSpec({...spec,analytical_cost_cny:+e.target.value})}/></label>
    <label>设备与能耗（元）<input type="number" step="any" value={spec.equipment_cost_cny} onChange={e=>setSpec({...spec,equipment_cost_cny:+e.target.value})}/></label>
    <label>废物与清洁（元）<input type="number" step="any" value={spec.waste_cost_cny} onChange={e=>setSpec({...spec,waste_cost_cny:+e.target.value})}/></label>
    <label>风险准备（%）<input type="number" step="any" value={spec.contingency_pct} onChange={e=>setSpec({...spec,contingency_pct:+e.target.value})}/></label>
    <label>目标毛利率（%）<input type="number" step="any" value={spec.gross_margin_pct} onChange={e=>setSpec({...spec,gross_margin_pct:+e.target.value})}/></label>
   </div>
   <p className="process-help">未填写单位物料成本时仍会给出路线和接单前置条件，但不会虚构报价。专利结论只提供核查任务，必须由专利人员复核。</p>
   <button disabled={busy}>生成并保存订单评估</button>
  </form>
  {item&&<OrderResult item={item}/>}
  <PatentRetrosynthesis projectId={projectId}/>
 </section>;
}

function OrderResult({item}:{item:Assessment}){
 const r=item.result;
 return <section className="process-result">
  <div className="process-command"><div><span className="process-stage">{r.commercial_decision.grade}</span><h4>{r.decision}</h4><p><b>可信度：</b>{r.commercial_decision.confidence}；{r.commercial_decision.why}</p>{r.identity.matched&&<p><b>{String(r.identity.name)}</b> · CAS {String(r.identity.cas)} · {String(r.identity.formula)} · {String(r.identity.molecular_weight)} g/mol</p>}</div><div className="process-progress"><strong>本次交付要求</strong><span><b>{item.spec.quantity_kg}</b> kg</span><small>{stateLabel[item.spec.delivery_state]} · 纯度 ≥ {item.spec.purity_pct}% · {item.spec.required_delivery_days} 天</small></div></div>
  <div className="process-metrics">{Object.entries(r.summary).map(([k,v])=><article key={k}><span>{k}</span><b>{v}</b></article>)}</div>
  {r.recommended_route&&<>
   <div className="process-best"><div><strong>优先复核路线</strong><p>{r.recommended_route.name} · {r.recommended_route.steps} 步 · 匹配分 {r.recommended_route.fit_score}</p></div><div><strong>产率与含量预期</strong><p>经验总收率约 {r.recommended_route.estimated_overall_yield_pct}% · 纯化经验 {r.recommended_route.expected_assay_range_pct.join("–")}%</p></div><small>{r.recommended_route.evidence_gap}</small></div>
   <div className="process-route-grid">{r.routes.map(route=><article key={route.name}><span>{route.fit}</span><h4>{route.name}</h4><p>{route.steps} 步 · 证据 {route.route_evidence} · {route.scale_evidence}</p><p><b>转化：</b>{route.transformations.join(" → ")}</p><p><b>纯化：</b>{route.purification.join(" → ")}</p>{route.hazards.length>0&&<p><b>关注：</b>{route.hazards.join("；")}</p>}</article>)}</div>
  </>}
  <div className="process-result-grid">
   <article><h4>纯化与实验建议</h4><p><b>{r.purification.recommendation}</b>{r.purification.expected_assay_range_pct&&<> · 经验 {r.purification.expected_assay_range_pct.join("–")}%</>}</p><ol>{r.purification.improvement_actions.map(x=><li key={x}>{x}</li>)}</ol>{r.purification.basis&&<small>{r.purification.basis}</small>}</article>
   <article><h4>证据完整度</h4>{r.evidence_matrix.map(x=><p key={x.item}><b>{x.item}：</b>{x.status}</p>)}</article>
  </div>
  {r.cost_estimate?<section className="order-cost"><h4>成本与建议报价</h4><div className="process-metrics"><article><span>直接成本</span><b>¥{r.cost_estimate.direct_cost_cny.toLocaleString()}</b></article><article><span>风险调整成本</span><b>¥{r.cost_estimate.risk_adjusted_cost_cny.toLocaleString()}</b></article><article><span>建议报价区间</span><b>¥{r.cost_estimate.quote_range_cny[0].toLocaleString()}–{r.cost_estimate.quote_range_cny[1].toLocaleString()}</b><small>中心值 ¥{r.cost_estimate.suggested_quote_cny.toLocaleString()}</small></article></div><p>{r.cost_estimate.basis}</p><small>{r.cost_estimate.limitations}</small></section>:<p className="notice">缺少单位物料成本，暂不输出价格。补齐采购价格、工时和设备费用后再生成报价区间。</p>}
  <div className="process-result-grid"><article><h4>当前阻断项</h4><ul>{r.blockers.map(x=><li key={x}>{x}</li>)}</ul></article><article><h4>下一步</h4><ol>{r.next_actions.map(x=><li key={x}>{x}</li>)}</ol></article></div>
  <details><summary>专利与 FTO 核查</summary><p>{r.patent_review.conclusion}</p><ol>{r.patent_review.checklist.map(x=><li key={x}>{x}</li>)}</ol><p><b>{r.patent_review.status}</b></p></details>
  <details><summary>停止接单条件</summary><ul>{r.commercial_decision.stop_conditions.map(x=><li key={x}>{x}</li>)}</ul></details>
  {r.sources.length>0&&<details><summary>核对公开来源</summary>{r.sources.map(x=><p key={x.url}><a href={x.url} target="_blank" rel="noreferrer">{x.label}</a></p>)}</details>}
 </section>;
}
