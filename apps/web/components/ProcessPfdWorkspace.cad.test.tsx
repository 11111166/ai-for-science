// @vitest-environment jsdom
import {existsSync,readFileSync} from "node:fs";
import {resolve} from "node:path";
import React,{act} from "react";
import {createRoot} from "react-dom/client";
import {expect,it,vi} from "vitest";
import {api,apiBlob} from "@/lib/api";
import {ProcessPfdWorkspace} from "./ProcessPfdWorkspace";

vi.mock("@/lib/api",()=>({api:vi.fn(),apiBlob:vi.fn(),apiBase:"/api/v1/drug-discovery"}));

Object.assign(globalThis,{IS_REACT_ACT_ENVIRONMENT:true});
Object.assign(globalThis,{ResizeObserver:class{observe(){}unobserve(){}disconnect(){}}});
Object.assign(Element.prototype,{
 setPointerCapture(){},releasePointerCapture(){},hasPointerCapture(){return false;}
});

// jsdom 没有 blob URL：给图纸预览用的字符串建一个可查询的标记。
const sheetObjectUrls=new Map<string,string>();
let sheetObjectUrlSerial=0;
Object.assign(URL,{
 createObjectURL(blob:Blob){const url=`blob:sheet/${++sheetObjectUrlSerial}`;sheetObjectUrls.set(url,String((blob as Blob&{tag?:string}).tag??""));return url;},
 revokeObjectURL(){},
});

const BASE="/projects/p/process/pfd";
const LIST=`${BASE}/diagrams`;
function findStylesheet(){
 for(const candidate of [".","..","../.."].map(base=>resolve(process.cwd(),base,"app","globals.css"))){
  if(existsSync(candidate))return candidate;
 }
 throw new Error("app/globals.css not found");
}
const css=readFileSync(findStylesheet(),"utf8");

const DRAFT={title:"API-X1 百公斤放大初评 · 概念 PFD",source_assessment_id:"a1",graph:{
 nodes:[{id:"n1",kind:"reactor",tag:"R-101",label:"搅拌反应釜",x:60,y:120,w:180,h:84,
  params:[
   {name:"温度",value:"60",unit:"℃",source:"放大评估《API-X1》输入"},
   {name:"管径",value:"待补",unit:"",source:"需合格工程人员设计（概念图不提供）"}],notes:[]}],
 edges:[],
}};

function pointer(type:string,init:MouseEventInit={}){
 return new MouseEvent(type,{bubbles:true,cancelable:true,button:0,...init});
}
function byText(host:HTMLElement,text:string){
 return [...host.querySelectorAll("button")].find(element=>element.textContent?.includes(text))!;
}
async function mount(element:React.ReactElement){
 const host=document.createElement("div");document.body.append(host);const root=createRoot(host);
 await act(async()=>root.render(element));
 return{host,root,unmount:async()=>{await act(async()=>root.unmount());host.remove();}};
}
async function pressAndRelease(host:HTMLElement,target:Element,x:number,y:number){
 const svg=host.querySelector("svg.pfd-canvas") as SVGSVGElement;
 await act(async()=>{
  target.dispatchEvent(pointer("pointerdown",{clientX:x,clientY:y}));
  svg.dispatchEvent(pointer("pointerup",{clientX:x,clientY:y}));
 });
}
async function openDraft(host:HTMLElement){
 const picker=host.querySelector(".pfd-toolbar select") as HTMLSelectElement;
 await act(async()=>{
  const setter=Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,"value")!.set!;
  setter.call(picker,"a1");
  picker.dispatchEvent(new Event("change",{bubbles:true}));
 });
 await act(async()=>byText(host,"从评估生成").click());
}

it("keeps the painted node body hit-testable",()=>{
 const blocks=css.match(/\.pfd-node[^{}]*\{[^}]*\}/g)??[];
 expect(blocks.length).toBeGreaterThan(0);
 const killed=blocks.filter(block=>{
  const selector=block.slice(0,block.indexOf("{"));
  return /\brect\b/.test(selector)&&/pointer-events\s*:\s*none/.test(block);
 });
 expect(killed).toEqual([]);
 expect(css).toMatch(/\.pfd-node\s*\{[^}]*cursor\s*:\s*move/);
});

