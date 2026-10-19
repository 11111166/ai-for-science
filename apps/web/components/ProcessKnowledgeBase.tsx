"use client";
import {useEffect,useMemo,useState} from "react";
import {BookOpen,Search} from "lucide-react";
import {api} from "@/lib/api";

type Summary={version:string;updated_at:string;material_count:number;reaction_template_count:number;principle_count:number;topic_count:number;categories:{id:string;label:string;count:number}[];runtime:string;scope:string;method_basis:{label:string;url:string}[]};
type Material={id:string;name:string;english_name:string;aliases:string[];cas:string;pubchem_cid:number;formula:string;molecular_weight:number;smiles:string;category:string;category_label:string;xlogp:number|null;tpsa:number|null;common_use:string;process_caution:string;decision_questions:string[];verify_before_scale:string[];evidence:{identity:string;guidance:string};source_url:string};
type PrincipleLink={id:string;name:string};
type Reaction={id:string;name:string;category:string;goal:string;common_choices:string[];screen:string[];workup:string[];analysis:string[];scale_risks:string[];next:string;principles:PrincipleLink[]};
type Formula={name:string;expression:string;variables:string};
type Principle={id:string;name:string;category:string;question:string;conclusion:string;formulas:Formula[];inputs:string[];signals:string[];mistakes:string[];next:string};
type Topic={id:string;name:string;category:string;goal:string;decision:string;checklist:string[];deliverables:string[];red_flags:string[];next:string};
type ListResult<T>={total:number;items:T[];version:string};
type View="materials"|"reactions"|"principles"|"topics";

const normalize=(value:string)=>value.toLocaleLowerCase().replace(/[\s_\-—–·,，;；()（）\[\].]+/g,"");
const includes=(values:(string|number)[],query:string)=>!query||values.some(x=>normalize(String(x)).includes(normalize(query)));

