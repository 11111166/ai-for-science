import { DISEASE_OPTIONS } from './diseaseCatalog';
import type { DrugDossier } from './drugDossier';
// A Chinese literature name, not an assertion of an approved Chinese generic name.
export const chineseNames: Record<string,{name:string;source:string}> = {
 CHEMBL149082: {"name": "高牛磺酸", "source": "https://patents.google.com/patent/CN110668980A/zh"},
 CHEMBL1743004: {"name": "克瑞珠单抗", "source": "https://patents.google.com/patent/CN106456729A/zh"},
 CHEMBL1743025: {"name": "甘特鲁单抗", "source": "https://patents.google.com/patent/CN117999096A/zh"},
 CHEMBL1743072: {"name": "索拉珠单抗", "source": "https://patents.google.com/patent/CN110662532A/zh"},
 CHEMBL2108576: {"name": "巴匹珠单抗", "source": "https://patents.google.com/patent/CN110662532A/zh"},
 CHEMBL1743058: {"name": "泊尼珠单抗", "source": "https://patents.google.com/patent/TW202035442A/zh"},
 CHEMBL3989948: {"name": "拉那贝司他", "source": "https://patents.google.com/patent/CN115038789A/zh"},
 CHEMBL2108328: {"name": "阿米莫肽", "source": "https://patents.google.com/patent/CN115666704A/zh"},
 CHEMBL1230609: {"name": "福瑞替尼", "source": "https://patents.google.com/patent/CN117561064A/zh"},
 CHEMBL300172: {"name": "柯胺-G", "source": "https://patents.google.com/patent/CN105247367A/zh"},

 CHEMBL3833321: {name:'仑卡奈单抗',source:'https://investors.biogen.com/news-releases/news-release-details/leqembir-lecanemab-approved-treatment-alzheimers-disease-china'},
 CHEMBL4297245: {name:'多奈单抗',source:'https://www.nhsa.gov.cn/attach/Ypsnsp2025/YPSW202500115/YPSW202500115.pdf'},
 CHEMBL3039540: {name:'阿杜单抗',source:'https://www.nhsa.gov.cn/attach/Ypsn2025/YPSW202500485/YPSW202500485(ppt).pdf'},
 CHEMBL289556: {name:'奥马曲拉',source:'https://patents.google.com/patent/CN102171193A/zh'},
};
// Exact full preferred-name match only; salts and derivatives are not inferred.
const kinaseNames:Record<string,string> = {"AFATINIB": "阿法替尼", "ALECTINIB": "艾乐替尼", "APATINIB": "阿帕替尼", "AXITINIB": "阿昔替尼", "BARICITINIB": "巴瑞替尼", "BOSUTINIB": "博舒替尼", "BRIGATINIB": "布格替尼", "CABOZANTINIB": "卡博替尼", "CEDIRANIB": "西地尼布", "CERITINIB": "色瑞替尼", "COBIMETINIB": "考比替尼", "CRIZOTINIB": "克唑替尼", "DABRAFENIB": "达拉非尼", "DASATINIB": "达沙替尼", "ERLOTINIB": "厄洛替尼", "GEFITINIB": "吉非替尼", "IBRUTINIB": "依鲁替尼", "IMATINIB": "伊马替尼", "LAPATINIB": "拉帕替尼", "LENVATINIB": "仑伐替尼", "NERATINIB": "来那替尼", "NILOTINIB": "尼洛替尼", "NINTEDANIB": "尼达尼布", "OSIMERTINIB": "奥希替尼", "PAZOPANIB": "帕唑帕尼", "PONATINIB": "普纳替尼", "REGORAFENIB": "瑞戈非尼", "RUXOLITINIB": "卢可替尼", "SORAFENIB": "索拉非尼", "SUNITINIB": "舒尼替尼", "TOFACITINIB": "托法替尼", "TRAMETINIB": "曲美替尼", "UPADACITINIB": "乌帕替尼", "VANDETANIB": "凡德他尼"};
export function chineseMoleculeName(id:string,name?:string) {
 return chineseNames[id.toUpperCase()] || (name && kinaseNames[name.toUpperCase()] ? {name:kinaseNames[name.toUpperCase()],source:'https://patents.google.com/patent/CN115192540A/zh'} : undefined);
}
export function displayMoleculeName(id:string,name:string) {const zh=chineseMoleculeName(id,name);return zh ? `${zh.name} · ${name}` : /^CHEMBL\d+$/i.test(name) ? `研究化合物 · ${name}` : /[\u4e00-\u9fff]/.test(name) ? name : `研发分子 · ${name}`;}
export function diseaseLabel(term?:string,id?:string) {
 const normalize=(v:string)=>v.split('/').pop()!.replace(':','_').toLowerCase();
 const found=DISEASE_OPTIONS.find(d=>(id && d.ontologyId && normalize(d.ontologyId)===normalize(id)) || (term && d.nameEn.toLowerCase()===term.toLowerCase()));
 const extra:Record<string,string>={'dementia':'痴呆','liver disease':'肝脏疾病','renal insufficiency':'肾功能不全','hypertension':'高血压','heart failure':'心力衰竭','breast cancer':'乳腺癌','lung cancer':'肺癌','neoplasms':'肿瘤','cancer':'癌症','mild cognitive impairment':'轻度认知障碍','cognitive dysfunction':'认知功能障碍'};
 const zh=found?.nameZh || extra[term?.toLowerCase() || ''];
 return zh ? `${zh} · ${term || found?.nameEn}` : term || id || '—';
}
export function mechanismLabel(text?:string) {
 const terms:Record<string,string>={'Amyloid-beta A4 protein inhibitor':'β-淀粉样蛋白（Aβ）抑制剂','Amyloid-beta A4 protein disrupting agent':'β-淀粉样蛋白（Aβ）干扰剂','Amyloid-beta A4 protein stabiliser':'β-淀粉样蛋白（Aβ）稳定剂','Amyloid-beta A4 protein vaccine antigen':'β-淀粉样蛋白（Aβ）疫苗抗原','Amyloid-beta A4 protein binding agent':'β-淀粉样蛋白（Aβ）结合剂','Beta-secretase 1 inhibitor':'β-分泌酶 1（BACE1）抑制剂','Angiotensin-converting enzyme inhibitor':'血管紧张素转换酶（ACE）抑制剂','Neprilysin inhibitor':'脑啡肽酶（NEP）抑制剂','Epidermal growth factor receptor erbB1 inhibitor':'表皮生长因子受体（EGFR）抑制剂'};
 if (!text) return '';
 if (terms[text]) return terms[text];
 const targets:Record<string,string>={'Beta-secretase 1':'β-分泌酶 1（BACE1）','Gamma-secretase':'γ-分泌酶','Cathepsin B':'组织蛋白酶 B','Ephrin type-A receptor 1':'Eph A1 受体','Protein-tyrosine kinase 2-beta':'蛋白酪氨酸激酶 2β（PTK2B）','Hepatocyte growth factor receptor':'肝细胞生长因子受体（MET）','Vascular endothelial growth factor receptor 2':'血管内皮生长因子受体 2（VEGFR2）'};
 const actions:Record<string,string>={'inhibitor':'抑制剂','agonist':'激动剂','antagonist':'拮抗剂','binding agent':'结合剂','modulator':'调节剂'};
 return text.split(/;\s*/).map(part=>{for(const [target,zh] of Object.entries(targets))for(const [action,actionZh] of Object.entries(actions))if(part.toLowerCase()===`${target} ${action}`.toLowerCase())return `${zh}${actionZh}`;return terms[part] || part;}).join('；');
}
export function identitySummary(data:DrugDossier) {
 const phase=data.max_phase;
 const category=phase===4 ? '有获批记录的药物' : typeof phase==='number' && phase>0 ? '有临床研究记录的分子' : '研究化合物';
 const mechanisms=[...new Set((data.mechanisms || []).map(m=>mechanismLabel(m.mechanism_of_action)).filter(Boolean))];
 const indication=data.indications?.slice().sort((a,b)=>(b.max_phase_for_ind ?? -1)-(a.max_phase_for_ind ?? -1))[0];
 return `${category}${mechanisms.length ? `；作用机制：${mechanisms.slice(0,2).join('、')}` : ''}${indication ? `；研究记录涉及${diseaseLabel(indication.efo_term || indication.mesh_heading,indication.efo_id || indication.mesh_id)}` : ''}。`;
}

export function moleculeTypeLabel(type?:string) { const labels:Record<string,string>={'Small molecule':'小分子','Antibody':'抗体','Protein':'蛋白质','Oligosaccharide':'寡糖','Oligonucleotide':'寡核苷酸','Cell':'细胞制剂','Enzyme':'酶'};return type ? labels[type] || type : ''; }
export function isBiologic(type?:string) {return !!type && ['antibody','protein','enzyme','cell','oligonucleotide'].includes(type.toLowerCase());}
