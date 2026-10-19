"""Local, versioned material and reaction-process knowledge for process R&D."""
from functools import lru_cache
import re

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from .auth import get_current_user, require_project_access
from .database import get_db

router = APIRouter(prefix="/api/v1/drug-discovery/projects/{project_id}/process/knowledge")

VERSION = "process-knowledge-v3-20260915"
UPDATED_AT = "2026-09-15"

# PubChem PUG REST property snapshot. Columns: id|中文名|英文名|CAS|CID|分子式|分子量|SMILES|分类|XLogP|TPSA
MATERIAL_ROWS = """
water|水|Water|7732-18-5|962|H2O|18.015|O|solvent|-0.5|1
methanol|甲醇|Methanol|67-56-1|887|CH4O|32.042|CO|solvent|-0.5|20.2
ethanol|乙醇|Ethanol|64-17-5|702|C2H6O|46.07|CCO|solvent|-0.1|20.2
isopropanol|异丙醇|Isopropanol|67-63-0|3776|C3H8O|60.10|CC(C)O|solvent|0.3|20.2
acetonitrile|乙腈|Acetonitrile|75-05-8|6342|C2H3N|41.05|CC#N|solvent|0|23.8
acetone|丙酮|Acetone|67-64-1|180|C3H6O|58.08|CC(=O)C|solvent|-0.1|17.1
ethyl-acetate|乙酸乙酯|Ethyl acetate|141-78-6|8857|C4H8O2|88.11|CCOC(=O)C|solvent|0.7|26.3
dichloromethane|二氯甲烷|Dichloromethane|75-09-2|6344|CH2Cl2|84.93|C(Cl)Cl|solvent|1.5|0
chloroform|氯仿|Chloroform|67-66-3|6212|CHCl3|119.37|C(Cl)(Cl)Cl|solvent|2.3|0
toluene|甲苯|Toluene|108-88-3|1140|C7H8|92.14|CC1=CC=CC=C1|solvent|2.7|0
tetrahydrofuran|四氢呋喃|Tetrahydrofuran|109-99-9|8028|C4H8O|72.11|C1CCOC1|solvent|0.5|9.2
dmf|N,N-二甲基甲酰胺|Dimethylformamide|68-12-2|6228|C3H7NO|73.09|CN(C)C=O|solvent|-1|20.3
dmso|二甲基亚砜|Dimethyl sulfoxide|67-68-5|679|C2H6OS|78.14|CS(=O)C|solvent|-0.6|36.3
nmp|N-甲基吡咯烷酮|N-methyl-2-pyrrolidone|872-50-4|13387|C5H9NO|99.13|CN1CCCC1=O|solvent|-0.5|20.3
heptane|正庚烷|Heptane|142-82-5|8900|C7H16|100.20|CCCCCCC|solvent|4.4|0
hexane|正己烷|Hexane|110-54-3|8058|C6H14|86.18|CCCCCC|solvent|3.9|0
mtbe|甲基叔丁基醚|tert-Butyl methyl ether|1634-04-4|15413|C5H12O|88.15|CC(C)(C)OC|solvent|0.9|9.2
dioxane|1,4-二氧六环|1,4-Dioxane|123-91-1|31275|C4H8O2|88.11|C1COCCO1|solvent|-0.3|18.5
sodium-hydroxide|氢氧化钠|Sodium hydroxide|1310-73-2|14798|HNaO|39.997|[OH-].[Na+]|base||1
potassium-hydroxide|氢氧化钾|Potassium hydroxide|1310-58-3|14797|HKO|56.106|[OH-].[K+]|base||1
sodium-carbonate|碳酸钠|Sodium carbonate|497-19-8|10340|CNa2O3|105.988|C(=O)([O-])[O-].[Na+].[Na+]|base||63.2
potassium-carbonate|碳酸钾|Potassium carbonate|584-08-7|11430|CK2O3|138.205|C(=O)([O-])[O-].[K+].[K+]|base||63.2
sodium-bicarbonate|碳酸氢钠|Sodium bicarbonate|144-55-8|516892|CHNaO3|84.007|C(=O)(O)[O-].[Na+]|base||60.4
triethylamine|三乙胺|Triethylamine|121-44-8|8471|C6H15N|101.19|CCN(CC)CC|base|1.4|3.2
dipea|N,N-二异丙基乙胺|Diisopropylethylamine|7087-68-5|81531|C8H19N|129.24|CCN(C(C)C)C(C)C|base|2.2|3.2
pyridine|吡啶|Pyridine|110-86-1|1049|C5H5N|79.10|C1=CC=NC=C1|base|0.7|12.9
dbu|DBU|1,8-Diazabicyclo[5.4.0]undec-7-ene|6674-22-2|81184|C9H16N2|152.24|C1CCC2=NCCCN2CC1|base|1.4|15.6
cesium-carbonate|碳酸铯|Cesium carbonate|534-17-8|10796|CCs2O3|325.820|C(=O)([O-])[O-].[Cs+].[Cs+]|base||63.2
hydrochloric-acid|盐酸|Hydrochloric acid|7647-01-0|313|ClH|36.46|Cl|acid|0.8|0
sulfuric-acid|硫酸|Sulfuric acid|7664-93-9|1118|H2O4S|98.08|OS(=O)(=O)O|acid|-1.4|83
phosphoric-acid|磷酸|Phosphoric acid|7664-38-2|1004|H3O4P|97.995|OP(=O)(O)O|acid|-2.1|77.8
acetic-acid|乙酸|Acetic acid|64-19-7|176|C2H4O2|60.05|CC(=O)O|acid|-0.2|37.3
tfa|三氟乙酸|Trifluoroacetic acid|76-05-1|6422|C2HF3O2|114.02|C(=O)(C(F)(F)F)O|acid|0.9|37.3
msa|甲磺酸|Methanesulfonic acid|75-75-2|6395|CH4O3S|96.11|CS(=O)(=O)O|acid|-0.9|62.8
acetic-anhydride|乙酸酐|Acetic anhydride|108-24-7|7918|C4H6O3|102.09|CC(=O)OC(=O)C|reagent|-0.1|43.4
oxalyl-chloride|草酰氯|Oxalyl chloride|79-37-8|65578|C2Cl2O2|126.92|C(=O)(C(=O)Cl)Cl|reagent|1.7|34.1
thionyl-chloride|氯化亚砜|Thionyl chloride|7719-09-7|24386|Cl2OS|118.97|O=S(Cl)Cl|reagent|1.5|36.3
hydrogen-peroxide|过氧化氢|Hydrogen peroxide|7722-84-1|784|H2O2|34.015|OO|reagent|-0.9|40.5
sodium-borohydride|硼氢化钠|Sodium borohydride|16940-66-2|4311764|BH4Na|37.84|[BH4-].[Na+]|reagent||0
lah|氢化铝锂|Lithium aluminum hydride|16853-85-3|28112|AlH4Li|38.0|[Li+].[AlH4-]|reagent||0
dibal-h|二异丁基氢化铝|Diisobutylaluminum hydride|1191-15-7|14487|C8H19Al|142.22|CC(C)C[AlH]CC(C)C|reagent||0
mcpba|间氯过氧苯甲酸|m-Chloroperbenzoic acid|937-14-4|70297|C7H5ClO3|172.56|C1=CC(=CC(=C1)Cl)C(=O)OO|reagent|2.4|46.5
nbs|N-溴代琥珀酰亚胺|N-Bromosuccinimide|128-08-5|67184|C4H4BrNO2|177.98|C1CC(=O)N(C1=O)Br|reagent|-0.1|37.4
iodine|碘|Iodine|7553-56-2|807|I2|253.8089|II|reagent|1.7|0
edci|1-(3-二甲氨基丙基)-3-乙基碳二亚胺|EDCI|1892-57-5|15908|C8H17N3|155.24|CCN=C=NCCCN(C)C|reagent|1.9|28
dcc|二环己基碳二亚胺|Dicyclohexylcarbodiimide|538-75-0|10868|C13H22N2|206.33|C1CCC(CC1)N=C=NC2CCCCC2|reagent|4.7|24.7
palladium-acetate|乙酸钯|Palladium(II) acetate|3375-31-3|167845|C4H6O4Pd|224.51|CC(=O)[O-].CC(=O)[O-].[Pd+2]|catalyst||80.3
pd-pph3-4|四(三苯基膦)钯|Tetrakis(triphenylphosphine)palladium|14221-01-3|11979704|C72H60P4Pd|1155.6|C1=CC=C(C=C1)P(C2=CC=CC=C2)C3=CC=CC=C3.C1=CC=C(C=C1)P(C2=CC=CC=C2)C3=CC=CC=C3.C1=CC=C(C=C1)P(C2=CC=CC=C2)C3=CC=CC=C3.C1=CC=C(C=C1)P(C2=CC=CC=C2)C3=CC=CC=C3.[Pd]|catalyst||0
copper-iodide|碘化亚铜|Copper(I) iodide|7681-65-4|6432705|CuI|190.45|[Cu+].[I-]|catalyst||0
diethyl-ether|乙醚|Diethyl ether|60-29-7|3283|C4H10O|74.12|CCOCC|solvent|0.9|9.2
2-methyltetrahydrofuran|2-甲基四氢呋喃|2-Methyltetrahydrofuran|96-47-9|7301|C5H10O|86.13|CC1CCCO1|solvent|1|9.2
cpme|环戊基甲醚|Cyclopentyl methyl ether|5614-37-9|138539|C6H12O|100.16|COC1CCCC1|solvent|1.3|9.2
dichloroethane|1,2-二氯乙烷|1,2-Dichloroethane|107-06-2|11|C2H4Cl2|98.96|C(CCl)Cl|solvent|1.5|0
chlorobenzene|氯苯|Chlorobenzene|108-90-7|7964|C6H5Cl|112.55|C1=CC=C(C=C1)Cl|solvent|2.9|0
anisole|苯甲醚|Anisole|100-66-3|7519|C7H8O|108.14|COC1=CC=CC=C1|solvent|2.1|9.2
mek|丁酮|2-Butanone|78-93-3|6569|C4H8O|72.11|CCC(=O)C|solvent|0.3|17.1
mibk|甲基异丁基酮|4-Methyl-2-pentanone|108-10-1|7909|C6H12O|100.16|CC(C)CC(=O)C|solvent|1.3|17.1
isopropyl-acetate|乙酸异丙酯|Isopropyl acetate|108-21-4|7915|C5H10O2|102.13|CC(C)OC(=O)C|solvent|0.9|26.3
butyl-acetate|乙酸正丁酯|Butyl acetate|123-86-4|31272|C6H12O2|116.16|CCCCOC(=O)C|solvent|1.8|26.3
dmac|N,N-二甲基乙酰胺|Dimethylacetamide|127-19-5|31374|C4H9NO|87.12|CC(=O)N(C)C|solvent|-0.8|20.3
sulfolane|环丁砜|Sulfolane|126-33-0|31347|C4H8O2S|120.17|C1CCS(=O)(=O)C1|solvent|-0.8|42.5
propylene-carbonate|碳酸丙烯酯|Propylene carbonate|108-32-7|7924|C4H6O3|102.09|CC1COC(=O)O1|solvent|-0.4|35.5
sodium-methoxide|甲醇钠|Sodium methoxide|124-41-4|10942334|CH3NaO|54.024|C[O-].[Na+]|base||23.1
potassium-tert-butoxide|叔丁醇钾|Potassium tert-butoxide|865-47-4|23665647|C4H9KO|112.21|CC(C)(C)[O-].[K+]|base||23.1
lithium-hydroxide|氢氧化锂|Lithium hydroxide|1310-65-2|3939|HLiO|24.0|[Li+].[OH-]|base||1
sodium-hydride|氢化钠|Sodium hydride|7646-69-7|24758|HNa|23.9977|[H-].[Na+]|base||0
nitric-acid|硝酸|Nitric acid|7697-37-2|944|HNO3|63.013|[N+](=O)(O)[O-]|acid|0.2|66.1
formic-acid|甲酸|Formic acid|64-18-6|284|CH2O2|46.025|C(=O)O|acid|-0.2|37.3
ptsa|对甲苯磺酸|p-Toluenesulfonic acid|104-15-4|6101|C7H8O3S|172.20|CC1=CC=C(C=C1)S(=O)(=O)O|acid|1.7|62.8
bromine|溴|Bromine|7726-95-6|24408|Br2|159.81|BrBr|reagent|1.9|0
phosphorus-oxychloride|三氯氧磷|Phosphorus oxychloride|10025-87-3|24813|Cl3OP|153.33|O=P(Cl)(Cl)Cl|reagent|1.7|17.1
triphosgene|固体光气|Triphosgene|32315-10-9|94429|C3Cl6O3|296.7|C(=O)(OC(Cl)(Cl)Cl)OC(Cl)(Cl)Cl|reagent|4.4|35.5
cdi|羰基二咪唑|Carbonyldiimidazole|530-62-1|68263|C7H6N4O|162.15|C1=CN(C=N1)C(=O)N2C=CN=C2|reagent|-0.2|52.7
tempo|TEMPO|2,2,6,6-Tetramethylpiperidine 1-oxyl|2564-83-2|2724126|C9H18NO|156.25|CC1(CCCC(N1[O])(C)C)C|reagent|1.4|4.2
dess-martin|戴斯-马丁氧化剂|Dess-Martin periodinane|87413-09-0|159087|C13H13IO8|424.14|CC(=O)OI1(C2=CC=CC=C2C(=O)O1)(OC(=O)C)OC(=O)C|reagent|2.7|105
hydrogen|氢气|Hydrogen|1333-74-0|783|H2|2.016|[HH]|gas|0|0
nitrogen|氮气|Nitrogen|7727-37-9|947|N2|28.014|N#N|gas|0.1|47.6
oxygen|氧气|Oxygen|7782-44-7|977|O2|31.999|O=O|gas|-1.1|34.1
carbon-dioxide|二氧化碳|Carbon dioxide|124-38-9|280|CO2|44.009|C(=O)=O|gas|0.9|34.1
ammonia|氨|Ammonia|7664-41-7|222|H3N|17.031|N|gas|-0.7|1
""".strip()