export function ProcessKnowledgeBase({projectId}:{projectId:string}){
 const base=`/projects/${projectId}/process/knowledge`;
 const [summary,setSummary]=useState<Summary|null>(null),[materials,setMaterials]=useState<Material[]>([]),[reactions,setReactions]=useState<Reaction[]>([]),[principles,setPrinciples]=useState<Principle[]>([]),[topics,setTopics]=useState<Topic[]>([]);
 const [view,setView]=useState<View>("materials"),[query,setQuery]=useState(""),[category,setCategory]=useState(""),[active,setActive]=useState(""),[error,setError]=useState("");
 useEffect(()=>{let live=true;Promise.all([api<Summary>(`${base}/summary`),api<ListResult<Material>>(`${base}/materials`),api<ListResult<Reaction>>(`${base}/reaction-templates`),api<ListResult<Principle>>(`${base}/principles`),api<ListResult<Topic>>(`${base}/topics`)]).then(([s,m,r,p,t])=>{if(live){setSummary(s);setMaterials(m.items);setReactions(r.items);setPrinciples(p.items);setTopics(t.items);}}).catch(e=>live&&setError(e instanceof Error?e.message:"知识库加载失败"));return()=>{live=false;};},[base]);
 const materialCategories=summary?.categories??[];
 const reactionCategories=useMemo(()=>[...new Set(reactions.map(x=>x.category))],[reactions]);
 const principleCategories=useMemo(()=>[...new Set(principles.map(x=>x.category))],[principles]);
 const topicCategories=useMemo(()=>[...new Set(topics.map(x=>x.category))],[topics]);
 const visibleMaterials=useMemo(()=>materials.filter(x=>(!category||x.category===category)&&includes([x.name,x.english_name,x.cas,x.pubchem_cid,x.formula,...x.aliases],query)),[materials,category,query]);
 const visibleReactions=useMemo(()=>reactions.filter(x=>(!category||x.category===category)&&includes([x.name,x.goal,x.category,...x.common_choices,...x.principles.map(p=>p.name)],query)),[reactions,category,query]);
 const visiblePrinciples=useMemo(()=>principles.filter(x=>(!category||x.category===category)&&includes([x.name,x.category,x.question,x.conclusion,...x.inputs,...x.signals,...x.mistakes,...x.formulas.flatMap(f=>[f.name,f.expression])],query)),[principles,category,query]);
 const visibleTopics=useMemo(()=>topics.filter(x=>(!category||x.category===category)&&includes([x.name,x.category,x.goal,x.decision,...x.checklist,...x.deliverables,...x.red_flags,x.next],query)),[topics,category,query]);
 function changeView(next:View,nextQuery=""){setView(next);setQuery(nextQuery);setCategory("");setActive("");}
 const categories=view==="materials"?materialCategories.map(x=>({id:x.id,label:`${x.label} ${x.count}`})):view==="reactions"?reactionCategories.map(x=>({id:x,label:x})):view==="principles"?principleCategories.map(x=>({id:x,label:x})):topicCategories.map(x=>({id:x,label:x}));
 const placeholder=view==="materials"?"搜索中文名、英文名、CAS、分子式或别名":view==="reactions"?"搜索反应名称、目标、风险或相关原理":view==="principles"?"搜索问题、公式、工程现象或操作步骤":"搜索路线、杂质、安全、分析、转移或成本问题";
 return <section className="process-tool-panel process-knowledge"><header className="process-subheading"><div><span>本地知识库</span><h3>从物料与反应推进到研发决策</h3><p>先看用途和结论；需要时再展开核查项、工程依据与下一步。</p></div>{summary&&<b>{summary.material_count} 种物料 · {summary.reaction_template_count} 类反应 · {summary.principle_count} 项原理 · {summary.topic_count} 个专题</b>}</header>
  {error&&<p role="alert" className="notice error">{error}</p>}
  <div className="knowledge-mode" role="group" aria-label="知识库类型"><button type="button" className={view==="materials"?"active":""} aria-pressed={view==="materials"} onClick={()=>changeView("materials")}>原料与试剂</button><button type="button" className={view==="reactions"?"active":""} aria-pressed={view==="reactions"} onClick={()=>changeView("reactions")}>反应与工艺</button><button type="button" className={view==="principles"?"active":""} aria-pressed={view==="principles"} onClick={()=>changeView("principles")}>化工原理</button><button type="button" className={view==="topics"?"active":""} aria-pressed={view==="topics"} onClick={()=>changeView("topics")}>研发专题</button></div>
  <label className="knowledge-search"><Search size={18}/><span className="sr-only">搜索知识库</span><input value={query} onChange={e=>{setQuery(e.target.value);setActive("");}} placeholder={placeholder}/></label>
  <div className="knowledge-filters"><button type="button" className={!category?"active":""} onClick={()=>{setCategory("");setActive("");}}>全部</button>{categories.map(x=><button type="button" key={x.id} className={category===x.id?"active":""} onClick={()=>{setCategory(x.id);setActive("");}}>{x.label}</button>)}</div>
  {view==="materials"?<MaterialList items={visibleMaterials} active={active} setActive={setActive}/>:view==="reactions"?<ReactionList items={visibleReactions} active={active} setActive={setActive} openPrinciple={(p)=>changeView("principles",p.name)}/>:view==="principles"?<PrincipleList items={visiblePrinciples} active={active} setActive={setActive}/>:<TopicList items={visibleTopics} active={active} setActive={setActive}/>} 
  {summary&&<details className="knowledge-scope"><summary>覆盖范围与方法口径</summary><p>{summary.scope}</p><p>{summary.runtime}</p><div className="knowledge-basis">{summary.method_basis.map(x=><a key={x.url} href={x.url} target="_blank" rel="noreferrer">{x.label}</a>)}</div><small>版本 {summary.version} · 更新 {summary.updated_at}</small></details>}
 </section>;
}

function MaterialList({items,active,setActive}:{items:Material[];active:string;setActive:(id:string)=>void}){
 if(!items.length)return <Empty type="material"/>;
 return <div className="knowledge-grid">{items.map(x=><article key={x.id} className={active===x.id?"active":""}><header><div><span>{x.category_label}</span><h4>{x.name}</h4><small>{x.english_name}</small></div><button type="button" aria-expanded={active===x.id} onClick={()=>setActive(active===x.id?"":x.id)}>{active===x.id?"收起":"查看详情"}</button></header><div className="knowledge-facts"><span>CAS <b>{x.cas}</b></span><span>{x.formula} · {x.molecular_weight} g/mol</span></div><p><b>常见用途：</b>{x.common_use}</p><p className="knowledge-caution"><b>工艺关注：</b>{x.process_caution}</p>{active===x.id&&<div className="knowledge-detail material-detail"><dl><div><dt>PubChem CID</dt><dd>{x.pubchem_cid}</dd></div><div><dt>XLogP</dt><dd>{x.xlogp??"暂无"}</dd></div><div><dt>TPSA</dt><dd>{x.tpsa??"暂无"} Å²</dd></div></dl><KnowledgeList title="选择时要回答" items={x.decision_questions}/><KnowledgeList title="放大前需核对" items={x.verify_before_scale}/><div className="material-structure"><strong>结构字符串（SMILES）</strong><code>{x.smiles}</code></div><small>{x.evidence.identity}；{x.evidence.guidance}。</small><a href={x.source_url} target="_blank" rel="noreferrer">核对 PubChem 记录</a></div>}</article>)}</div>;
}

