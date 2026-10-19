// @vitest-environment jsdom
import React,{act} from "react";
import {createRoot} from "react-dom/client";
import {expect,it,vi} from "vitest";
import {api} from "@/lib/api";
import {PatentRetrosynthesis} from "./PatentRetrosynthesis";

vi.mock("@/lib/api",()=>({api:vi.fn()}));

it("submits a target and shows precursor and patent boundaries",async()=>{
 Object.assign(globalThis,{IS_REACT_ACT_ENVIRONMENT:true});
 vi.mocked(api).mockResolvedValue({
  target:{name:"阿司匹林",cas:"50-78-2",smiles:"CC(=O)Oc1ccccc1C(=O)O",formula:"C9H8O4",identity_status:"本地目录已识别"},
  patent:{publication:null,jurisdiction:"CN",status:"未自动核对专利原文、权利要求或法律状态",search_url:null},
  candidates:[{id:"ester-1",disconnection:"酯键断裂",reaction_class:"酯化或酰基化",precursors:[{name:"乙酸",smiles:"CC(=O)O",formula:"C2H4O2",cas:"64-19-7",catalog_status:"本地原料库已识别；采购可得性未核实",process_caution:null}],forward_hypothesis:"比较活化方式",process_checks:"核查放热",evidence_level:"结构规则推断"}],
  known_route_summaries:[],known_route_sources:[],decision:"先复核规则拆解",next_checks:["核对原文"],scope:"不能证明路线可行"
 });
 const host=document.createElement("div");document.body.append(host);const root=createRoot(host);
 try{
  await act(async()=>root.render(<PatentRetrosynthesis projectId="p"/>));
  await act(async()=>[...host.querySelectorAll("button")].find(item=>item.textContent?.includes("填入阿司匹林"))!.click());
  await act(async()=>host.querySelector("form")!.requestSubmit());
  expect(vi.mocked(api)).toHaveBeenCalledWith("/projects/p/process/retrosynthesis/analyze",expect.objectContaining({method:"POST",body:expect.stringContaining("50-78-2")}));
  expect(host.textContent).toContain("酯键断裂");
  expect(host.textContent).toContain("未自动核对专利原文");
 }finally{await act(async()=>root.unmount());host.remove();}
});

it("reads a patent example and exposes source, step yield and comparison",async()=>{
 Object.assign(globalThis,{IS_REACT_ACT_ENVIRONMENT:true});
 vi.mocked(api).mockResolvedValue({
  source:{kind:"Google Patents 公开文本镜像",url:"https://patents.google.com/patent/CN116354819A/en",publication:"CN116354819A",original:false},
  examples:[{label:"实施例1",steps:[{index:1,heading:"(1)制备水杨酸",kind:"分离/纯化",product:"水杨酸",mentioned_molecules:["水杨酸"],yield_pct:95.2,purity_pct:99.3,source_locator:"p3",source_url:"https://patents.google.com/patent/CN116354819A/en#p3",evidence_excerpt:"重结晶得到水杨酸，收率95.2%，纯度99.3%",extraction_note:"产物/角色需核对原文"}]}],
  molecules:[{name:"水杨酸",cas:"69-72-7",smiles:"O=C(O)c1ccccc1O",identity_source:"PubChem CID 338",structure_status:"身份映射"}],
  comparison:[{candidate_id:"ester-1",disconnection:"酯键断裂",matched_count:1,status:"结构对照",matches:[{name:"水杨酸",status:"专利实施例提及同一已映射结构",patent_name:"水杨酸"}]}],
  summary:"读取 1 个实施例",limitations:["图示结构需人工核对"]
 });
 const host=document.createElement("div");document.body.append(host);const root=createRoot(host);
 try{
  await act(async()=>root.render(<PatentRetrosynthesis projectId="p"/>));
  await act(async()=>[...host.querySelectorAll("button")].find(item=>item.textContent?.includes("填入阿司匹林"))!.click());
  await act(async()=>[...host.querySelectorAll("button")].find(item=>item.textContent?.includes("读取专利实施例"))!.click());
  expect(vi.mocked(api)).toHaveBeenCalledWith("/projects/p/process/retrosynthesis/patent/read",expect.objectContaining({method:"POST",body:expect.stringContaining("CN116354819A")}));
  expect(host.textContent).toContain("实施例1");
  expect(host.textContent).toContain("95.2%");
  expect(host.textContent).toContain("酯键断裂");
 }finally{await act(async()=>root.unmount());host.remove();}
});
