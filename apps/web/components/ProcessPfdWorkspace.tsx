"use client";
import {useCallback,useEffect,useMemo,useRef,useState} from "react";
import {ChevronDown,Download,Eye,FileDown,FileText,ImageUp,Link2,Maximize2,Plus,Redo2,Save,ScanLine,Sparkles,Trash2,Undo2,X,ZoomIn,ZoomOut} from "lucide-react";
import {api,apiBase,apiBlob} from "@/lib/api";

export type PfdParam={name:string;value:string;unit:string;source:string};
export type PfdKind="feed"|"reactor"|"vessel"|"pump"|"heat_exchanger"|"separation"|"filter"|"dryer"|"column"|"compressor"|"product"|"waste"|"analysis"|"other";
export type PfdOutlet={name:string;value:string;unit:string;source:string};
export type PfdComponent={kind:string;tag:string};
export type PfdNode={id:string;kind:PfdKind;tag:string;label:string;x:number;y:number;w:number;h:number;params:PfdParam[];notes:string[];outlets?:PfdOutlet[];synthetic?:boolean;utilities?:PfdUtility[];reliefs?:PfdRelief[]};
export type PfdUtility={medium?:string;service?:string;value?:string;unit?:string;port?:string;direction?:string;source?:string};
export type PfdRelief={kind?:string;tag?:string;value?:string;unit?:string;to?:string;source?:string};
export type PfdSignal={tag:string;target?:string;kind?:string;source?:string};
export type PfdEdge={id:string;source:string;target:string;label:string;params:PfdParam[];media?:"material"|"utility";components?:PfdComponent[];instruments?:string[];service?:string;signals?:PfdSignal[]};
export type PfdSheetView="section"|"legend";
export type PfdGraph={nodes:PfdNode[];edges:PfdEdge[]};
export type PfdSummary={id:string;title:string;version:number;root_id:string;previous_id:string|null;source_assessment_id:string|null;revision:number;created_at:string;updated_at:string;node_count:number;edge_count:number};
type PfdDetail=PfdSummary&{graph:PfdGraph;disclaimer:string;pending_label:string};
type DemoCase={slug:string;title:string;product:string;case_note:string;target_product_kg:number;material_count:number};
type DemoDetail={slug:string;title:string;product:string;case_note:string;route:string;data_note:string;disclaimer:string;pending_label:string;assessment:Record<string,unknown>;graph:PfdGraph;node_count:number;edge_count:number;result_summary:{risk_level?:string|null;pmi?:number|null;e_factor?:number|null;total_kwh?:number|null;known_total_cny?:number|null;equipment?:string[]}};

export const MEDIA_OPTIONS:{value:string;label:string}[]=[{value:"steam",label:"蒸汽"},{value:"cooling_water",label:"循环冷却水"},{value:"chilled_water",label:"冷冻水"},{value:"brine",label:"载冷盐水"},{value:"heat_oil",label:"导热油"},{value:"condensate",label:"蒸汽冷凝水"},{value:"nitrogen",label:"氮气"},{value:"instrument_air",label:"仪表空气"},{value:"compressed_air",label:"压缩空气"},{value:"process_water",label:"工艺水"},{value:"electricity",label:"电（电加热/电伴热）"},{value:"vacuum",label:"真空"}];
export const RELIEF_OPTIONS:{value:string;label:string}[]=[{value:"safety_valve",label:"安全阀组"},{value:"vent",label:"放空"},{value:"drain",label:"排污/排空"},{value:"rupture_disc",label:"爆破片"}];
export const SIGNAL_OPTIONS:{value:string;label:string}[]=[{value:"",label:"形式待补"},{value:"electric",label:"电信号"},{value:"pneumatic",label:"气信号"},{value:"interlock",label:"联锁"}];
export const KIND_LABELS:Record<PfdKind,string>={
 feed:"进料/计量",reactor:"反应设备",vessel:"储罐/暂存",pump:"泵/输送",heat_exchanger:"换热/冷凝",
 separation:"分离/纯化",filter:"过滤",dryer:"干燥",column:"精馏/层析柱",compressor:"压缩/风机",
 product:"产品",waste:"废物/母液",analysis:"分析点（待设计）",other:"其他单元"
};
const KIND_ORDER=Object.keys(KIND_LABELS) as PfdKind[];
const KIND_FILL:Record<PfdKind,string>={
 feed:"#eef8f4",reactor:"#e3f2ec",vessel:"#eef4f1",pump:"#f2f7f5",heat_exchanger:"#f6f1e7",
 separation:"#e9f4f7",filter:"#e9f4f7",dryer:"#f7f2e7",column:"#eef0f7",compressor:"#f2f7f5",
 product:"#e6f4e9",waste:"#f8efef",analysis:"#f5f4ee",other:"#f4f6f5"
};
const VALVE_LABELS:Record<string,string>={valve_gate:"闸阀",valve_globe:"截止阀",valve_ball:"球阀",valve_butterfly:"蝶阀",valve_check:"止回阀",valve_control:"气动调节阀",valve_electric:"电动阀",valve_safety:"安全阀",valve_regulator:"减压阀"};
const ACTUATED_VALVES=["valve_control","valve_electric","valve_regulator"];
const PENDING="待补";
const EMPTY_GRAPH:PfdGraph={nodes:[],edges:[]};
const clone=(graph:PfdGraph):PfdGraph=>JSON.parse(JSON.stringify(graph)) as PfdGraph;

type IntakeModels={text_model?:string;vision_model?:string;ocr_model?:string;vision_mode?:string;configured?:boolean};
type IntakeRequiredInput={key:string;level?:string;category:string;status:string;detail:string;impact?:string;how:string};
type IntakeBasisRow={key:string;label:string;state:string};
type IntakeResult={draft_ref:string;title:string;graph:PfdGraph;node_count:number;edge_count:number;
 unreadable:string[];required_inputs?:IntakeRequiredInput[];required_inputs_note?:string;required_core?:IntakeRequiredInput[];required_detail?:IntakeRequiredInput[];drawing_basis?:IntakeBasisRow[];notes:string[];warnings:string[];balance:{status?:string|null;verdict:string};
 model:{name:string;vision:boolean;vision_mode?:string;ocr_model?:string|null};evidence_note:string;pending_label:string};

type AspenBlock={id:string;no:number;tag:string;name:string;kind:string;kind_name:string;model:string;params:string};
type AspenStream={id:string;no:number;code:string;label:string;source:string;target:string;flow:string;media:string;utility:boolean;assets:string};
type AspenStage={title:string;svg:string;blocks:AspenBlock[];streams:AspenStream[];assets:{stream:string;name:string;where:string}[];inp:string;stamp:string;notes:string[];disclaimer?:string};

function lineFrame(x1:number,y1:number,x2:number,y2:number,t:number){
 const dx=x2-x1,dy=y2-y1,len=Math.hypot(dx,dy)||1,ux=dx/len,uy=dy/len;
 return{cx:x1+ux*len*t,cy:y1+uy*len*t,ux,uy,px:-uy,py:ux,len};
}
function bowtiePath(cx:number,cy:number,ux:number,uy:number,r:number){
 const hx=ux*r,hy=uy*r,wx=-uy*r*.82,wy=ux*r*.82;
 const pt=(a:number,b:number)=>`${(cx+a).toFixed(1)} ${(cy+b).toFixed(1)}`;
 return `M ${pt(-hx+wx,-hy+wy)} L ${pt(-hx-wx,-hy-wy)} L ${pt(0,0)} Z `
      + `M ${pt(hx+wx,hy+wy)} L ${pt(hx-wx,hy-wy)} L ${pt(0,0)} Z`;
}
function endpoints(source:PfdNode,target:PfdNode):[[number,number],[number,number]]{
 if(target.y>=source.y+source.h-1)return[[source.x+source.w/2,source.y+source.h],[target.x+target.w/2,target.y]];
 if(target.x>=source.x+source.w-1)return[[source.x+source.w,source.y+source.h/2],[target.x,target.y+target.h/2]];
 if(target.x+target.w<=source.x+1)return[[source.x,source.y+source.h/2],[target.x+target.w,target.y+target.h/2]];
 return[[source.x+source.w/2,source.y],[target.x+target.w/2,target.y+target.h]];
}
function fitView(nodes:PfdNode[],width:number,height:number){
 if(!nodes.length)return{k:1,tx:40,ty:40};
 const minX=Math.min(...nodes.map(x=>x.x))-40,minY=Math.min(...nodes.map(x=>x.y))-40;
 const maxX=Math.max(...nodes.map(x=>x.x+x.w))+40,maxY=Math.max(...nodes.map(x=>x.y+x.h))+40;
 const k=Math.min(1.6,Math.max(.2,Math.min(width/(maxX-minX),height/(maxY-minY))));
 return{k,tx:(width-(maxX-minX)*k)/2-minX*k,ty:(height-(maxY-minY)*k)/2-minY*k};
}