CATEGORY_LABELS = {"solvent": "溶剂", "base": "碱", "acid": "酸", "reagent": "试剂", "catalyst": "催化剂", "gas": "工艺气体"}
CATEGORY_GUIDANCE = {
    "solvent": ("反应介质、萃取或结晶筛选", "核对可燃性、职业暴露、残留限度与溶剂回收条件"),
    "base": ("调节酸碱性、缚酸或促进成键/水解", "关注加料放热、盐析、气体释放及后处理 pH 窗口"),
    "acid": ("催化、脱保护、成盐或 pH 调节", "关注腐蚀、稀释放热、设备材质及淬灭顺序"),
    "reagent": ("官能团转化或活化", "先做相容性与量热筛查，再确定加料和淬灭方案"),
    "catalyst": ("催化成键或官能团转化", "关注金属残留、失活、过滤回收和放大传质"),
    "gas": ("反应气体、惰化保护、吹扫或 pH/相态调节", "核对气体纯度、供气压力、泄漏、可燃/氧化/毒性、尾气和压力保护"),
}
ALIASES = {
    "isopropanol": ["IPA", "2-propanol", "2-丙醇"], "acetonitrile": ["MeCN"], "ethyl-acetate": ["EtOAc"],
    "dichloromethane": ["DCM", "二氯甲烷"], "tetrahydrofuran": ["THF"], "dmf": ["DMF"], "dmso": ["DMSO"],
    "nmp": ["NMP"], "mtbe": ["MTBE"], "triethylamine": ["TEA"], "dipea": ["DIPEA", "Hünig's base"],
    "dbu": ["1,8-二氮杂双环[5.4.0]十一碳-7-烯"], "tfa": ["TFA"], "msa": ["MSA"],
    "lah": ["LAH", "LiAlH4"], "dibal-h": ["DIBAL-H"], "mcpba": ["mCPBA"], "nbs": ["NBS"],
    "edci": ["EDC", "EDC free base"], "dcc": ["DCC"], "palladium-acetate": ["Pd(OAc)2"],
    "pd-pph3-4": ["Pd(PPh3)4"], "copper-iodide": ["CuI"],
    "diethyl-ether": ["Et2O", "ether"], "2-methyltetrahydrofuran": ["2-MeTHF"], "cpme": ["CPME"],
    "dichloroethane": ["DCE", "ethylene dichloride"], "mek": ["MEK"], "mibk": ["MIBK"], "dmac": ["DMAc"],
    "sodium-methoxide": ["NaOMe"], "potassium-tert-butoxide": ["KOtBu", "t-BuOK"], "sodium-hydride": ["NaH"],
    "ptsa": ["p-TsOH", "TsOH"], "phosphorus-oxychloride": ["POCl3"], "triphosgene": ["BTC"],
    "cdi": ["CDI"], "tempo": ["TEMPO"], "dess-martin": ["DMP"],
}
SPECIAL_GUIDANCE = {
    "water": ("反应介质、洗涤、淬灭和结晶反溶剂", "使用前确认底物、试剂和设备对水的相容性"),
    "tetrahydrofuran": ("醚类反应溶剂，常用于有机金属或还原体系", "可形成过氧化物；使用和浓缩前核对储存状态"),
    "dioxane": ("醚类共溶剂", "可形成过氧化物，并需评估职业暴露和残留控制"),
    "dmf": ("高极性非质子溶剂", "沸点较高，需评估职业暴露、残留与废液处理"),
    "nmp": ("高极性非质子溶剂", "沸点较高，需评估生殖毒性相关暴露与残留"),
    "hexane": ("非极性溶剂，常用于萃取、打浆和色谱", "高度易燃，并需评估正己烷神经毒性相关暴露"),
    "sodium-bicarbonate": ("温和碱、酸性体系淬灭和水洗", "与酸接触释放二氧化碳；放大时控制加料与排气"),
    "oxalyl-chloride": ("羧酸活化、酰氯制备和氧化体系组分", "遇水反应并释放气体；必须评估放热、尾气和加料顺序"),
    "thionyl-chloride": ("酰氯制备和脱水氯化", "遇水反应并产生腐蚀性气体；需配置密闭加料与尾气吸收"),
    "hydrogen-peroxide": ("氧化剂", "浓度、污染物和加料方式显著影响分解与放热风险"),
    "lah": ("强还原剂", "与水和质子溶剂剧烈反应；先做相容性、量热和受控淬灭研究"),
    "dibal-h": ("选择性还原试剂", "通常以可燃溶液供应；遇水反应，需控制温度、加料和淬灭"),
    "mcpba": ("过酸氧化剂", "活性含量和含水量会变化；避免受热、污染和不受控浓缩"),
    "palladium-acetate": ("钯催化反应前体", "筛查配体、碱和溶剂相容性，并建立钯残留控制"),
    "diethyl-ether": ("低沸点醚类反应/萃取溶剂", "高度易燃、易形成过氧化物且易产生蒸气压力；浓缩前核对储存与设备条件"),
    "2-methyltetrahydrofuran": ("醚类反应、萃取和结晶溶剂", "可形成过氧化物；核对含水、分相、回收和残留溶剂控制"),
    "cpme": ("疏水醚类反应与萃取溶剂", "可形成过氧化物；评估低温流动、回收和残留控制"),
    "dmac": ("高极性非质子反应溶剂", "高沸点且需控制职业暴露和残留；优先评估后处理去除与替代方案"),
    "sodium-methoxide": ("强碱、酯交换或亲核试剂", "对水敏感；溶液有效浓度、甲醇含量、加料放热和受控淬灭需核对"),
    "potassium-tert-butoxide": ("非亲核强碱", "遇水反应；关注粉体/溶液形态、加料放热、局部高碱浓度和淬灭"),
    "sodium-hydride": ("强碱和去质子化试剂", "常为矿物油分散体；遇水放氢，必须评估有效含量、放热、供排气与淬灭"),
    "nitric-acid": ("硝化、氧化或酸化试剂", "强氧化和腐蚀性；与有机物相容性、混酸热行为、加料和失冷情景需专门评估"),
    "bromine": ("溴化和氧化试剂", "挥发、强腐蚀且有毒；需密闭计量、尾气吸收、残余活性溴检测和受控淬灭"),
    "phosphorus-oxychloride": ("氯化、脱水和磷酰化试剂", "遇水剧烈反应并产生腐蚀性气体；关注加料、材质、淬灭和尾气"),
    "triphosgene": ("羰基化、氯甲酸酯或异氰酸酯制备用试剂", "可释放光气等有毒气体；需封闭系统、监测、尾气处理和应急评估"),
    "dess-martin": ("醇氧化为醛或酮", "高价碘氧化剂；评估热稳定性、污染敏感性、残余氧化剂和放大替代路线"),
    "hydrogen": ("催化氢化和还原反应气体", "高度易燃；核对惰化、泄漏、点火源、压力保护、气液传质和催化剂过滤"),
    "nitrogen": ("惰化、吹扫、压料和干燥气体", "可造成缺氧；核对置换终点、供气压力、排放位置和空间通风"),
    "oxygen": ("氧化反应或富氧工艺气体", "提高燃烧风险；必须核对材料相容性、可燃区间、清洁度、惰化和尾气"),
    "carbon-dioxide": ("惰化、调 pH、沉淀或超临界介质", "可造成缺氧并在密闭系统升压；核对溶解/放气、压力保护和通风"),
    "ammonia": ("胺化、pH 调节和制盐气体/溶液", "有毒、腐蚀且易挥发；核对吸收放热、供气、泄漏检测、尾气和材质"),
}

