// @vitest-environment jsdom
import React,{act} from "react";
import {createRoot} from "react-dom/client";
import {expect,it,vi} from "vitest";
import {ProcessEngineeringCalculators} from "./ProcessEngineeringCalculators";

vi.mock("@/lib/api",()=>({api:vi.fn()}));

it("opens every engineering calculator with usable default inputs",async()=>{
 Object.assign(globalThis,{IS_REACT_ACT_ENVIRONMENT:true});
 const host=document.createElement("div");document.body.append(host);const root=createRoot(host);
 const click=async(text:string)=>act(async()=>[...host.querySelectorAll("button")].find(x=>x.textContent?.includes(text))!.click());
 try{
  await act(async()=>root.render(<ProcessEngineeringCalculators projectId="p"/>));
  expect(host.textContent).toContain("二元气液平衡");expect(host.querySelectorAll("input").length).toBeGreaterThan(8);
  await click("高压气体物性");expect(host.textContent).toContain("Peng–Robinson");
  await click("动力学拟合");expect(host.textContent).toContain("Arrhenius 动力学");
  await click("设备详细核算");expect(host.textContent).toContain("设备与生产线核算");
  await click("反应安全筛查");expect(host.textContent).toContain("放热反应安全筛查");
  await click("晶体与固体工程");expect(host.textContent).toContain("结晶、过滤与干燥");
 }finally{await act(async()=>root.unmount());host.remove();}
});
