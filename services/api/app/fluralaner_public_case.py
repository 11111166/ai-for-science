"""Source-backed fluralaner demo assembled from versioned patent paragraphs.

The English patent text is a mirror snapshot. Chinese route labels are editorial
annotations, not a machine translation or an independently verified experiment.
"""
from __future__ import annotations

import json
from pathlib import Path
import re

from .process_portfolio import PortfolioInput

SOURCE_FILE = Path(__file__).parent / "data/fluralaner_patent_sources.json"
WO_STEPS = [
    ("p0215", None, "1-氟-2-甲苯与乙酰氯/氯化铝反应；二氯甲烷、25°C、4 小时", "1-(4-氟-3-甲基苯基)乙酮", "萃取后柱层析"),
    ("p0218", "p0219", "芳基氟被氰基取代；氰化钠/NMP、130°C、15 小时", "4-乙酰基-2-甲基苯甲腈", "萃取后柱层析"),
    ("p0222", "p0223", "腈水解成羧酸；硫酸/乙酸、100°C、12 小时", "4-乙酰基-2-甲基苯甲酸", "酸化、乙酸乙酯萃取"),
    ("p0226", "p0227", "羧酸用氯化亚砜活化，再与甘氨酸乙酯偶联", "(4-乙酰基-2-甲基苯甲酰)甘氨酸乙酯", "水洗、浓缩"),
    ("p0230", "p0231", "与 3,5-二氯-2,2,2-三氟苯乙酮缩合；碳酸铯/乙腈回流", "含 3,5-二氯苯基的三氟烯酮甘氨酸乙酯", "过滤、乙酸乙酯萃取"),
    ("p0234", "p0235", "羟胺环化并水解乙酯；乙腈/DBU/LiOH、25°C", "异噁唑啉苯甲酰甘氨酸中间体", "酸化沉淀、过滤"),
    ("p0238", "p0239", "与 2,2,2-三氟乙胺盐酸盐/EDC 偶联；二氯甲烷、25°C", "氟雷拉纳", "二氯甲烷萃取、浓缩"),
]


def _reported_percent(text: str) -> float | None:
    # WO example process paragraphs end with isolated yield. Do not apply this
    # heuristic to general patent text where concentrations may be percentages.
    values = re.findall(r"(?<!\d)(\d{1,3}(?:\.\d+)?)\s*%", text)
    return float(values[-1]) if values else None


def build_public_case() -> dict:
    snapshot = json.loads(SOURCE_FILE.read_text(encoding="utf-8"))
    sources = snapshot["sources"]
    wo, ep = sources["WO2023012821A1"], sources["EP4077285A1"]
    wo_paras = {item["locator"]: item["text"] for item in wo["paragraphs"]}
    ep_paras = {item["locator"]: item["text"] for item in ep["paragraphs"]}

    def evidence(publication: str, spec: dict, locators: list[str]) -> list[dict]:
        paragraphs = wo_paras if publication.startswith("WO") else ep_paras
        return [{"locator": locator, "text": paragraphs[locator], "url": f'{spec["url"]}#{locator}'} for locator in locators]

    steps, wo_evidence = [], []
    expected = [57, 64, 76, 96, 82, 87, 82]
    for number, (heading, process, transformation, intermediate, purification) in enumerate(WO_STEPS, 1):
        process = process or heading
        reported = _reported_percent(wo_paras[process])
        if reported != expected[number - 1]:
            raise ValueError(f"WO patent step {number} yield changed: {reported}")
        questionable = number == 7 and "110 mg (769 mg" in wo_paras[process]
        if number == 7 and not questionable:
            raise ValueError("WO patent step 7 mass discrepancy changed; review source before publication")
        locators = list(dict.fromkeys([heading, process]))
        steps.append({
            "transformation": transformation,
            "intermediate": intermediate,
            "yield_pct": None if questionable else reported,
            "purification": purification,
            "source_reference": f'WO2023012821A1 实施例 1 步骤 {number} · {wo["url"]}#{process}' + ("；原文质量数字矛盾，收率未采纳" if questionable else ""),
        })
        wo_evidence.append({"number": number, "reported_yield_pct": reported, "accepted_yield_pct": None if questionable else reported,
                            "warning": "原文同句出现 110 mg 与 769 mg，质量数字冲突，82% 不纳入路线计算" if questionable else "",
                            "paragraphs": evidence("WO2023012821A1", wo, locators)})

    ep_variants = []
    for index, start, end, reported, purity in [(1, 99, 103, 75.5, ">99 area%"), (2, 104, 108, 73.9, ">98 area%")]:
        locators = [f"p{number:04}" for number in range(start, end + 1)]
        if f"{reported} %" not in ep_paras[locators[-1]]:
            raise ValueError(f"EP patent example {index} reported yield changed")
        ep_variants.append({
            "input_step": {
                "transformation": "异噁唑啉苯甲酰甘氨酸中间体与三氟乙胺偶联，再结晶" if index == 1 else "同一中间体经 NHS/EDC 活化、三氟乙胺偶联，再结晶",
                "intermediate": "氟雷拉纳",
                "yield_pct": reported,
                "purification": f"乙酸乙酯/甲苯结晶；专利报道 HPLC {purity}",
                "source_reference": f'EP4077285A1 实施例 {index} [{73 if index == 1 else 78}]–[{77 if index == 1 else 82}] · {ep["url"]}#{locators[0]}',
            },
            "name": f"末端偶联与结晶 · 专利实施例 {index}",
            "paragraphs": evidence("EP4077285A1", ep, locators),
            "reported_yield_pct": reported,
            "hplc_area_pct": purity,
        })

    payload = PortfolioInput(
        target_name="氟雷拉纳（Fluralaner）", cas="864731-61-3", smiles="",
        product_class="veterinary_api", intended_use="兽药活性成分；犬猫跳蚤、蜱虫防治产品的工艺研究示例",
        jurisdiction="CN", target_purity_pct=None, target_annual_kg=None,
        routes=[
            {"name": "完整公开路径 · 7 步 · WO2023012821A1", "kind": "patent", "source_reference": f'WO2023012821A1 实施例 1 · {wo["url"]}#p0214', "steps": steps},
            *[{"name": variant["name"], "kind": "patent", "source_reference": f'EP4077285A1 · {ep["url"]}', "steps": [variant["input_step"]]} for variant in ep_variants],
        ],
    )
    from .fluralaner_intermediate_screen import build_screen
    intermediate_screen = build_screen(payload.model_dump()["routes"][0]["steps"], wo_evidence)
    return {
        "input": payload.model_dump(),
        "intermediate_screen": intermediate_screen,
        "snapshot": {publication: {key: spec[key] for key in ("url", "source_kind", "retrieved_at", "source_sha256")} for publication, spec in sources.items()},
        "wo_steps": wo_evidence,
        "ep_variants": [{key: variant[key] for key in ("name", "paragraphs", "reported_yield_pct", "hplc_area_pct")} for variant in ep_variants],
        "hplc_method": evidence("EP4077285A1", ep, ["p0097", "p0098"]),
        "limitations": [
            "公开专利是实验记录，不等于已验证的生产工艺或可自由实施路线。",
            "WO 实施例第 4、5 步的备料规模与前后步不连续，不计算全路线实测总收率。",
            "EP 实施例的 HPLC area% 是色谱面积占比，不能直接当作含量、法规纯度限度。",
            "镜像原文保留 OCR/排版错误；具体配料、危险性和质量数字需对照原始专利及实验复核。",
        ],
    }