CATEGORY_DECISIONS = {
    "solvent": {"choose":["能否同时满足反应选择性和后处理分离","沸点、互溶性、黏度和残留是否适合设备","职业暴露、可燃性、废液与回收负担是否可接受"],"verify":["实际含水/纯度和关键杂质","目标物/杂质的溶解度与分配","升温、浓缩和置换时的物性与稳定性"]},
    "base": {"choose":["碱强度和亲核性是否匹配反应","固体、溶液或浆料形态是否影响加料和混合","生成盐能否顺利过滤、分相或结晶"],"verify":["有效含量、水分和供应形态","加料与淬灭热、放气和 pH 轨迹","盐负荷、持液和后处理回收率"]},
    "acid": {"choose":["酸强度、氧化性和含水是否匹配目标转化","设备材质和尾气系统是否相容","中和后盐负荷与分相/析晶是否可控"],"verify":["浓度、含水和关键杂质","稀释/加料/中和量热与温升","腐蚀、尾气、盐析和产品稳定性"]},
    "reagent": {"choose":["选择性、当量和杂质谱是否优于备选路线","活性含量与供应形态能否稳定控制","副产物和残余试剂是否容易检测与去除"],"verify":["有效含量、含水和批间差异","相容性、反应/淬灭热和分解边界","终点方法、残余活性物和后处理去除"]},
    "catalyst": {"choose":["活性与选择性能否降低温度和副反应","催化剂是否易失活并受水氧/杂质影响","金属或配体残留能否稳定清除"],"verify":["负载、活性、粒径和批间差异","传质敏感性与反应时间序列","过滤、吸附、回收和残留控制"]},
    "gas": {"choose":["气体作用、纯度和供气方式是否明确","压力、溶解度和传质是否满足反应","可燃、氧化、毒性和窒息风险是否有工程控制"],"verify":["气源组成、露点、压力和最大流量","惰化/置换终点、气体消耗与尾气组成","泄漏检测、压力保护、通风和尾气处理能力"]},
}