it("styles the two-sheet CAD tabs and the utility line type",()=>{
 expect(css).toMatch(/\.pfd-sheet-tabs\s*\{/);
 expect(css).toMatch(/\.pfd-sheet-tabs button\.active/);
 expect(css).toMatch(/\.pfd-edge\.utility/);
});

it("selects, drags and edits a node through pointer events",async()=>{
 vi.mocked(api).mockReset().mockImplementation(async(path:string,options?:RequestInit)=>{
  if(path===`${BASE}/draft-from-assessment?assessment_id=a1`)return DRAFT;
  if(path===LIST&&options?.method==="POST")return{...DRAFT,id:"d1",version:1,root_id:"d1",previous_id:null,revision:1};
  return [];
 });
 const view=await mount(<ProcessPfdWorkspace projectId="p" assessments={[{id:"a1",spec:{name:"API-X1 百公斤放大初评"}}]}/>);
 const{host}=view;
 try{
  await openDraft(host);
  expect(host.querySelectorAll(".pfd-node")).toHaveLength(1);
  const node=()=>host.querySelectorAll(".pfd-node")[0] as SVGGElement;

  await pressAndRelease(host,node().querySelector("rect")!,120,140);
  const inspector=()=>host.querySelector(".pfd-inspector")!.textContent??"";
  expect(inspector()).toContain("节点 n1");
  expect((host.querySelector(".pfd-inspector input") as HTMLInputElement).value).toBe("搅拌反应釜");
  const column=(label:string)=>[...host.querySelectorAll(`.pfd-params input[aria-label="${label}"]`)].map(element=>(element as HTMLInputElement).value);
  expect(column("参数名")).toEqual(["温度","管径"]);
  expect(column("数值")).toEqual(["60","待补"]);
  expect(column("单位")).toEqual(["℃",""]);
  expect(column("来源")[1]).toContain("需合格工程人员设计");
  expect(host.querySelector(".pfd-node")!.textContent).toContain("温度：60");

  const xField=()=>Number((host.querySelector(".pfd-geometry input") as HTMLInputElement).value);
  const before=xField();
  const svg=host.querySelector("svg.pfd-canvas") as SVGSVGElement;
  await act(async()=>{
   node().querySelector("rect")!.dispatchEvent(pointer("pointerdown",{clientX:120,clientY:140}));
   svg.dispatchEvent(pointer("pointermove",{clientX:200,clientY:200}));
   svg.dispatchEvent(pointer("pointerup",{clientX:200,clientY:200}));
  });
  expect(xField()).toBeGreaterThan(before);

  await act(async()=>(host.querySelector('button[title="撤销"]') as HTMLButtonElement).click());
  expect(xField()).toBe(before);

  const labelInput=host.querySelector(".pfd-inspector input") as HTMLInputElement;
  await act(async()=>{
   Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,"value")!.set!.call(labelInput,"反应釜A");
   labelInput.dispatchEvent(new Event("input",{bubbles:true}));
   labelInput.dispatchEvent(new Event("change",{bubbles:true}));
  });
  expect(host.querySelector(".pfd-node text")!.textContent).toContain("反应釜A");
 }finally{await view.unmount();}
});

it("connects two nodes into a stream when connect mode is armed",async()=>{
 vi.mocked(api).mockReset().mockImplementation(async(path:string)=>{
  if(path===`${BASE}/draft-from-assessment?assessment_id=a1`)return DRAFT;
  return [];
 });
 const view=await mount(<ProcessPfdWorkspace projectId="p" assessments={[{id:"a1",spec:{name:"API-X1 百公斤放大初评"}}]}/>);
 const{host}=view;
 try{
  await openDraft(host);
  await act(async()=>byText(host,"添加节点").click());
  expect(host.querySelectorAll(".pfd-node")).toHaveLength(2);
  expect(host.querySelectorAll(".pfd-edge")).toHaveLength(0);
  await act(async()=>byText(host,"连线模式").click());
  const nodes=()=>host.querySelectorAll(".pfd-node");
  await pressAndRelease(host,nodes()[0].querySelector("rect")!,120,140);
  expect(host.querySelector(".pfd-canvas-tools button.active")!.textContent).toContain("点击目标节点");
  await pressAndRelease(host,nodes()[1].querySelector("rect")!,320,140);
  expect(host.querySelectorAll(".pfd-edge")).toHaveLength(1);
  const hit=host.querySelector('.pfd-edge line[stroke="transparent"]') as SVGLineElement;
  expect(hit).not.toBeNull();
  expect(hit.getAttribute("stroke-width")).toBe("14");
 }finally{await view.unmount();}
});

it("keeps saved-drawing exports off the portal static-cache extensions",()=>{
 const files:Array<[string,string]>=[
  ["ProcessPfdWorkspace.tsx","/diagrams/${current.id}/export"],
  ["ProcessScaleUp.tsx","/assessments/${item.id}/pfd"],
 ];
 for(const [name,expected] of files){
  const source=readFileSync(resolve(process.cwd(),"components",name),"utf8");
  expect(source).not.toContain("/export.svg");
  expect(source).not.toContain("/pfd.svg");
  expect(source).toContain(expected);
 }
});


it("shows the sheet the user asked for last even when the earlier answer arrives later",async()=>{
 const pending:Array<{view:string;resolve:(blob:Blob)=>void}>=[];
 sheetObjectUrls.clear();
 vi.mocked(api).mockReset().mockImplementation(async(path:string,options?:RequestInit)=>{
  if(path===`${BASE}/draft-from-assessment?assessment_id=a1`)return DRAFT;
  if(path===LIST&&options?.method==="POST")return{...DRAFT,id:"d1",version:1,root_id:"d1",previous_id:null,revision:1};
  return [];
 });
 vi.mocked(apiBlob).mockReset().mockImplementation((_path:string,options?:RequestInit)=>{
  const view=String(JSON.parse(String(options?.body)).view);
  return new Promise<Blob>(resolve=>pending.push({view,resolve}));
 });
 const view=await mount(<ProcessPfdWorkspace projectId="p" assessments={[{id:"a1",spec:{name:"API-X1 百公斤放大初评"}}]}/>);
 const{host}=view;
 try{
  await openDraft(host);
  await act(async()=>byText(host,"图例图纸预览").click());
  await act(async()=>byText(host,"② CAD 工段图预览").click());
  expect(pending.map(item=>item.view)).toEqual(["legend","section"]);

  await act(async()=>pending[1].resolve(Object.assign(new Blob(["section"]),{tag:"section"})));
  await act(async()=>pending[0].resolve(Object.assign(new Blob(["legend"]),{tag:"legend"})));

  const shown=host.querySelector(".pfd-sheet-canvas img")?.getAttribute("src")??"";
  expect(sheetObjectUrls.get(shown)).toBe("section");
  expect(host.querySelector('[role="tab"][aria-selected="true"]')!.textContent).toContain("第 2 张");
 }finally{await view.unmount();}
});
