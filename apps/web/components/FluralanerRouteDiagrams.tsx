"use client";

import Image from "next/image";
import structures from "@/data/fluralaner_route_structures.json";

const base=`${process.env.NEXT_PUBLIC_APP_BASE_PATH ?? ""}/process/fluralaner`;
const patent="https://patents.google.com/patent/WO2023012821A1/en";

export function FluralanerRouteDiagrams(){
 return <section className="process-route-figures" aria-label="氟雷拉纳路线图">
  <details open>
   <summary>带中间体结构的合成路线图</summary>
   <p>依据 WO2023012821A1 实施例 1 整理七步产物。二维结构来自公开分子记录或按专利名称转换，图中已标注原文差异。</p>
   <div className="process-route-figure-scroll"><Image unoptimized src={`${base}/synthesis-route.svg`} width={1840} height={1060} alt="氟雷拉纳从1-氟-2-甲苯到产物的七步合成路线，含各中间体二维结构和步骤5另投入的三氟乙酮"/></div>
   <a href={`${base}/synthesis-route.svg`} target="_blank" rel="noopener noreferrer">打开合成路线大图（SVG）</a>
   <details className="process-route-provenance"><summary>结构来源与需要复核的地方</summary>
    <ul>{[...structures.nodes,structures.branch_reagent].map(item=><li key={item.id}><strong>{item.label} · {item.name}</strong>：<a href={item.source_url} target="_blank" rel="noopener noreferrer">{item.source}</a> · <a href={`${patent}#${item.patent_locator}`} target="_blank" rel="noopener noreferrer">专利 {item.patent_locator}</a></li>)}</ul>
    {structures.notes.map(note=><p key={note}>{note}</p>)}
   </details>
  </details>
  <details>
   <summary>拟议工艺流程图 · 概念草图</summary>
   <p>把专利实验中的反应和后处理串成设备单元示意，另将 EP4077285A1 的末端结晶列为可选分离步骤。设备、物料衡算和放大条件仍待验证。</p>
   <div className="process-route-figure-scroll"><Image unoptimized src={`${base}/process-flow-concept.svg`} width={1840} height={920} alt="氟雷拉纳拟议工艺流程图，七个反应与后处理单元及可选结晶，设备物料衡算和放大条件待验证"/></div>
   <a href={`${base}/process-flow-concept.svg`} target="_blank" rel="noopener noreferrer">打开工艺流程大图（SVG）</a>
   <p className="process-route-figure-warning">这张图不是专利原图或可施工 PFD。图中设备编号仅为流程占位；柱层析、溶剂回收、废液去向及冷却能力需通过实验和工程核算确定。</p>
  </details>
 </section>;
}