REACTION_TEMPLATES = [
    {"id":"amide-coupling","name":"酰胺化","category":"成键反应","goal":"羧酸或活化衍生物与胺形成酰胺键","common_choices":["羧酸活化剂或酰氯路线","有机碱或无机碱","极性非质子溶剂或两相体系"],"screen":["活化方式与加料顺序","温度、当量和浓度","副反应、消旋与残留试剂"],"workup":["分层或调 pH 萃取","洗涤去除活化剂副产物","结晶、打浆或必要时精制"],"analysis":["原料/产物含量","相关杂质与手性纯度","残留活化剂及副产物"],"scale_risks":["活化和淬灭放热","气体释放或刺激性副产物","难滤副产物影响传质"],"next":"先做小规模活化方式×碱×溶剂筛选，再用量热和淬灭试验确认可放大窗口"},
    {"id":"esterification","name":"酯化","category":"成键反应","goal":"羧酸与醇或活化羧酸衍生物形成酯","common_choices":["酸催化平衡反应","活化剂路线","酰氯/酸酐路线"],"screen":["除水方式与平衡转化","催化剂、温度和浓度","醇解、水解及异构化杂质"],"workup":["中和与相分离","洗涤除酸","蒸馏、结晶或打浆"],"analysis":["转化率","酸值/水分","水解和过度反应杂质"],"scale_risks":["酸碱中和放热","共沸/蒸馏能力","易燃溶剂蒸气负荷"],"next":"先比较平衡驱动与活化路线，以分离方式和杂质谱决定优先工艺"},
    {"id":"hydrolysis","name":"水解与皂化","category":"官能团转化","goal":"酯、酰胺或保护基在酸/碱条件下水解","common_choices":["酸性水解","碱性皂化","水与有机共溶剂"],"screen":["pH、温度和水量","相态与传质","目标物稳定性和过度水解"],"workup":["受控调 pH","萃取或直接析晶","脱盐、洗涤和干燥"],"analysis":["原料残留","水解产物与降解物","盐、水分和残留溶剂"],"scale_risks":["中和放热和气体释放","固体析出导致搅拌困难","乳化与相分离变慢"],"next":"用 pH×温度×时间建立反应与降解窗口，并单独验证放大后的中和和析晶"},
    {"id":"sn-substitution","name":"亲核取代","category":"成键反应","goal":"亲核试剂取代卤素、磺酸酯等离去基形成新键","common_choices":["无机碱或有机碱","极性非质子溶剂","相转移催化条件"],"screen":["亲核试剂形态与当量","溶剂、温度和浓度","消除、过度烷基化和区域选择性"],"workup":["过滤无机盐","水洗/萃取","结晶或蒸馏"],"analysis":["转化率与选择性","异构体和过度反应物","残留烷基化试剂"],"scale_risks":["非均相传质","烷基化试剂暴露","高温积累和放热"],"next":"先比较碱、溶剂和投料次序，以选择性和后处理难度共同定条件"},
    {"id":"suzuki","name":"Suzuki 偶联","category":"金属催化","goal":"有机硼试剂与芳基/烯基卤化物形成碳—碳键","common_choices":["钯催化剂与配体","碳酸盐等碱","水/有机溶剂混合体系"],"screen":["催化剂/配体组合","碱、含水量和温度","脱硼、均偶联和脱卤"],"workup":["催化剂过滤或吸附","相分离与洗涤","结晶/打浆控制金属残留"],"analysis":["转化与偶联选择性","钯残留","硼残留及相关杂质"],"scale_risks":["催化剂失活与批间差异","非均相混合","金属残留和废物处置"],"next":"用催化剂/配体×碱×溶剂小矩阵筛选，并尽早验证无柱层析分离与金属清除"},
    {"id":"buchwald-hartwig","name":"Buchwald–Hartwig 胺化","category":"金属催化","goal":"芳基卤化物与胺形成碳—氮键","common_choices":["钯前体与膦配体","无机或有机碱","无水有机溶剂"],"screen":["催化剂/配体与底物匹配","碱、温度和浓度","脱卤、过度芳基化及催化剂失活"],"workup":["过滤盐和催化剂","萃取/洗涤","结晶或打浆"],"analysis":["转化率与选择性","钯残留","配体及脱卤杂质"],"scale_risks":["水氧敏感程度","固体碱传质","放热和金属残留"],"next":"先用公开实验工具比较条件，再针对实际底物做催化剂/碱/溶剂筛选与复验"},
    {"id":"reductive-amination","name":"还原胺化","category":"成键反应","goal":"羰基化合物与胺经亚胺/亚胺鎓中间体形成胺","common_choices":["分步或一锅亚胺形成","选择性氢化物或催化氢化","醇类、卤代或极性溶剂"],"screen":["pH、脱水与投料顺序","还原剂选择和当量","直接还原、过度烷基化和非对映选择性"],"workup":["受控淬灭还原剂","调 pH 萃取","成盐、结晶或蒸馏"],"analysis":["羰基原料和目标胺","醇类副产物","过度烷基化物和手性纯度"],"scale_risks":["还原剂淬灭放热/放气","氢化时气液传质","胺类气味和暴露"],"next":"比较分步与一锅路线，先确认亚胺形成和还原选择性，再研究淬灭及成盐分离"},
    {"id":"hydrogenation","name":"催化氢化","category":"还原反应","goal":"以氢气和金属催化剂还原不饱和键或脱保护","common_choices":["Pd/C、Pt 或 Ni 类催化剂","醇、酯、烃或混合溶剂","常压至加压氢化设备"],"screen":["催化剂种类/负载与用量","压力、温度和搅拌","过度还原、脱卤和催化剂中毒"],"workup":["惰性条件下安全过滤","催化剂润湿与回收","浓缩、结晶或成盐"],"analysis":["原料和过度还原物","金属残留","残余催化剂和过滤完整性"],"scale_risks":["氢气可燃性和压力","传质限制与放热","干燥催化剂自燃风险"],"next":"先做催化剂与选择性筛选，再用量热、气体消耗和过滤试验定义放大条件"},
    {"id":"oxidation","name":"氧化","category":"氧化还原","goal":"醇、硫、烯烃等官能团受控氧化","common_choices":["过氧化物/过酸","高价碘或金属氧化体系","氧气或空气催化氧化"],"screen":["氧化剂种类、活性含量与当量","温度、加料和浓度","过度氧化及底物分解"],"workup":["确认残余氧化剂并受控淬灭","相分离和洗涤","结晶、打浆或蒸馏"],"analysis":["转化和氧化态分布","残余氧化剂","降解物和潜在高能杂质"],"scale_risks":["热积累与自加速分解","氧气传质和可燃区间","污染物引发分解"],"next":"先做兼容性和反应量热，再以选择性、残余氧化剂和可控淬灭决定条件"},
    {"id":"hydride-reduction","name":"氢化物还原","category":"还原反应","goal":"用硼氢化物或铝氢化物还原羰基及相关官能团","common_choices":["硼氢化钠等温和体系","氢化铝锂或 DIBAL-H 等强还原体系","醚类、醇类或其他相容溶剂"],"screen":["试剂、当量和溶剂相容性","温度、加料和反应终点","化学/区域/立体选择性"],"workup":["分阶段受控淬灭","过滤无机盐","萃取、结晶或蒸馏"],"analysis":["原料和还原程度","水解/过度还原杂质","残留金属和水分"],"scale_risks":["加料和淬灭放热/放气","遇水反应","固体盐导致搅拌与过滤困难"],"next":"先以小规模相容性和量热确认试剂窗口，再验证可控淬灭和固液分离"},
    {"id":"protection","name":"保护与脱保护","category":"官能团转化","goal":"暂时屏蔽胺、醇、酸等官能团或在后续步骤恢复","common_choices":["酸敏、碱敏或氢解型保护基","酸/碱/氢化脱保护","与后续步骤相容的溶剂"],"screen":["选择性与后续条件兼容性","试剂当量、温度和时间","迁移、过度反应及难除副产物"],"workup":["中和或催化剂过滤","萃取/洗涤","成盐、结晶或打浆"],"analysis":["保护/脱保护完全度","区域异构体","残留保护基试剂和副产物"],"scale_risks":["额外步骤降低总收率","强酸/强碱或氢气风险","油化和难结晶"],"next":"把总路线收率和分离成本纳入选择，优先验证能否取消保护基步骤"},
    {"id":"salt-crystallization","name":"成盐与结晶","category":"分离纯化","goal":"通过盐型、溶剂和过饱和控制实现分离、纯化或固态控制","common_choices":["酸/碱成盐剂筛选","溶剂/反溶剂体系","冷却、蒸发或反应结晶"],"screen":["盐型、化学计量与溶解度","加料、降温、晶种和熟化","晶型、油化和夹带杂质"],"workup":["过滤与洗涤","受控干燥","母液和洗液回收评估"],"analysis":["纯度与收率","晶型/粒径/水分","残留溶剂和成盐比"],"scale_risks":["混合与传热改变成核","过滤和干燥能力","晶型转变、结块和溶剂化物"],"next":"先做溶解度与小型成盐/结晶筛选，再用加料、冷却和晶种实验建立可重复工艺"},
    {"id":"snar","name":"芳香亲核取代（SNAr）","category":"成键反应","goal":"亲核试剂取代缺电子芳环或杂芳环上的离去基","common_choices":["胺、醇盐或硫醇盐亲核体","有机碱或无机碱","极性非质子溶剂"],"screen":["底物电子效应与离去基","碱、温度、浓度和加料顺序","区域异构、二取代及水解杂质"],"workup":["调 pH 后萃取或直接析晶","过滤无机盐","打浆或结晶去除异构体"],"analysis":["转化率和区域选择性","二取代/水解杂质","残留亲核体与溶剂"],"scale_risks":["高温下热积累","固体碱和盐影响混合","强亲核试剂暴露及淬灭放热"],"next":"先用碱×溶剂×温度小矩阵确认选择性，再验证盐负荷、淬灭和无柱分离"},
    {"id":"grignard-organolithium","name":"格氏与有机锂加成","category":"碳碳成键","goal":"有机镁或有机锂试剂与羰基等亲电体形成碳—碳键","common_choices":["预制或原位有机金属试剂","无水醚类溶剂","低温滴加和受控淬灭"],"screen":["水氧敏感性与试剂有效浓度","加料次序、温度和停留时间","偶联、烯醇化及过度加成"],"workup":["分阶段低温淬灭","调 pH 分相并处理盐泥","结晶、蒸馏或打浆"],"analysis":["试剂滴定与原料残留","目标物及加成副产物","水分和残留金属盐"],"scale_risks":["起始诱导与失控放热","可燃溶剂及气体释放","低温传热和盐浆搅拌"],"next":"先完成试剂滴定、相容性和反应量热，再定义低温加料、失冷及淬灭边界"},
    {"id":"heck","name":"Heck 偶联","category":"金属催化","goal":"芳基或烯基卤化物与烯烃形成取代烯烃","common_choices":["钯催化剂与膦/无膦体系","有机或无机碱","极性溶剂或混合介质"],"screen":["催化剂、配体和碱","温度、浓度及烯烃当量","E/Z、区域选择性和脱卤"],"workup":["过滤或吸附除钯","水洗去盐","结晶或蒸馏分离异构体"],"analysis":["转化率与 E/Z 比","区域异构体和脱卤物","钯与配体残留"],"scale_risks":["催化剂失活和析钯","放热与气体副产物排放","异构体分离及金属清除"],"next":"先筛催化体系和碱，以选择性、钯负荷和可结晶性共同选路线"},
    {"id":"sonogashira","name":"Sonogashira 偶联","category":"金属催化","goal":"芳基或烯基卤化物与末端炔形成碳—碳键","common_choices":["钯/铜双催化或无铜体系","胺碱或无机碱","惰性气氛下有机溶剂"],"screen":["催化剂体系和碱","水氧、温度和底物浓度","炔烃均偶联、脱卤及异构化"],"workup":["除铜/钯和过滤固体","调 pH 萃取与洗涤","结晶或打浆"],"analysis":["偶联产物和炔均偶联物","钯/铜残留","残留末端炔"],"scale_risks":["金属乙炔化物相关风险需单独评估","氧进入促进副反应","金属残留和废液处理"],"next":"优先比较无铜与低铜体系，并尽早做金属清除、热稳定性和放大过滤试验"},
    {"id":"chan-lam-ullmann","name":"Chan–Lam / Ullmann 成键","category":"金属催化","goal":"用铜体系形成碳—氮或碳—氧等键","common_choices":["铜盐催化氧化偶联","铜催化芳基卤化物偶联","配体、碱与含氧/惰性气氛"],"screen":["底物与铜/配体匹配","氧、水、碱和温度","氧化副反应、均偶联与脱卤"],"workup":["络合、吸附或过滤除铜","调 pH 萃取","结晶/打浆去除有色杂质"],"analysis":["转化与成键选择性","铜残留和颜色","均偶联及氧化杂质"],"scale_risks":["气液传质改变反应速率","铜盐浆料混合与过滤","氧化体系热安全"],"next":"明确反应是否受氧传质控制，再以催化剂、配体和碱做小矩阵并验证除铜"},
    {"id":"olefination","name":"Wittig / HWE 烯化","category":"碳碳成键","goal":"羰基化合物转化为烯烃并控制 E/Z 选择性","common_choices":["Wittig 叶立德","Horner–Wadsworth–Emmons 膦酸酯","强碱、无水溶剂和低温加料"],"screen":["试剂/碱组合与有效当量","温度、加料和熟化时间","E/Z 比、羰基残留和异构化"],"workup":["受控淬灭并除无机盐","去除含磷副产物","结晶、打浆或蒸馏"],"analysis":["E/Z 比和转化率","含磷副产物","残留强碱及溶剂"],"scale_risks":["强碱加料和淬灭放热","盐负荷导致搅拌/过滤困难","含磷副产物难清除"],"next":"用小规模试验同时比较 E/Z、含磷残留和后处理，不只按反应转化率选择条件"},
    {"id":"diazotization-sandmeyer","name":"重氮化与 Sandmeyer 反应","category":"官能团转化","goal":"芳胺经重氮盐转化为卤素、氰基、羟基等官能团","common_choices":["亚硝酸盐/酸体系原位重氮化","铜盐介导取代","低温生成后受控转化"],"screen":["酸度、温度和加料速率","重氮盐积累与转化终点","偶氮、酚类及脱氨杂质"],"workup":["确认中间体消耗后受控淬灭","除铜与调 pH 分相","蒸馏、萃取或结晶"],"analysis":["芳胺残留与重氮盐终点","目标物和偶氮副产物","铜残留及亚硝酸盐"],"scale_risks":["高能中间体不得分离或不受控积累","放气、放热与尾气处理","低温传热和加料联锁"],"next":"先用反应量热和在线终点确认最大积累，再设计连续生成/消耗或低持液量方案"},
    {"id":"halogenation","name":"卤化与卤素交换","category":"官能团转化","goal":"在碳骨架或官能团上引入氟、氯、溴、碘或完成卤素交换","common_choices":["亲电卤化试剂","自由基卤化","亲核卤素交换"],"screen":["试剂活性、当量和加料顺序","温度、光照和水分","区域选择性、过度卤化及水解"],"workup":["检测残余活性卤并淬灭","洗涤去酸/盐","蒸馏、结晶或打浆"],"analysis":["取代位置与卤化程度","残余卤化剂","腐蚀性离子和相关杂质"],"scale_risks":["强放热和腐蚀","有毒/刺激性蒸气与尾气","光/自由基引发的速率变化"],"next":"先做相容性、量热和残余活性卤检测，再优化选择性与淬灭/尾气方案"},
    {"id":"sulfonylation","name":"磺酰化","category":"成键反应","goal":"胺或醇与磺酰氯等形成磺酰胺或磺酸酯","common_choices":["磺酰氯或磺酸酐","有机碱/无机碱缚酸","卤代、醚类或极性溶剂"],"screen":["试剂当量、碱和加料次序","温度、含水和底物盐型","二磺酰化、水解及区域选择性"],"workup":["受控水解残余磺酰化剂","调 pH 分相与洗涤","结晶/打浆去除磺酸副产物"],"analysis":["原料、目标物和二取代物","残余活性试剂","磺酸/盐和残留溶剂"],"scale_risks":["加料与淬灭放热","腐蚀性酸性副产物","盐负荷、析固和混合"],"next":"先比较碱和加料顺序，以选择性、残余活性物和后处理分相共同选条件"},
    {"id":"urea-carbamate","name":"脲与氨基甲酸酯形成","category":"成键反应","goal":"胺、醇与异氰酸酯或羰基化试剂形成脲/氨基甲酸酯","common_choices":["异氰酸酯直接加成","CDI/氯甲酸酯活化","光气等价物路线"],"screen":["活化方式、当量和加料顺序","水分、温度和浓度","对称脲、缩二脲及水解杂质"],"workup":["确认活性羰基试剂耗尽后淬灭","调 pH 萃取或过滤","结晶/打浆去除咪唑或盐"],"analysis":["胺/醇原料和目标物","异氰酸酯/活性中间体终点","脲类副产物及残余活化剂"],"scale_risks":["有毒活性中间体和尾气","加料/淬灭放热","析固、黏度和局部过量"],"next":"优先比较低危活化路线；以终点可测、淬灭可控和副产物易除决定放大方案"},
    {"id":"epoxide-opening","name":"环氧化物开环","category":"成键反应","goal":"亲核体开环环氧化物形成 β-取代醇并控制区域/立体选择性","common_choices":["胺、醇、硫醇或碳亲核体","酸/碱或 Lewis 酸催化","极性或醚类溶剂"],"screen":["催化模式与亲核体当量","温度、浓度和加料顺序","区域异构、聚合和过度开环"],"workup":["淬灭催化剂并调 pH","萃取/洗涤或直接结晶","去除未反应环氧化物"],"analysis":["环氧化物残留和区域异构比","二次反应/聚合物","对映或非对映纯度"],"scale_risks":["环氧化物反应性和职业暴露","放热与局部高浓度","聚合或黏度上升"],"next":"先用加料和温度控制区域选择性，再做量热、残余环氧检测与失冷评估"},
    {"id":"michael-addition","name":"Michael 共轭加成","category":"碳碳成键","goal":"亲核体向 α,β-不饱和受体共轭加成形成碳—碳或杂原子—碳键","common_choices":["碳、氮、氧或硫亲核体","碱、Lewis 酸或有机催化","极性/非极性溶剂"],"screen":["供体/受体活化程度","催化剂、碱、温度和浓度","1,2/1,4 选择性、聚合及逆反应"],"workup":["淬灭催化剂或碱","调 pH 萃取","结晶、打浆或蒸馏"],"analysis":["区域选择性和转化率","加合异构体/聚合物","立体选择性和残余受体"],"scale_risks":["放热和受体局部积累","不饱和受体聚合","高黏度或析固影响混合"],"next":"用供体/受体比例、加料和温度建立选择性窗口，再验证热积累和聚合倾向"},
    {"id":"aldol-claisen","name":"Aldol / Claisen 缩合","category":"碳碳成键","goal":"通过烯醇盐或烯醇等价体形成新的碳—碳键","common_choices":["强碱预制烯醇盐","催化酸/碱条件","硅烯醇醚或其他受控等价体"],"screen":["碱、温度、加料和烯醇盐生成","交叉选择性和立体控制","自缩合、脱水及消旋"],"workup":["低温受控淬灭","调 pH 分相和除盐","结晶或色谱替代工艺"],"analysis":["交叉/自缩合比例","非对映/对映选择性","脱水物和残余碱"],"scale_risks":["强碱与低温放热","局部浓度影响选择性","盐浆、黏度和低温传热"],"next":"先用时间序列确认烯醇盐生成和加成顺序，再以量热、混合和无柱分离验证条件"},
    {"id":"mannich","name":"Mannich 反应","category":"碳碳成键","goal":"羰基/富电子底物与胺、醛形成 β-氨基化产物","common_choices":["预制亚胺/亚胺鎓","三组分一锅反应","酸、Lewis 酸或有机催化"],"screen":["亚胺形成、含水和 pH","投料顺序、温度和浓度","双取代、聚合及立体选择性"],"workup":["调 pH 控制游离碱/盐","萃取或直接成盐","结晶/打浆"],"analysis":["亚胺/羰基原料残留","区域与立体异构体","胺类副产物和水分"],"scale_risks":["亚胺形成与放热耦合","pH/含水变化影响选择性","胺/醛挥发和气味暴露"],"next":"分开确认亚胺形成和成键时间窗，再研究一锅化、成盐和结晶分离"},
    {"id":"olefin-metathesis","name":"烯烃复分解","category":"金属催化","goal":"通过金属卡宾催化实现关环、交叉或开环复分解","common_choices":["钌或钼催化剂","稀溶液关环或交叉复分解","惰性气氛和乙烯脱除"],"screen":["催化剂世代、负载和加料方式","浓度、温度与气体脱除","二聚、异构化和催化剂分解"],"workup":["催化剂淬灭/吸附","过滤和金属清除","结晶或打浆"],"analysis":["转化率和 E/Z/环化选择性","钌/钼残留","烯烃异构化和低聚物"],"scale_risks":["催化剂对杂质敏感","气体释放与传质","高稀释造成溶剂和周期负担"],"next":"先确认浓度上限与催化剂最低负载，再同步验证金属清除和溶剂回收"},
    {"id":"click-cycloaddition","name":"叠氮–炔点击环加成","category":"成键反应","goal":"叠氮化物与炔烃形成三唑，并按催化方式控制区域选择性","common_choices":["铜催化 CuAAC","应变促进无铜 SPAAC","热 Huisgen 环加成"],"screen":["叠氮物热稳定性与浓度","催化剂、配体、溶剂和温度","区域异构、氧化及金属残留"],"workup":["络合/吸附除铜","萃取或过滤","结晶/打浆"],"analysis":["叠氮物残留和三唑异构体","铜残留","低聚物及配体残留"],"scale_risks":["低分子叠氮物高能风险需专门评估","放热和中间体积累","铜盐浆料和金属清除"],"next":"先做叠氮物热风险与最大安全浓度评估，再优化催化剂和金属清除"},
    {"id":"photoredox","name":"光氧化还原反应","category":"新型反应","goal":"利用光催化单电子转移完成成键或官能团转化","common_choices":["可见光光催化剂","牺牲性氧化/还原剂","批式薄层或连续流光反应器"],"screen":["波长、光强和光程","催化剂、浓度、氧/水和停留时间","光漂白、自由基副反应及量子效率"],"workup":["淬灭活性自由基/氧化还原物","除催化剂和有色杂质","萃取、结晶或连续后处理"],"analysis":["转化/选择性随光剂量变化","催化剂降解物","暗反应对照和质量平衡"],"scale_risks":["光程随放大恶化","光热耦合和照射不均","气液传质及连续设备堵塞"],"next":"先建立光剂量—转化关系和暗对照；放大优先评估薄层或连续流几何"},
    {"id":"biocatalysis","name":"生物催化转化","category":"新型反应","goal":"利用酶或全细胞实现高选择性氧化、还原、水解或成键","common_choices":["游离酶或固定化酶","辅酶再生体系","水相、两相或共溶剂体系"],"screen":["酶种、负载、pH 和温度","底物/产物抑制与共溶剂耐受","辅酶、供体和氧传质"],"workup":["除酶/细胞和蛋白","调 pH 萃取或吸附","结晶、膜分离或浓缩"],"analysis":["转化和对映/区域选择性","酶活衰减与批间差异","残留蛋白、生物负载和辅酶"],"scale_risks":["传质和底物溶解度","酶稳定性与供应一致性","生物负载、泡沫和下游分离"],"next":"先筛酶和共溶剂窗口，再用时间序列、重复使用和下游分离评估整体生产力"},
]

