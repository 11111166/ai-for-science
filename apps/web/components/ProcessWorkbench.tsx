"use client";
import {useState} from "react";
import {Beaker,BookOpen,ClipboardCheck,Factory} from "lucide-react";
import {ProcessDevelopment} from "./ProcessDevelopment";
import {PublicHteDemo} from "./PublicHteDemo";
import {ProcessScaleUp} from "./ProcessScaleUp";
import {CompoundOrderAssessment} from "./CompoundOrderAssessment";
import {ProcessKnowledgeBase} from "./ProcessKnowledgeBase";
type Tool="optimization"|"scale-up"|"order"|"knowledge";
export function ProcessWorkbench({projectId,molecules=[]}:{projectId:string;molecules?:{identifier:string;name:string}[]}){const [tool,setTool]=useState<Tool>("scale-up");const tools=[
 {id:"scale-up" as const,title:"工艺工程、CAD 与生产设计",text:"可视化 CAD/PFD 画布、衡算、设备材质与 Aspen 物流",icon:<Factory size={22}/>},
 {id:"optimization" as const,title:"实验条件与贝叶斯优化",text:"用真实实验逐轮推荐下一批条件",icon:<Beaker size={22}/>},
 {id:"order" as const,title:"路线、成本与接单评估",text:"公开路线、纯化、报价与专利核查",icon:<ClipboardCheck size={22}/>}
 ];return <section className="process-workbench"><header className="process-workbench-heading"><span>小分子合成与制药工艺</span><h2>从合成目标推进到实验、生产和接单决策</h2></header><nav className="process-tool-nav" aria-label="工艺研发工具">{tools.map(x=><button type="button" key={x.id} className={tool===x.id?"active":""} onClick={()=>setTool(x.id)}>{x.icon}<span><strong>{x.title}</strong><small>{x.text}</small></span></button>)}</nav><button type="button" className={`process-knowledge-entry ${tool==="knowledge"?"active":""}`} aria-pressed={tool==="knowledge"} onClick={()=>setTool("knowledge")}><BookOpen size={20}/><span><strong>化工原料、原理与反应工艺知识库</strong><small>查询物料、反应、工程原理和研发决策清单</small></span></button>{tool==="optimization"&&<><PublicHteDemo projectId={projectId}/><ProcessDevelopment projectId={projectId} molecules={molecules}/></>}{tool==="scale-up"&&<ProcessScaleUp projectId={projectId}/>} {tool==="order"&&<CompoundOrderAssessment projectId={projectId}/>} {tool==="knowledge"&&<ProcessKnowledgeBase projectId={projectId}/>}</section>}
