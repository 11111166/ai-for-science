// @vitest-environment jsdom
import React,{act} from "react";
import {createRoot} from "react-dom/client";
import {expect,it,vi} from "vitest";
import {api} from "@/lib/api";
import {PublicHteDemo} from "./PublicHteDemo";

vi.mock("@/lib/api",()=>({api:vi.fn()}));

it("states the fixed public-example target without inventing product identity or state",async()=>{
 vi.mocked(api).mockImplementation(async(path:string)=>path.endsWith("/datasets")?[{id:"buchwald-hartwig-48",name:"公开反应",description:"固定底物筛选",reaction:"C–N 偶联",target:{name:"固定底物的 C–N 偶联产物",identity:"公开子集未提供完整产物身份",state:"公开数据未报告目标物态",amount:"条件筛选数据，不对应指定交付量"},size:48,fixed:{底物:"CC"},legends:{},source:{dataset_url:"https://example.test/data",paper_url:"https://example.test/paper",citation:"公开研究",snapshot:"v1"}}]:[] as never);
 Object.assign(globalThis,{IS_REACT_ACT_ENVIRONMENT:true});
 const host=document.createElement("div");document.body.append(host);const root=createRoot(host);
 try{
  await act(async()=>{root.render(<PublicHteDemo projectId="p"/>);await Promise.resolve();await Promise.resolve();});
  expect(host.textContent).toContain("本示例合成目标");
  expect(host.textContent).toContain("固定底物的 C–N 偶联产物");
  expect(host.textContent).toContain("公开数据未报告目标物态");
 }finally{await act(async()=>root.unmount());host.remove();vi.mocked(api).mockReset();}
});