PRINCIPLES = [
    {"id":"mass-balance","name":"物料衡算与理论收率","category":"衡算","question":"原料、溶剂和产物在每一步去了哪里？","conclusion":"先闭合质量与摩尔衡算，再讨论收率和成本；衡算缺口通常意味着取样、含量、挥发、持液或未知杂质未被解释。","formulas":[{"name":"总物料衡算","expression":"累积 = 输入 − 输出 + 生成 − 消耗","variables":"边界、时间段和各物流必须一致"},{"name":"分离收率","expression":"Y = 实得目标物质量 ÷ 理论目标物质量 × 100%","variables":"理论量需按限量试剂、化学计量和原料含量计算"}],"inputs":["各物料实际质量、含量和分子量","反应式及化学计量","各相体积/密度、取样和设备持液"],"signals":["总质量闭合率","关键元素或标志物闭合率","母液、洗液和尾气中的目标物"],"mistakes":["把面积归一化纯度直接当质量含量","忽略溶剂化物、盐型和水分","只报单步收率，不追踪母液损失"],"next":"建立逐股物流表；先解释最大三项损失，再决定优化反应还是分离。"},
    {"id":"energy-balance","name":"能量衡算与绝热温升","category":"衡算","question":"升温、反应和淬灭会释放或吸收多少热？","conclusion":"釜温稳定不代表没有放热；放大前应把显热、反应热、加料速率和最大可移热能力放在同一时间尺度比较。","formulas":[{"name":"显热","expression":"Q = m × Cp × ΔT","variables":"m 为物料质量，Cp 为混合物比热，ΔT 为温差"},{"name":"绝热温升估算","expression":"ΔTad = |Qreaction| ÷ (m × Cp)","variables":"需使用该批次反应热和反应混合物热容"}],"inputs":["反应/淬灭热与转化速率","批量、组成和比热","冷却介质、UA、加料时间和失冷情景"],"signals":["放热峰和热累积","绝热终温与分解起始温度间距","正常/失冷时的温压变化"],"mistakes":["用小试控温良好代替量热","只看总热量不看释热速率","遗漏淬灭、稀释和结晶热"],"next":"先做反应量热和兼容性筛查；若存在热累积，再调整加料、浓度、温度或设备能力。"},
    {"id":"reaction-kinetics","name":"反应动力学与停留时间","category":"反应工程","question":"温度、浓度和时间如何改变转化与杂质？","conclusion":"终点数据只能说明某一时刻；至少需要时间序列区分反应控制、传质控制和产物继续降解。","formulas":[{"name":"经验速率式","expression":"−rA = k × CA^n","variables":"级数 n 需由数据拟合，不能仅按反应式假设"},{"name":"Arrhenius 关系","expression":"k = A × exp(−Ea / RT)","variables":"只在机制和控制区间未改变时外推"}],"inputs":["不同温度/浓度下的时间序列","真实含量与取样淬灭方法","副产物随时间的变化"],"signals":["初始速率和半衰期","转化平台与杂质拐点","升温后的速率倍数和选择性变化"],"mistakes":["只测一个终点","把混合或溶解受限当本征动力学","跨相变或机理区间直接外推"],"next":"用少量时间点建立转化—杂质曲线，优先选择达到终点后仍有操作裕量的条件。"},
    {"id":"heat-transfer","name":"传热与控温能力","category":"传递过程","question":"设备能否及时加热、冷却并控制热点？","conclusion":"放大后体积增长快于换热面积，实验室可控的升降温和放热不一定能在生产釜复制。","formulas":[{"name":"换热速率","expression":"Q̇ = U × A × ΔTlm","variables":"U 为总传热系数，A 为面积，ΔTlm 为对数平均温差"},{"name":"升降温时间估算","expression":"t ≈ m × Cp × ΔT ÷ Q̇available","variables":"需扣除反应热、散热和设备限制"}],"inputs":["釜体/盘管面积与实测 U","批量、比热和黏度","介质温度、流量及允许温差"],"signals":["升降温时间","釜内外温差和热点","最大可控加料速率"],"mistakes":["按体积线性放大加料速率","使用空釜或清水传热系数","忽略结垢、浆料和黏度变化"],"next":"用目标物料体系做升降温测试，按最差 UA 计算加料和失冷边界。"},
    {"id":"mixing","name":"混合、悬浮与尺度放大","category":"传递过程","question":"加料后多快混匀，固体能否悬浮，局部浓度会不会过高？","conclusion":"转速相同并不代表混合相同；应按控制问题选择功率密度、桨尖速度、循环时间或最小悬浮转速。","formulas":[{"name":"桨尖速度","expression":"utip = π × N × D","variables":"N 为转速，D 为桨径"},{"name":"功率密度","expression":"P/V = Np × ρ × N³ × D⁵ / V","variables":"Np 取决于桨型和雷诺数"}],"inputs":["釜径、液位、桨型/桨径和挡板","密度、黏度、固含量和粒径","转速、加料位置和相态变化"],"signals":["混合时间与浓度均匀性","固体离底悬浮和沉降","局部颜色、温度或副产物"],"mistakes":["所有步骤都用同一放大准则","只记录 rpm 不记录几何和物性","忽略反应中黏度、气含率或固含变化"],"next":"先判断控制目标，再选一个主放大准则并检查桨尖速度、功率和设备上限。"},
    {"id":"gas-liquid-transfer","name":"气液传质与气体反应","category":"传递过程","question":"气体是否真正进入液相并到达催化/反应位点？","conclusion":"提高压力不一定解决传质；气体溶解度、kLa、搅拌、气泡和反应消耗共同决定供气能力。","formulas":[{"name":"气液传质速率","expression":"N = kLa × (C* − C)","variables":"C* 由压力、温度和组成决定，kLa 需实验或可靠关联式"}],"inputs":["气体组成、压力、温度和流量","搅拌、桨型、液深和气体分布方式","气体消耗/吸收时间序列"],"signals":["压力或流量变化","搅拌速率改变是否影响反应速率","气体利用率和尾气组成"],"mistakes":["把表压当绝对压力","只报设定压力不报供气和搅拌","忽略易燃区间、惰化和尾气能力"],"next":"做搅拌/压力敏感性试验；若速率随搅拌显著变化，先解决传质再拟合动力学。"},
    {"id":"liquid-liquid-extraction","name":"液液萃取与分相","category":"分离过程","question":"目标物和杂质在两相如何分配，几次萃取最合适？","conclusion":"一次大体积萃取通常不如多次小体积有效，但实际选择还受乳化、持液、pH 和操作时间限制。","formulas":[{"name":"分配比","expression":"D = Corganic ÷ Caqueous","variables":"D 随 pH、温度、浓度和离子强度变化"},{"name":"单级萃取分数","expression":"E = D × Vorg ÷ (D × Vorg + Vaq)","variables":"假设两相充分接触并达到平衡"}],"inputs":["不同 pH/温度下的分配比","两相体积、密度和互溶度","混合/静置时间及乳化情况"],"signals":["目标物回收率和杂质清除率","分相时间、界面和第三相","水相/有机相体积变化"],"mistakes":["只按溶剂极性选萃取剂","忽略电离、盐析和溶剂互溶","过度搅拌造成稳定乳化"],"next":"用小型分配与分相试验确定 pH 和相比，再比较一次与多次萃取的总回收和操作负担。"},
    {"id":"distillation","name":"蒸馏、减压与溶剂置换","category":"分离过程","question":"能否在不降解目标物的条件下完成分离或换溶剂？","conclusion":"沸点是压力相关的；先用汽液平衡、相对挥发度和热稳定性判断可分性，再谈塔板数或回流比。","formulas":[{"name":"相对挥发度","expression":"αAB = (yA/xA) ÷ (yB/xB)","variables":"x、y 为平衡液相/气相组成，α 接近 1 时难分"},{"name":"简单回收衡算","expression":"进料 = 馏出 + 釜残","variables":"还需对关键组分分别闭合衡算"}],"inputs":["压力下的沸点/汽液平衡","组成、热稳定性和允许残留","设备真空、冷凝及受热面积"],"signals":["塔顶/釜温与压力趋势","各馏分组成和回收率","浓缩后黏度、析晶或降解"],"mistakes":["用常压沸点判断减压操作","把蒸干当作默认终点","忽略共沸、起泡、冲料和冷凝能力"],"next":"先做小规模减压浓缩/溶剂置换试验并逐段分析馏分，再确定终点、压力和温度上限。"},
    {"id":"crystallization","name":"结晶、过饱和与晶型","category":"分离过程","question":"怎样同时获得纯度、收率、晶型和可过滤颗粒？","conclusion":"结晶不是单纯降温；溶解度、过饱和生成速率、成核、晶体生长和熟化共同决定结果。","formulas":[{"name":"过饱和比","expression":"S = C ÷ C*","variables":"C 为实际浓度，C* 为该温度和溶剂组成下平衡溶解度"},{"name":"理论析出量","expression":"mcrystal ≈ mdissolved − C*final × Vmother","variables":"需修正溶剂蒸发、溶剂化物和母液持液"}],"inputs":["溶解度—温度—溶剂组成数据","冷却/加料曲线、晶种和搅拌","晶型、粒径、母液含量和过滤表现"],"signals":["成核点和过饱和轨迹","晶型/粒径分布","母液损失、洗涤效率和过滤时间"],"mistakes":["只按终点收率选条件","未区分油化、无定形和晶体","小试快速降温直接照搬放大"],"next":"先建立溶解度曲线和固态筛查，再用晶种、过饱和生成和熟化实验定义操作窗口。"},
    {"id":"filtration","name":"过滤、洗涤与滤饼阻力","category":"分离过程","question":"固体能否稳定过滤、洗净并从设备卸出？","conclusion":"过滤时间由液体黏度、滤饼比阻、厚度和压差共同决定；小漏斗的快慢不能按面积直接外推。","formulas":[{"name":"Darcy 形式","expression":"J = ΔP ÷ [μ × (Rm + Rc)]","variables":"J 为通量，Rm/Rc 为介质/滤饼阻力"},{"name":"恒压过滤趋势","expression":"过滤时间随滤饼厚度和比阻增加","variables":"可用小型压力过滤数据拟合后再放大"}],"inputs":["浆料固含、粒径和黏度","过滤面积、压差和介质","滤液体积—时间曲线及洗涤量"],"signals":["单位面积通量","滤饼含湿量和开裂","洗涤置换效率与产品损失"],"mistakes":["只记录总过滤时间","忽略滤饼压缩性和温度","用过量洗液换纯度却不算收率/周期"],"next":"用目标浆料做恒压过滤和分段洗涤试验，记录单位面积曲线后再估算设备周期。"},
    {"id":"drying","name":"干燥、残留溶剂与终点","category":"分离过程","question":"怎样在不降解、不转晶和不结块的情况下达到干燥终点？","conclusion":"干燥终点应由水分/残留溶剂和固态质量共同定义；只延长时间可能增加降解而不能解决内部扩散限制。","formulas":[{"name":"湿基失重","expression":"LOD = (mwet − mdry) ÷ mwet × 100%","variables":"LOD 不区分水、溶剂和挥发性降解物"},{"name":"干燥负荷","expression":"mvolatile = mwet − mdry","variables":"设备还需满足传热、真空和冷凝能力"}],"inputs":["初始含湿/溶剂和目标限度","温度、真空、氮气流量和铺料厚度","时间序列、晶型和降解数据"],"signals":["残留溶剂/水分随时间变化","产品温度与尾气/冷凝速率","结块、晶型变化和杂质增长"],"mistakes":["把 LOD 等同于水分","只测最终一点","忽略溶剂化物、转晶和静电/粉尘风险"],"next":"用时间序列识别恒速与扩散受限阶段，以质量达标和产品稳定性共同设终点。"},
    {"id":"scale-up-design-space","name":"放大准则与工艺设计空间","category":"开发策略","question":"哪些条件变化仍能稳定得到合格产品？","conclusion":"单点成功不构成稳健工艺；设计空间需要把物料属性、工艺参数和关键质量属性之间的关系用数据说明。","formulas":[{"name":"雷诺数","expression":"Re = ρ × N × D² ÷ μ","variables":"用于判断搅拌流动区间，不能单独代表混合效果"},{"name":"无量纲比较","expression":"选择与控制机理相关的 Re、Pr、Sc、We 等","variables":"通常无法同时保持所有准数不变"}],"inputs":["关键质量属性和可接受标准","物料属性、参数范围与交互试验","设备几何、控制能力和测量误差"],"signals":["范围内成功概率和边界失败模式","跨批次/尺度的趋势一致性","模型不确定性与确认点"],"mistakes":["把一组最佳条件称为设计空间","只做单因素考察而忽略交互","模型拟合好但缺少边界确认"],"next":"先定义质量目标和风险排序，再用覆盖边界的实验建立关系并安排独立确认批。"},
    {"id":"solubility-ionization","name":"溶解度、pKa 与 pH","category":"物性与相平衡","question":"目标物为什么溶解、析出或在两相间迁移？","conclusion":"对可电离化合物，总溶解度和分配随 pH 明显变化；应区分中性本征溶解度、盐型、过饱和和动力学溶解。","formulas":[{"name":"酸的 Henderson–Hasselbalch","expression":"pH = pKa + log([A−]/[HA])","variables":"近似适用于稀溶液和单一酸碱平衡"},{"name":"弱酸总溶解度近似","expression":"S = S0 × [1 + 10^(pH−pKa)]","variables":"S0 为中性型本征溶解度；盐析、共溶剂和固态变化会使近似失效"}],"inputs":["pKa、盐型和固态信息","不同 pH/温度/溶剂组成下溶解度","离子强度、共溶剂和停留时间"],"signals":["澄清点、析出点和回溶滞后","pH—浓度曲线和盐型变化","油化、无定形或晶型转变"],"mistakes":["把短时澄清称为平衡溶解度","忽略加入酸碱引起的体积和盐效应","用一个 pKa 解释多位点或聚集体系"],"next":"先测与工艺相关的 pH—溶解度和固态，再设计萃取、成盐或结晶窗口。"},
    {"id":"phase-equilibria","name":"相平衡、活度与非理想混合物","category":"物性与相平衡","question":"混合物会形成几相，各组分在相中如何分布？","conclusion":"真实体系常偏离理想溶液；共沸、盐析、互溶度和温度变化会改变分相、蒸馏及结晶判断。","formulas":[{"name":"相平衡分配","expression":"Ki = yi ÷ xi","variables":"Ki 随温度、压力和组成变化"},{"name":"理想稀溶液起点","expression":"pi ≈ xi × pi*","variables":"仅作 Raoult 定律起点；强相互作用体系需活度系数或实测"}],"inputs":["温度/压力/组成下的相态","汽液或液液平衡数据","盐、酸碱和水含量"],"signals":["浊点、分层和第三相","共沸或恒沸组成","溶剂置换中的突发析出"],"mistakes":["默认有机相和水相体积可相加","用纯组分沸点推断混合物分离","忽略盐和痕量水改变相平衡"],"next":"在真实组成和温度下做小型相图/分配试验，再确定分相、蒸馏或置换路径。"},
    {"id":"rheology","name":"黏度、流变与浆料输送","category":"传递过程","question":"体系是否仍能被混合、传热、泵送和过滤？","conclusion":"浆料、聚合物和高固含体系可能是非牛顿流体；单一黏度值不足以描述剪切、沉降和设备负荷。","formulas":[{"name":"幂律模型","expression":"τ = K × γ̇^n","variables":"τ 为剪切应力，γ̇ 为剪切速率；K、n 需由流变数据拟合"},{"name":"表观黏度","expression":"μapp = τ ÷ γ̇","variables":"非牛顿体系的 μapp 随剪切速率变化"}],"inputs":["温度、固含与剪切速率下流变曲线","粒径、形貌、沉降和屈服应力","泵、管线和搅拌器能力"],"signals":["扭矩、电流和压降","死区、挂壁、团聚和沉降","取样不均与传热变慢"],"mistakes":["只测室温低剪切黏度","把上清液黏度代表整个浆料","忽略结晶或溶剂置换中的流变突变"],"next":"在最差固含和温度下测流变、扭矩与可泵送性，再确定加料、转速和转料方案。"},
    {"id":"impurity-fate","name":"杂质形成、归趋与清除","category":"开发策略","question":"关键杂质在哪一步形成，又在哪一步真正被清除？","conclusion":"降低反应杂质不一定最经济；应把形成量、后续转化、分配、结晶排斥和累积风险连成全路线归趋。","formulas":[{"name":"杂质质量衡算","expression":"杂质输出 = 带入 + 生成 − 转化 − 清除","variables":"需按质量或摩尔口径，不能只比较峰面积"},{"name":"表观清除因子","expression":"PF = 进入步骤的杂质量 ÷ 离开步骤的杂质量","variables":"需同时报告产品回收，避免用产品损失制造高 PF"}],"inputs":["原料和各中间体杂质谱","加标/追踪或质量平衡数据","母液、洗液、馏分和返工数据"],"signals":["杂质随时间和步骤的变化","同系物/异构体在结晶和萃取中的行为","回收母液或循环物流中的累积"],"mistakes":["面积百分比直接跨方法比较","只研究最终产品已看到的杂质","忽略未知峰和分析响应差异"],"next":"为高风险杂质建立形成—归趋图；用加标和物料衡算确认真正的清除步骤。"},
    {"id":"sampling-analytics","name":"取样、分析与数据可信度","category":"过程测量","question":"测到的差异来自工艺，还是取样与检测误差？","conclusion":"不均相体系、未及时淬灭或方法不专属性会制造假趋势；优化前先确认样品代表性和方法能力。","formulas":[{"name":"相对标准偏差","expression":"RSD = s ÷ x̄ × 100%","variables":"需要独立重复，且均值不能接近零"},{"name":"总测量差异","expression":"观察差异 = 过程差异 + 取样差异 + 分析差异","variables":"可用重复取样和重复进样分层估计"}],"inputs":["取样位置、时间、体积和淬灭方式","方法专属性、线性、精密度和检出能力","重复取样/制样/进样数据"],"signals":["同一批不同位置/时间差异","重复进样与重复制样 RSD","样品放置后的继续反应或相变"],"mistakes":["浆料只取上清液","用未验证面积归一化报告质量收率","把低于检出限写成零"],"next":"先做取样与淬灭可行性试验，确认方法分辨关键物种后再比较工艺条件。"},
    {"id":"solvent-selection","name":"溶剂选择、回收与环境负担","category":"开发策略","question":"所选溶剂能否兼顾反应、分离、安全、供应与废物？","conclusion":"最高反应收率不等于最佳溶剂；应把分离用量、回收难度、残留、危害和设备适配纳入同一决策。","formulas":[{"name":"过程质量强度","expression":"PMI = 全部投入物质量 ÷ 产品质量","variables":"应包括溶剂、水和返工投入"},{"name":"E-factor","expression":"E = 废物质量 ÷ 产品质量","variables":"边界和水是否计入必须说明"}],"inputs":["反应/分离表现与全流程用量","物性、残留限度和回收条件","危害、法规、供应、成本和废物路径"],"signals":["溶剂占 PMI 比例","换溶剂/浓缩次数和周期","回收纯度、能耗和废液分类"],"mistakes":["只按绿色溶剂表替换","忽略新溶剂改变相平衡和晶型","只算反应溶剂，不算萃取和洗涤"],"next":"比较候选溶剂的全流程质量、能耗和风险；优先减少换溶剂和高用量分离。"},
    {"id":"pressure-relief","name":"产气、压力与泄放边界","category":"工艺安全","question":"最坏情况下会生成多少气体、以多快速度升压？","conclusion":"正常排气能力不能代表异常泄放能力；化学产气、汽化、气体膨胀和两相夹带需按可信失效情景分别评估。","formulas":[{"name":"理想气体数量级","expression":"P × V = n × R × T","variables":"高压、非理想或冷凝体系需使用合适状态方程"},{"name":"压力积累","expression":"气体累积速率 = 生成速率 − 排出速率","variables":"需考虑堵塞、冷凝、泡沫和泄放背压"}],"inputs":["最大产气/汽化速率和组成","容器气相体积、温度和允许压力","尾气、冷凝、洗涤和泄放系统能力"],"signals":["压力上升速率和尾气流量","泡沫、冲料和两相流","失冷、误加料、火灾和堵塞情景"],"mistakes":["用总产气量代替峰值速率","把表压和绝压混用","用通气管代替专业泄放设计"],"next":"建立可信最坏情景和产气速率；由合格工程人员完成泄放与尾气系统核算。"},
    {"id":"flow-residence-time","name":"连续流与停留时间分布","category":"反应工程","question":"物料在流动设备中实际经历了多长时间和怎样的温度历史？","conclusion":"平均停留时间不代表所有物料；返混、轴向扩散、传热和堵塞会改变转化与杂质分布。","formulas":[{"name":"名义停留时间","expression":"τ = Vreactor ÷ Qvolumetric","variables":"需使用操作温压下有效反应体积和总体积流量"},{"name":"空时收率","expression":"STY = 产品质量 ÷ (反应器体积 × 时间)","variables":"必须同时报告选择性、稳定运行时间和下游负荷"}],"inputs":["流量、有效体积、温压和混合方式","动力学、传热和压降数据","停留时间分布、启动/停机和堵塞记录"],"signals":["转化随流量/温度变化","压降、热点和稳态漂移","不同停留时间组分的杂质谱"],"mistakes":["只按 τ=V/Q 放大","忽略泵脉动和多相流","只报短时最高 STY，不报稳定性"],"next":"先用示踪或动力学验证停留时间分布，再定义稳定运行、清洗和异常停机策略。"},
]

