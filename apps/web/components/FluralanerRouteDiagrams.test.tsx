// @vitest-environment jsdom
import React,{act} from "react";
import {createRoot} from "react-dom/client";
import {expect,it} from "vitest";
import {FluralanerRouteDiagrams} from "./FluralanerRouteDiagrams";

it("keeps source-linked chemistry and proposed engineering diagrams separate",async()=>{
 Object.assign(globalThis,{IS_REACT_ACT_ENVIRONMENT:true});
 const host=document.createElement("div");document.body.append(host);const root=createRoot(host);
 try{
  await act(async()=>root.render(<FluralanerRouteDiagrams/>));
  const sections=[...host.querySelectorAll(".process-route-figures>details")];
  expect(sections).toHaveLength(2);
  expect(sections[0].open).toBe(true);
  expect(sections[1].open).toBe(false);
  expect(sections[0].textContent).toContain("带中间体结构的合成路线图");
  expect(sections[1].textContent).toContain("设备、物料衡算和放大条件仍待验证");
  expect(host.querySelectorAll(".process-route-figure-scroll img")).toHaveLength(2);
  expect(host.querySelectorAll(".process-route-provenance li")).toHaveLength(9);
  expect(host.textContent).toContain("步骤 6 标题仍写乙酯");
  expect(host.textContent).toContain("不是专利原图或可施工 PFD");
  expect([...host.querySelectorAll("a")].some(link=>link.href.includes("WO2023012821A1/en#p0235"))).toBe(true);
 }finally{await act(async()=>root.unmount());host.remove();}
});