export function ProcessPfdWorkspace({projectId,assessments,assessmentsLoaded=true,onLoadDemo}:{projectId:string;assessments:{id:string;spec:{name:string}}[];assessmentsLoaded?:boolean;onLoadDemo?:(spec:Record<string,unknown>,detail:DemoDetail)=>void}){
 const base=`/projects/${projectId}/process/pfd`;
 const intakeBase=`/projects/${projectId}/process/intake`;
 const listPath=`${base}/diagrams`;
 const [items,setItems]=useState<PfdSummary[]>([]);
 const [listLoaded,setListLoaded]=useState(false);
 const [title,setTitle]=useState("概念 PFD");
 const [graph,setGraph]=useState<PfdGraph>(EMPTY_GRAPH);
 const [current,setCurrent]=useState<PfdDetail|null>(null);
 const [sourceAssessment,setSourceAssessment]=useState("");
 const [openId,setOpenId]=useState("");
 const [selection,setSelection]=useState<{type:"node"|"edge";id:string}|null>(null);
 const [connectFrom,setConnectFrom]=useState<string|null>(null);
 const [newKind,setNewKind]=useState<PfdKind>("reactor");
 const [view,setView]=useState({k:1,tx:40,ty:40});
 const [size,setSize]=useState({w:900,h:520});
 const [history,setHistory]=useState<PfdGraph[]>([]);
 const [future,setFuture]=useState<PfdGraph[]>([]);
 const [savedSnapshot,setSavedSnapshot]=useState(JSON.stringify(EMPTY_GRAPH));
 const [busy,setBusy]=useState(false);
 const [error,setError]=useState("");
 const [notice,setNotice]=useState("");
 const stage=useRef<HTMLDivElement|null>(null);
 const svgRef=useRef<SVGSVGElement|null>(null);
 const drag=useRef<{mode:"pan"|"node";id?:string;startX:number;startY:number;originX:number;originY:number;snapshot:PfdGraph;moved:boolean}|null>(null);
 const editBase=useRef<PfdGraph|null>(null);
 const [intakeText,setIntakeText]=useState("");
 const [intakeHint,setIntakeHint]=useState("");
 const [intakeFiles,setIntakeFiles]=useState<File[]>([]);
 const [intakeImages,setIntakeImages]=useState<File[]>([]);
 const [intakeBusy,setIntakeBusy]=useState(false);
 const [intakeResult,setIntakeResult]=useState<IntakeResult|null>(null);
 const [intakeOpen,setIntakeOpen]=useState(false);
 const [intakeFormOpen,setIntakeFormOpen]=useState(true);
 const [intakeModels,setIntakeModels]=useState<IntakeModels|null>(null);
 const [aspenStage,setAspenStage]=useState<AspenStage|null>(null);
 const [aspenUrl,setAspenUrl]=useState("");
 const [aspenBusy,setAspenBusy]=useState(false);
 const [aspenError,setAspenError]=useState("");
 const [aspenOpen,setAspenOpen]=useState(false);
 const [aspenModal,setAspenModal]=useState(false);
 const [aspenZoom,setAspenZoom]=useState(1);

 const dirty=useMemo(()=>JSON.stringify(graph)!==savedSnapshot,[graph,savedSnapshot]);

 useEffect(()=>{let live=true;api<PfdSummary[]>(listPath).then(x=>{if(live){setItems(x);setListLoaded(true);}}).catch(e=>{if(live){setListLoaded(true);setError(e.message);}});return()=>{live=false;};},[listPath]);
 useEffect(()=>{let live=true;api<IntakeModels>(`${intakeBase}/models`).then(x=>{if(live&&x&&!Array.isArray(x)&&typeof x==="object")setIntakeModels(x);}).catch(()=>{});return()=>{live=false;};},[intakeBase]);
 useEffect(()=>{
  const el=svgRef.current;if(!el)return;
  const onWheel=(e:WheelEvent)=>{
   e.preventDefault();
   const rect=el.getBoundingClientRect();
   const px=e.clientX-rect.left,py=e.clientY-rect.top;
   setView(v=>{
    const k=Math.min(3,Math.max(.2,v.k*(e.deltaY<0?1.12:1/1.12)));
    if(k===v.k)return v;
    return {k,tx:px-(px-v.tx)*(k/v.k),ty:py-(py-v.ty)*(k/v.k)};
   });
  };
  el.addEventListener("wheel",onWheel,{passive:false});
  return ()=>el.removeEventListener("wheel",onWheel);
 },[]);
 useEffect(()=>{
  const node=stage.current;if(!node)return;
  const observer=new ResizeObserver(()=>{const box=node.getBoundingClientRect();setSize({w:Math.max(320,Math.round(box.width)),h:Math.max(320,Math.round(box.height))});});
  observer.observe(node);
  const box=node.getBoundingClientRect();setSize({w:Math.max(320,Math.round(box.width)),h:Math.max(320,Math.round(box.height))});
  return()=>observer.disconnect();
 },[]);

 const refreshList=useCallback(async()=>{
  try{
   const list=await api<PfdSummary[]>(listPath);
   setItems(list);
   return list;
  }finally{
   setListLoaded(true);
  }
 }, [listPath]);

 const applyGraph=useCallback((next:PfdGraph,options?:{reset?:boolean})=>{
  setGraph((previous)=>{
   if(!options?.reset){setHistory(h=>[...h.slice(-49),clone(previous)]);setFuture([]);}
   return next;
  });
 },[]);

 const beginEdit=useCallback(()=>{editBase.current=clone(graph);},[graph]);
 const pushEdit=useCallback(()=>{
  if(!editBase.current)return;
  const base=editBase.current;editBase.current=null;
  setHistory(h=>[...h.slice(-49),base]);setFuture([]);
 },[]);

 const undo=useCallback(()=>{
  setHistory(h=>{
   if(!h.length)return h;
   const previous=h[h.length-1];
   setFuture(f=>[clone(graph),...f].slice(0,50));
   setGraph(previous);
   return h.slice(0,-1);
  });
 },[graph]);
 const redo=useCallback(()=>{
  setFuture(f=>{
   if(!f.length)return f;
   const next=f[0];
   setHistory(h=>[...h,clone(graph)]);
   setGraph(next);
   return f.slice(1);
  });
 },[graph]);

 function resetWorkspace(nextTitle:string,nextGraph:PfdGraph,detail:PfdDetail|null){
  setTitle(nextTitle);setGraph(nextGraph);setCurrent(detail);
  setAspenStage(null);setAspenError("");setAspenModal(false);setAspenOpen(false);
  setSavedSnapshot(JSON.stringify(nextGraph));setHistory([]);setFuture([]);setSelection(null);setConnectFrom(null);
  setView(fitView(nextGraph.nodes,size.w,size.h));
 }
 function readDetail(detail:PfdDetail|null|undefined):PfdDetail{
  if(!detail||!detail.graph||!Array.isArray(detail.graph.nodes)||!Array.isArray(detail.graph.edges)){
   throw new Error("服务返回的图纸数据不完整，请重新打开图纸；若持续出现请联系管理员。");
  }
  return detail;
 }

 async function persist():Promise<PfdDetail>{
  if(current){
   return api<PfdDetail>(`${listPath}/${current.id}`,{method:"PUT",body:JSON.stringify({title,graph,base_revision:current.revision})});
  }
  return api<PfdDetail>(listPath,{method:"POST",body:JSON.stringify({title,graph,source_assessment_id:sourceAssessment||null})});
 }

 async function save(){
  if(!graph.nodes.length&&!current){setError("图纸还没有任何单元操作节点。请先从放大评估生成，或新建空白图并添加节点。");return;}
  setBusy(true);setError("");setNotice("");
  try{
   const detail=readDetail(await persist());
   setCurrent(detail);setTitle(detail.title);setGraph(detail.graph);setSavedSnapshot(JSON.stringify(detail.graph));setOpenId(detail.id);
   await refreshList();
   setNotice(`已保存 v${detail.version}（第 ${detail.revision} 次修订）。保存图纸不会改写原放大评估。`);
  }catch(e){setError((e as Error).message);}
  finally{setBusy(false);}
 }

 async function saveAsVersion(){
  if(!graph.nodes.length&&!current){setError("图纸还没有任何单元操作节点，无法建立新版本。");return;}
  setBusy(true);setError("");setNotice("");
  try{
   let baseDetail=current;
   if(!baseDetail||dirty){baseDetail=readDetail(await persist());setSavedSnapshot(JSON.stringify(graph));}
   const version=readDetail(await api<PfdDetail>(`${listPath}/${baseDetail.id}/versions`,{method:"POST",body:JSON.stringify({})}));
   const merged=readDetail(await api<PfdDetail>(`${listPath}/${version.id}`,{method:"PUT",body:JSON.stringify({title,graph,base_revision:version.revision})}));
   setCurrent(merged);setTitle(merged.title);setGraph(merged.graph);setSavedSnapshot(JSON.stringify(merged.graph));setOpenId(merged.id);
   await refreshList();
   setNotice(`已建立新版本 v${merged.version}。原版本保持不变，可在列表中随时打开。`);
  }catch(e){setError((e as Error).message);}
  finally{setBusy(false);}
 }

 async function generateFromAssessment(){
  if(!sourceAssessment){setError("请先选择一份已保存的放大评估。");return;}
  setBusy(true);setError("");setNotice("");
  try{
   const draft=await api<{title:string;source_assessment_id:string;graph:PfdGraph}>(`${base}/draft-from-assessment?assessment_id=${encodeURIComponent(sourceAssessment)}`);
   resetWorkspace(draft.title,draft.graph,null);
   setNotice("已从放大评估生成可编辑草图（尚未保存）。图中参数均带来源与单位，缺失工程数据标为“待补”。");
  }catch(e){setError((e as Error).message);}
  finally{setBusy(false);}
 }

 async function runAspen(g:PfdGraph,t:string){
  if(!g.nodes.length){setAspenStage(null);return;}
  setAspenBusy(true);setAspenError("");
  try{
   const stage=await api<AspenStage>(`${intakeBase}/aspen-draft`,{method:"POST",body:JSON.stringify({title:t||"概念 PFD",graph:g})});
   setAspenStage(stage);setAspenOpen(false);setAspenZoom(1);
  }catch(e){setAspenStage(null);setAspenError((e as Error).message);}
  finally{setAspenBusy(false);}
 }

 function downloadAspenInp(){
  const stage=aspenStage;
  if(!stage?.inp){setAspenError("还没有可导出的 Aspen 输入文件：先生成流程模拟图。");return;}
  const url=URL.createObjectURL(new Blob([stage.inp],{type:"text/plain;charset=utf-8"}));
  const link=document.createElement("a");
  link.href=url;link.download=`aspen-${String(stage.title||"flowsheet").replace(/[\\/:*?"<>|]+/g,"-")}.inp`;
  document.body.append(link);link.click();link.remove();
  window.setTimeout(()=>URL.revokeObjectURL(url),4000);
 }

 async function runIntake(){
  if(!intakeText.trim()&&!intakeFiles.length&&!intakeImages.length){setError("请先粘贴工艺描述、选择工艺文档，或上传流程图/设备清单图片。");return;}
  setIntakeBusy(true);setError("");setNotice("");setIntakeResult(null);
  try{
   const form=new FormData();
   if(intakeText.trim())form.set("text",intakeText);
   if(intakeHint.trim())form.set("hint",intakeHint);
   intakeFiles.forEach(file=>form.append("files",file));
   intakeImages.forEach(file=>form.append("images",file));
   const result=await api<IntakeResult>(`${intakeBase}/extract`,{method:"POST",body:form});
   setIntakeResult(result);setIntakeOpen(false);setIntakeFormOpen(false);
   resetWorkspace(result.title,result.graph,null);
   void runAspen(result.graph,result.title);
   const utilityCount=result.graph.nodes.reduce((sum,n)=>sum+(n.utilities?.length??0),0);
   const reliefCount=result.graph.nodes.reduce((sum,n)=>sum+(n.reliefs?.length??0),0);
   const signalCount=result.graph.edges.reduce((sum,e)=>sum+(e.signals?.length??0),0);
   setNotice(`AI 已识别 ${result.node_count} 个单元 / ${result.edge_count} 条物流（公用工程接管 ${utilityCount} 条、安全阀组与放空 ${reliefCount} 处、仪表信号回路 ${signalCount} 条）并生成草稿（${result.model.name}）。${result.evidence_note}`);
  }catch(e){setError((e as Error).message);}
  finally{setIntakeBusy(false);}
 }

 function clearIntake(){setIntakeText("");setIntakeHint("");setIntakeFiles([]);setIntakeImages([]);setIntakeResult(null);setIntakeOpen(false);setIntakeFormOpen(true);setAspenStage(null);setAspenError("");setAspenModal(false);}

 async function openDiagram(id:string){
  if(!id)return;
  setBusy(true);setError("");setNotice("");
  try{
   const detail=readDetail(await api<PfdDetail>(`${listPath}/${id}`));
   resetWorkspace(detail.title,detail.graph,detail);
   setOpenId(detail.id);
   setNotice(`已打开 v${detail.version}（来源评估：${detail.source_assessment_id?"有":"无"}）。`);
  }catch(e){setError((e as Error).message);}
  finally{setBusy(false);}
 }

 function newBlank(){
  const first:PfdNode={id:"n1",kind:"feed",tag:"",label:KIND_LABELS.feed,x:80,y:200,w:180,h:84,
   params:[{name:"设备参数",value:PENDING,unit:"",source:"需补充实验或工程数据"}],notes:[]};
  resetWorkspace("概念 PFD",{nodes:[first],edges:[]},null);
  setNotice("已新建空白图。请添加节点、连线并填写参数来源后保存。");
 }

 function nextId(prefix:string,existing:string[]){
  let index=existing.length+1,id=`${prefix}${index}`;
  while(existing.includes(id)){index+=1;id=`${prefix}${index}`;}
  return id;
 }

 function addNode(){
  const id=nextId("n",graph.nodes.map(x=>x.id));
  const x=Math.round((size.w/2-view.tx)/view.k-90),y=Math.round((size.h/2-view.ty)/view.k-42);
  const node:PfdNode={id,kind:newKind,tag:"",label:KIND_LABELS[newKind],x,y,w:180,h:84,
   params:[{name:"设备参数",value:PENDING,unit:"",source:"需补充实验或工程数据"}],notes:[]};
  applyGraph({nodes:[...graph.nodes,node],edges:graph.edges});
  setSelection({type:"node",id});
 }

 function deleteSelection(){
  if(!selection)return;
  if(selection.type==="node"){
   applyGraph({nodes:graph.nodes.filter(n=>n.id!==selection.id),edges:graph.edges.filter(e=>e.source!==selection.id&&e.target!==selection.id)});
  }else{
   applyGraph({nodes:graph.nodes,edges:graph.edges.filter(e=>e.id!==selection.id)});
  }
  setSelection(null);setConnectFrom(null);
 }

 function patchNode(id:string,patch:Partial<PfdNode>){setGraph({nodes:graph.nodes.map(n=>n.id===id?{...n,...patch}:n),edges:graph.edges});}
 function patchEdge(id:string,patch:Partial<PfdEdge>){setGraph({nodes:graph.nodes,edges:graph.edges.map(e=>e.id===id?{...e,...patch}:e)});}
 function patchUtility(id:string,index:number,patch:Partial<PfdUtility>){setGraph({nodes:graph.nodes.map(n=>n.id===id?{...n,utilities:(n.utilities??[]).map((u,i)=>i===index?{...u,...patch}:u)}:n),edges:graph.edges});}
 function addUtility(id:string){const node=graph.nodes.find(n=>n.id===id);if(!node||(node.utilities??[]).length>=8)return;setGraph({nodes:graph.nodes.map(n=>n.id===id?{...n,utilities:[...(n.utilities??[]),{medium:"steam",service:"",value:"",unit:"",port:"",direction:"in",source:"待补（需人工填写依据）"}]}:n),edges:graph.edges});}
 function removeUtility(id:string,index:number){setGraph({nodes:graph.nodes.map(n=>n.id===id?{...n,utilities:(n.utilities??[]).filter((_u,i)=>i!==index)}:n),edges:graph.edges});}
 function patchRelief(id:string,index:number,patch:Partial<PfdRelief>){setGraph({nodes:graph.nodes.map(n=>n.id===id?{...n,reliefs:(n.reliefs??[]).map((r,i)=>i===index?{...r,...patch}:r)}:n),edges:graph.edges});}
 function addRelief(id:string){const node=graph.nodes.find(n=>n.id===id);if(!node||(node.reliefs??[]).length>=6)return;setGraph({nodes:graph.nodes.map(n=>n.id===id?{...n,reliefs:[...(n.reliefs??[]),{kind:"safety_valve",tag:"",value:"",unit:"",to:"",source:"待补（需人工填写依据）"}]}:n),edges:graph.edges});}
 function removeRelief(id:string,index:number){setGraph({nodes:graph.nodes.map(n=>n.id===id?{...n,reliefs:(n.reliefs??[]).filter((_r,i)=>i!==index)}:n),edges:graph.edges});}
 function patchSignal(id:string,index:number,patch:Partial<PfdSignal>){setGraph({nodes:graph.nodes,edges:graph.edges.map(e=>e.id===id?{...e,signals:(e.signals??[]).map((s,i)=>i===index?{...s,...patch}:s)}:e)});}
 function addSignal(id:string){const edge=graph.edges.find(e=>e.id===id);if(!edge||(edge.signals??[]).length>=6)return;setGraph({nodes:graph.nodes,edges:graph.edges.map(e=>e.id===id?{...e,signals:[...(e.signals??[]),{tag:"",target:"",kind:"",source:"待补（需人工填写依据）"}]}:e)});}
 function removeSignal(id:string,index:number){setGraph({nodes:graph.nodes,edges:graph.edges.map(e=>e.id===id?{...e,signals:(e.signals??[]).filter((_s,i)=>i!==index)}:e)});}
 function patchParam(target:"node"|"edge",id:string,index:number,patch:Partial<PfdParam>,record=false){
  const next:Partial<PfdParam>=patch;
  if(target==="node"){
   const node=graph.nodes.find(n=>n.id===id);if(!node)return;
   const params=node.params.map((p,i)=>i===index?{...p,...next}:p);
   if(record)applyGraph({nodes:graph.nodes.map(n=>n.id===id?{...n,params}:n),edges:graph.edges});
   else patchNode(id,{params});
  }else{
   const edge=graph.edges.find(e=>e.id===id);if(!edge)return;
   const params=edge.params.map((p,i)=>i===index?{...p,...next}:p);
   if(record)applyGraph({nodes:graph.nodes,edges:graph.edges.map(e=>e.id===id?{...e,params}:e)});
   else patchEdge(id,{params});
  }
 }
 function addParam(target:"node"|"edge",id:string){
  const param:PfdParam={name:"新参数",value:PENDING,unit:"",source:"需补充来源"};
  if(target==="node"){
   const node=graph.nodes.find(n=>n.id===id);if(!node||node.params.length>=12)return;
   applyGraph({nodes:graph.nodes.map(n=>n.id===id?{...n,params:[...node.params,param]}:n),edges:graph.edges});
  }else{
   const edge=graph.edges.find(e=>e.id===id);if(!edge||edge.params.length>=8)return;
   applyGraph({nodes:graph.nodes,edges:graph.edges.map(e=>e.id===id?{...e,params:[...edge.params,param]}:e)});
  }
 }
 function removeParam(target:"node"|"edge",id:string,index:number){
  if(target==="node"){
   const node=graph.nodes.find(n=>n.id===id);if(!node)return;
   applyGraph({nodes:graph.nodes.map(n=>n.id===id?{...n,params:node.params.filter((_,i)=>i!==index)}:n),edges:graph.edges});
  }else{
   const edge=graph.edges.find(e=>e.id===id);if(!edge)return;
   applyGraph({nodes:graph.nodes,edges:graph.edges.map(e=>e.id===id?{...e,params:edge.params.filter((_,i)=>i!==index)}:e)});
  }
 }

 function createEdge(from:string,to:string){
  if(from===to){setError("连接线不能指向同一个节点。");return;}
  if(graph.edges.some(e=>e.source===from&&e.target===to)){setNotice("这两个节点之间已有连接线。");return;}
  const id=nextId("e",graph.edges.map(x=>x.id));
  const edge:PfdEdge={id,source:from,target:to,label:"",params:[{name:"物流数据",value:PENDING,unit:"",source:"需补充流量、组成与物料闭合"}]};
  applyGraph({nodes:graph.nodes,edges:[...graph.edges,edge]});
  setSelection({type:"edge",id});setConnectFrom(null);setError("");
 }

 const handleNodeClick=(id:string)=>{
  setSelection({type:"node",id});
  if(connectFrom===null)return;
  if(!connectFrom){setConnectFrom(id);return;}
  if(connectFrom===id){setConnectFrom("");return;}
  createEdge(connectFrom,id);
 };

 function onStagePointerDown(e:React.PointerEvent<SVGSVGElement>){
  if(e.button!==0&&e.button!==1)return;
  drag.current={mode:"pan",startX:e.clientX,startY:e.clientY,originX:view.tx,originY:view.ty,snapshot:clone(graph),moved:false};
  svgRef.current?.setPointerCapture(e.pointerId);
  setConnectFrom(null);setSelection(null);
 }
 function onNodePointerDown(e:React.PointerEvent<SVGGElement>,node:PfdNode){
  if(e.button!==0)return;
  e.stopPropagation();
  drag.current={mode:"node",id:node.id,startX:e.clientX,startY:e.clientY,originX:node.x,originY:node.y,snapshot:clone(graph),moved:false};
  svgRef.current?.setPointerCapture(e.pointerId);
 }
 function onPointerMove(e:React.PointerEvent<SVGSVGElement>){
  const active=drag.current;if(!active)return;
  const dx=e.clientX-active.startX,dy=e.clientY-active.startY;
  if(Math.abs(dx)>2||Math.abs(dy)>2)active.moved=true;
  if(active.mode==="pan"){setView(v=>({...v,tx:active.originX+dx,ty:active.originY+dy}));return;}
  const id=active.id??"";
  setGraph(g=>({nodes:g.nodes.map(n=>n.id===id?{...n,x:Math.round(active.originX+dx/view.k),y:Math.round(active.originY+dy/view.k)}:n),edges:g.edges}));
 }
 function onPointerUp(e:React.PointerEvent<SVGSVGElement>){
  const active=drag.current;if(!active)return;
  drag.current=null;
  svgRef.current?.releasePointerCapture(e.pointerId);
  if(active.mode==="node"){
   if(active.moved){setHistory(h=>[...h.slice(-49),active.snapshot]);setFuture([]);}
   else handleNodeClick(active.id??"");
  }
 }

 const [demoCases,setDemoCases]=useState<DemoCase[]>([]);
 const [demoSlug,setDemoSlug]=useState("");
 const [sheetOpen,setSheetOpen]=useState(false);
 const [sheetView,setSheetView]=useState<PfdSheetView>("section");
  const [sheet,setSheet]=useState<{busy:boolean;error:string;url:string}>({busy:false,error:"",url:""});
  const [sheetZoom,setSheetZoom]=useState(1);
 const sheetUrl=useRef("");
 const sheetTicket=useRef(0);

 useEffect(()=>{
  let live=true;
  api<DemoCase[]>(`/projects/${projectId}/process/demo/cases`).then(x=>{if(!live)return;setDemoCases(x);setDemoSlug(s=>s||x[0]?.slug||"");}).catch(()=>{});
  return()=>{live=false;};
 },[projectId]);

 useEffect(()=>()=>{if(sheetUrl.current)URL.revokeObjectURL(sheetUrl.current);},[]);

 useEffect(()=>{
  if(!aspenStage?.svg){setAspenUrl("");return;}
  const url=URL.createObjectURL(new Blob([aspenStage.svg],{type:"image/svg+xml"}));
  setAspenUrl(url);
  return()=>{URL.revokeObjectURL(url);};
 },[aspenStage]);

 async function loadDemo(slug:string){
  if(!slug)return;
  setBusy(true);setError("");setNotice("");
  try{
   const detail=await api<DemoDetail>(`/projects/${projectId}/process/demo/cases/${slug}`);
   resetWorkspace(detail.title,detail.graph,null);
   onLoadDemo?.(detail.assessment,detail);
   setNotice(`已载入演示案例「${detail.title}」：${detail.node_count} 个单元 / ${detail.edge_count} 条物流，图中参数均带来源与单位，缺失工程数据标为“待补”。${detail.data_note}`);
  }catch(e){setError((e as Error).message);}
  finally{setBusy(false);}
 }

 function sheetPayload(view:PfdSheetView="section"){
  return JSON.stringify({title:title||"概念 PFD",graph,view,meta:{
   project:current?.title??"",product:"",version:current?`v${current.version}`:"未保存",
   revision:current?String(current.revision):"-",
   source:current?.source_assessment_id?"由已保存放大评估生成":"手工绘制或演示草稿",
  }});
 }

 async function previewSheet(view:PfdSheetView="section"){
  if(view==="section"&&!graph.nodes.length){setError("图纸为空：请先添加节点或载入演示案例，再预览工段图。图例图纸不依赖节点，可直接预览。");return;}
  const ticket=sheetTicket.current+1;sheetTicket.current=ticket;
  setSheetView(view);
  setSheet({busy:true,error:"",url:""});setSheetOpen(true);setSheetZoom(1);
  try{
   const blob=await apiBlob(`${base}/preview/sheet.svg`,{method:"POST",body:sheetPayload(view)});
   if(ticket!==sheetTicket.current)return;
   if(sheetUrl.current)URL.revokeObjectURL(sheetUrl.current);
   sheetUrl.current=URL.createObjectURL(blob);
   setSheet({busy:false,error:"",url:sheetUrl.current});
  }catch(e){
   if(ticket!==sheetTicket.current)return;
   setSheet({busy:false,error:(e as Error).message,url:""});
  }
 }

 async function downloadSheet(kind:"svg"|"dxf",view:PfdSheetView="section"){
  if(view==="section"&&!graph.nodes.length){setError("图纸为空：请先添加节点或载入演示案例，再导出工段图。图例图纸不依赖节点，可直接导出。");return;}
  setSheet(s=>({...s,busy:true,error:""}));
  try{
   const blob=await apiBlob(`${base}/preview/sheet.${kind}`,{method:"POST",body:sheetPayload(view)});
   const url=URL.createObjectURL(blob);
   const link=document.createElement("a");
   link.href=url;link.download=`concept-pfd-${view==="legend"?"legend":"sheet"}.${kind}`;
   document.body.append(link);link.click();link.remove();
   window.setTimeout(()=>URL.revokeObjectURL(url),4000);
   setSheet(s=>({...s,busy:false}));
  }catch(e){setSheet(s=>({...s,busy:false,error:(e as Error).message}));}
 }
 const selectedNode=selection?.type==="node"?graph.nodes.find(n=>n.id===selection.id)??null:null;
 const selectedEdge=selection?.type==="edge"?graph.edges.find(e=>e.id===selection.id)??null:null;
 const intakeSources=[intakeText.trim()?"粘贴的工艺描述":"",intakeFiles.length?`${intakeFiles.length} 个文档`:"",intakeImages.length?`${intakeImages.length} 张图片`:""].filter(Boolean).join(" + ")||"（空）";
 const needAll=intakeResult?.required_inputs??[];
 const needCore=intakeResult?.required_core??needAll.filter(item=>item.level!=="detail");
 const needDetail=intakeResult?.required_detail??needAll.filter(item=>item.level==="detail");
 const basisRows=intakeResult?.drawing_basis??[];

 return <section className="pfd-workspace">
  <header className="pfd-heading">
   <div>
    <span>工艺工程与生产设计 · 概念 PFD / CAD 工作区</span>
    <h4>在浏览器中编辑概念级工艺流程图，并导出两张 CAD 图纸（第 1 张图例 + 第 2 张工段图）的 SVG 与 DXF</h4>
    <p className="pfd-disclaimer">{current?.disclaimer??"概念级工艺流程图，仅供研发讨论，非施工图或已验证 P&ID。管径、仪表、安全联锁、设备强度与已验证产能均待合格工程人员设计。"}</p>
   </div>
   <b>{current?`v${current.version} · 修订 ${current.revision}`:"未保存"}</b>
  </header>

  <div className="pfd-intake">
   <div className="pfd-intake-head">
    <b>AI 智能输入 · 多模态识别自动出图</b>
    {intakeFormOpen
     ? <span>不用再一个个手工点节点：粘贴工艺描述、上传工艺文档（txt/md/csv/docx/pdf），或上传流程图照片、设备清单截图，AI 识别后自动排布成一张可继续编辑的概念流程图草稿。</span>
     : <span className="pfd-intake-summary">输入已收起 · 识别来源：{intakeSources}。要改资料或换一份资料时点右侧“展开输入”。</span>}
    <span className="pfd-intake-head-btns">
     {!intakeFormOpen&&<button type="button" className="primary" disabled={busy||intakeBusy||(!intakeText.trim()&&!intakeFiles.length&&!intakeImages.length)} onClick={()=>void runIntake()}><ScanLine size={15}/>{intakeBusy?"AI 识别中…":"重新识别"}</button>}
     <button type="button" className="pfd-intake-form-toggle" aria-expanded={intakeFormOpen} onClick={()=>setIntakeFormOpen(v=>!v)}><ChevronDown size={15} className={intakeFormOpen?"pfd-fold-caret open":"pfd-fold-caret"}/>{intakeFormOpen?"收起输入":"展开输入"}</button>
    </span>
   </div>
   {intakeFormOpen&&<>
    <div className="pfd-intake-grid">
     <label>工艺描述（可粘贴操作规程、工艺说明或物料表）
      <textarea value={intakeText} onChange={e=>setIntakeText(e.target.value)} maxLength={24000}
       placeholder="例：原料 A 自原料罐 V-101 经计量泵 P-101 以 500 kg/h 送入反应器 R-101；反应器底部排污 50 kg/h 送废水罐 V-105；反应液 455 kg/h 进精馏塔 T-101……"/>
     </label>
     <div className="pfd-intake-side">
      <label>工艺文档（可多选）
       <input type="file" multiple accept=".txt,.md,.markdown,.csv,.tsv,.json,.log,.yaml,.yml,.xml,.docx,.pdf" onChange={e=>setIntakeFiles(Array.from(e.target.files??[]))}/>
      </label>
      <label>流程图 / 设备清单图片（可多选）
       <input type="file" multiple accept="image/*" onChange={e=>setIntakeImages(Array.from(e.target.files??[]))}/>
      </label>
      <label>补充说明（可选）
       <input type="text" value={intakeHint} onChange={e=>setIntakeHint(e.target.value)} maxLength={500} placeholder="例：按工段识别；位号用 R-101 这样的格式"/>
      </label>
     </div>
    </div>
    <div className="pfd-intake-actions">
     <button type="button" className="primary" disabled={busy||intakeBusy||(!intakeText.trim()&&!intakeFiles.length&&!intakeImages.length)} onClick={()=>void runIntake()}>
      <ScanLine size={16}/>{intakeBusy?"AI 识别中…":"AI 识别并生成图纸"}
     </button>
     <button type="button" disabled={busy||intakeBusy} onClick={clearIntake}>清空输入</button>
     {intakeFiles.length>0&&<span className="pfd-intake-chip"><FileText size={12}/>{intakeFiles.length} 个文档</span>}
     {intakeImages.length>0&&<span className="pfd-intake-chip"><ImageUp size={12}/>{intakeImages.length} 张图片</span>}
     {intakeModels?.text_model&&<span className="pfd-intake-note">识别引擎 {intakeModels.text_model}{intakeModels.vision_mode==="ocr"&&intakeModels.ocr_model?`；图片先经 ${intakeModels.ocr_model} 转成文字再识别`:intakeModels.vision_model?`；图片用 ${intakeModels.vision_model}`:"（纯文本）"}</span>}
    </div>
   </>}
   {intakeBusy&&<p className="pfd-intake-note">正在识别资料并排布图面，图片较多时约需 1–3 分钟，请勿关闭页面。识别结果只是草稿，需要您复核后才可作为设计输入。</p>}
   {intakeResult&&<div className="pfd-intake-result">
    <div className="pfd-intake-result-head">
     <button type="button" className="pfd-intake-fold-toggle" aria-expanded={intakeOpen} onClick={()=>setIntakeOpen(v=>!v)}>
      <ChevronDown size={16} className={intakeOpen?"pfd-fold-caret open":"pfd-fold-caret"}/>
      <b>{intakeOpen?"收起识别结果":"展开识别结果"}</b>
      <span className="pfd-intake-fold-summary">已识别 <b>{intakeResult.node_count}</b> 个单元操作 / <b>{intakeResult.edge_count}</b> 条物流 · {intakeResult.model.name}</span>
      <span className="pfd-intake-badge muted">公用工程接管 {intakeResult.graph.nodes.reduce((s,n)=>s+(n.utilities?.length??0),0)} 条 · 安全阀组与放空 {intakeResult.graph.nodes.reduce((s,n)=>s+(n.reliefs?.length??0),0)} 处 · 仪表信号回路 {intakeResult.graph.edges.reduce((s,e)=>s+(e.signals?.length??0),0)} 条</span>
      {intakeResult.warnings.length>0&&<span className="pfd-intake-badge">{intakeResult.warnings.length} 项待人工确认</span>}
      {intakeResult.unreadable.length>0&&<span className="pfd-intake-badge muted">{intakeResult.unreadable.length} 项资料未读清</span>}
      {needCore.length>0&&<span className="pfd-intake-badge">{needCore.length} 项影响图面待补</span>}
      {needDetail.length>0&&<span className="pfd-intake-badge optional">{needDetail.length} 项可选补充</span>}
     </button>
     <button type="button" className="pfd-intake-peek" disabled={busy||!graph.nodes.length} onClick={()=>void previewSheet("section")}><Eye size={16}/>看图纸</button>
    </div>
    {intakeOpen&&<div className="pfd-intake-result-body">
     <div>已识别 <b>{intakeResult.node_count}</b> 个单元操作 / <b>{intakeResult.edge_count}</b> 条物流（{intakeResult.model.name}）。草稿已放入下方画布，可继续编辑后保存；图中的阀门、仪表位号与差额去向会画在管线上。公用工程总管与支管、安全阀组与泄放去向、仪表信号回路也按资料画出来：本次读到公用工程接管 <b>{intakeResult.graph.nodes.reduce((s,n)=>s+(n.utilities?.length??0),0)}</b> 条、安全阀组与放空 <b>{intakeResult.graph.nodes.reduce((s,n)=>s+(n.reliefs?.length??0),0)}</b> 处、仪表信号回路 <b>{intakeResult.graph.edges.reduce((s,e)=>s+(e.signals?.length??0),0)}</b> 条。</div>
     <div style={{marginTop:6}}>{intakeResult.graph.nodes.slice(0,16).map(node=><span key={node.id} className="pfd-intake-chip">{node.tag?`${node.tag} `:""}{node.label}</span>)}</div>
     {intakeResult.balance?.verdict&&<div style={{marginTop:8}}>{intakeResult.balance.verdict}</div>}
     {intakeResult.warnings.length>0&&<div className="pfd-intake-warn"><b>需要人工确认的问题（流程断点 / 物料守恒）</b><ul>{intakeResult.warnings.map((item,index)=><li key={index}>{item}</li>)}</ul></div>}
     {needAll.length>0&&<div className="pfd-intake-need">
      <b>图上还缺的资料（图纸已按这次能确定的内容画出来，缺的地方图上标了「待补」）</b>
      {needCore.length>0&&<ul>{needCore.map((item,index)=><li key={index}><b>{item.category}</b>（{item.status}）：{item.detail}{item.how?`　补法：${item.how}`:""}</li>)}</ul>}
      {needDetail.length>0&&<details className="pfd-intake-more"><summary>详细设计补充清单（可选，不影响出图）：{needDetail.length} 项</summary>
       <ul>{needDetail.map((item,index)=><li key={index}><b>{item.category}</b>（{item.status}）：{item.detail}{item.how?`　补法：${item.how}`:""}</li>)}</ul></details>}
      {intakeResult.required_inputs_note&&<div style={{marginTop:6}}>{intakeResult.required_inputs_note}</div>}
     </div>}
     {basisRows.length>0&&<div className="pfd-intake-basis"><b>这次成图用到的关键输入</b>{basisRows.map((row,index)=><span key={index} className={row.state==="已用于成图"?"pfd-intake-basis-ok":"pfd-intake-basis-gap"}>{row.label} · {row.state}</span>)}</div>}
     {intakeResult.unreadable.length>0&&<div className="pfd-intake-pending"><b>资料里没读到或读不清的内容（不会自动补全）</b><ul>{intakeResult.unreadable.map((item,index)=><li key={index}>{item}</li>)}</ul></div>}
     {intakeResult.notes.length>0&&<ul>{intakeResult.notes.map((item,index)=><li key={index}>{item}</li>)}</ul>}
    </div>}
   </div>}
   {(aspenBusy||aspenStage||aspenError)&&<div className="pfd-aspen">
    <div className="pfd-aspen-head">
     <b>① Aspen 流程模拟图</b>
     <span className="pfd-aspen-step">出图两步：文本 / 图片 → <b>① Aspen 流程模拟图</b>（先核对单元、物流与流量）→ <b>② CAD 图纸</b>（第 1 张图例 + 第 2 张工段图）</span>
     <span className="pfd-intake-head-btns">
      <button type="button" disabled={aspenBusy||!aspenStage?.svg} onClick={()=>setAspenModal(true)}><Eye size={15}/>放大预览</button>
      <button type="button" disabled={aspenBusy||!aspenStage?.inp} onClick={downloadAspenInp}><FileDown size={15}/>导出 Aspen 输入 .inp</button>
      <button type="button" className="pfd-intake-form-toggle" aria-expanded={aspenOpen} onClick={()=>setAspenOpen(v=>!v)}><ChevronDown size={15} className={aspenOpen?"pfd-fold-caret open":"pfd-fold-caret"}/>{aspenOpen?"收起模拟图":"展开模拟图"}</button>
     </span>
    </div>
    {aspenBusy&&<p className="pfd-intake-note">正在按识别出的单元操作排 Aspen 流程模拟图（方块单元 + 物流号与流量 + 物流表 / 单元表）…</p>}
    {aspenError&&<p className="pfd-error" role="alert">{aspenError}</p>}
    {aspenStage&&<>
     {aspenStage.blocks.length>0&&<div className="pfd-aspen-meta"><b>Aspen 模型建议：</b>{aspenStage.blocks.map(b=>`${b.tag} = ${b.model}`).join("；")}</div>}
     {aspenOpen&&<div className="pfd-aspen-body">
      {aspenStage.svg?<div className="pfd-aspen-canvas">
       {/* eslint-disable-next-line @next/next/no-img-element -- Aspen 流程模拟图是本会话生成的 blob URL，不是远端资源 */}
       <img src={aspenUrl} alt="Aspen 风格流程模拟图草稿"/>
      </div>:<p className="pfd-intake-note">{aspenStage.notes[0]??"图中还没有单元操作。"}</p>}
      {aspenStage.streams.length>0&&<table className="pfd-aspen-table">
       <thead><tr><th>物流号</th><th>说明</th><th>从</th><th>到</th><th>流量</th><th>介质</th><th>管道附件 / 仪表</th></tr></thead>
       <tbody>{aspenStage.streams.map(s=><tr key={s.id}><td>{s.code}</td><td>{s.label}</td><td>{s.source}</td><td>{s.target}</td><td>{s.flow}</td><td>{s.media}</td><td>{s.assets}</td></tr>)}</tbody>
      </table>}
      {aspenStage.notes.length>0&&<ul className="pfd-aspen-notes">{aspenStage.notes.map((n,i)=><li key={i}>{n}</li>)}</ul>}
      <p className="pfd-disclaimer">{aspenStage.disclaimer??"本图是 Aspen 流程模拟的草稿：本机未运行 Aspen Plus，组分、物性方法、热力学数据与设备尺寸均为待补，须由工程师在 Aspen 中补全并运行。"}</p>
      <div className="pfd-aspen-next">
       <span>下一步：按这张模拟图出 CAD 两张图纸</span>
       <button type="button" disabled={busy||!graph.nodes.length} onClick={()=>void previewSheet("section")}><Eye size={15}/>② 工段图预览</button>
       <button type="button" disabled={busy} onClick={()=>void previewSheet("legend")}><Eye size={15}/>② 图例图预览</button>
      </div>
     </div>}
    </>}
   </div>}
  </div>

  <div className="pfd-toolbar">
   <label>来源评估
    <select value={sourceAssessment} onChange={e=>setSourceAssessment(e.target.value)}>
     <option value="">选择已保存的放大评估</option>
     {assessments.map(a=><option key={a.id} value={a.id}>{a.spec.name}</option>)}
    </select>
   </label>
   <button type="button" disabled={busy||!assessments.length} onClick={generateFromAssessment}>从评估生成</button>
   <label>已保存图纸
    <select value={openId} onChange={e=>{setOpenId(e.target.value);void openDiagram(e.target.value);}}>
     <option value="">选择图纸</option>
     {items.map(d=><option key={d.id} value={d.id}>{d.title} · v{d.version}（{d.node_count} 节点/{d.edge_count} 连线）</option>)}
    </select>
   </label>
   <button type="button" disabled={busy} onClick={newBlank}>新建空白图</button>
   <label className="pfd-title-field">图纸名称
    <input value={title} onChange={e=>setTitle(e.target.value)} maxLength={160}/>
   </label>
   <button type="button" disabled={busy} onClick={()=>void save()}><Save size={16}/>{current?"保存修改":"保存为新图纸"}</button>
   <button type="button" disabled={busy} onClick={()=>void saveAsVersion()}>建立新版本</button>
   <label>演示案例
    <select value={demoSlug} onChange={e=>setDemoSlug(e.target.value)}>
     <option value="">选择演示案例</option>
     {demoCases.map(c=><option key={c.slug} value={c.slug}>{c.title}</option>)}
    </select>
   </label>
   <button type="button" disabled={busy||!demoSlug} onClick={()=>void loadDemo(demoSlug)}><Sparkles size={16}/>载入演示案例</button>
   <button type="button" disabled={busy||aspenBusy||!graph.nodes.length} onClick={()=>void runAspen(graph,title)}><ScanLine size={16}/>{aspenBusy?"生成模拟图…":"① 生成 Aspen 流程模拟图"}</button>
   <button type="button" disabled={busy||!graph.nodes.length} onClick={()=>void previewSheet("section")}><Eye size={16}/>② CAD 工段图预览</button>
   <button type="button" disabled={busy} onClick={()=>void previewSheet("legend")}><Eye size={16}/>② 图例图纸预览</button>
   <button type="button" disabled={busy||!graph.nodes.length} onClick={()=>void downloadSheet("svg")}><Download size={16}/>下载工段图 SVG</button>
   <button type="button" disabled={busy||!graph.nodes.length} onClick={()=>void downloadSheet("dxf")}><FileDown size={16}/>下载工段图 DXF</button>
   <button type="button" disabled={busy} onClick={()=>void downloadSheet("svg","legend")}><Download size={16}/>下载图例 SVG</button>
   <button type="button" disabled={busy} onClick={()=>void downloadSheet("dxf","legend")}><FileDown size={16}/>下载图例 DXF</button>  </div>

  {busy&&<p className="pfd-status">处理中…</p>}
  {error&&<p className="pfd-error" role="alert">{error}</p>}
  {notice&&<p className="pfd-notice">{notice}</p>}

 {assessmentsLoaded&&!assessments.length&&<p className="pfd-empty" role="status">还没有可用的放大评估：请先在上方“工艺工程与生产设计”里填写并保存一份放大评估，再回到这里生成可编辑图。也可以直接用上面的“AI 智能输入”粘贴工艺描述或上传资料自动出图，或“新建空白图”手工绘制。</p>}
 {listLoaded&&!items.length&&<p className="pfd-empty">本项目还没有保存过概念图纸。</p>}
  <div className="pfd-layout">
   <div className="pfd-stage" ref={stage}>
    <div className="pfd-canvas-tools">
     <button type="button" title="缩小" onClick={()=>setView(v=>({...v,k:Math.max(.2,v.k*.85)}))}><ZoomOut size={16}/></button>
     <button type="button" title="放大" onClick={()=>setView(v=>({...v,k:Math.min(3,v.k*1.18)}))}><ZoomIn size={16}/></button>
     <button type="button" title="适应画布" onClick={()=>setView(fitView(graph.nodes,size.w,size.h))}><Maximize2 size={16}/></button>
     <button type="button" title="撤销" disabled={!history.length} onClick={undo}><Undo2 size={16}/></button>
     <button type="button" title="重做" disabled={!future.length} onClick={redo}><Redo2 size={16}/></button>
     <span className="pfd-divider"/>
     <label className="pfd-inline">节点类型
      <select value={newKind} onChange={e=>setNewKind(e.target.value as PfdKind)}>{KIND_ORDER.map(k=><option key={k} value={k}>{KIND_LABELS[k]}</option>)}</select>
     </label>
     <button type="button" onClick={addNode}><Plus size={16}/>添加节点</button>
     <button type="button" className={connectFrom!==null?"active":""} onClick={()=>setConnectFrom(connectFrom===null?"":null)}><Link2 size={16}/>{connectFrom===null?"连线模式":connectFrom?"点击目标节点完成连线":"请点击起点节点"}</button>
     <button type="button" disabled={!selection} onClick={deleteSelection}><Trash2 size={16}/>删除选中</button>
     <span className="pfd-divider"/>
     <a download aria-disabled={!current} title="导出已保存图纸的工段概念流程图 SVG（白图纸面、黑线设备图形符号、正交物流线、主物料来源与去向标注、图名与右下角图签栏）" href={current?`${apiBase}${base}/diagrams/${current.id}/export`:undefined} onClick={e=>{if(!current)e.preventDefault();}}><Download size={16}/>导出工段图 SVG</a>
     <a download aria-disabled={!current} title="导出已保存图纸的工段概念流程图 DXF（AutoCAD R12）" href={current?`${apiBase}${base}/diagrams/${current.id}/export.dxf`:undefined} onClick={e=>{if(!current)e.preventDefault();}}><FileDown size={16}/>导出工段图 DXF</a>
     <a download aria-disabled={!current} title="导出已保存图纸的图例与符号说明图 SVG（CAD 图册第 1 张）" href={current?`${apiBase}${base}/diagrams/${current.id}/export?view=legend`:undefined} onClick={e=>{if(!current)e.preventDefault();}}><Download size={16}/>导出图例 SVG</a>
     <a download aria-disabled={!current} title="导出已保存图纸的图例与符号说明图 DXF（CAD 图册第 1 张）" href={current?`${apiBase}${base}/diagrams/${current.id}/export.dxf?view=legend`:undefined} onClick={e=>{if(!current)e.preventDefault();}}><FileDown size={16}/>导出图例 DXF</a>
    </div>
    <svg ref={svgRef} className="pfd-canvas" width={size.w} height={size.h} viewBox={`0 0 ${size.w} ${size.h}`}
     onPointerDown={onStagePointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp} onPointerCancel={onPointerUp}>
     <defs>
      <pattern id="pfd-grid" width="24" height="24" patternUnits="userSpaceOnUse"><path d="M24 0H0V24" fill="none" stroke="#e9f0ed" strokeWidth="1"/></pattern>
      <marker id="pfd-canvas-arrow" markerWidth="10" markerHeight="10" refX="9" refY="3" orient="auto"><path d="M0,0 L0,6 L9,3 z" fill="#0f7d68"/></marker>
     </defs>
     <rect width="100%" height="100%" fill="#fbfdfc"/>
     <rect width="100%" height="100%" fill="url(#pfd-grid)"/>
     <g transform={`translate(${view.tx},${view.ty}) scale(${view.k})`}>
      {graph.edges.map(edge=>{
       const from=graph.nodes.find(n=>n.id===edge.source),to=graph.nodes.find(n=>n.id===edge.target);
       if(!from||!to)return null;
       const [[x1,y1],[x2,y2]]=endpoints(from,to);
       const active=selection?.type==="edge"&&selection.id===edge.id;
       const utility=(edge.media??"material")==="utility";
       const valves=edge.components??[],meters=edge.instruments??[];
       const broken=Boolean(from.synthetic||to.synthetic);
       const stroke=active?"#a96b14":utility?"#2f6f9f":broken?"#b04a3a":"#0f7d68";
       return <g key={edge.id} className={active?"pfd-edge active":utility?"pfd-edge utility":"pfd-edge"}>
        <line x1={x1} y1={y1} x2={x2} y2={y2} stroke={stroke} strokeWidth={active?2.4:1.6} strokeDasharray={utility?"7 4":broken?"5 3":undefined} markerEnd="url(#pfd-canvas-arrow)"/>
        <line x1={x1} y1={y1} x2={x2} y2={y2} stroke="transparent" strokeWidth={14} onPointerDown={e=>{e.stopPropagation();setSelection({type:"edge",id:edge.id});}}/>
        {edge.label&&<text x={(x1+x2)/2} y={(y1+y2)/2-5} fontSize={10.5} textAnchor="middle" fill="#3c6a60">{edge.label}</text>}
        {valves.map((valve,index)=>{const f=lineFrame(x1,y1,x2,y2,Math.min(.34+index*.15,.72));
         return <g key={`v${index}`}>
          <path d={bowtiePath(f.cx,f.cy,f.ux,f.uy,7)} fill="#ffffff" stroke={stroke} strokeWidth={1.5} pointerEvents="none"/>
          {ACTUATED_VALVES.includes(valve.kind)&&<>
           <line x1={f.cx} y1={f.cy} x2={f.cx+f.px*15} y2={f.cy+f.py*15} stroke={stroke} strokeWidth={1.4} pointerEvents="none"/>
           <circle cx={f.cx+f.px*19} cy={f.cy+f.py*19} r={4.6} fill="#ffffff" stroke={stroke} strokeWidth={1.4} pointerEvents="none"/>
          </>}
          <text x={f.cx-f.px*13} y={f.cy-f.py*13-3} fontSize={8.5} textAnchor="middle" fill="#7a4a20" pointerEvents="none">{valve.tag||VALVE_LABELS[valve.kind]||"阀门"}</text>
         </g>;})}
        {meters.map((tag,index)=>{const f=lineFrame(x1,y1,x2,y2,Math.min(.74+index*.12,.92));
         return <g key={`m${index}`}>
          <circle cx={f.cx-f.px*15} cy={f.cy-f.py*15} r={8.5} fill="#ffffff" stroke="#2f6f9f" strokeWidth={1.4} pointerEvents="none"/>
          <line x1={f.cx-f.px*23.5} y1={f.cy-f.py*23.5} x2={f.cx-f.px*6.5} y2={f.cy-f.py*6.5} stroke="#2f6f9f" strokeWidth={1.2} pointerEvents="none"/>
          <text x={f.cx-f.px*15} y={f.cy-f.py*15+20} fontSize={8.5} textAnchor="middle" fill="#2f6f9f" pointerEvents="none">{tag}</text>
         </g>;})}
       </g>;
      })}
      {graph.nodes.map(node=>{
       const active=selection?.type==="node"&&selection.id===node.id;
       const armed=connectFrom!==null&&connectFrom!==node.id;
       const outlets=node.outlets??[];
       return <g key={node.id} className={active?"pfd-node active":node.synthetic?"pfd-node synthetic":"pfd-node"} onPointerDown={e=>onNodePointerDown(e,node)}>
        <rect x={node.x} y={node.y} width={node.w} height={node.h} rx={9} fill={node.synthetic?"#fdf1ef":KIND_FILL[node.kind]}
         stroke={active?"#a96b14":node.synthetic?"#b04a3a":armed?"#0f7d68":"#087e6b"} strokeWidth={active?2.4:1.4} strokeDasharray={node.synthetic?"6 4":armed?"5 3":undefined}/>
        <text x={node.x+10} y={node.y+19} fontSize={12.5} fontWeight={700} fill={node.synthetic?"#8f2f2f":"#123d35"}>{`${node.tag} ${node.label}`.trim()}</text>
        <text x={node.x+10} y={node.y+33} fontSize={9.5} fill={node.synthetic?"#b04a3a":"#6b837c"}>{node.synthetic?"未识别单元 · 请人工确认或删除":KIND_LABELS[node.kind]}</text>
        {node.params.slice(0,2).map((p,i)=><text key={i} x={node.x+10} y={node.y+47+i*12} fontSize={9.5} fill="#38564f">{`${p.name}：${p.value}${p.unit?" "+p.unit:""}`}</text>)}
        {node.params.length>2&&<text x={node.x+10} y={node.y+71} fontSize={9} fill="#8a9c96">{`另有 ${node.params.length-2} 项参数`}</text>}
        {outlets.slice(0,2).map((outlet,i)=><text key={`o${i}`} x={node.x+8} y={node.y+node.h+12+i*11} fontSize={9} fill="#a8452c" pointerEvents="none">{`差额去向：${outlet.name} ${outlet.value}${outlet.unit}`}</text>)}
       </g>;
      })}
     </g>
    </svg>
    <p className="pfd-hint">拖动空白处平移，滚轮缩放，拖动节点移动。启用“连线模式”后依次点击两个节点即可建立物流连接。</p>
   </div>

   <aside className="pfd-inspector">
    {!selection&&<p className="pfd-inspector-empty">选择图中节点或连线后，可在此编辑标签、位号、类型与参数。每个参数都必须填写来源与单位；缺失数据请填“待补”。</p>}
    {selectedNode&&<>
     <h5>节点 {selectedNode.id}</h5>
     <label>名称<input value={selectedNode.label} maxLength={80} onFocus={beginEdit} onChange={e=>{pushEdit();patchNode(selectedNode.id,{label:e.target.value});}}/></label>
     <label>设备位号<input value={selectedNode.tag} maxLength={24} placeholder="可留空" onFocus={beginEdit} onChange={e=>{pushEdit();patchNode(selectedNode.id,{tag:e.target.value});}}/></label>
     <label>类型<select value={selectedNode.kind} onChange={e=>patchNode(selectedNode.id,{kind:e.target.value as PfdKind})}>{KIND_ORDER.map(k=><option key={k} value={k}>{KIND_LABELS[k]}</option>)}</select></label>
     <div className="pfd-geometry">
      <label>X<input type="number" value={selectedNode.x} onChange={e=>patchNode(selectedNode.id,{x:Math.round(+e.target.value)})}/></label>
      <label>Y<input type="number" value={selectedNode.y} onChange={e=>patchNode(selectedNode.id,{y:Math.round(+e.target.value)})}/></label>
      <label>宽<input type="number" min={20} value={selectedNode.w} onChange={e=>patchNode(selectedNode.id,{w:Math.max(20,+e.target.value)})}/></label>
      <label>高<input type="number" min={20} value={selectedNode.h} onChange={e=>patchNode(selectedNode.id,{h:Math.max(20,+e.target.value)})}/></label>
     </div>
     <h6>参数（{selectedNode.params.length}/12）</h6>
     <ParamEditor params={selectedNode.params} target="node" id={selectedNode.id} beginEdit={beginEdit} pushEdit={pushEdit}
      onChange={(i,patch)=>patchParam("node",selectedNode.id,i,patch)} onRemove={i=>removeParam("node",selectedNode.id,i)} onAdd={()=>addParam("node",selectedNode.id)}/>
     {selectedNode.params.some(p=>!p.source.trim())&&<p className="pfd-error">有参数缺少来源；请补充来源或标注“待补”，不要留空。</p>}
     <h6>公用工程接管（{(selectedNode.utilities??[]).length}/8）</h6>
     {(selectedNode.utilities??[]).length===0&&<p className="pfd-inspector-meta">这台设备还没有公用工程接管：逐段温度要求、用蒸汽还是冷却水、接入点与用量都可以在这里补。</p>}
     {(selectedNode.utilities??[]).map((u,i)=><div className="pfd-geometry" key={`u${i}`}>
      <label>介质<select value={u.medium??""} onChange={e=>{pushEdit();patchUtility(selectedNode.id,i,{medium:e.target.value});}}>
       <option value="">（未填）</option>
       {MEDIA_OPTIONS.map(m=><option key={m.value} value={m.value}>{m.label}</option>)}
      </select></label>
      <label>规格 / 温度<input value={u.service??""} maxLength={80} placeholder="如 0.6 MPa 饱和蒸汽 158 ℃" onFocus={beginEdit} onChange={e=>{pushEdit();patchUtility(selectedNode.id,i,{service:e.target.value});}}/></label>
      <label>用量<input value={u.value??""} maxLength={40} onFocus={beginEdit} onChange={e=>{pushEdit();patchUtility(selectedNode.id,i,{value:e.target.value});}}/></label>
      <label>单位<input value={u.unit??""} maxLength={20} placeholder="kg/h" onFocus={beginEdit} onChange={e=>{pushEdit();patchUtility(selectedNode.id,i,{unit:e.target.value});}}/></label>
      <label>接入方位<select value={u.port??""} onChange={e=>{pushEdit();patchUtility(selectedNode.id,i,{port:e.target.value});}}>
       <option value="">自动</option><option value="n">上</option><option value="s">下</option><option value="w">左</option><option value="e">右</option>
      </select></label>
      <label>进 / 出<select value={u.direction??""} onChange={e=>{pushEdit();patchUtility(selectedNode.id,i,{direction:e.target.value});}}>
       <option value="">未定</option><option value="in">进（供入本设备）</option><option value="out">出（回水/冷凝水）</option>
      </select></label>
      <label>依据<input value={u.source??""} maxLength={160} placeholder="资料原文出处" onFocus={beginEdit} onChange={e=>{pushEdit();patchUtility(selectedNode.id,i,{source:e.target.value});}}/></label>
      <button type="button" onClick={()=>removeUtility(selectedNode.id,i)}>删除这条接管</button>
     </div>)}
     <button type="button" disabled={(selectedNode.utilities??[]).length>=8} onClick={()=>addUtility(selectedNode.id)}>增加公用工程接管</button>
     <h6>安全阀组 / 放空 / 排污（{(selectedNode.reliefs??[]).length}/6）</h6>
     {(selectedNode.reliefs??[]).length===0&&<p className="pfd-inspector-meta">资料里没有提到安全泄放就不画：安全阀定压、泄放去向都要有依据才落在图上。</p>}
     {(selectedNode.reliefs??[]).map((r,i)=><div className="pfd-geometry" key={`r${i}`}>
      <label>型式<select value={r.kind??"safety_valve"} onChange={e=>{pushEdit();patchRelief(selectedNode.id,i,{kind:e.target.value});}}>
       {RELIEF_OPTIONS.map(o=><option key={o.value} value={o.value}>{o.label}</option>)}
      </select></label>
      <label>位号<input value={r.tag??""} maxLength={24} placeholder="如 PSV-101" onFocus={beginEdit} onChange={e=>{pushEdit();patchRelief(selectedNode.id,i,{tag:e.target.value});}}/></label>
      <label>定压 / 口径<input value={r.value??""} maxLength={40} onFocus={beginEdit} onChange={e=>{pushEdit();patchRelief(selectedNode.id,i,{value:e.target.value});}}/></label>
      <label>单位<input value={r.unit??""} maxLength={20} placeholder="MPa" onFocus={beginEdit} onChange={e=>{pushEdit();patchRelief(selectedNode.id,i,{unit:e.target.value});}}/></label>
      <label>泄放去向<input value={r.to??""} maxLength={40} placeholder="放空 / 火炬 / 回收罐" onFocus={beginEdit} onChange={e=>{pushEdit();patchRelief(selectedNode.id,i,{to:e.target.value});}}/></label>
      <label>依据<input value={r.source??""} maxLength={160} placeholder="资料原文出处" onFocus={beginEdit} onChange={e=>{pushEdit();patchRelief(selectedNode.id,i,{source:e.target.value});}}/></label>
      <button type="button" onClick={()=>removeRelief(selectedNode.id,i)}>删除这一处</button>
     </div>)}
     <button type="button" disabled={(selectedNode.reliefs??[]).length>=6} onClick={()=>addRelief(selectedNode.id)}>增加安全阀组 / 放空</button>
    </>}
    {selectedEdge&&<>
     <h5>连接线 {selectedEdge.id}</h5>
     <p className="pfd-inspector-meta">{selectedEdge.source} → {selectedEdge.target}</p>
     <label>物流标签<input value={selectedEdge.label} maxLength={80} onFocus={beginEdit} onChange={e=>{pushEdit();patchEdge(selectedEdge.id,{label:e.target.value});}}/></label>
     <label>介质（决定是否计入物料守恒）<select value={selectedEdge.media??"material"} onChange={e=>{pushEdit();patchEdge(selectedEdge.id,{media:e.target.value as "material"|"utility"});}}>
      <option value="material">工艺物料流（计入物料守恒校核）</option>
      <option value="utility">公用工程（蒸汽/冷却水/冷冻水等，不计入）</option>
     </select></label>
     <label>介质与规格<input value={selectedEdge.service??""} maxLength={80} placeholder="公用工程线填介质与温度，如 0.6 MPa 饱和蒸汽 158 ℃" onFocus={beginEdit} onChange={e=>{pushEdit();patchEdge(selectedEdge.id,{service:e.target.value});}}/></label>
     <h6>仪表信号回路（{(selectedEdge.signals??[]).length}/6）</h6>
     {(selectedEdge.signals??[]).length===0&&<p className="pfd-inspector-meta">没有信号回路时，仪表只画一条引线；填上“由哪个仪表控制哪个执行机构”，图上就会把信号线连到调节阀。</p>}
     {(selectedEdge.signals??[]).map((s,i)=><div className="pfd-geometry" key={`g${i}`}>
      <label>仪表位号<input value={s.tag} maxLength={24} placeholder="如 FIC-101" onFocus={beginEdit} onChange={e=>{pushEdit();patchSignal(selectedEdge.id,i,{tag:e.target.value});}}/></label>
      <label>执行机构<input value={s.target??""} maxLength={24} placeholder="如 FV-101" onFocus={beginEdit} onChange={e=>{pushEdit();patchSignal(selectedEdge.id,i,{target:e.target.value});}}/></label>
      <label>信号形式<select value={s.kind??""} onChange={e=>{pushEdit();patchSignal(selectedEdge.id,i,{kind:e.target.value});}}>
       {SIGNAL_OPTIONS.map(o=><option key={o.value} value={o.value}>{o.label}</option>)}
      </select></label>
      <label>依据<input value={s.source??""} maxLength={160} onFocus={beginEdit} onChange={e=>{pushEdit();patchSignal(selectedEdge.id,i,{source:e.target.value});}}/></label>
      <button type="button" onClick={()=>removeSignal(selectedEdge.id,i)}>删除这条回路</button>
     </div>)}
     <button type="button" disabled={(selectedEdge.signals??[]).length>=6} onClick={()=>addSignal(selectedEdge.id)}>增加仪表信号回路</button>
     <h6>参数（{selectedEdge.params.length}/8）</h6>
     <ParamEditor params={selectedEdge.params} target="edge" id={selectedEdge.id} beginEdit={beginEdit} pushEdit={pushEdit}
      onChange={(i,patch)=>patchParam("edge",selectedEdge.id,i,patch)} onRemove={i=>removeParam("edge",selectedEdge.id,i)} onAdd={()=>addParam("edge",selectedEdge.id)}/>
     {selectedEdge.params.some(p=>!p.source.trim())&&<p className="pfd-error">有参数缺少来源；请补充来源或标注“待补”，不要留空。</p>}
    </>}
   </aside>
  </div>
     {aspenModal&&<div className="pfd-sheet-overlay" role="dialog" aria-modal="true" aria-label="Aspen 流程模拟图预览（第 1 步）">
    <div className="pfd-sheet-modal">
     <header className="pfd-sheet-head">
      <div><strong>Aspen 流程模拟图（草稿）· 第 ① 步</strong><small>按 Aspen 出图习惯绘制的方块单元流程图：方块 = 单元操作并给出 Aspen 模型建议，物流线上标注物流号与流量，下方为物流表。本机没有 Aspen Plus 授权，本图与 .inp 骨架不含组分 / 物性 / 热力学结果，须由工程师在 Aspen 中补全并运行；跑通模拟后再据此出 ② CAD 的 PFD 与图例两张图纸。这样出图比“文本直接生 CAD”更接近设计院流程：先模拟核对，再落图纸。</small></div>
      <button type="button" onClick={()=>setAspenModal(false)}><X size={18}/>关闭预览</button>
     </header>
     {aspenStage?.svg&&<>
      <div className="pfd-sheet-tools">
       <span>图面缩放</span>
       <button type="button" className={aspenZoom===1?"active":undefined} onClick={()=>setAspenZoom(1)}>适应窗口</button>
       <button type="button" className={aspenZoom===1.6?"active":undefined} onClick={()=>setAspenZoom(1.6)}>160%</button>
       <button type="button" className={aspenZoom===2.4?"active":undefined} onClick={()=>setAspenZoom(2.4)}>240%</button>
       <a href={aspenUrl||undefined} target="_blank" rel="noreferrer">在新标签页打开</a>
      </div>
      <div className="pfd-sheet-canvas">
       {/* eslint-disable-next-line @next/next/no-img-element -- Aspen 流程模拟图是本会话生成的 blob URL，不是远端资源 */}
       <img src={aspenUrl} alt="Aspen 风格流程模拟图草稿" style={{width:`${aspenZoom*100}%`}}/>
      </div>
     </>}
     <footer className="pfd-sheet-foot">
      <p className="pfd-disclaimer">本图是 Aspen 流程模拟的草稿：本机未运行 Aspen Plus，组分、物性方法、热力学数据与设备尺寸均为待补，须由工程师在 Aspen 中补全并运行。</p>
      <div>
       <button type="button" disabled={!aspenStage?.inp} onClick={downloadAspenInp}><FileDown size={16}/>导出 Aspen 输入 .inp</button>
       <button type="button" disabled={!graph.nodes.length} onClick={()=>{setAspenModal(false);void previewSheet("section");}}><Eye size={16}/>进入 ② CAD 工段图</button>
      </div>
     </footer>
    </div>
   </div>}
   {sheetOpen&&<div className="pfd-sheet-overlay" role="dialog" aria-modal="true" aria-label="CAD 概念图纸预览（两张）">
   <div className="pfd-sheet-modal">
    <header className="pfd-sheet-head">
      <div><strong>CAD 概念图纸预览 · 两张图纸</strong><small>出图流程分两步：文本 / 图片 → <b>① Aspen 流程模拟图</b>（先核对单元、物流与流量）→ <b>② CAD 图纸</b>。按设计院惯例，一个 CAD 文档出两张图：第 1 张图例与符号说明（设备 / 阀门 / 仪表符号、线型与介质、物料流向与守恒说明，以及设备一览表、物流表、管道附件与仪表表、物料守恒校核表四张附表）；第 2 张工段概念流程图（白图纸面、黑线设备图形符号、正交物流线与流向箭头、每条物流的流量、主物料线的来源与去向标注与图纸说明，文字表格不再占用图面）。下载的 SVG / DXF 与预览内容完全一致，均由平台按当前图纸生成。</small></div>
     <button type="button" onClick={()=>setSheetOpen(false)}><X size={18}/>关闭预览</button>
    </header>
    <div className="pfd-sheet-tabs" role="tablist" aria-label="两张 CAD 图纸">
     <button type="button" role="tab" aria-selected={sheetView==="legend"} className={sheetView==="legend"?"active":undefined} onClick={()=>void previewSheet("legend")}>第 1 张 · 图例与符号说明</button>
     <button type="button" role="tab" aria-selected={sheetView==="section"} className={sheetView==="section"?"active":undefined} onClick={()=>void previewSheet("section")}>第 2 张 · 工段概念流程图</button>
     <span className="pfd-sheet-tab-note">图例不含流程节点，四张附表（设备一览表、物流表、管道附件与仪表表、物料守恒校核表）统一排在图例图下方；工段图含节点、物流与流量，只在右下角保留图签栏，文字表格不再占用图面。</span>
    </div>
    {sheet.busy&&<p className="pfd-status">正在生成图纸…</p>}
    {sheet.error&&<p className="pfd-error" role="alert">{sheet.error}</p>}
    {sheet.url&&<>
     <div className="pfd-sheet-tools">
      <span>图面缩放</span>
      <button type="button" className={sheetZoom===1?"active":undefined} onClick={()=>setSheetZoom(1)}>适应窗口</button>
      <button type="button" className={sheetZoom===1.6?"active":undefined} onClick={()=>setSheetZoom(1.6)}>160%</button>
      <button type="button" className={sheetZoom===2.4?"active":undefined} onClick={()=>setSheetZoom(2.4)}>240%</button>
      <a href={sheet.url} target="_blank" rel="noreferrer">在新标签页打开（可缩放、可打印）</a>
     </div>
     <div className="pfd-sheet-canvas">
      {/* eslint-disable-next-line @next/next/no-img-element -- the CAD sheet is a same-session blob URL, not a remote asset */}
      <img src={sheet.url} alt={sheetView==="legend"?"概念级工艺流程 CAD 图例与说明图纸":"概念级工艺流程 CAD 工段概念流程图"} style={{width:`${sheetZoom*100}%`}}/>
     </div>
    </>}
    <footer className="pfd-sheet-foot">
     <p className="pfd-disclaimer">概念级工艺流程图，仅供研发讨论，非施工图或已验证 P&amp;ID。管径、仪表、安全联锁、设备强度与已验证产能均待合格工程人员设计。</p>
     <div>
      <button type="button" disabled={sheet.busy} onClick={()=>void downloadSheet("svg",sheetView)}><Download size={16}/>下载本页 SVG</button>
      <button type="button" disabled={sheet.busy} onClick={()=>void downloadSheet("dxf",sheetView)}><FileDown size={16}/>下载本页 DXF</button>
     </div>
    </footer>
   </div>
  </div>} </section>;
}