DEVELOPMENT_TOPICS = [
    {"id":"route-selection","name":"路线选择与步骤排序","category":"路线策略","goal":"在反应可行之外，比较总收率、原料、分离、安全、供应和知识产权风险。","decision":"优先选择能稳定放大、避免高危中间体和柱层析、关键原料可供应且杂质可控制的路线。","checklist":["逐步理论/实测收率和总收率","关键原料可得性、质量与成本","危险反应、中间体和特殊设备","每步分离方式、溶剂量和周期","关键杂质来源与后续清除"],"deliverables":["路线比较表与淘汰理由","关键风险实验清单","首选路线及备选路线"],"red_flags":["依赖不可稳定供应的定制原料","多个步骤需要柱层析","高危中间体被分离、浓缩或长期存放"],"next":"先用少量判别实验淘汰不可放大路线，再投入精细条件优化。"},
    {"id":"starting-material-control","name":"起始物料与试剂质量控制","category":"物料控制","goal":"确认原料含量、水分、异构体和关键杂质不会改变反应与产品质量。","decision":"对反应敏感的物料属性应成为入厂或使用前控制项，而不是出现偏差后再追查。","checklist":["身份、含量、水分和盐型/溶剂化物","异构体、残留溶剂、金属和抑制剂","供应商、批次和储存稳定性","有效含量的滴定或校准方法","替代供应商与可接受范围"],"deliverables":["关键物料属性清单","使用前放行标准","供应商/批次桥接方案"],"red_flags":["按标签质量计算当量但未校正含量","吸湿或易分解试剂无使用前检测","供应商变更后直接沿用工艺"],"next":"把最影响转化、选择性和安全的物料属性列入风险排序并做批次挑战。"},
    {"id":"impurity-control-strategy","name":"杂质控制策略","category":"质量与分析","goal":"从来源、形成、归趋、毒理关注和分析能力建立完整控制。","decision":"先控制可能进入最终产品且难以清除的高风险杂质；可在后续稳定清除的杂质可采用过程控制。","checklist":["原料带入、过程生成和降解杂质","遗传毒性/元素/残留溶剂等特殊关注","加标清除与母液归趋","方法专属性、响应因子和定量限","返工、回收和循环物流累积"],"deliverables":["杂质来源—归趋图","关键杂质限度与控制点","分析方法和清除证据"],"red_flags":["未知峰长期未鉴定","只看最终一次检测","通过牺牲大量产品获得表面清除"],"next":"先为前三个高风险杂质建立质量衡算和加标清除数据。"},
    {"id":"process-safety-package","name":"工艺安全数据包","category":"安全与放大","goal":"在放大前识别热、压、反应性、可燃、毒性和操作失效风险。","decision":"没有量热、相容性和可信失效情景，不能只凭小试可控判断可放大。","checklist":["反应/淬灭量热和最大热累积","原料、中间体、残渣的热稳定性","产气、汽化、压力和尾气负荷","失冷、误加料、停搅拌和堵塞情景","材质、静电、惰化和泄放需求"],"deliverables":["热安全与相容性报告","关键操作边界和联锁需求","PHA/HAZOP 输入包"],"red_flags":["高能中间体被分离或蒸干","氧化剂与可燃物无相容性数据","用常压开口小试推断密闭釜行为"],"next":"先完成会改变路线或加料方式的判别性安全实验，再冻结放大方案。"},
    {"id":"solid-form-particle","name":"晶型、粒径与固体操作","category":"固态与分离","goal":"让晶型、纯度、粒径、过滤、干燥和制剂需求同时可控。","decision":"晶型和粒径是工艺结果；结晶、湿磨、过滤和干燥应作为一个连续系统开发。","checklist":["盐型/晶型/溶剂化物和转变关系","溶解度、亚稳区与晶种策略","粒径形貌、过滤与洗涤","干燥温度/真空与残留溶剂","储存、研磨和下游制剂稳定性"],"deliverables":["固态选择依据","结晶与晶种规程","过滤、洗涤和干燥窗口"],"red_flags":["只凭一次 XRPD 宣称晶型稳定","结晶收率高但无法过滤","干燥导致转晶、结块或降解"],"next":"用溶解度和固态筛查选择目标形态，再联动验证过滤和干燥。"},
    {"id":"analytical-control","name":"过程分析与终点控制","category":"质量与分析","goal":"用可执行的测量判断何时加料、停止反应、淬灭、分相或干燥。","decision":"终点必须与关键质量和后续操作相关，并明确取样、淬灭、方法误差与行动规则。","checklist":["关键物种和可接受终点","取样位置、代表性和淬灭","离线/在线方法的周期和专属性","超限后的等待、补料或停止规则","数据完整性和原始记录"],"deliverables":["取样与分析规程","终点判定及行动限","方法能力与不确定性记录"],"red_flags":["终点样品继续反应","面积归一化替代含量测定","每批凭经验延长固定时间"],"next":"先证明取样和方法能区分关键状态，再把终点转成明确行动规则。"},
    {"id":"scale-up-transfer","name":"放大与技术转移包","category":"安全与放大","goal":"让接收团队理解工艺目的、关键参数、设备依赖、风险和异常处理。","decision":"技术转移不是复制实验步骤；必须说明每个控制的原因、允许范围和超限后果。","checklist":["物料规格、配方和投料顺序","设备几何、混合、传热和传质要求","关键参数、质量属性和采样计划","正常操作、偏差、停电/失冷等异常","清洁、持液、收率和周期基准"],"deliverables":["主批记录草案与参数依据","设备适配/差距分析","现场确认批与偏差处理计划"],"red_flags":["只传递一份实验记录","用 rpm、时间直接跨设备复制","关键参数没有科学依据或行动限"],"next":"按单元操作逐项完成设备适配和最差条件评估，再安排现场确认。"},
    {"id":"cost-cycle-sustainability","name":"成本、周期与可持续性","category":"路线策略","goal":"从全流程投入、设备占用、收率、回收和废物判断工艺价值。","decision":"单步试剂便宜不代表总成本低；溶剂、周期、产能损失、返工和废物往往决定整体经济性。","checklist":["原料/试剂/溶剂单位消耗","总收率、返工率和母液回收","设备占用、过滤/干燥/分析周期","能耗、溶剂回收和废物处置","供应风险与规模价格"],"deliverables":["每公斤产品的质量与成本模型","周期瓶颈和产能情景","减废/回收优先项目"],"red_flags":["只比较目录价格","忽略失败批、清洁和检测周期","用高稀释或多次萃取换取小幅收率"],"next":"建立每公斤产品的全流程基线，优先优化占 PMI、周期和失败风险最大的步骤。"},
]

