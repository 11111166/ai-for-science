// @vitest-environment jsdom
import React,{act} from "react";
import {createRoot} from "react-dom/client";
import {expect,it,vi} from "vitest";
import {ProcessWorkbench} from "./ProcessWorkbench";

vi.mock("./PublicHteDemo",()=>({PublicHteDemo:()=> <div>公开实验已挂载</div>}));
vi.mock("./ProcessDevelopment",()=>({ProcessDevelopment:()=> <div>企业实验研究已挂载</div>}));
vi.mock("./ProcessScaleUp",()=>({ProcessScaleUp:()=> <div>放大计算已挂载</div>}));
vi.mock("./CompoundOrderAssessment",()=>({CompoundOrderAssessment:()=> <div>订单评估已挂载</div>}));
vi.mock("./ProcessKnowledgeBase",()=>({ProcessKnowledgeBase:()=> <div>知识库已挂载</div>}));

it("opens all three process tools from one page",async()=>{
 Object.assign(globalThis,{IS_REACT_ACT_ENVIRONMENT:true});
 const host=document.createElement("div");document.body.append(host);const root=createRoot(host);
 const button=(text:string)=>[...host.querySelectorAll("button")].find(x=>x.textContent?.includes(text))!;
 try{
  await act(async()=>root.render(<ProcessWorkbench projectId="p"/>));
  expect([...host.querySelectorAll(".process-tool-nav button strong")].map(x=>x.textContent)).toEqual(["工艺工程、CAD 与生产设计","实验条件与贝叶斯优化","路线、成本与接单评估"]);
  expect(host.textContent).toContain("放大计算已挂载");expect(host.textContent).not.toContain("公开实验已挂载");
  await act(async()=>button("实验条件与贝叶斯优化").click());expect(host.textContent).toContain("公开实验已挂载");expect(host.textContent).toContain("企业实验研究已挂载");
  await act(async()=>button("路线、成本与接单评估").click());expect(host.textContent).toContain("订单评估已挂载");
  await act(async()=>button("化工原料、原理与反应工艺知识库").click());expect(host.textContent).toContain("知识库已挂载");
 }finally{await act(async()=>root.unmount());host.remove();}
});
