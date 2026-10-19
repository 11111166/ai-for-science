type ProductClass="human_api"|"veterinary_api"|"intermediate"|"other_chemical";
type Props={productClass:ProductClass;cas:string;jurisdiction:string;targetPurityPct:number|null};
type Source={label:string;url:string};

const veterinarySources:Source[]=[
 {label:"VICH GL39 · 质量标准与可接受限度",url:"https://vichsec.org/guidelines/pharmaceuticals-pharma-quality-pharma-specifications/"},
 {label:"VICH GL10 · 原料药杂质",url:"https://vichsec.org/guidline/impurities-in-new-veterinary-drug-substances-revision/"},
 {label:"VICH GL18 · 残留溶剂",url:"https://vichsec.org/guidelines/pharmaceuticals-pharma-quality-impurities/"},
 {label:"中国兽药 GMP（2020）",url:"https://fgs.moa.gov.cn/flfg/202005/t20200515_6344129.htm"},
];
const humanSources:Source[]=[
 {label:"ICH Q6A · 质量标准",url:"https://admin.ich.org/page/quality-guidelines"},
 {label:"ICH Q3A/Q3C/Q3D · 杂质、溶剂、元素",url:"https://admin.ich.org/page/quality-guidelines"},
 {label:"ICH Q7/Q11 · 原料药 GMP 与工艺开发",url:"https://admin.ich.org/page/quality-guidelines"},
];

export function ProcessQualityGuidance({productClass,cas,jurisdiction,targetPurityPct}:Props){
 const fluralaner=cas.trim()==="864731-61-3";
 const veterinary=productClass==="veterinary_api";
 const human=productClass==="human_api";
 const sources=veterinary?veterinarySources:human?humanSources:[];
 return <section className="process-quality" aria-label="质量目标与注册依据">
  <div className="process-quality-head"><strong>质量目标与注册依据</strong><span>{veterinary?"兽药":human?"人药":"按实际用途确认适用体系"} · {jurisdiction}</span></div>
  {fluralaner?<p><b>公开可达水平：</b>专利 EP4077285A1 实施例 1 的结晶产物报道 HPLC 纯度 &gt;99 area%，实施例 2 &gt;98 area%。研发阶段可优先复现 &gt;99 area% 并确认重复性；色谱面积百分比不等于含量测定或批准放行标准。</p>:<p><b>纯度目标：</b>不存在适用于所有产品的统一“最高纯度”。应由产品用途、杂质风险、分析方法、稳定性与适用注册标准共同确定。</p>}
  <p><b>项目拟定值：</b>{targetPurityPct===null?"尚未设定；核对注册质量标准和分析方法后填写。":`${targetPurityPct}%（用户拟定，尚非监管限度）`}</p>
  {fluralaner&&jurisdiction==="CN"&&<p><b>中国注册状态：</b><a href="https://xmsyj.moa.gov.cn/zwfw/202601/t20260107_6480492.htm" target="_blank" rel="noopener noreferrer">农业农村部第 986 号公告</a>已公布氟雷拉纳新兽药注册和质量标准；当前页面未核对到标准正文中的具体限度，因此不填造法定纯度数值。</p>}
  {fluralaner&&<p><b>由案例路线带出的优先考察：</b>最终采用路线涉及的二氯甲烷、NMP、乙腈、甲苯和乙酸乙酯残留；氰化步骤的残留/副产物风险；异噁唑啉中间体残留、立体异构与结晶形态。先结合工艺和分析风险筛选项目，再按适用标准确定方法与限度。</p>}
  {(veterinary||human)&&<><p><b>应形成的质量研究：</b>身份与含量、有关物质/降解杂质、残留溶剂、必要时元素杂质、水分及晶型/粒度、分析方法验证、稳定性、批次一致性；具体项目和限度按剂型、工艺及目标法域裁定。</p><p><b>申报与生产：</b>{veterinary?"VICH 可作技术参考；中国申报还需核对农业农村部批准标准、注册资料和兽药 GMP。":"按目标法域核对 ICH/当地注册要求，并建立 GMP 下的工艺控制和放行依据。"}</p></>}
  {!veterinary&&!human&&<p>中间体或其他化学品不能自动套用原料药放行标准；先确认下游用途、采购规格及适用法规。</p>}
  {sources.length>0&&<details><summary>查看指导原则与原文</summary><ul>{sources.map(source=><li key={source.label}><a href={source.url} target="_blank" rel="noopener noreferrer">{source.label}</a></li>)}</ul></details>}
 </section>;
}