REACTION_PRINCIPLES = {
    "amide-coupling":["mass-balance","energy-balance","liquid-liquid-extraction"], "esterification":["reaction-kinetics","distillation","mass-balance"],
    "hydrolysis":["reaction-kinetics","energy-balance","crystallization"], "sn-substitution":["mixing","reaction-kinetics","filtration"],
    "suzuki":["mixing","filtration","mass-balance"], "buchwald-hartwig":["mixing","reaction-kinetics","filtration"],
    "reductive-amination":["reaction-kinetics","energy-balance","liquid-liquid-extraction"], "hydrogenation":["gas-liquid-transfer","energy-balance","filtration"],
    "oxidation":["energy-balance","reaction-kinetics","gas-liquid-transfer"], "hydride-reduction":["energy-balance","mixing","filtration"],
    "protection":["reaction-kinetics","mass-balance","crystallization"], "salt-crystallization":["crystallization","filtration","drying"],
    "snar":["reaction-kinetics","mixing","crystallization"], "grignard-organolithium":["energy-balance","heat-transfer","mixing"],
    "heck":["reaction-kinetics","filtration","crystallization"], "sonogashira":["reaction-kinetics","filtration","energy-balance"],
    "chan-lam-ullmann":["gas-liquid-transfer","mixing","filtration"], "olefination":["energy-balance","mixing","filtration"],
    "diazotization-sandmeyer":["energy-balance","heat-transfer","reaction-kinetics"], "halogenation":["energy-balance","reaction-kinetics","distillation"],
    "sulfonylation":["energy-balance","mixing","liquid-liquid-extraction"], "urea-carbamate":["energy-balance","impurity-fate","sampling-analytics"],
    "epoxide-opening":["reaction-kinetics","energy-balance","impurity-fate"], "michael-addition":["reaction-kinetics","energy-balance","rheology"],
    "aldol-claisen":["reaction-kinetics","heat-transfer","mixing"], "mannich":["solubility-ionization","reaction-kinetics","liquid-liquid-extraction"],
    "olefin-metathesis":["reaction-kinetics","gas-liquid-transfer","impurity-fate"], "click-cycloaddition":["energy-balance","impurity-fate","filtration"],
    "photoredox":["flow-residence-time","heat-transfer","sampling-analytics"], "biocatalysis":["reaction-kinetics","gas-liquid-transfer","liquid-liquid-extraction"],
}


