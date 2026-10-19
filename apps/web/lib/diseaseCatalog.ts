import diseaseIdentities from "./diseaseIdentities.json";
const fixedIdentities: Record<string, string> = diseaseIdentities;

export type DiseaseReadiness = "complete" | "enhanced" | "catalog" | "custom";
export type TherapeuticArea = "immunology" | "oncology" | "neurology" | "cardiovascular" | "metabolic" | "respiratory" | "renal" | "digestive" | "infectious" | "rare";

export interface DiseaseOption {
  key: string;
  nameZh: string;
  nameEn: string;
  ontologyId?: string;
  area?: TherapeuticArea;
  readiness: DiseaseReadiness;
  availableModules: string[];
}

export const THERAPEUTIC_AREA_LABELS: Record<TherapeuticArea, string> = {
  immunology: "免疫与炎症",
  oncology: "肿瘤",
  neurology: "神经系统",
  cardiovascular: "心血管",
  metabolic: "代谢与内分泌",
  respiratory: "呼吸与纤维化",
  renal: "肾脏与泌尿",
  digestive: "消化与肝病",
  infectious: "感染性疾病",
  rare: "罕见病与遗传病",
};

const complete: DiseaseOption[] = [
  ["ra", "类风湿关节炎", "Rheumatoid arthritis", "MONDO_0008383", "immunology"],
  ["sle", "系统性红斑狼疮", "Systemic lupus erythematosus", "MONDO_0007915", "immunology"],
  ["crohns", "克罗恩病", "Crohn disease", "MONDO_0005011", "immunology"],
  ["copd", "慢性阻塞性肺疾病", "Chronic obstructive pulmonary disease", "MONDO_0005002", "respiratory"],
  ["nsclc", "非小细胞肺癌", "Non-small cell lung carcinoma", "MONDO_0005233", "oncology"],
].map(([key, nameZh, nameEn, ontologyId, area]) => ({
  key, nameZh, nameEn, ontologyId, area: area as TherapeuticArea, readiness: "complete" as const,
  availableModules: ["疾病证据", "已有证据靶点", "新靶点探索", "虚拟干预", "虚拟细胞", "化合物评价", "综合排序"],
}));

const enhanced: DiseaseOption[] = [
  ["ipf", "特发性肺纤维化", "Idiopathic pulmonary fibrosis", "EFO_0000768", "respiratory"],
  ["psoriasis", "银屑病", "Psoriasis", "MONDO_0005083", "immunology"],
  ["ulcerative-colitis", "溃疡性结肠炎", "Ulcerative colitis", "MONDO_0005101", "immunology"],
  ["asthma", "哮喘", "Asthma", "MONDO_0004979", "respiratory"],
  ["t2d", "2 型糖尿病", "Type 2 diabetes mellitus", "MONDO_0005148", "metabolic"],
  ["ckd", "慢性肾病", "Chronic kidney disease", "MONDO_0005300", "renal"],
  ["alzheimers", "阿尔茨海默病", "Alzheimer disease", "MONDO_0004975", "neurology"],
  ["parkinsons", "帕金森病", "Parkinson disease", "MONDO_0005180", "neurology"],
  ["breast-cancer", "乳腺癌", "Breast cancer", "MONDO_0007254", "oncology"],
  ["colorectal-cancer", "结直肠癌", "Colorectal cancer", "MONDO_0005575", "oncology"],
  ["hcc", "肝细胞癌", "Hepatocellular carcinoma", "MONDO_0007256", "oncology"],
  ["melanoma", "黑色素瘤", "Melanoma", "MONDO_0005105", "oncology"],
  ["aml", "急性髓系白血病", "Acute myeloid leukemia", "MONDO_0018874", "oncology"],
  ["glioblastoma", "胶质母细胞瘤", "Glioblastoma", "MONDO_0018177", "oncology"],
  ["pancreatic-cancer", "胰腺癌", "Pancreatic cancer", "MONDO_0009831", "oncology"],
  ["dmd", "杜氏肌营养不良", "Duchenne muscular dystrophy", "MONDO_0010679", "rare"],
  ["cystic-fibrosis", "囊性纤维化", "Cystic fibrosis", "MONDO_0009061", "rare"],
  ["sma", "脊髓性肌萎缩症", "Spinal muscular atrophy", "MONDO_0001516", "rare"],
  ["fabry", "法布里病", "Fabry disease", "MONDO_0010526", "rare"],
  ["gaucher", "戈谢病", "Gaucher disease", "MONDO_0018150", "rare"],
].map(([key, nameZh, nameEn, ontologyId, area]) => ({
  key, nameZh, nameEn, ontologyId, area: area as TherapeuticArea, readiness: "enhanced" as const,
  availableModules: ["公共证据新靶点探索", "疾病实体", "多源靶点证据", "三轴评分", "Target-to-Hit v3", "回顾性评测"],
}));

