// @vitest-environment jsdom
import React,{act} from "react";
import {createRoot} from "react-dom/client";
import {expect,it,vi} from "vitest";
import {api,apiBlob} from "@/lib/api";
import {ProcessPfdWorkspace} from "./ProcessPfdWorkspace";

vi.mock("@/lib/api",()=>({api:vi.fn(),apiBlob:vi.fn(),apiBase:"/api/v1/drug-discovery"}));

Object.assign(globalThis,{IS_REACT_ACT_ENVIRONMENT:true});
Object.assign(globalThis,{ResizeObserver:class{observe(){}unobserve(){}disconnect(){}}});
Object.assign(Element.prototype,{setPointerCapture(){},releasePointerCapture(){},hasPointerCapture(){return false;}});
const blobs:Blob[]=[];
Object.assign(URL,{
 createObjectURL:(blob:Blob)=>{blobs.push(blob);return `blob:mock-${blobs.length}`;},
 revokeObjectURL:()=>{},
});

const BASE="/projects/p/process/pfd";
const DEMO_BASE="/projects/p/process/demo/cases";
const GRAPH={nodes:[
 {id:"n1",kind:"reactor",tag:"R-201",label:"固定化酶转化反应器",x:60,y:120,w:190,h:92,
  params:[{name:"温度",value:"37",unit:"℃",source:"演示案例输入（虚构设计值，不得用于工程设计）"},
          {name:"管径",value:"待补",unit:"",source:"需合格工程人员设计（概念图不提供）"}],notes:[]},
 {id:"n2",kind:"product",tag:"B-201",label:"产品暂存与包装",x:320,y:120,w:190,h:92,
  params:[{name:"目标含量",value:"99",unit:"%",source:"演示案例输入（虚构设计值，不得用于工程设计）"}],notes:[]},
],edges:[{id:"e1",source:"n1",target:"n2",label:"成品",params:[
 {name:"S04 成品",value:"100",unit:"kg/批",source:"按本案例输入调用平台放大评估引擎计算"}]}]};
const ASSESSMENT={name:"L-苯丙氨酸 酶法转化—结晶精制 概念放大评估（演示）",product:"L-苯丙氨酸",
 product_state:"solid",process_system:"homogeneous_liquid",product_molecular_weight:165.19,
 product_density_kg_l:.6,target_purity_pct:99,target_product_kg:100,conversion_pct:95,
 isolation_yield_pct:88,product_concentration_kg_l:.11,temperature_c:37,pressure_bar:1,
 reaction_time_h:24,separation:"结晶",solvent:"工艺水",solvent_density_kg_l:1,
 heat_capacity_kj_kg_k:4.18,reaction_enthalpy_kj_mol:-58,separation_energy_kwh:120,
 solvent_unit_cost_cny_per_kg:.005,utility_cost_cny_per_kwh:.8,waste_cost_cny_per_kg:3,
 fill_fraction:.7,corrosive:false,
 materials:[{name:"反式肉桂酸",role:"limiting",molecular_weight:148.16,equivalents:1,phase:"solid",unit_cost_cny_per_kg:45},
            {name:"氨水（以 NH3 计）",role:"reagent",molecular_weight:17.03,equivalents:3,phase:"liquid",unit_cost_cny_per_kg:1.2}]};
const DEMO={slug:"lphenylalanine",title:"L-苯丙氨酸 酶法转化—结晶精制 概念工艺包（演示）",
 product:"L-苯丙氨酸",case_note:"演示案例：所有输入框均已填入虚构设计值。",
 route:"演示路线假设",data_note:"图中数值分三类",disclaimer:"概念级工艺流程图，仅供研发讨论，非施工图或已验证 P&ID。",
 pending_label:"待补",assessment:ASSESSMENT,graph:GRAPH,node_count:2,edge_count:1,result_summary:{}};

function byText(host:HTMLElement,text:string){
 return [...host.querySelectorAll("button")].find(element=>element.textContent?.includes(text))!;
}
async function mount(element:React.ReactElement){
 const host=document.createElement("div");document.body.append(host);const root=createRoot(host);
 await act(async()=>root.render(element));
 return{host,root,unmount:async()=>{await act(async()=>root.unmount());host.remove();}};
}