def _number(value: str):
    return None if value == "" else float(value)


@lru_cache(maxsize=1)
def materials() -> list[dict]:
    result = []
    for line in MATERIAL_ROWS.splitlines():
        material_id, name, english_name, cas, cid, formula, mw, smiles, category, xlogp, tpsa = line.split("|")
        common_use, caution = SPECIAL_GUIDANCE.get(material_id, CATEGORY_GUIDANCE[category])
        result.append({
            "id": material_id, "name": name, "english_name": english_name,
            "aliases": ALIASES.get(material_id, []), "cas": cas, "pubchem_cid": int(cid),
            "formula": formula, "molecular_weight": float(mw), "smiles": smiles,
            "category": category, "category_label": CATEGORY_LABELS[category],
            "xlogp": _number(xlogp), "tpsa": _number(tpsa),
            "common_use": common_use, "process_caution": caution,
            "decision_questions": CATEGORY_DECISIONS[category]["choose"],
            "verify_before_scale": CATEGORY_DECISIONS[category]["verify"],
            "evidence": {"identity": "PubChem PUG REST 固化快照", "guidance": "通用工艺提示，需结合 SDS 与企业规程复核"},
            "source_url": f"https://pubchem.ncbi.nlm.nih.gov/compound/{cid}",
        })
    return result


@lru_cache(maxsize=1)
def principles() -> list[dict]:
    return PRINCIPLES


@lru_cache(maxsize=1)
def development_topics() -> list[dict]:
    return DEVELOPMENT_TOPICS


@lru_cache(maxsize=1)
def reaction_templates() -> list[dict]:
    principle_index = {item["id"]: item["name"] for item in principles()}
    return [item | {"principles": [{"id": principle_id, "name": principle_index[principle_id]}
                                    for principle_id in REACTION_PRINCIPLES[item["id"]]]}
            for item in REACTION_TEMPLATES]


def _normalized(value: str) -> str:
    return re.sub(r"[\s_\-—–·,，;；()（）\[\].]+", "", value).casefold()


def _matches(item: dict, query: str) -> bool:
    if not query.strip():
        return True
    needle = _normalized(query)
    fields = [item.get("id", ""), item.get("name", ""), item.get("english_name", ""), item.get("cas", ""),
              str(item.get("pubchem_cid", "")), item.get("formula", ""), item.get("goal", ""), item.get("category", ""),
              item.get("category_label", ""), item.get("question", ""), item.get("conclusion", ""), *item.get("aliases", [])]
    for key in ("common_choices", "screen", "workup", "analysis", "scale_risks", "inputs", "signals", "mistakes",
                "decision_questions", "verify_before_scale", "checklist", "deliverables", "red_flags"):
        fields.extend(item.get(key, []))
    fields.extend((item.get("decision", ""), item.get("next", "")))
    fields.extend(principle.get("name", "") for principle in item.get("principles", []))
    for formula in item.get("formulas", []):
        fields.extend((formula.get("name", ""), formula.get("expression", ""), formula.get("variables", "")))
    return any(needle in _normalized(str(value)) for value in fields)


def _access(db: Session, project_id: str, user):
    require_project_access(db, project_id, user)


@router.get("/summary")
def summary(project_id: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    _access(db, project_id, user)
    return {
        "version": VERSION, "updated_at": UPDATED_AT, "material_count": len(materials()),
        "reaction_template_count": len(reaction_templates()), "principle_count": len(principles()),
        "topic_count": len(development_topics()),
        "categories": [{"id": key, "label": label, "count": sum(x["category"] == key for x in materials())} for key, label in CATEGORY_LABELS.items()],
        "runtime": "服务器本地版本快照，查询时不访问外部网站",
        "scope": "常用小分子合成原料、试剂、工艺气体、反应工艺、工程原理与研发专题；公式用于研究设计和数量级判断，不替代物性实测、量热、设备核算、SDS、企业规程或项目实验",
        "method_basis": [
            {"label": "NIST Chemistry WebBook：热化学与流体物性", "url": "https://webbook.nist.gov/chemistry/"},
            {"label": "ICH Q8(R2)：药物开发与设计空间", "url": "https://database.ich.org/sites/default/files/Q8_R2_Guideline.pdf"},
            {"label": "FDA PAT：过程理解、测量与控制", "url": "https://www.fda.gov/media/71012/download"},
            {"label": "OSHA PSM：工艺危害识别与控制", "url": "https://www.osha.gov/process-safety-management"},
        ],
    }


@router.get("/materials")
def list_materials(project_id: str, q: str = Query("", max_length=120), category: str = Query("", max_length=30),
                   limit: int = Query(100, ge=1, le=100), db: Session = Depends(get_db), user=Depends(get_current_user)):
    _access(db, project_id, user)
    if category and category not in CATEGORY_LABELS:
        raise HTTPException(422, "未知物料分类")
    found = [x for x in materials() if (not category or x["category"] == category) and _matches(x, q)]
    return {"total": len(found), "items": found[:limit], "version": VERSION}


@router.get("/materials/{material_id}")
def get_material(project_id: str, material_id: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    _access(db, project_id, user)
    item = next((x for x in materials() if x["id"] == material_id), None)
    if not item:
        raise HTTPException(404, "当前知识库未收录该物料")
    return item


@router.get("/reaction-templates")
def list_reaction_templates(project_id: str, q: str = Query("", max_length=120), category: str = Query("", max_length=30),
                            limit: int = Query(100, ge=1, le=100), db: Session = Depends(get_db), user=Depends(get_current_user)):
    _access(db, project_id, user)
    categories = {x["category"] for x in reaction_templates()}
    if category and category not in categories:
        raise HTTPException(422, "未知反应工艺分类")
    found = [x for x in reaction_templates() if (not category or x["category"] == category) and _matches(x, q)]
    return {"total": len(found), "items": found[:limit], "version": VERSION}


@router.get("/reaction-templates/{template_id}")
def get_reaction_template(project_id: str, template_id: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    _access(db, project_id, user)
    item = next((x for x in reaction_templates() if x["id"] == template_id), None)
    if not item:
        raise HTTPException(404, "当前知识库未收录该反应工艺")
    return item | {"evidence": "通用工艺模板，具体条件需用项目底物和实验数据验证", "version": VERSION}


@router.get("/principles")
def list_principles(project_id: str, q: str = Query("", max_length=120), category: str = Query("", max_length=30),
                    limit: int = Query(100, ge=1, le=100), db: Session = Depends(get_db), user=Depends(get_current_user)):
    _access(db, project_id, user)
    categories = {x["category"] for x in principles()}
    if category and category not in categories:
        raise HTTPException(422, "未知化工原理分类")
    found = [x for x in principles() if (not category or x["category"] == category) and _matches(x, q)]
    return {"total": len(found), "items": found[:limit], "version": VERSION}


@router.get("/principles/{principle_id}")
def get_principle(project_id: str, principle_id: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    _access(db, project_id, user)
    item = next((x for x in principles() if x["id"] == principle_id), None)
    if not item:
        raise HTTPException(404, "当前知识库未收录该化工原理")
    return item | {"evidence": "通用工程关系，用于研究设计和数量级判断；项目结论需使用实测物性、设备能力与实验数据", "version": VERSION}


@router.get("/topics")
def list_development_topics(project_id: str, q: str = Query("", max_length=120), category: str = Query("", max_length=30),
                            limit: int = Query(100, ge=1, le=100), db: Session = Depends(get_db), user=Depends(get_current_user)):
    _access(db, project_id, user)
    categories = {x["category"] for x in development_topics()}
    if category and category not in categories:
        raise HTTPException(422, "未知研发专题分类")
    found = [x for x in development_topics() if (not category or x["category"] == category) and _matches(x, q)]
    return {"total": len(found), "items": found[:limit], "version": VERSION}


@router.get("/topics/{topic_id}")
def get_development_topic(project_id: str, topic_id: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    _access(db, project_id, user)
    item = next((x for x in development_topics() if x["id"] == topic_id), None)
    if not item:
        raise HTTPException(404, "当前知识库未收录该研发专题")
    return item | {"evidence": "通用研发决策框架；项目结论需结合路线、物料、分析、安全、设备和批次数据", "version": VERSION}
