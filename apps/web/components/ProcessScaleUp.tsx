"use client";
import {FormEvent,useEffect,useState} from "react";
import {api,apiBase} from "@/lib/api";
import {ProcessEngineeringCalculators} from "./ProcessEngineeringCalculators";
import {ProcessPfdWorkspace} from "./ProcessPfdWorkspace";

type Phase="solid"|"liquid"|"gas"|"resin";
type ProductState="solid"|"liquid"|"gas";
type ProcessSystem="homogeneous_liquid"|"solid_liquid"|"liquid_resin"|"gas_liquid";
type Material={name:string;role:"limiting"|"reagent"|"catalyst";molecular_weight:number;equivalents:number;phase:Phase;unit_cost_cny_per_kg:number|null};
type Spec={
 name:string;product:string;product_state:ProductState;process_system:ProcessSystem;product_molecular_weight:number;
 product_density_kg_l:number|null;target_purity_pct:number;target_product_kg:number;conversion_pct:number;isolation_yield_pct:number;
 product_concentration_kg_l:number;temperature_c:number;pressure_bar:number;reaction_time_h:number;separation:string;solvent:string;
 solvent_density_kg_l:number;heat_capacity_kj_kg_k:number;reaction_enthalpy_kj_mol:number|null;separation_energy_kwh:number;
 solvent_unit_cost_cny_per_kg:number|null;utility_cost_cny_per_kwh:number;waste_cost_cny_per_kg:number;fill_fraction:number;
 corrosive:boolean;materials:Material[];
};
type Equipment={tag:string;type:string;suggested_size_l:number;working_volume_l:number;fill_pct:number;material:string;purpose:string;selection_basis:string;lab_data_needed:string[]};
type Result={
 basis:Record<string,string|number>;product_state:{state:string;estimated_standard_volume_nm3?:number;estimated_volume_l?:number;note:string};
 materials:(Material&{amount_kmol:number;amount_kg:number;estimated_cost_cny:number|null})[];
 solvent:{name:string;estimated_l:number;estimated_kg:number};mass_metrics:{total_input_kg:number;estimated_waste_kg:number;pmi:number;e_factor:number};
 utilities:{sensible_kwh:number;reaction_kwh:number|null;separation_kwh:number;total_kwh:number;temperature_change_c:number;scope:string};
 purification:{method:string;expected_assay_range_pct:number[];target_purity_pct:number;actions:string[];basis:string};
 equipment:Equipment[];materials_of_construction:{recommended:string;basis:string;verify:string[]};pfd:string[];
 aspen_streams:{id:string;name:string;phase:string;temperature_c:number;pressure_bar:number;mass_kg_batch:number;purpose:string}[];
 lab_to_plant:{dimension:string;lab:string;plant:string;acceptance:string}[];
 cost:{known_material_cny:number;utility_cny:number;waste_cny:number;known_total_cny:number;range_cny:number[];per_product_kg_cny:number;complete:boolean;missing_prices:string[];scope:string};
 risk_level:string;flags:string[];decision:string;next_experiments:string[];method:string;version:string;
};
type Assessment={id:string;spec:Spec;result:Result;created_at:string};
const initial:Spec={
 name:"API-X1 百公斤放大初评",product:"API-X1",product_state:"solid",process_system:"homogeneous_liquid",
 product_molecular_weight:250,product_density_kg_l:null,target_purity_pct:98,target_product_kg:100,conversion_pct:90,
 isolation_yield_pct:80,product_concentration_kg_l:.1,temperature_c:60,pressure_bar:1,reaction_time_h:6,
 separation:"结晶",solvent:"乙腈",solvent_density_kg_l:.786,heat_capacity_kj_kg_k:2,reaction_enthalpy_kj_mol:null,
 separation_energy_kwh:0,solvent_unit_cost_cny_per_kg:null,utility_cost_cny_per_kwh:.8,waste_cost_cny_per_kg:3,
 fill_fraction:.7,corrosive:false,materials:[
  {name:"限量原料 A",role:"limiting",molecular_weight:180,equivalents:1,phase:"solid",unit_cost_cny_per_kg:null},
  {name:"试剂 B",role:"reagent",molecular_weight:90,equivalents:1.1,phase:"liquid",unit_cost_cny_per_kg:null},
  {name:"催化剂 C",role:"catalyst",molecular_weight:400,equivalents:.02,phase:"solid",unit_cost_cny_per_kg:null},
 ],
};
const phaseLabel:Record<Phase,string>={solid:"固体",liquid:"液体",gas:"气体",resin:"树脂"};
const stateLabel:Record<ProductState,string>={solid:"固体",liquid:"液体",gas:"气体"};
const systemLabel:Record<ProcessSystem,string>={homogeneous_liquid:"均相液体",solid_liquid:"液固浆料",liquid_resin:"液体-树脂",gas_liquid:"气液反应"};

