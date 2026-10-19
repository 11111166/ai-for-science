import { DISEASE_OPTIONS, THERAPEUTIC_AREA_LABELS, type DiseaseOption, type TherapeuticArea } from "./diseaseCatalog";

export interface DiseaseProfile {
  disease: DiseaseOption | null;
  summary: string;
  tissues: string[];
  cellContexts: string[];
  biologyThemes: string[];
  developmentQuestions: string[];
}

type ProfileFacts = Omit<DiseaseProfile, "disease">;

const AREA_DEFAULTS: Record<TherapeuticArea, ProfileFacts> = {
  immunology: {
    summary: "重点判断免疫失衡发生在哪些组织和细胞中，并区分抑制炎症与恢复免疫稳态两类策略。",
    tissues: ["外周血", "疾病受累组织"], cellContexts: ["T / B 细胞", "髓系细胞"],
    biologyThemes: ["炎症因子", "免疫细胞活化", "组织损伤"], developmentQuestions: ["作用方向是否一致", "长期免疫抑制风险", "患者分层标志物"],
  },
  oncology: {
    summary: "同时考察肿瘤细胞依赖性、肿瘤微环境和正常组织安全窗，避免只凭疾病关联选择靶点。",
    tissues: ["原发肿瘤", "转移灶或邻近组织"], cellContexts: ["肿瘤细胞", "免疫与基质细胞"],
    biologyThemes: ["驱动信号", "细胞存活依赖", "免疫逃逸"], developmentQuestions: ["肿瘤选择性", "耐药机制", "可用分子路线"],
  },
  neurology: {
    summary: "优先区分致病细胞、脑区和病程阶段，并把血脑屏障与神经系统安全窗作为开发前置条件。",
    tissues: ["中枢神经系统", "外周血或脑脊液"], cellContexts: ["神经元", "胶质细胞"],
    biologyThemes: ["神经炎症", "突触与神经元功能", "蛋白稳态"], developmentQuestions: ["血脑屏障暴露", "细胞类型特异性", "病程窗口"],
  },
  cardiovascular: {
    summary: "围绕心血管结构、代谢和炎症信号建立疾病画像，并重点检查心肌与血管系统安全窗。",
    tissues: ["心脏", "血管与外周血"], cellContexts: ["心肌细胞", "内皮与免疫细胞"],
    biologyThemes: ["收缩与电生理", "血管重塑", "炎症代谢"], developmentQuestions: ["心脏安全性", "组织选择性", "慢病给药可行性"],
  },
  metabolic: {
    summary: "连接代谢器官、激素调控与慢性炎症，关注疗效信号能否与体重、血糖或脂质等表型对应。",
    tissues: ["肝脏", "脂肪与胰腺"], cellContexts: ["代谢实质细胞", "免疫细胞"],
    biologyThemes: ["能量代谢", "胰岛素信号", "慢性炎症"], developmentQuestions: ["代谢终点关联", "长期安全性", "组织暴露"],
  },
  respiratory: {
    summary: "从气道、肺实质和免疫微环境解释疾病，区分抗炎、抗纤维化和组织修复策略。",
    tissues: ["肺组织", "气道"], cellContexts: ["上皮细胞", "免疫与成纤维细胞"],
    biologyThemes: ["炎症", "组织重塑", "纤维化"], developmentQuestions: ["肺部递送", "慢性用药安全", "可逆与不可逆病变"],
  },
  renal: {
    summary: "围绕肾小球、肾小管和免疫损伤建立靶点情境，并把肾功能变化与系统暴露风险分开判断。",
    tissues: ["肾脏", "外周血"], cellContexts: ["肾小球细胞", "肾小管与免疫细胞"],
    biologyThemes: ["滤过屏障", "炎症", "纤维化"], developmentQuestions: ["肾功能安全窗", "细胞定位", "疾病阶段"],
  },
  digestive: {
    summary: "结合消化器官的上皮、免疫和代谢情境，判断局部作用与系统给药哪种方式更合适。",
    tissues: ["受累消化器官", "外周血"], cellContexts: ["上皮或实质细胞", "免疫与基质细胞"],
    biologyThemes: ["屏障功能", "炎症", "代谢与纤维化"], developmentQuestions: ["局部递送", "组织选择性", "慢性安全性"],
  },
  infectious: {
    summary: "区分直接抗病原体与宿主靶向策略，避免把免疫相关性直接解释为治疗获益。",
    tissues: ["感染受累组织", "外周血"], cellContexts: ["宿主易感细胞", "先天与适应性免疫细胞"],
    biologyThemes: ["病原体生命周期", "宿主防御", "过度炎症"], developmentQuestions: ["宿主安全窗", "病原体变异", "治疗时机"],
  },
  rare: {
    summary: "优先确认致病基因、变异机制和受累组织，再选择补偿、抑制或基因层面的干预方式。",
    tissues: ["遗传病主要受累组织", "可获得的替代样本"], cellContexts: ["致病相关细胞", "可验证的患者来源细胞"],
    biologyThemes: ["基因—表型关系", "功能缺失或获得", "组织损伤"], developmentQuestions: ["致病方向", "递送可达性", "患者与变异分层"],
  },
};

