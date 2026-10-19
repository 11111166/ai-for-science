// @vitest-environment jsdom
import React,{act} from "react";
import {createRoot} from "react-dom/client";
import {expect,it,vi} from "vitest";
import {api} from "@/lib/api";
import {ProcessPortfolio} from "./ProcessPortfolio";
import {ProcessQualityGuidance} from "./ProcessQualityGuidance";

vi.mock("@/lib/api",()=>({api:vi.fn()}));

it("allows a new target and DIY route without inventing a total yield",async()=>{
 Object.assign(globalThis,{IS_REACT_ACT_ENVIRONMENT:true});
 const saved={id:"one",created_at:"2026-09-17T00:00:00Z",input:{target_name:"目标 X",cas:"",smiles:"",product_class:"intermediate",intended_use:"兽药中间体",jurisdiction:"CN",target_purity_pct:null,target_annual_kg:null,routes:[{name:"自拟路线 A",kind:"user",source_reference:"",steps:[{transformation:"形成目标",intermediate:"",yield_pct:null,purification:"",source_reference:""}]}]},result:{route_summaries:[{name:"自拟路线 A",kind:"user",step_count:1,overall_yield_pct:null,missing_yield_steps:[1],source_status:"用户自拟，待实验"}],published_route_count:0,user_route_count:1,best_reported_yield_route:null,scorecard:[{module:"多路线工艺开发",status:"需补证",finding:"待测收率"}],gaps:["需核实来源"],decision:"先验证路线"}};
 vi.mocked(api).mockImplementation(async(_path,options)=>options?saved:[]);
 const host=document.createElement("div");document.body.append(host);const root=createRoot(host);
 const button=(text:string)=>[...host.querySelectorAll("button")].find(element=>element.textContent?.includes(text))!;
 try{
  await act(async()=>root.render(<ProcessPortfolio projectId="p"/>));
  await act(async()=>button("添加路线").click());
  const inputs=[...host.querySelectorAll("input")];
  function input(element:HTMLInputElement,value:string){const setter=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,"value")!.set!;setter.call(element,value);element.dispatchEvent(new Event("input",{bubbles:true}));}
  await act(async()=>{input(inputs[0],"目标 X");input(inputs[2],"");input(inputs[3],"兽药中间体");});
  const named=[...host.querySelectorAll("input")].find(element=>element.closest("label")?.textContent?.includes("路线名称"))!;
  const transformed=[...host.querySelectorAll("input")].find(element=>element.closest("label")?.textContent?.includes("转化 / 操作"))!;
  await act(async()=>{input(named,"自拟路线 A");input(transformed,"形成目标");});
  await act(async()=>{host.querySelector("form")!.dispatchEvent(new Event("submit",{bubbles:true,cancelable:true}));});
  expect(vi.mocked(api).mock.calls.at(-1)?.[1]).toMatchObject({method:"POST"});
  expect(host.textContent).toContain("未纳入第 1 步");
  expect(host.textContent).toContain("Process Development Scorecard");
 }finally{await act(async()=>root.unmount());host.remove();}
});

it("maps human-drug guidance separately from veterinary guidance",async()=>{
 Object.assign(globalThis,{IS_REACT_ACT_ENVIRONMENT:true});
 const host=document.createElement("div");document.body.append(host);const root=createRoot(host);
 try{
  await act(async()=>root.render(<ProcessQualityGuidance productClass="human_api" cas="" jurisdiction="EP" targetPurityPct={99.5}/>));
  expect(host.textContent).toContain("ICH Q6A");
  expect(host.textContent).toContain("99.5%（用户拟定，尚非监管限度）");
  expect(host.textContent).not.toContain("VICH GL39");
  await act(async()=>root.render(<ProcessQualityGuidance productClass="veterinary_api" cas="864731-61-3" jurisdiction="CN" targetPurityPct={null}/>));
  expect(host.textContent).toContain("VICH GL39");
  expect(host.textContent).toContain("第 986 号公告");
  expect(host.textContent).toContain("色谱面积百分比不等于含量测定");
  expect(host.textContent).toContain("氰化步骤的残留/副产物风险");
 }finally{await act(async()=>root.unmount());host.remove();}
});