export function ProcessScaleUp({projectId}:{projectId:string}){
 const base=`/projects/${projectId}/process/scale-up`;
 const [spec,setSpec]=useState<Spec>(structuredClone(initial)),[items,setItems]=useState<Assessment[]>([]),[assessmentsLoaded,setAssessmentsLoaded]=useState(false),[active,setActive]=useState(""),[busy,setBusy]=useState(false),[error,setError]=useState(""),[demoNotice,setDemoNotice]=useState("");
 const item=items.find(x=>x.id===active)??items.at(-1);
 useEffect(()=>{let live=true;api<Assessment[]>(`${base}/assessments`).then(x=>{if(live){setItems(x);setActive(x.at(-1)?.id??"");setAssessmentsLoaded(true);}}).catch(e=>live&&setError(e.message));return()=>{live=false;};},[base]);
 function material(i:number,patch:Partial<Material>){setSpec({...spec,materials:spec.materials.map((x,j)=>i===j?{...x,...patch}:x)});}
 async function submit(e:FormEvent){e.preventDefault();setBusy(true);setError("");try{const x=await api<Assessment>(`${base}/assessments`,{method:"POST",body:JSON.stringify(spec)});setItems(old=>[...old,x]);setActive(x.id);}catch(err){setError(err instanceof Error?err.message:"计算失败");}finally{setBusy(false);}}
 return <section className="process-tool-panel scale-up-tool">
  <header className="process-subheading"><div><span>概念级工程包</span><h3>把实验条件转换为生产线可复核的数据</h3><p>兼容固体、液体、气体及树脂/浆料体系，输出物料能量衡算、设备材质、成本和流程文件。</p></div>{items.length>0&&<label>历史评估<select value={item?.id} onChange={e=>setActive(e.target.value)}>{items.map(x=><option value={x.id} key={x.id}>{x.spec.name}</option>)}</select></label>}</header>
 {error&&<p role="alert" className="notice error">{error}</p>}
  <ProcessPfdWorkspace projectId={projectId} assessments={items} assessmentsLoaded={assessmentsLoaded} onLoadDemo={spec=>{setSpec(spec as unknown as Spec);setDemoNotice("已把演示案例填入下方全部输入框：数据为虚构设计值，仅用于演示；点击“计算并保存工程包”即可生成本项目自己的放大评估。");}}/>
  {demoNotice&&<p className="pfd-notice" role="status">{demoNotice}</p>}
 <form onSubmit={submit}>
   <h4>产品与反应体系</h4>
   <div className="workbench-form">
    <label>评估名称<input required value={spec.name} onChange={e=>setSpec({...spec,name:e.target.value})}/></label>
    <label>目标产品<input required value={spec.product} onChange={e=>setSpec({...spec,product:e.target.value})}/></label>
    <label>产品物态<select aria-label="产品物态" value={spec.product_state} onChange={e=>setSpec({...spec,product_state:e.target.value as ProductState})}><option value="solid">固体</option><option value="liquid">液体</option><option value="gas">气体</option></select></label>
    <label>反应体系<select aria-label="放大反应体系" value={spec.process_system} onChange={e=>setSpec({...spec,process_system:e.target.value as ProcessSystem})}><option value="homogeneous_liquid">均相液体</option><option value="solid_liquid">液固浆料</option><option value="liquid_resin">液体-树脂</option><option value="gas_liquid">气液反应</option></select></label>
    <label>产品分子量（g/mol）<input required type="number" step="any" value={spec.product_molecular_weight} onChange={e=>setSpec({...spec,product_molecular_weight:+e.target.value})}/></label>
    <label>目标产量（kg/批）<input required type="number" step="any" value={spec.target_product_kg} onChange={e=>setSpec({...spec,target_product_kg:+e.target.value})}/></label>
    <label>目标含量/纯度（%）<input required type="number" step="any" value={spec.target_purity_pct} onChange={e=>setSpec({...spec,target_purity_pct:+e.target.value})}/></label>
    {spec.product_state==="liquid"&&<label>产品密度（kg/L，可选）<input type="number" step="any" value={spec.product_density_kg_l??""} onChange={e=>setSpec({...spec,product_density_kg_l:e.target.value?+e.target.value:null})}/></label>}
    <label>反应转化率（%）<input required type="number" step="any" value={spec.conversion_pct} onChange={e=>setSpec({...spec,conversion_pct:+e.target.value})}/></label>
    <label>分离收率（%）<input required type="number" step="any" value={spec.isolation_yield_pct} onChange={e=>setSpec({...spec,isolation_yield_pct:+e.target.value})}/></label>
    <label>产品当量浓度（kg/L）<input required type="number" step="any" value={spec.product_concentration_kg_l} onChange={e=>setSpec({...spec,product_concentration_kg_l:+e.target.value})}/></label>
    <label>反应温度（℃）<input required type="number" step="any" value={spec.temperature_c} onChange={e=>setSpec({...spec,temperature_c:+e.target.value})}/></label>
    <label>绝对压力（bar）<input required type="number" step="any" value={spec.pressure_bar} onChange={e=>setSpec({...spec,pressure_bar:+e.target.value})}/></label>
    <label>反应时间（h）<input required type="number" step="any" value={spec.reaction_time_h} onChange={e=>setSpec({...spec,reaction_time_h:+e.target.value})}/></label>
    <label>主要纯化方式<select value={spec.separation} onChange={e=>setSpec({...spec,separation:e.target.value})}>{["萃取","过滤","结晶","蒸馏","气体净化","柱层析"].map(x=><option key={x}>{x}</option>)}</select></label>
    <label className="process-check"><input type="checkbox" checked={spec.corrosive} onChange={e=>setSpec({...spec,corrosive:e.target.checked})}/>存在腐蚀性介质</label>
   </div>
   <h4>溶剂、热量与成本参数</h4>
   <div className="workbench-form">
    <label>溶剂<input required value={spec.solvent} onChange={e=>setSpec({...spec,solvent:e.target.value})}/></label>
    <label>溶剂密度（kg/L）<input required type="number" step="any" value={spec.solvent_density_kg_l} onChange={e=>setSpec({...spec,solvent_density_kg_l:+e.target.value})}/></label>
    <label>混合物比热（kJ/kg·K）<input required type="number" step="any" value={spec.heat_capacity_kj_kg_k} onChange={e=>setSpec({...spec,heat_capacity_kj_kg_k:+e.target.value})}/></label>
    <label>反应焓（kJ/mol，可选）<input type="number" step="any" value={spec.reaction_enthalpy_kj_mol??""} onChange={e=>setSpec({...spec,reaction_enthalpy_kj_mol:e.target.value?+e.target.value:null})}/></label>
    <label>分离能耗（kWh/批）<input type="number" step="any" value={spec.separation_energy_kwh} onChange={e=>setSpec({...spec,separation_energy_kwh:+e.target.value})}/></label>
    <label>溶剂单价（元/kg，可选）<input type="number" step="any" value={spec.solvent_unit_cost_cny_per_kg??""} onChange={e=>setSpec({...spec,solvent_unit_cost_cny_per_kg:e.target.value?+e.target.value:null})}/></label>
    <label>能源单价（元/kWh）<input type="number" step="any" value={spec.utility_cost_cny_per_kwh} onChange={e=>setSpec({...spec,utility_cost_cny_per_kwh:+e.target.value})}/></label>
    <label>废物处置（元/kg）<input type="number" step="any" value={spec.waste_cost_cny_per_kg} onChange={e=>setSpec({...spec,waste_cost_cny_per_kg:+e.target.value})}/></label>
   </div>
   <h4>投料化学计量</h4>
   {spec.materials.map((x,i)=><div className="process-input-row" key={i}>
    <label>物料名称<input required value={x.name} onChange={e=>material(i,{name:e.target.value})}/></label>
    <label>角色<select value={x.role} onChange={e=>material(i,{role:e.target.value as Material["role"]})}><option value="limiting">限量原料</option><option value="reagent">试剂</option><option value="catalyst">催化剂</option></select></label>
    <label>物态<select aria-label={`物料${i+1}物态`} value={x.phase} onChange={e=>material(i,{phase:e.target.value as Phase})}>{Object.entries(phaseLabel).map(([v,l])=><option value={v} key={v}>{l}</option>)}</select></label>
    <label>分子量<input type="number" step="any" value={x.molecular_weight} onChange={e=>material(i,{molecular_weight:+e.target.value})}/></label>
    <label>当量<input type="number" step="any" value={x.equivalents} onChange={e=>material(i,{equivalents:+e.target.value})}/></label>
    <label>单价（元/kg，可选）<input type="number" step="any" value={x.unit_cost_cny_per_kg??""} onChange={e=>material(i,{unit_cost_cny_per_kg:e.target.value?+e.target.value:null})}/></label>
    <button type="button" disabled={spec.materials.length===1} onClick={()=>setSpec({...spec,materials:spec.materials.filter((_,j)=>i!==j)})}>移除</button>
   </div>)}
   <p className="process-help">所有计算均保留输入口径和缺失项。反应焓、相平衡、设备能力或物料价格未提供时，结果会明确标记为待补数据。</p>
   <div className="workbench-controls"><button type="button" disabled={spec.materials.length>=8} onClick={()=>setSpec({...spec,materials:[...spec.materials,{name:"新物料",role:"reagent",molecular_weight:100,equivalents:1,phase:"solid",unit_cost_cny_per_kg:null}]})}>添加物料</button><button disabled={busy}>计算并保存工程包</button></div>
  </form>
  {item&&<ScaleResult item={item} base={base}/>}
  <ProcessEngineeringCalculators projectId={projectId}/>
 </section>;
}