const FOCUS: Record<string, Partial<ProfileFacts>> = {
  MONDO_0008383: { summary: "以滑膜炎症和免疫失衡为核心，比较遗传因果、免疫细胞状态与关节局部组织信号。", tissues: ["滑膜与关节", "外周血"], cellContexts: ["滑膜成纤维细胞", "巨噬细胞", "T / B 细胞"], biologyThemes: ["TNF / IL-6", "JAK–STAT", "抗原呈递"] },
  MONDO_0007915: { summary: "围绕全身自身免疫、Ⅰ型干扰素和器官受累构建画像，需要按患者与受累器官分层。", tissues: ["外周血", "肾脏", "皮肤"], cellContexts: ["B / 浆细胞", "T 细胞", "单核细胞"], biologyThemes: ["Ⅰ型干扰素", "B 细胞活化", "免疫复合物"] },
  MONDO_0005011: { summary: "聚焦肠黏膜屏障、髓系炎症和适应性免疫，并区分局部肠道作用与系统性免疫抑制。", tissues: ["回肠", "结肠", "外周血"], cellContexts: ["肠上皮", "髓系细胞", "T 细胞"], biologyThemes: ["黏膜屏障", "TNF / IL-23", "微生物—宿主互作"] },
  MONDO_0005002: { summary: "连接气道炎症、肺泡损伤和组织重塑，重点识别仍可逆的疾病程序。", tissues: ["气道", "肺实质", "外周血"], cellContexts: ["气道上皮", "肺泡巨噬细胞", "成纤维细胞"], biologyThemes: ["氧化应激", "慢性炎症", "组织重塑"] },
  MONDO_0005233: { summary: "同时评估肿瘤驱动依赖、免疫微环境与正常肺组织安全窗，并保留分子亚型差异。", tissues: ["肺肿瘤", "邻近肺组织", "转移灶"], cellContexts: ["肿瘤细胞", "T / NK 细胞", "髓系与基质细胞"], biologyThemes: ["驱动激酶", "细胞周期", "免疫逃逸"] },
  EFO_0000768: { summary: "围绕肺泡上皮损伤、成纤维细胞活化和异常基质沉积，寻找可阻断进展的干预点。", tissues: ["肺实质", "肺泡区域"], cellContexts: ["肺泡上皮", "成纤维细胞", "巨噬细胞"], biologyThemes: ["TGF-β", "细胞外基质", "损伤修复"] },
  MONDO_0005083: { tissues: ["病变皮肤", "外周血"], cellContexts: ["角质形成细胞", "T 细胞", "树突细胞"], biologyThemes: ["IL-23 / IL-17", "皮肤屏障", "免疫募集"] },
  MONDO_0005101: { tissues: ["结肠黏膜", "外周血"], cellContexts: ["肠上皮", "髓系细胞", "T 细胞"], biologyThemes: ["屏障损伤", "细胞因子", "组织修复"] },
  MONDO_0004979: { tissues: ["气道", "外周血"], cellContexts: ["气道上皮", "嗜酸性粒细胞", "T 细胞"], biologyThemes: ["2 型炎症", "气道高反应", "黏液分泌"] },
  MONDO_0005148: { tissues: ["胰岛", "肝脏", "脂肪与骨骼肌"], cellContexts: ["胰岛 β 细胞", "肝细胞", "脂肪细胞"], biologyThemes: ["胰岛素抵抗", "β 细胞功能", "代谢炎症"] },
  MONDO_0005300: { tissues: ["肾小球", "肾小管间质", "外周血"], cellContexts: ["足细胞", "肾小管细胞", "免疫与基质细胞"], biologyThemes: ["滤过屏障", "缺氧炎症", "纤维化"] },
  MONDO_0004975: { tissues: ["脑组织", "脑脊液与外周血"], cellContexts: ["神经元", "小胶质细胞", "星形胶质细胞"], biologyThemes: ["淀粉样蛋白", "Tau", "神经炎症"] },
  MONDO_0005180: { tissues: ["黑质—纹状体", "脑脊液与外周血"], cellContexts: ["多巴胺能神经元", "小胶质细胞", "星形胶质细胞"], biologyThemes: ["线粒体功能", "α-突触核蛋白", "神经炎症"] },
  MONDO_0007254: { tissues: ["乳腺肿瘤", "邻近组织", "转移灶"], cellContexts: ["肿瘤细胞", "T / NK 细胞", "成纤维细胞"], biologyThemes: ["激素 / HER2 信号", "DNA 修复", "免疫微环境"] },
  MONDO_0005575: { tissues: ["结直肠肿瘤", "邻近黏膜", "肝转移灶"], cellContexts: ["肿瘤上皮", "髓系细胞", "成纤维细胞"], biologyThemes: ["WNT / MAPK", "微卫星状态", "免疫微环境"] },
  MONDO_0007256: { tissues: ["肝肿瘤", "邻近肝组织"], cellContexts: ["肿瘤细胞", "肝巨噬细胞", "内皮细胞"], biologyThemes: ["增殖信号", "代谢重编程", "免疫抑制"] },
  MONDO_0005105: { tissues: ["皮肤肿瘤", "淋巴结或转移灶"], cellContexts: ["黑色素瘤细胞", "T 细胞", "髓系细胞"], biologyThemes: ["MAPK", "免疫检查点", "耐药演化"] },
  MONDO_0018874: { tissues: ["骨髓", "外周血"], cellContexts: ["白血病干 / 祖细胞", "髓系细胞", "骨髓基质"], biologyThemes: ["分化阻滞", "表观遗传", "凋亡逃逸"] },
  MONDO_0018177: { tissues: ["脑肿瘤", "肿瘤周边脑组织"], cellContexts: ["胶质瘤细胞", "小胶质 / 髓系细胞", "内皮细胞"], biologyThemes: ["受体酪氨酸激酶", "干性与侵袭", "免疫抑制"] },
  MONDO_0009831: { tissues: ["胰腺肿瘤", "肝或腹膜转移灶"], cellContexts: ["肿瘤细胞", "成纤维细胞", "髓系细胞"], biologyThemes: ["KRAS 信号", "致密基质", "代谢适应"] },
  MONDO_0010679: { tissues: ["骨骼肌", "心肌"], cellContexts: ["肌纤维", "肌卫星细胞", "免疫与纤维细胞"], biologyThemes: ["抗肌萎缩蛋白", "膜稳定", "炎症纤维化"] },
  MONDO_0009061: { tissues: ["肺与气道", "胰腺与消化道"], cellContexts: ["气道上皮", "分泌细胞", "免疫细胞"], biologyThemes: ["CFTR 功能", "离子转运", "感染炎症"] },
  MONDO_0001516: { tissues: ["脊髓", "骨骼肌"], cellContexts: ["运动神经元", "肌细胞", "胶质细胞"], biologyThemes: ["SMN 缺失", "RNA 加工", "神经肌肉连接"] },
  MONDO_0010526: { tissues: ["肾脏", "心脏", "神经系统"], cellContexts: ["内皮细胞", "心肌细胞", "肾脏细胞"], biologyThemes: ["GLA 缺陷", "糖鞘脂累积", "器官损伤"] },
  MONDO_0018150: { tissues: ["脾脏与肝脏", "骨髓", "神经系统（部分亚型）"], cellContexts: ["巨噬细胞", "造血细胞", "神经细胞"], biologyThemes: ["GBA 缺陷", "溶酶体累积", "炎症与器官损伤"] },
};

export function getDiseaseProfile(name: string, ontologyId?: string): DiseaseProfile {
  const normalized = name.trim().toLocaleLowerCase("zh-CN");
  const disease = (ontologyId ? DISEASE_OPTIONS.find((item) => item.ontologyId === ontologyId) : undefined)
    ?? DISEASE_OPTIONS.find((item) => [item.nameZh, item.nameEn].some((value) => value.toLocaleLowerCase("zh-CN") === normalized))
    ?? null;
  const area = disease?.area ?? "rare";
  const base = AREA_DEFAULTS[area];
  const focus = disease?.ontologyId ? FOCUS[disease.ontologyId] : undefined;
  return { disease, ...base, ...focus };
}

export function diseaseAreaLabel(profile: DiseaseProfile) {
  return profile.disease?.area ? THERAPEUTIC_AREA_LABELS[profile.disease.area] : "待确认疾病领域";
}
