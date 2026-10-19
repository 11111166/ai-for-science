import type { TargetCandidate } from "@drug-discovery/contracts";

type Profile = NonNullable<NonNullable<TargetCandidate["evidence_breakdown"]>["target_to_hit"]>;

/** Displays saved evidence flags only; does not generate an investment score. */
export function TargetReviewDetail({ hit }: { hit: Profile }) {
  const basis = [
    hit.has_small_molecule_binder && "已有小分子结合物",
    hit.has_pocket && "已有结合口袋线索",
    hit.has_ligand && "已有配体线索",
    hit.has_high_quality_probe && "已有高质量化学探针",
    hit.is_membrane && "膜蛋白定位线索",
    hit.is_secreted && "分泌蛋白定位线索",
  ].filter((item): item is string => Boolean(item));

  return <details className="target-review-detail">
    <summary>决策复核：依据、风险与尚缺信息</summary>
    <p>以下整理当前靶点批次的公开数据注释；路线分数与推进建议属于规则派生，尚不能视为实验确认或正式立项结论。</p>
    <h4>可追溯的开发起点</h4>
    {basis.length ? <ul>{basis.map((item) => <li key={item}>{item}</li>)}</ul> : <p>当前结果未提供明确的结合物、结构或定位起点；缺少注释不等于靶点不可成药。</p>}
    <p>{hit.evidence_replication
      ? `疾病证据覆盖 ${hit.evidence_replication.source_count} 类；类型数不等于独立研究数，也不能证明跨队列复现。`
      : "当前结果未提供疾病证据类型计数，请复核下方证据分类与来源。"}</p>
    <h4>风险与反对证据复核</h4>
    <p>{hit.known_safety_signal
      ? "已登记公开安全性信号：推进前需复核具体事件、剂量与组织情境，不能仅凭准备度分数跳过。"
      : "当前未标记公开安全性信号；这不代表已完成安全性评估。"}</p>
    <p>本卡尚未汇总反对疾病机制的证据、干预方向冲突或项目中的敲除结果。广泛必需性请结合“基因敲除”模块单独复核。</p>
    <h4>决策仍需补齐</h4>
    <ul>
      <li>患者分层与组织：确认疾病样本、目标细胞、作用方向及正常组织暴露。</li>
      <li>竞品与临床：当前未建立完整竞争格局；已有药物记录不能用来推断临床拥挤度。</li>
      <li>独立验证：用原始研究或实验检验支持与反对证据，再结合下方路线缺口确定下一步。</li>
    </ul>
  </details>;
}
