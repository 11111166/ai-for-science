// @vitest-environment jsdom
import React,{act} from "react";
import {createRoot} from "react-dom/client";
import {expect,it,vi} from "vitest";
import {api} from "@/lib/api";
import {ProcessKnowledgeBase} from "./ProcessKnowledgeBase";

vi.mock("@/lib/api",()=>({api:vi.fn()}));
const mocked=vi.mocked(api);

it("searches materials and opens every knowledge view",async()=>{
 mocked.mockImplementation(async(path:string)=>{
  if(path.endsWith("/summary"))return {version:"v3",updated_at:"2026-09-15",material_count:1,reaction_template_count:1,principle_count:1,topic_count:1,categories:[{id:"solvent",label:"溶剂",count:1}],runtime:"本地",scope:"测试范围",method_basis:[{label:"NIST",url:"https://webbook.nist.gov"}]} as never;
  if(path.endsWith("/materials"))return {total:1,version:"v3",items:[{id:"tetrahydrofuran",name:"四氢呋喃",english_name:"Tetrahydrofuran",aliases:["THF"],cas:"109-99-9",pubchem_cid:8028,formula:"C4H8O",molecular_weight:72.11,smiles:"C1CCOC1",category:"solvent",category_label:"溶剂",xlogp:.5,tpsa:9.2,common_use:"反应溶剂",process_caution:"可形成过氧化物",decision_questions:["是否满足反应与分离"],verify_before_scale:["核对含水与纯度"],evidence:{identity:"固化快照",guidance:"通用提示"},source_url:"https://pubchem.ncbi.nlm.nih.gov/compound/8028"}]} as never;
  if(path.endsWith("/reaction-templates"))return {total:1,version:"v2",items:[{id:"buchwald-hartwig",name:"Buchwald–Hartwig 胺化",category:"金属催化",goal:"形成碳—氮键",common_choices:["钯催化剂"],screen:["配体"],workup:["过滤"],analysis:["转化率"],scale_risks:["金属残留"],next:"先筛选",principles:[{id:"mixing",name:"混合、悬浮与尺度放大"}]}]} as never;
  if(path.endsWith("/principles"))return {total:1,version:"v3",items:[{id:"mixing",name:"混合、悬浮与尺度放大",category:"传递过程",question:"加料后多快混匀？",conclusion:"转速相同并不代表混合相同。",formulas:[{name:"桨尖速度",expression:"utip = π × N × D",variables:"N 为转速"}],inputs:["设备几何"],signals:["混合时间"],mistakes:["只记录 rpm"],next:"先判断控制目标。"}]} as never;
  return {total:1,version:"v3",items:[{id:"scale-up-transfer",name:"放大与技术转移包",category:"安全与放大",goal:"让接收团队理解工艺目的和风险。",decision:"必须说明每个控制的原因和范围。",checklist:["设备适配"],deliverables:["主批记录草案"],red_flags:["只传递实验记录"],next:"完成设备适配。"}]} as never;
 });
 Object.assign(globalThis,{IS_REACT_ACT_ENVIRONMENT:true});
 const host=document.createElement("div");document.body.append(host);const root=createRoot(host);
 const button=(text:string)=>[...host.querySelectorAll("button")].find(x=>x.textContent?.includes(text))!;
 try{
  await act(async()=>{root.render(<ProcessKnowledgeBase projectId="p"/>);await Promise.resolve();await Promise.resolve();});
  expect(host.textContent).toContain("1 种物料");expect(host.textContent).toContain("1 项原理");expect(host.textContent).toContain("1 个专题");
  await act(async()=>button("查看详情").click());
  expect(host.textContent).toContain("C1CCOC1");expect(host.textContent).toContain("可形成过氧化物");expect(host.textContent).toContain("核对含水与纯度");
  await act(async()=>button("反应与工艺").click());
  expect(host.textContent).toContain("Buchwald–Hartwig 胺化");
  await act(async()=>button("查看工艺要点").click());
  expect(host.textContent).toContain("金属残留");expect(host.textContent).toContain("下一步");
  await act(async()=>button("混合、悬浮与尺度放大").click());
  expect(host.textContent).toContain("转速相同并不代表混合相同");
  await act(async()=>button("查看判断方法").click());
  expect(host.textContent).toContain("utip = π × N × D");expect(host.textContent).toContain("只记录 rpm");
  await act(async()=>button("研发专题").click());
  expect(host.textContent).toContain("放大与技术转移包");
  await act(async()=>button("查看完整清单").click());
  expect(host.textContent).toContain("主批记录草案");expect(host.textContent).toContain("只传递实验记录");
 }finally{await act(async()=>root.unmount());host.remove();mocked.mockReset();}
});