function ScaleResult({item,base}:{item:Assessment;base:string}){
 const r=item.result;
 return <section className="process-result">
  <div className="process-command"><div><span className="process-stage">放大风险 {r.risk_level}</span><h4>{r.decision}</h4><p><b>产品：</b>{stateLabel[item.spec.product_state]} · {item.spec.target_product_kg} kg {item.spec.product}</p><p><b>体系：</b>{systemLabel[item.spec.process_system]}；{r.method}</p></div><div className="process-progress"><strong>建议反应设备</strong><span><b>{r.equipment[0].suggested_size_l}</b> L</span><small>{r.equipment[0].type} · 装载 {r.equipment[0].fill_pct}%</small></div></div>
  <div className="process-metrics">
   <article><span>总投入 / PMI</span><b>{r.mass_metrics.total_input_kg} kg</b><small>PMI {r.mass_metrics.pmi} · E-factor {r.mass_metrics.e_factor}</small></article>
   <article><span>总热负荷</span><b>{r.utilities.total_kwh} kWh</b><small>显热 {r.utilities.sensible_kwh} · 反应热 {r.utilities.reaction_kwh??"待补"}</small></article>
   <article><span>已知成本</span><b>¥{r.cost.known_total_cny.toLocaleString()}</b><small>约 ¥{r.cost.per_product_kg_cny}/kg · {r.cost.complete?"价格已填全":"仍有缺价"}</small></article>
  </div>
  <h4>概念工艺流程</h4><div className="process-pfd">{r.pfd.map((step,i)=><span key={`${step}-${i}`}>{step}</span>)}</div>
  <div className="workbench-controls"><a download href={`${apiBase}${base}/assessments/${item.id}/streams.csv`}>下载 Aspen 物流表 CSV</a><a download href={`${apiBase}${base}/assessments/${item.id}/aspen-package.zip`}>下载 Aspen 概念工程包</a><a download href={`${apiBase}${base}/assessments/${item.id}/pfd`}>下载 PFD（SVG）</a><a download href={`${apiBase}${base}/assessments/${item.id}/pfd.dxf`}>下载 CAD 草图（DXF）</a></div>
  <div className="process-result-grid">
   <article><h4>纯化与含量预期</h4><p><b>{r.purification.method}</b> · 通用经验 {r.purification.expected_assay_range_pct.join("–")}%</p><p>目标 {r.purification.target_purity_pct}%</p><ol>{r.purification.actions.map(x=><li key={x}>{x}</li>)}</ol><small>{r.purification.basis}</small></article>
   <article><h4>设备与材质</h4>{r.equipment.map(x=><details key={x.tag}><summary>{x.tag} · {x.type} · {x.suggested_size_l} L</summary><p>{x.material}</p><p>{x.selection_basis}</p><ul>{x.lab_data_needed.map(v=><li key={v}>{v}</li>)}</ul></details>)}</article>
  </div>
  <h4>Aspen 物流输入表</h4><div className="table-scroll"><table><thead><tr><th>物流</th><th>物态</th><th>温度</th><th>压力</th><th>质量/批</th></tr></thead><tbody>{r.aspen_streams.map(x=><tr key={x.id}><td>{x.id} · {x.name}<small>{x.purpose}</small></td><td>{x.phase}</td><td>{x.temperature_c}℃</td><td>{x.pressure_bar} bar(a)</td><td>{x.mass_kg_batch} kg</td></tr>)}</tbody></table></div>
  <details><summary>实验室到生产线衔接 · {r.lab_to_plant.length} 项</summary><div className="process-route-grid">{r.lab_to_plant.map(x=><article key={x.dimension}><h4>{x.dimension}</h4><p><b>实验室：</b>{x.lab}</p><p><b>生产：</b>{x.plant}</p><p><b>通过标准：</b>{x.acceptance}</p></article>)}</div></details>
  <details open><summary>关键缺口与下一步</summary><ul>{r.flags.map(x=><li key={x}>{x}</li>)}</ul><p><b>优先实验：</b>{r.next_experiments.join("；")}。</p><p><b>材质复核：</b>{r.materials_of_construction.verify.join("；")}。</p><small>{r.cost.scope}</small></details>
 </section>;
}
