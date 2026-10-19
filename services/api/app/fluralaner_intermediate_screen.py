"""Source-linked intermediate facts for the public fluralaner route."""
from __future__ import annotations

import json
from pathlib import Path

SNAPSHOT = Path(__file__).parent / "data/fluralaner_supplier_snapshot.json"

# Each index points to the product of a WO2023012821A1 Example 1 step.
# These are reviewer assessments based on those process paragraphs, not claims
# of validated plant capability or proprietary know-how.
PROFILES = [
    (1, "step_1", "中等", "铝盐酰化后专利用柱层析得到 57%；需要核实放大时的放热、铝盐废物及替代分离。", "若开发非柱层析分离并控制废物负荷，需用重复批次验证其稳定性。"),
    (2, "step_2", "高", "专利以氰化钠/NMP 在 130°C 反应 15 小时，分离仍用柱层析；EHS 与放大替代路线是硬门槛。", "安全操作、废物处理及非柱层析分离可能形成工艺能力要求，尚需放大验证。"),
    (3, "step_3", "中等", "腈水解采用强酸/100°C 与酸碱后处理；需测杂质、腐蚀与废液负荷。", "杂质和废液控制可能需要工艺开发；目前没有验证过的独特优势。"),
    (4, None, "中等", "酰氯活化与甘氨酸乙酯偶联，专利报 96%，该步与前后步备料规模不连续。", "选择性、溶剂/酸性尾气控制和替代分离需验证；目前缺采购与规模数据。"),
    (5, None, "高", "含氟酮片段缩合并形成三氟烯酮；需核对原料成本、烯酮异构体与纯化。", "异构体与纯化控制可能形成工艺能力要求，尚无成本和稳定生产实证。"),
    (6, "step_6", "高", "羟胺环化并水解得到末端偶联用酸；需核对异构体/杂质、后处理及重复批次。", "环化异构体和杂质控制可能需要专门工艺，尚需重复批次验证。"),
]


def build_screen(route_steps: list[dict], wo_evidence: list[dict]) -> dict:
    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    listed = snapshot["records"]
    if len(route_steps) != 7 or len(wo_evidence) != 7:
        raise ValueError("Intermediate screen expects the seven verified WO steps")

    entries = []
    for number, key, difficulty, process_reason, barrier in PROFILES:
        source = listed.get(key) if key else None
        count = source["listed_suppliers"] if source else None
        entries.append({
            "step": number,
            "name": route_steps[number - 1]["intermediate"],
            "cas": source["cas"] if source else None,
            "supplier_listings": count,
            "manufacturer_count": None,
            "supplier_source": source["source_url"] if source else None,
            "supplier_checked_at": source["checked_at"] if source else None,
            "patent_source": wo_evidence[number - 1]["paragraphs"][-1]["url"],
            "difficulty": difficulty,
            "difficulty_basis": process_reason,
            "barrier_to_verify": barrier,
        })

    branch = listed["branch_reagent"]
    return {
        "basis": "ChemicalBook 单一平台公开挂牌快照，可能包含贸易商、分装商、试剂或杂质标准品；不等于实际生产厂家、产能或报价。步骤 4、5 暂无可靠的同物质挂牌总数。",
        "entries": entries,
        "branch_reagent": {"name": "步骤 5 外购片段：1-(3,5-二氯苯基)-2,2,2-三氟乙酮", "cas": branch["cas"],
                           "supplier_listings": branch["listed_suppliers"], "manufacturer_count": None,
                           "supplier_source": branch["source_url"], "supplier_checked_at": branch["checked_at"]},
        "next_verification": [
            "核对专利结构、盐型/异构体和 CAS，补查步骤 4、5 的同物质挂牌。",
            "按公司主体去重，分开贸易、分装、试剂/标准品与实有工厂；索取厂址、生产批记录或可审计质量资料。",
            "向候选工厂询价并索取年产能、规格、批次 COA 和交期；验证关键步纯化、重复收率及 EHS。",
        ],
    }
