// @vitest-environment jsdom
import React,{act} from "react";
import {createRoot} from "react-dom/client";
import {expect,it,vi} from "vitest";
import {api} from "@/lib/api";
import {ProcessPfdWorkspace} from "./ProcessPfdWorkspace";

vi.mock("@/lib/api",()=>({api:vi.fn(),apiBlob:vi.fn(),apiBase:"/api/v1/drug-discovery"}));

Object.assign(globalThis,{IS_REACT_ACT_ENVIRONMENT:true});
Object.assign(globalThis,{ResizeObserver:class{observe(){}unobserve(){}disconnect(){}}});

const BASE="/projects/p/process/pfd";
const LIST=`${BASE}/diagrams`;
const DRAFT={title:"API-X1 百公斤放大初评 · 概念 PFD",source_assessment_id:"a1",graph:{
 nodes:[{id:"n1",kind:"reactor",tag:"R-101",label:"带搅拌间歇反应釜",x:40,y:140,w:180,h:84,
   params:[{name:"温度",value:"60",unit:"℃",source:"放大评估《API-X1》输入"},{name:"管径",value:"待补",unit:"",source:"需合格工程人员设计（概念图不提供）"}],notes:[]}],
 edges:[],
}};
const SAVED={id:"d1",title:DRAFT.title,version:1,root_id:"d1",previous_id:null,source_assessment_id:"a1",revision:1,
 created_at:"2026-09-22T00:00:00Z",updated_at:"2026-09-22T00:00:00Z",node_count:1,edge_count:0,graph:DRAFT.graph,
 disclaimer:"概念级工艺流程图，仅供研发讨论，非施工图或已验证 P&ID。",pending_label:"待补"};

function setValue(element:HTMLInputElement|HTMLSelectElement,value:string){
 const prototype=element instanceof HTMLSelectElement?HTMLSelectElement.prototype:HTMLInputElement.prototype;
 Object.getOwnPropertyDescriptor(prototype,"value")!.set!.call(element,value);
 element.dispatchEvent(new Event("change",{bubbles:true}));
}
function byText(host:HTMLElement,tag:"button"|"a",text:string){
 return [...host.querySelectorAll(tag)].find(element=>element.textContent?.includes(text))!;
}
async function mount(element:React.ReactElement){
 const host=document.createElement("div");document.body.append(host);const root=createRoot(host);
 await act(async()=>root.render(element));
 return{host,root,unmount:async()=>{await act(async()=>root.unmount());host.remove();}};
}

it("generates an editable diagram from a saved assessment, undoes and saves it",async()=>{
 vi.mocked(api).mockReset().mockImplementation(async(path:string,options?:RequestInit)=>{
  if(path===`${BASE}/draft-from-assessment?assessment_id=a1`)return DRAFT;
  if(path===LIST&&options?.method==="POST")return SAVED;
  return [];
 });
 const view=await mount(<ProcessPfdWorkspace projectId="p" assessments={[{id:"a1",spec:{name:"API-X1 百公斤放大初评"}}]}/>);
 const{host}=view;
 try{
  expect(host.textContent).toContain("非施工图或已验证 P&ID");
  await act(async()=>setValue(host.querySelector("select")! as HTMLSelectElement,"a1"));
  await act(async()=>byText(host,"button","从评估生成").click());
  expect(host.textContent).toContain("已从放大评估生成可编辑草图");
  expect(host.querySelectorAll(".pfd-node")).toHaveLength(1);
  expect(host.textContent).toContain("带搅拌间歇反应釜");
  await act(async()=>byText(host,"button","添加节点").click());
  expect(host.querySelectorAll(".pfd-node")).toHaveLength(2);
  await act(async()=>byText(host,"button","删除选中").click());
  expect(host.querySelectorAll(".pfd-node")).toHaveLength(1);
  const undo=host.querySelector('button[title="撤销"]')!;
  await act(async()=>(undo as HTMLButtonElement).click());
  expect(host.querySelectorAll(".pfd-node")).toHaveLength(2);
  await act(async()=>(host.querySelector('button[title="重做"]') as HTMLButtonElement).click());
  expect(host.querySelectorAll(".pfd-node")).toHaveLength(1);
  await act(async()=>byText(host,"button","保存为新图纸").click());
  const posted=vi.mocked(api).mock.calls.find(call=>call[1]?.method==="POST");
  expect(posted?.[0]).toBe(LIST);
  expect(String(posted?.[1]?.body)).toContain("带搅拌间歇反应釜");
  expect(host.textContent).toContain("已保存 v1");
 }finally{await view.unmount();}
});