const catalogRows: Array<[string, string, TherapeuticArea]> = [
  ["银屑病关节炎", "Psoriatic arthritis", "immunology"],
  ["强直性脊柱炎", "Ankylosing spondylitis", "immunology"],
  ["特应性皮炎", "Atopic dermatitis", "immunology"],
  ["幼年特发性关节炎", "Juvenile idiopathic arthritis", "immunology"],
  ["干燥综合征", "Sjögren syndrome", "immunology"],
  ["系统性硬化症", "Systemic sclerosis", "immunology"],
  ["自身免疫性甲状腺病", "Autoimmune thyroid disease", "immunology"],
  ["格雷夫斯病", "Graves disease", "immunology"],
  ["桥本甲状腺炎", "Hashimoto thyroiditis", "immunology"],
  ["乳糜泻", "Celiac disease", "immunology"],
  ["重症肌无力", "Myasthenia gravis", "immunology"],
  ["白塞病", "Behçet disease", "immunology"],
  ["大动脉炎", "Takayasu arteritis", "immunology"],
  ["巨细胞动脉炎", "Giant cell arteritis", "immunology"],
  ["肉芽肿性多血管炎", "Granulomatosis with polyangiitis", "immunology"],
  ["结节病", "Sarcoidosis", "immunology"],
  ["化脓性汗腺炎", "Hidradenitis suppurativa", "immunology"],
  ["斑秃", "Alopecia areata", "immunology"],

  ["肺腺癌", "Lung adenocarcinoma", "oncology"],
  ["小细胞肺癌", "Small cell lung carcinoma", "oncology"],
  ["前列腺癌", "Prostate cancer", "oncology"],
  ["胃癌", "Gastric cancer", "oncology"],
  ["食管癌", "Esophageal cancer", "oncology"],
  ["卵巢癌", "Ovarian cancer", "oncology"],
  ["宫颈癌", "Cervical cancer", "oncology"],
  ["子宫内膜癌", "Endometrial cancer", "oncology"],
  ["肾细胞癌", "Renal cell carcinoma", "oncology"],
  ["膀胱癌", "Bladder cancer", "oncology"],
  ["甲状腺癌", "Thyroid cancer", "oncology"],
  ["头颈鳞状细胞癌", "Head and neck squamous cell carcinoma", "oncology"],
  ["低级别胶质瘤", "Low-grade glioma", "oncology"],
  ["弥漫大 B 细胞淋巴瘤", "Diffuse large B-cell lymphoma", "oncology"],
  ["滤泡性淋巴瘤", "Follicular lymphoma", "oncology"],
  ["多发性骨髓瘤", "Multiple myeloma", "oncology"],
  ["慢性淋巴细胞白血病", "Chronic lymphocytic leukemia", "oncology"],
  ["骨肉瘤", "Osteosarcoma", "oncology"],
  ["胰腺导管腺癌", "Pancreatic ductal adenocarcinoma", "oncology"],
  ["胆管癌", "Cholangiocarcinoma", "oncology"],
  ["胆囊癌", "Gallbladder cancer", "oncology"],
  ["肛管癌", "Anal cancer", "oncology"],
  ["睾丸癌", "Testicular cancer", "oncology"],
  ["阴茎癌", "Penile cancer", "oncology"],
  ["外阴癌", "Vulvar cancer", "oncology"],
  ["胸腺瘤", "Thymoma", "oncology"],
  ["间皮瘤", "Mesothelioma", "oncology"],
  ["肾上腺皮质癌", "Adrenocortical carcinoma", "oncology"],
  ["葡萄膜黑色素瘤", "Uveal melanoma", "oncology"],
  ["胃肠道间质瘤", "Gastrointestinal stromal tumor", "oncology"],
  ["神经母细胞瘤", "Neuroblastoma", "oncology"],
  ["髓母细胞瘤", "Medulloblastoma", "oncology"],
  ["视网膜母细胞瘤", "Retinoblastoma", "oncology"],
  ["尤文肉瘤", "Ewing sarcoma", "oncology"],
  ["横纹肌肉瘤", "Rhabdomyosarcoma", "oncology"],
  ["肾母细胞瘤", "Wilms tumor", "oncology"],
  ["肝母细胞瘤", "Hepatoblastoma", "oncology"],
  ["霍奇金淋巴瘤", "Hodgkin lymphoma", "oncology"],
  ["套细胞淋巴瘤", "Mantle cell lymphoma", "oncology"],
  ["T 细胞淋巴瘤", "T-cell lymphoma", "oncology"],
  ["急性淋巴细胞白血病", "Acute lymphoblastic leukemia", "oncology"],
  ["慢性髓系白血病", "Chronic myeloid leukemia", "oncology"],
  ["骨髓增生异常综合征", "Myelodysplastic syndrome", "oncology"],
  ["鼻咽癌", "Nasopharyngeal carcinoma", "oncology"],

  ["肌萎缩侧索硬化", "Amyotrophic lateral sclerosis", "rare"],
  ["亨廷顿病", "Huntington disease", "rare"],
  ["额颞叶痴呆", "Frontotemporal dementia", "neurology"],
  ["路易体痴呆", "Lewy body dementia", "neurology"],
  ["癫痫", "Epilepsy", "neurology"],
  ["偏头痛", "Migraine", "neurology"],
  ["精神分裂症", "Schizophrenia", "neurology"],
  ["重度抑郁障碍", "Major depressive disorder", "neurology"],
  ["双相情感障碍", "Bipolar disorder", "neurology"],
  ["自闭症谱系障碍", "Autism spectrum disorder", "neurology"],
  ["注意缺陷多动障碍", "Attention deficit hyperactivity disorder", "neurology"],
  ["脑卒中", "Stroke", "neurology"],
  ["脑出血", "Intracerebral hemorrhage", "neurology"],
  ["神经病理性疼痛", "Neuropathic pain", "neurology"],
  ["多发性硬化", "Multiple sclerosis", "neurology"],

  ["心力衰竭", "Heart failure", "cardiovascular"],
  ["心房颤动", "Atrial fibrillation", "cardiovascular"],
  ["高血压", "Hypertension", "cardiovascular"],
  ["扩张型心肌病", "Dilated cardiomyopathy", "cardiovascular"],
  ["肥厚型心肌病", "Hypertrophic cardiomyopathy", "cardiovascular"],
  ["外周动脉疾病", "Peripheral artery disease", "cardiovascular"],
  ["动脉粥样硬化", "Atherosclerosis", "cardiovascular"],
  ["肺动脉高压", "Pulmonary arterial hypertension", "cardiovascular"],
  ["腹主动脉瘤", "Abdominal aortic aneurysm", "cardiovascular"],
  ["静脉血栓栓塞", "Venous thromboembolism", "cardiovascular"],
  ["冠状动脉疾病", "Coronary artery disease", "cardiovascular"],

  ["1 型糖尿病", "Type 1 diabetes mellitus", "metabolic"],
  ["代谢综合征", "Metabolic syndrome", "metabolic"],
  ["高胆固醇血症", "Hypercholesterolemia", "metabolic"],
  ["高甘油三酯血症", "Hypertriglyceridemia", "metabolic"],
  ["痛风", "Gout", "metabolic"],
  ["骨质疏松症", "Osteoporosis", "metabolic"],
  ["多囊卵巢综合征", "Polycystic ovary syndrome", "metabolic"],
  ["甲状腺功能减退", "Hypothyroidism", "metabolic"],
  ["甲状腺功能亢进", "Hyperthyroidism", "metabolic"],
  ["肢端肥大症", "Acromegaly", "metabolic"],
  ["肥胖症", "Obesity", "metabolic"],

  ["肺纤维化", "Pulmonary fibrosis", "respiratory"],
  ["支气管扩张症", "Bronchiectasis", "respiratory"],
  ["过敏性鼻炎", "Allergic rhinitis", "respiratory"],
  ["急性呼吸窘迫综合征", "Acute respiratory distress syndrome", "respiratory"],
  ["肺结节病", "Pulmonary sarcoidosis", "respiratory"],
  ["间质性肺病", "Interstitial lung disease", "respiratory"],
  ["阻塞性睡眠呼吸暂停", "Obstructive sleep apnea", "respiratory"],

  ["糖尿病肾病", "Diabetic nephropathy", "renal"],
  ["IgA 肾病", "IgA nephropathy", "renal"],
  ["膜性肾病", "Membranous nephropathy", "renal"],
  ["多囊肾病", "Polycystic kidney disease", "renal"],
  ["急性肾损伤", "Acute kidney injury", "renal"],
  ["肾小球肾炎", "Glomerulonephritis", "renal"],
  ["肾病综合征", "Nephrotic syndrome", "renal"],
  ["膀胱过度活动症", "Overactive bladder", "renal"],

  ["非酒精性脂肪性肝病", "Non-alcoholic fatty liver disease", "digestive"],
  ["代谢相关脂肪性肝炎", "Metabolic dysfunction-associated steatohepatitis", "digestive"],
  ["肝硬化", "Liver cirrhosis", "digestive"],
  ["原发性胆汁性胆管炎", "Primary biliary cholangitis", "digestive"],
  ["原发性硬化性胆管炎", "Primary sclerosing cholangitis", "digestive"],
  ["急性胰腺炎", "Acute pancreatitis", "digestive"],
  ["慢性胰腺炎", "Chronic pancreatitis", "digestive"],
  ["肠易激综合征", "Irritable bowel syndrome", "digestive"],
  ["胃食管反流病", "Gastroesophageal reflux disease", "digestive"],

  ["新型冠状病毒感染", "COVID-19", "infectious"],
  ["流感", "Influenza", "infectious"],
  ["结核病", "Tuberculosis", "infectious"],
  ["慢性乙型肝炎", "Chronic hepatitis B", "infectious"],
  ["慢性丙型肝炎", "Chronic hepatitis C", "infectious"],
  ["人类免疫缺陷病毒感染", "HIV infection", "infectious"],
  ["登革热", "Dengue fever", "infectious"],
  ["脓毒症", "Sepsis", "infectious"],

  ["镰状细胞病", "Sickle cell disease", "rare"],
  ["β 地中海贫血", "Beta thalassemia", "rare"],
  ["血友病 A", "Hemophilia A", "rare"],
  ["再生障碍性贫血", "Aplastic anemia", "rare"],
  ["阵发性睡眠性血红蛋白尿", "Paroxysmal nocturnal hemoglobinuria", "rare"],
  ["特发性血小板减少性紫癜", "Immune thrombocytopenia", "rare"],
  ["庞贝病", "Pompe disease", "rare"],
  ["贝克型肌营养不良", "Becker muscular dystrophy", "rare"],
  ["尼曼-匹克病", "Niemann-Pick disease", "rare"],
  ["黏多糖贮积症", "Mucopolysaccharidosis", "rare"],
  ["遗传性转甲状腺素蛋白淀粉样变性", "Hereditary transthyretin amyloidosis", "rare"],
  ["Rett 综合征", "Rett syndrome", "rare"],
  ["Dravet 综合征", "Dravet syndrome", "rare"],
  ["脆性 X 综合征", "Fragile X syndrome", "rare"],
  ["Angelman 综合征", "Angelman syndrome", "rare"],
  ["Prader-Willi 综合征", "Prader-Willi syndrome", "rare"],
  ["结节性硬化症", "Tuberous sclerosis complex", "rare"],
  ["马凡综合征", "Marfan syndrome", "rare"],
  ["Ehlers-Danlos 综合征", "Ehlers-Danlos syndrome", "rare"],
  ["成骨不全症", "Osteogenesis imperfecta", "rare"],
  ["软骨发育不全", "Achondroplasia", "rare"],
  ["苯丙酮尿症", "Phenylketonuria", "rare"],
  ["枫糖尿症", "Maple syrup urine disease", "rare"],
  ["甲基丙二酸血症", "Methylmalonic acidemia", "rare"],
  ["丙酸血症", "Propionic acidemia", "rare"],
  ["鸟氨酸氨甲酰转移酶缺乏症", "Ornithine transcarbamylase deficiency", "rare"],
  ["急性间歇性卟啉病", "Acute intermittent porphyria", "rare"],
  ["原发性高草酸尿症", "Primary hyperoxaluria", "rare"],
  ["Alport 综合征", "Alport syndrome", "rare"],
  ["非典型溶血性尿毒症综合征", "Atypical hemolytic uremic syndrome", "rare"],
  ["遗传性血管性水肿", "Hereditary angioedema", "rare"],
  ["家族性地中海热", "Familial Mediterranean fever", "rare"],
  ["系统性肥大细胞增多症", "Systemic mastocytosis", "rare"],
  ["原发性纤毛运动障碍", "Primary ciliary dyskinesia", "rare"],
  ["α-1 抗胰蛋白酶缺乏症", "Alpha-1 antitrypsin deficiency", "rare"],
  ["肺淋巴管肌瘤病", "Lymphangioleiomyomatosis", "rare"],
  ["特发性肺动脉高压", "Idiopathic pulmonary arterial hypertension", "rare"],
  ["遗传性视网膜营养不良", "Inherited retinal dystrophy", "rare"],
  ["Leber 遗传性视神经病变", "Leber hereditary optic neuropathy", "rare"],
  ["X 连锁肾上腺脑白质营养不良", "X-linked adrenoleukodystrophy", "rare"],
  ["先天性肌无力综合征", "Congenital myasthenic syndrome", "rare"],
  ["进行性骨化性纤维发育不良", "Fibrodysplasia ossificans progressiva", "rare"],
  ["低磷性佝偻病", "Hypophosphatemic rickets", "rare"],
  ["威尔逊病", "Wilson disease", "rare"],
  ["神经元蜡样脂褐质沉积症", "Neuronal ceroid lipofuscinosis", "rare"],
];