function ParamEditor({params,target,id,onChange,onRemove,onAdd,beginEdit,pushEdit}:{
 params:PfdParam[];target:"node"|"edge";id:string;
 onChange:(index:number,patch:Partial<PfdParam>)=>void;onRemove:(index:number)=>void;onAdd:()=>void;
 beginEdit:()=>void;pushEdit:()=>void;
}){
 return <div className="pfd-params" data-target={target} data-id={id}>
  {params.map((param,index)=><div className="pfd-param" key={index}>
   <input value={param.name} maxLength={40} placeholder="参数名" aria-label="参数名" onFocus={beginEdit}
    onChange={e=>{pushEdit();onChange(index,{name:e.target.value});}}/>
   <input value={param.value} maxLength={60} placeholder="数值/待补" aria-label="数值" onFocus={beginEdit}
    onChange={e=>{pushEdit();onChange(index,{value:e.target.value});}}/>
   <input value={param.unit} maxLength={20} placeholder="单位" aria-label="单位" onFocus={beginEdit}
    onChange={e=>{pushEdit();onChange(index,{unit:e.target.value});}}/>
   <input value={param.source} maxLength={160} placeholder="来源（必填）" aria-label="来源" onFocus={beginEdit}
    onChange={e=>{pushEdit();onChange(index,{source:e.target.value});}}/>
   <button type="button" title="删除参数" onClick={()=>onRemove(index)}><Trash2 size={14}/></button>
  </div>)}
  <button type="button" className="pfd-param-add" onClick={onAdd}><Plus size={14}/>添加参数</button>
 </div>;
}