it("shows an explicit empty state when no assessment has been saved",async()=>{
 vi.mocked(api).mockReset().mockResolvedValue([]);
 const view=await mount(<ProcessPfdWorkspace projectId="p" assessments={[]}/>);
 try{
  expect(view.host.textContent).toContain("还没有可用的放大评估");
  expect(view.host.textContent).toContain("新建空白图");
  expect(view.host.querySelector('button[title="撤销"]')).not.toBeNull();
 }finally{await view.unmount();}
});

it("surfaces an API error instead of silently rendering an empty diagram",async()=>{
 vi.mocked(api).mockReset().mockRejectedValue(new Error("项目服务正在恢复，请稍后重试。"));
 const view=await mount(<ProcessPfdWorkspace projectId="p" assessments={[{id:"a1",spec:{name:"评估 A"}}]}/>);
 try{
  expect(view.host.textContent).toContain("项目服务正在恢复");
  expect(view.host.querySelector(".pfd-error")).not.toBeNull();
 }finally{await view.unmount();}
});

it("stays silent about empty states while the assessment and diagram lists are still loading",async()=>{
 vi.mocked(api).mockReset().mockImplementation(()=>new Promise(()=>{}));
 const view=await mount(<ProcessPfdWorkspace projectId="p" assessments={[]} assessmentsLoaded={false}/>);
 try{
  expect(view.host.textContent).not.toContain("还没有可用的放大评估");
  expect(view.host.textContent).not.toContain("本项目还没有保存过概念图纸");
 }finally{await view.unmount();}
});

it("shows the no-saved-diagram empty state once the diagram list has loaded",async()=>{
 vi.mocked(api).mockReset().mockResolvedValue([]);
 const view=await mount(<ProcessPfdWorkspace projectId="p" assessments={[{id:"a1",spec:{name:"评估 A"}}]}/>);
 try{
  expect(view.host.textContent).toContain("本项目还没有保存过概念图纸");
  expect(view.host.textContent).not.toContain("还没有可用的放大评估");
 }finally{await view.unmount();}
});

it("creates a blank diagram and reports that no parameters were invented",async()=>{
 vi.mocked(api).mockReset().mockImplementation(async(path:string,options?:RequestInit)=>{
  if(path===LIST&&options?.method==="POST")return{...SAVED,source_assessment_id:null,graph:{nodes:[{id:"n1",kind:"feed",tag:"",label:"进料/计量",x:80,y:200,w:180,h:84,params:[{name:"设备参数",value:"待补",unit:"",source:"需补充实验或工程数据"}],notes:[]}],edges:[]}};
  return [];
 });
 const view=await mount(<ProcessPfdWorkspace projectId="p" assessments={[]}/>);
 try{
  await act(async()=>byText(view.host,"button","新建空白图").click());
  expect(view.host.querySelectorAll(".pfd-node")).toHaveLength(1);
  expect(view.host.textContent).toContain("已新建空白图");
  await act(async()=>byText(view.host,"button","保存为新图纸").click());
  const posted=vi.mocked(api).mock.calls.find(call=>call[1]?.method==="POST");
  expect(String(posted?.[1]?.body)).toContain("待补");
 }finally{await view.unmount();}
});