it("loads the demonstration case into the canvas and fills every assessment input",async()=>{
 const onLoadDemo=vi.fn();
 vi.mocked(api).mockReset().mockImplementation(async(path:string)=>{
  if(path===DEMO_BASE)return[{slug:"lphenylalanine",title:DEMO.title,product:DEMO.product,case_note:DEMO.case_note,target_product_kg:100,material_count:2}];
  if(path===`${DEMO_BASE}/lphenylalanine`)return DEMO;
  if(path===`${BASE}/diagrams`)return[];
  if(path===`${BASE}/draft-from-assessment?assessment_id=a1`)return{title:"草稿",source_assessment_id:"a1",graph:GRAPH};
  return[];
 });
 const view=await mount(<ProcessPfdWorkspace projectId="p" assessments={[{id:"a1",spec:{name:"演示评估"}}]} onLoadDemo={onLoadDemo}/>);
 const{host}=view;
 try{
  const selects=[...host.querySelectorAll(".pfd-toolbar select")] as HTMLSelectElement[];
  const demoSelect=selects.find(item=>[...item.options].some(option=>option.value==="lphenylalanine"))!;
  expect(demoSelect).toBeTruthy();
  await act(async()=>{
   Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,"value")!.set!.call(demoSelect,"lphenylalanine");
   demoSelect.dispatchEvent(new Event("change",{bubbles:true}));
  });
  await act(async()=>byText(host,"载入演示案例").click());
  expect(host.querySelectorAll(".pfd-node")).toHaveLength(2);
  expect((host.querySelector(".pfd-title-field input") as HTMLInputElement).value).toBe(DEMO.title);
  expect(host.querySelector(".pfd-notice")!.textContent).toContain("已载入演示案例");
  expect(onLoadDemo).toHaveBeenCalledTimes(1);
  const spec=onLoadDemo.mock.calls[0][0] as Record<string,unknown>;
  const empty=Object.entries(spec).filter(([,value])=>value===null||value===undefined||value==="");
  expect(empty).toEqual([]);
  expect((spec.materials as unknown[]).length).toBe(2);
  expect(host.querySelector(".pfd-node")!.textContent).toContain("管径：待补");
 }finally{await view.unmount();}
});

it("previews the CAD sheet in the browser and downloads the sheet SVG and DXF",async()=>{
 vi.mocked(api).mockReset().mockImplementation(async(path:string)=>{
  if(path===DEMO_BASE)return[];
  if(path===`${BASE}/diagrams`)return[];
  return[];
 });
 vi.mocked(apiBlob).mockReset().mockImplementation(async()=>new Blob(["<svg/>"],{type:"image/svg+xml"}));
 const view=await mount(<ProcessPfdWorkspace projectId="p" assessments={[]} />);
 const{host}=view;
 try{
  expect(byText(host,"② CAD 工段图预览").disabled).toBe(true);
  await act(async()=>byText(host,"新建空白图").click());
  expect(byText(host,"② CAD 工段图预览").disabled).toBe(false);
  await act(async()=>byText(host,"② CAD 工段图预览").click());
  expect(vi.mocked(apiBlob).mock.calls[0][0]).toBe(`${BASE}/preview/sheet.svg`);
  const body=JSON.parse(String(vi.mocked(apiBlob).mock.calls[0][1]?.body));
  expect(body.graph.nodes).toHaveLength(1);
  const preview=host.querySelector(".pfd-sheet-canvas img") as HTMLImageElement;
  expect(preview).not.toBeNull();
  expect(preview.getAttribute("src")).toBe("blob:mock-1");
  expect(body.view).toBe("section");
  expect(host.querySelector(".pfd-sheet-modal")!.textContent).toContain("非施工图或已验证 P&ID");
  expect(host.querySelector(".pfd-sheet-tabs")).not.toBeNull();
  await act(async()=>byText(host,"下载工段图 DXF").click());
  await act(async()=>{});
  const paths=vi.mocked(apiBlob).mock.calls.map(call=>call[0]);
  expect(paths).toContain(`${BASE}/preview/sheet.dxf`);
 }finally{await view.unmount();}
});

it("exports the two-sheet CAD set: a legend sheet plus the section sheet",async()=>{
 vi.mocked(api).mockReset().mockImplementation(async(path:string)=>{
  if(path===DEMO_BASE)return[];
  if(path===`${BASE}/diagrams`)return[];
  return[];
 });
 vi.mocked(apiBlob).mockReset().mockImplementation(async()=>new Blob(["<svg/>"],{type:"image/svg+xml"}));
 const view=await mount(<ProcessPfdWorkspace projectId="p" assessments={[]} />);
 const{host}=view;
 const lastCall=()=>{const calls=vi.mocked(apiBlob).mock.calls;return calls[calls.length-1];};
 try{
  expect(byText(host,"图例图纸预览").disabled).toBe(false);
  await act(async()=>byText(host,"图例图纸预览").click());
  expect(vi.mocked(apiBlob).mock.calls[0][0]).toBe(`${BASE}/preview/sheet.svg`);
  expect(JSON.parse(String(vi.mocked(apiBlob).mock.calls[0][1]?.body)).view).toBe("legend");
  const tabs=[...host.querySelectorAll(".pfd-sheet-tabs button")];
  expect(tabs).toHaveLength(2);
  expect(tabs[0].getAttribute("aria-selected")).toBe("true");
  expect(host.querySelector(".pfd-sheet-canvas img")!.getAttribute("alt")).toContain("图例");
  await act(async()=>byText(host,"下载本页 DXF").click());
  await act(async()=>{});
  expect(lastCall()[0]).toBe(`${BASE}/preview/sheet.dxf`);
  expect(JSON.parse(String(lastCall()[1]?.body)).view).toBe("legend");
  await act(async()=>byText(host,"新建空白图").click());
  await act(async()=>byText(host,"下载图例 SVG").click());
  await act(async()=>{});
  expect(lastCall()[0]).toBe(`${BASE}/preview/sheet.svg`);
  expect(JSON.parse(String(lastCall()[1]?.body)).view).toBe("legend");
 }finally{await view.unmount();}
});