it("reads the public patent case without saving or fabricating a full-route yield",async()=>{
 Object.assign(globalThis,{IS_REACT_ACT_ENVIRONMENT:true});
 const steps=Array.from({length:7},(_,index)=>({transformation:`公开步骤 ${index+1}`,intermediate:index===6?"氟雷拉纳":`中间体 ${index+1}`,yield_pct:index===6?null:[57,64,76,96,82,87][index],purification:"过滤",source_reference:`WO2023012821A1 #p${index+1}`}));
 const payload={input:{target_name:"氟雷拉纳（Fluralaner）",cas:"864731-61-3",smiles:"",product_class:"veterinary_api",intended_use:"兽药活性成分",jurisdiction:"CN",target_purity_pct:null,target_annual_kg:null,routes:[{name:"完整公开路径",kind:"patent",source_reference:"WO2023012821A1",steps},{name:"末端结晶 1",kind:"patent",source_reference:"EP4077285A1",steps:[{...steps[6],yield_pct:75.5}]},{name:"末端结晶 2",kind:"patent",source_reference:"EP4077285A1",steps:[{...steps[6],yield_pct:73.9}]}]},snapshot:{WO2023012821A1:{url:"https://patents.google.com/patent/WO2023012821A1/en",source_kind:"镜像",retrieved_at:"2026-09-17T00:00:00Z",source_sha256:"a"},EP4077285A1:{url:"https://patents.google.com/patent/EP4077285A1/en",source_kind:"镜像",retrieved_at:"2026-09-17T00:00:00Z",source_sha256:"b"}},wo_steps:steps.map((_,index)=>({number:index+1,reported_yield_pct:index===6?82:[57,64,76,96,82,87][index],accepted_yield_pct:index===6?null:[57,64,76,96,82,87][index],warning:index===6?"质量数字矛盾":"",paragraphs:[{locator:`p${index+1}`,text:`Patent step ${index+1} original text`,url:`https://patents.google.com/patent/WO2023012821A1/en#p${index+1}`}]})),ep_variants:[{name:"末端结晶 1",reported_yield_pct:75.5,hplc_area_pct:">99 area%",paragraphs:[]},{name:"末端结晶 2",reported_yield_pct:73.9,hplc_area_pct:">98 area%",paragraphs:[]}],hplc_method:[],limitations:["不可当成全路线收率"]};
 Object.assign(payload,{intermediate_screen:{screening_rule:"超过 5 暂缓",basis:"平台挂牌不等于生产厂家",shortlist_count:0,research_priority:"优先核对步骤 4 和 5",entries:[{step:2,name:"4-乙酰基-2-甲基苯甲腈",cas:"1138444-80-0",supplier_listings:56,manufacturer_count:null,supplier_source:"https://www.chemicalbook.com/CAS_1138444-80-0.htm",supplier_checked_at:"2026-09-17T00:00:00Z",patent_source:"https://patents.google.com/patent/WO2023012821A1/en#p0219",difficulty:"高",difficulty_basis:"氰化物与柱层析",barrier_to_verify:"工厂能力需复核",decision_status:"挂牌较多 · 暂缓",decision_reason:"超过筛选阈值",priority_to_investigate:false},{step:5,name:"三氟烯酮中间体",cas:null,supplier_listings:null,manufacturer_count:null,supplier_source:null,supplier_checked_at:null,patent_source:"https://patents.google.com/patent/WO2023012821A1/en#p0231",difficulty:"高",difficulty_basis:"多片段拼接",barrier_to_verify:"成本待核",decision_status:"待核供应",decision_reason:"未知不能当成少",priority_to_investigate:true}],branch_reagent:{name:"三氟酮",cas:"130336-16-2",supplier_listings:284,manufacturer_count:null,supplier_source:"https://www.chemicalbook.com",supplier_checked_at:"2026-09-17T00:00:00Z",decision_status:"挂牌较多 · 暂缓"},next_verification:["核对结构", "核实厂家"]}});
 vi.mocked(api).mockReset().mockImplementation(async path=>path.includes("public-case")?payload:[]);
 const host=document.createElement("div");document.body.append(host);const root=createRoot(host);
 try{
  await act(async()=>root.render(<ProcessPortfolio projectId="demo-project"/>));
  const load=[...host.querySelectorAll("button")].find(button=>button.textContent?.includes("读取氟雷拉纳公开案例"));
  expect(load).toBeTruthy();
  await act(async()=>load!.click());
  expect(host.querySelector('[role="status"]')?.textContent).toContain("尚未保存");
  const values=[...host.querySelectorAll("input")].map(input=>input.value);
  expect(values).toContain("氟雷拉纳（Fluralaner）");
  expect(values).toContain("864731-61-3");
  expect(values).toContain("75.5");
  expect(values).toContain("73.9");
  expect(values).toContain("57");
  expect(values).toContain("87");
  expect(host.querySelectorAll(".process-portfolio-route")[0].querySelectorAll(".process-portfolio-step")).toHaveLength(7);
  expect(host.textContent).toContain("质量数字矛盾");
  expect(host.textContent).toContain("HPLC >99 area%");
  expect(host.textContent).toContain("当前页面未核对到标准正文中的具体限度");
  expect(host.textContent).toContain("VICH");
  expect(host.querySelectorAll(".process-portfolio-route")).toHaveLength(3);
  expect(vi.mocked(api).mock.calls.filter(([,options])=>options?.method==="POST")).toHaveLength(0);
  expect(host.querySelectorAll(".process-demo-note a")).toHaveLength(2);
  expect(host.textContent).toContain("Patent step 1 original text");
  expect(host.textContent).toContain("专利中间体资料");
  expect(host.textContent).toContain("公开挂牌 56 条");
  const sourceDetails=[...host.querySelectorAll<HTMLDetailsElement>(".process-screen-grid details")];
  await act(async()=>sourceDetails[0].querySelector("summary")!.click());
  expect(sourceDetails[0].open).toBe(true);
  expect(sourceDetails[0].textContent).toContain("专利记载：公开步骤 2；过滤，报道该步收率 64%");
  expect(sourceDetails[0].textContent).toContain("该平台对这一 CAS 显示 56 条目录记录");
  expect(sourceDetails[0].textContent).toContain("尚未核实实际生产厂家、产能与报价");
  expect(sourceDetails[1].textContent).toContain("暂无核对过的同物质挂牌总数");
  expect(host.textContent).toContain("合成难点（高）");
  expect(host.textContent).toContain("可能的工艺壁垒");
  expect(host.textContent).not.toContain("符合 3–5 家");
  expect(host.textContent).not.toContain("挂牌较多 · 暂缓");
  expect(vi.mocked(api).mock.calls.some(([path])=>path.includes("public-case/fluralaner"))).toBe(true);
 }finally{await act(async()=>root.unmount());host.remove();}
});