const catalog: DiseaseOption[] = catalogRows.map(([nameZh, nameEn, area], index) => ({
  key: `catalog-${index + 1}`,
  nameZh,
  nameEn,
  ontologyId: fixedIdentities[nameEn],
  area,
  readiness: "catalog",
  availableModules: ["公共证据新靶点探索", "已固定疾病实体", "已有靶点证据检索"],
}));

export const DISEASE_OPTIONS: DiseaseOption[] = [...complete, ...enhanced, ...catalog];
export const CUSTOM_DISEASE: DiseaseOption = {
  key: "custom", nameZh: "其他疾病", nameEn: "输入中文名、英文名或疾病本体 ID", readiness: "custom", availableModules: ["疾病实体解析"],
};

export const DISEASE_CATALOG_STATS = {
  total: DISEASE_OPTIONS.length,
  complete: complete.length,
  enhanced: enhanced.length,
  catalog: catalog.length,
  oncology: DISEASE_OPTIONS.filter((item) => item.area === "oncology").length,
  rare: DISEASE_OPTIONS.filter((item) => item.area === "rare").length,
};

export const RESEARCH_TRACKS = [
  { key: "full", label: "完整候选发现流程", description: "从靶点证据开始，逐步推进到细胞情境、化合物和综合排序。" },
  { key: "target_discovery", label: "靶点发现与排序", description: "先整理疾病相关证据，形成可解释的优先靶点清单。" },
  { key: "novel_discovery", label: "新靶点探索", description: "优先使用专属多组学；其他目录疾病使用公共非药物证据生成待验证候选。" },
  { key: "compound_prioritization", label: "候选化合物评价", description: "围绕已有靶点比较活性、药物性质和开发风险。" },
] as const;