function ReactionList({items,active,setActive,openPrinciple}:{items:Reaction[];active:string;setActive:(id:string)=>void;openPrinciple:(p:PrincipleLink)=>void}){
 if(!items.length)return <Empty type="reaction"/>;
 return <div className="knowledge-grid reaction-knowledge">{items.map(x=><article key={x.id} className={active===x.id?"active":""}><header><div><span>{x.category}</span><h4>{x.name}</h4></div><button type="button" aria-expanded={active===x.id} onClick={()=>setActive(active===x.id?"":x.id)}>{active===x.id?"收起":"查看工艺要点"}</button></header><p>{x.goal}</p><div className="knowledge-tags">{x.common_choices.map(v=><span key={v}>{v}</span>)}</div>{active===x.id&&<div className="knowledge-detail reaction-detail"><KnowledgeList title="重点考察" items={x.screen}/><KnowledgeList title="后处理思路" items={x.workup}/><KnowledgeList title="分析项目" items={x.analysis}/><KnowledgeList title="放大风险" items={x.scale_risks}/><section className="knowledge-related"><strong>相关工程原理</strong><div>{x.principles.map(p=><button type="button" key={p.id} onClick={()=>openPrinciple(p)}>{p.name}</button>)}</div></section><p className="knowledge-next"><b>下一步：</b>{x.next}</p><small>这是通用工艺模板；具体条件需用项目底物、设备和实验数据验证。</small></div>}</article>)}</div>;
}

function PrincipleList({items,active,setActive}:{items:Principle[];active:string;setActive:(id:string)=>void}){
 if(!items.length)return <Empty type="principle"/>;
 return <div className="knowledge-grid principle-knowledge">{items.map(x=><article key={x.id} className={active===x.id?"active":""}><header><div><span>{x.category}</span><h4>{x.name}</h4></div><button type="button" aria-expanded={active===x.id} onClick={()=>setActive(active===x.id?"":x.id)}>{active===x.id?"收起":"查看判断方法"}</button></header><p className="principle-question">{x.question}</p><p><b>结论：</b>{x.conclusion}</p>{active===x.id&&<div className="knowledge-detail principle-detail"><section className="formula-panel"><strong>常用关系</strong>{x.formulas.map(f=><div key={f.name}><span>{f.name}</span><code>{f.expression}</code><small>{f.variables}</small></div>)}</section><KnowledgeList title="需要先有的数据" items={x.inputs}/><KnowledgeList title="重点观察" items={x.signals}/><KnowledgeList title="常见误判" items={x.mistakes}/><p className="knowledge-next"><b>建议动作：</b>{x.next}</p><small>公式只用于研究设计和数量级判断；代入项目实测物性与设备能力后才能形成项目结论。</small></div>}</article>)}</div>;
}
function TopicList({items,active,setActive}:{items:Topic[];active:string;setActive:(id:string)=>void}){
 if(!items.length)return <Empty type="topic"/>;
 return <div className="knowledge-grid topic-knowledge">{items.map(x=><article key={x.id} className={active===x.id?"active":""}><header><div><span>{x.category}</span><h4>{x.name}</h4></div><button type="button" aria-expanded={active===x.id} onClick={()=>setActive(active===x.id?"":x.id)}>{active===x.id?"收起":"查看完整清单"}</button></header><p>{x.goal}</p><p className="topic-decision"><b>判断：</b>{x.decision}</p>{active===x.id&&<div className="knowledge-detail topic-detail"><KnowledgeList title="需要核查" items={x.checklist}/><KnowledgeList title="应形成的交付物" items={x.deliverables}/><section className="topic-red-flags"><strong>高风险信号</strong><ul>{x.red_flags.map(v=><li key={v}>{v}</li>)}</ul></section><p className="knowledge-next"><b>下一步：</b>{x.next}</p></div>}</article>)}</div>;
}
function KnowledgeList({title,items}:{title:string;items:string[]}){return <section><strong>{title}</strong><ul>{items.map(x=><li key={x}>{x}</li>)}</ul></section>}
function Empty({type}:{type:"material"|"reaction"|"principle"|"topic"}){const hint=type==="material"?"可换用中文名、英文名、CAS 或分子式。":type==="reaction"?"可换用反应名称、成键类型、风险或工程原理。":type==="principle"?"可搜索传热、混合、萃取、结晶、公式或具体工程现象。":"可搜索路线、物料、杂质、安全、固态、分析、转移或成本。";return <div className="knowledge-empty"><BookOpen size={26}/><h4>当前版本未找到匹配内容</h4><p>{hint} 没有命中表示尚未收录。</p></div>}