export function defaultTrack(readiness: DiseaseReadiness) {
  return readiness === "complete" ? "full" : "target_discovery";
}

export function analysisQueryForDisease(displayName: string, diseaseId?: string) {
  if (diseaseId?.trim()) return diseaseId.trim();
  const normalized = displayName.trim().toLocaleLowerCase("zh-CN");
  const option = DISEASE_OPTIONS.find((item) =>
    item.nameZh.toLocaleLowerCase("zh-CN") === normalized || item.nameEn.toLocaleLowerCase("zh-CN") === normalized
  );
  return option?.ontologyId ?? option?.nameEn ?? displayName.trim();
}

export function readinessCopy(readiness: DiseaseReadiness) {
  if (readiness === "complete") return { title: "完整公开数据链路已验证", description: "已有疾病专属遗传、单细胞和下游评价产物；每一步仍会单独显示数据覆盖与质量门。" };
  if (readiness === "enhanced") return { title: "增强靶点分析已验证", description: "可运行公共证据新靶点探索、已有靶点分析与 Target-to-Hit v3；专属细胞模型仍按数据覆盖使用。" };
  if (readiness === "catalog") return { title: "公共证据探索已接入", description: "可离线运行新靶点证据探索，查看候选依据与验证建议；没有合格候选时显示证据缺口。专属单细胞模型需另行验证。" };
  return { title: "先确认疾病能否被公共数据库识别", description: "创建后先运行疾病解析和已有证据检索；未被准确识别时不会生成推测结果。" };
}
