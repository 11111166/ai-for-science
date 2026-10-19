"use client";

import type { Job, PerturbationResult, TargetCompatibility } from "@drug-discovery/contracts";
import {
  CircleAlert,
  Dna,
  Network,
  LoaderCircle,
  Play,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
function metric(value: unknown) { return typeof value === "number" ? value.toFixed(3) : "—"; }
function effectLabel(value?: number) { return value == null ? "暂无数据" : value <= -1 ? "严重影响" : value <= -0.5 ? "明显影响" : value <= -0.2 ? "轻度影响" : "影响较小"; }
function riskLabel(value?: number) { return value == null ? "待评估" : value >= 0.67 ? "较高风险" : value >= 0.34 ? "中等风险" : "较低风险"; }
function virtualTargetId(symbol?: string) { return `virtual-target-${(symbol ?? "unknown").toLowerCase().replace(/[^a-z0-9_-]+/g, "-")}`; }
function clamp(value: number, minimum: number, maximum: number) { return Math.min(maximum, Math.max(minimum, value)); }

export function GenePerturbationView({ results, job, targetJob, compatibility, busy, run }: { results: PerturbationResult[]; job?: Job; targetJob?: Job; compatibility: TargetCompatibility | null; busy: string; run: () => void }) {
  const first = results[0]?.result as { benchmark?: { control_auroc?: number; control_auprc?: number; screened_models?: number; hematopoietic_proxy_models?: number; pooled_snapshot_count?: number; pooled_unique_gene_count?: number; pooled_screened_gene_count?: number; coverage_scope?: string; risk_distribution?: { minimum?: number; median?: number; maximum?: number; unique_values?: number }; quality_gates?: { passed?: boolean } } } | undefined;
  const benchmark = first?.benchmark;
  const unavailablePreview = compatibility?.unavailable_symbols.slice(0, 12) ?? [];
  return (
    <>
      <div className="panel-header virtual-cell-heading">
        <div>
          <span>基因敲除</span>
          <h2>敲除靶点后，细胞能否正常存活</h2>
          <p>根据 CRISPR 实验识别广泛必需基因，排查靶点风险。</p>
        </div>
        <div className="action-pair"><span className={`job-badge ${job?.status ?? "neutral"}`}>{job?.status === "completed" ? <ShieldCheck size={14} /> : job?.status === "running" || job?.status === "queued" ? <LoaderCircle className="spin" size={14} /> : <Dna size={14} />}{job?.status === "completed" ? "已完成" : job?.status === "failed" ? "运行失败" : job?.status === "running" || job?.status === "queued" ? "运行中" : "尚未运行"}</span><button className="primary-button" disabled={!targetJob || targetJob.adapter_mode !== "real" || !compatibility?.compatible || !!busy} onClick={run}><Play size={16} /> 运行基因敲除评估</button></div>
      </div>
      {(!targetJob || targetJob.adapter_mode !== "real") && <div className="notice"><CircleAlert /> 请先使用真实数据完成靶点发现；演示靶点不会与真实敲除数据混用。</div>}
      {targetJob?.adapter_mode === "real" && compatibility && <div className={`compatibility-card ${compatibility.compatible ? "ready" : "blocked"}`}><div><strong>本次计算范围</strong><span>{compatibility.upstream_disease_name} · {compatibility.strategy === "novel" ? "新靶点探索" : "已有证据靶点"} · {compatibility.selection_mode === "selected" ? `已选择 ${compatibility.selected_count} 个` : "默认排名前 10 个"}</span></div><div><b>{compatibility.computable_count}</b><small>个有真实 CRISPR 数据</small></div><p>{compatibility.message}{compatibility.unavailable_count > 0 && unavailablePreview.length > 0 ? ` 当前未覆盖示例：${unavailablePreview.join("、")}${compatibility.unavailable_count > unavailablePreview.length ? ` 等 ${compatibility.unavailable_count} 个` : ""}。` : ""}</p></div>}
      {compatibility?.coverage_scope === "gene_level_global" && <details className="technical-details"><summary>DepMap 覆盖口径</summary>DepMap 是跨疾病基因敲除数据库。本页按基因是否做过 CRISPR 筛选来判断能否计算，不再要求当前疾病必须存在专属快照；未覆盖的基因不会阻止已覆盖部分运行。</details>}
      {benchmark && <div className="quality-grid"><div><small>有实验数据的细胞系</small><strong>{benchmark.screened_models ?? "—"}</strong></div><div><small>其中血液 / 免疫细胞系</small><strong>{benchmark.hematopoietic_proxy_models ?? "—"}</strong></div><div><small>合并后可查基因</small><strong>{benchmark.pooled_screened_gene_count ?? benchmark.pooled_unique_gene_count ?? "—"}</strong></div><div title="已知必需基因与非必需基因的区分能力"><small>对照基因区分能力</small><strong>{metric(benchmark.control_auroc)}</strong></div><div title="最小值 / 中位数 / 最大值"><small>风险分数范围</small><strong>{benchmark.risk_distribution ? `${metric(benchmark.risk_distribution.minimum)} / ${metric(benchmark.risk_distribution.median)} / ${metric(benchmark.risk_distribution.maximum)}` : "—"}</strong></div><div><small>自动质量检查</small><strong>{benchmark.quality_gates?.passed ? "通过" : "未通过"}</strong></div></div>}
      <div className="notice"><CircleAlert /> 数据库主要是癌细胞系，血液和免疫细胞系只能作为免疫疾病的近似参考。本页回答“敲除后细胞是否受损”，不能回答“能否治疗疾病”。</div>
      <details className="technical-details"><summary>查看风险分数的详细口径</summary><p>敲除影响越负，说明细胞越依赖这个基因。风险分数综合典型影响、明显受损细胞系比例和严重受损比例，用于保留不同靶点之间的差异；它不是人体毒性概率。</p></details>
      <div className="table-card knockout-table"><table><thead><tr><th>候选基因</th><th>数据覆盖</th><th>典型敲除影响</th><th>免疫相关模型影响</th><th>细胞系广泛依赖信号</th><th>初步判断与详情</th></tr></thead><tbody>{results.map((row) => { const value = row.result as { symbol?: string; screened?: boolean; pan_model_count?: number; hematopoietic_model_count?: number; pan_median_gene_effect?: number; pan_q10_gene_effect?: number; pan_q25_gene_effect?: number; pan_q75_gene_effect?: number; hematopoietic_median_gene_effect?: number; hematopoietic_q25_gene_effect?: number; hematopoietic_q75_gene_effect?: number; pan_dependency_fraction?: number; pan_strong_dependency_fraction?: number; hematopoietic_dependency_fraction?: number; hematopoietic_effect_delta?: number; hematopoietic_context_sensitivity?: number; cell_line_dependency_risk?: number; universal_essentiality_risk?: number; knockout_tolerability?: number }; const symbol = value.symbol ?? row.intervention.replace("CRISPR 敲除 ", ""); const dependencyRisk = value.cell_line_dependency_risk ?? value.universal_essentiality_risk; return <tr key={row.id}><td><strong>{symbol}</strong><small>{value.screened ? "真实 CRISPR 数据" : "当前数据未覆盖"}</small></td><td><strong>{value.pan_model_count ?? "—"}</strong><small>个细胞模型</small></td><td><strong>{effectLabel(value.pan_median_gene_effect)}</strong><small>中位数 {metric(value.pan_median_gene_effect)}</small></td><td><strong>{effectLabel(value.hematopoietic_median_gene_effect)}</strong><small>{value.hematopoietic_model_count ?? "—"} 个血液/免疫模型</small></td><td><span className={`risk-pill risk-${riskLabel(dependencyRisk)}`}>{riskLabel(dependencyRisk)}</span><small>连续分数 {metric(dependencyRisk)}</small></td><td><strong>{riskLabel(dependencyRisk) === "较低风险" ? "可优先做进一步验证" : riskLabel(dependencyRisk) === "较高风险" ? "需重点评估安全性" : "结合其他证据审慎评估"}</strong><details><summary>查看原始统计量与计算依据</summary><dl><div><dt>全体模型四分位区间</dt><dd>{metric(value.pan_q25_gene_effect)} ～ {metric(value.pan_q75_gene_effect)}</dd></div><div><dt>最敏感 10% 模型影响</dt><dd>{metric(value.pan_q10_gene_effect)}</dd></div><div><dt>明显受损模型比例（≤-0.5）</dt><dd>{typeof value.pan_dependency_fraction === "number" ? `${Math.round(value.pan_dependency_fraction * 100)}%` : "—"}</dd></div><div><dt>严重受损模型比例（≤-1.0）</dt><dd>{typeof value.pan_strong_dependency_fraction === "number" ? `${Math.round(value.pan_strong_dependency_fraction * 100)}%` : "—"}</dd></div><div><dt>免疫模型四分位区间</dt><dd>{metric(value.hematopoietic_q25_gene_effect)} ～ {metric(value.hematopoietic_q75_gene_effect)}</dd></div><div><dt>免疫模型受损比例</dt><dd>{typeof value.hematopoietic_dependency_fraction === "number" ? `${Math.round(value.hematopoietic_dependency_fraction * 100)}%` : "—"}</dd></div><div><dt>免疫与全体中位数差</dt><dd>{metric(value.hematopoietic_effect_delta)}</dd></div><div><dt>免疫情境敏感度</dt><dd>{metric(value.hematopoietic_context_sensitivity)}</dd></div><div><dt>预计细胞系耐受程度</dt><dd>{metric(value.knockout_tolerability)}</dd></div></dl></details></td></tr>; })}{!results.length && <tr><td colSpan={6}><div className="inline-empty">尚无基因敲除结果。运行前会显示疾病、选择数量和可计算靶点数。</div></td></tr>}</tbody></table></div>
    </>
  );
}

type VirtualCellContext = {
  cell_type?: string; case_donors?: number; control_donors?: number; cells?: number;
  target_disease_log2fc?: number; direction_consistent?: boolean; state_axis_auroc?: number;
  state_axis_balanced_accuracy?: number; disease_burden_before?: number;
  projected_disease_burden_after?: number; projected_reversal?: number;
  projected_reversal_ci95?: number[]; top_projected_restored_genes?: { symbol?: string; disease_log2fc?: number; projected_change?: number; projected_residual?: number }[];
  context_priority_score?: number; context_confidence?: number; rationale?: string; basis?: string[]; data_sources?: string[];
};

type VirtualCellPayload = {
  analysis_tier?: "disease_specific" | "context_prior_baseline";
  metric_type?: "cell_context_validation_priority" | string;
  symbol?: string; approved_name?: string; rank?: number; direction?: string; best_cell_type?: string;
  cell_context_count?: number; projected_reversal?: number; projected_reversal_ci95?: number[];
  state_axis_auroc?: number; confidence?: number; contexts?: VirtualCellContext[];
  evidence_factors?: { causal_support?: number; disease_context?: number; upstream_target_score?: number; direction_confidence?: number; independent_source_count?: number };
  decision_summary?: string; recommended_validation?: string[]; boundary?: string;
  benchmark?: { candidate_count?: number; context_family?: string; context_template_count?: number; direction_coverage?: number; priority_distribution?: { minimum?: number; median?: number; maximum?: number; unique_values?: number }; confidence_distribution?: { minimum?: number; median?: number; maximum?: number }; cell_type_count?: number; evaluated_cell_type_count?: number; rejected_cell_type_count?: number; target_count?: number; target_context_coverage?: number; held_out_donor_auroc?: { minimum?: number; median?: number; maximum?: number }; held_out_donor_balanced_accuracy?: { median?: number }; projected_reversal_distribution?: { minimum?: number; median?: number; maximum?: number; unique_values?: number }; quality_gates?: { passed?: boolean } };
  qc?: { input_cells?: number; retained_cells?: number; case_donors?: number; control_donors?: number; statistical_unit?: string; cell_types_validated?: number; cell_types_rejected?: number; dedicated_single_cell_data?: boolean; input_target_count?: number; context_templates?: number };
  provenance?: { source?: string; snapshot_id?: string; model_version?: string; scientific_status?: string; data_mode?: string };
};

export function VirtualCellView({ results, job, targetJob, compatibility, busy, run, onConfigure }: { results: PerturbationResult[]; job?: Job; targetJob?: Job; compatibility: TargetCompatibility | null; busy: string; run: () => void; onConfigure: () => void }) {
  const first = results[0]?.result as VirtualCellPayload | undefined;
  const baselineCount = results.filter((row) => (row.result as VirtualCellPayload).analysis_tier === "context_prior_baseline").length;
  const baselineOnly = baselineCount > 0 && baselineCount === results.length;
  const benchmark = first?.benchmark;
  const qc = first?.qc;
  const canRun = Boolean(targetJob?.adapter_mode === "real" && compatibility?.compatible);
  const comparison = results
    .map((row) => ({ row, value: row.result as VirtualCellPayload }))
    .filter(({ value }) => typeof value.projected_reversal === "number")
    .sort((a, b) => (b.value.projected_reversal ?? 0) - (a.value.projected_reversal ?? 0) || (b.value.confidence ?? 0) - (a.value.confidence ?? 0))
    .slice(0, 10);
  const comparisonCellTypes = Array.from(new Set(comparison.map(({ value }) => value.best_cell_type ?? "未标注细胞类型")));
  const observedUpper = Math.max(0, ...comparison.map(({ value }) => value.projected_reversal_ci95?.[1] ?? value.projected_reversal ?? 0));
  const comparisonScaleMax = Math.max(0.75, Math.ceil(observedUpper * 10) / 10);
  const comparisonTicks = Array.from({ length: 6 }, (_, index) => comparisonScaleMax * index / 5);
  return (
    <>
      <div className="panel-header virtual-cell-heading">
        <div>
          <span>虚拟细胞模拟</span>
          <h2>虚拟细胞</h2>
          <p>比较细胞状态的预计恢复、主要细胞类型与不确定性。</p>
        </div>
        <div className="action-pair"><span className={`job-badge ${job?.status ?? "neutral"}`}>{job?.status === "completed" ? <ShieldCheck size={14} /> : job?.status === "running" || job?.status === "queued" ? <LoaderCircle className="spin" size={14} /> : <Network size={14} />}{job?.status === "completed" ? "已完成" : job?.status === "failed" ? "运行失败" : job?.status === "running" || job?.status === "queued" ? "运行中" : "尚未运行"}</span><button className="primary-button" disabled={!canRun || !!busy} onClick={run}><Play size={16} /> 运行虚拟细胞</button></div>
      </div>
      {!targetJob && <div className="notice"><CircleAlert /> 请先完成一次真实靶点分析，再运行虚拟细胞。</div>}
      {targetJob?.adapter_mode === "real" && compatibility && <div className={`compatibility-card ${compatibility.compatible ? "ready" : "blocked"}`}><div><strong>本次计算范围</strong><span>{compatibility.upstream_disease_name} · {compatibility.strategy === "novel" ? "新靶点探索" : "已有证据靶点"} · {compatibility.selection_mode === "selected" ? `已选择 ${compatibility.selected_count} 个` : "默认排名前 10 个"}</span></div><div><b>{compatibility.computable_count}</b><small>个可投影</small></div><p>{compatibility.message}</p></div>}
      {!canRun && targetJob?.adapter_mode === "real" && <div className="virtual-result-toolbar"><div><Network size={18} /><div><strong>当前靶点批次暂不可运行</strong><span>请返回靶点发现确认存在可追溯的真实候选；已有疾病专属数据时运行完整投影，其余疾病运行透明情境基线。</span></div></div><button type="button" className="secondary-button" onClick={onConfigure}><Dna size={16} /> 查看基因敲除</button></div>}
      {first?.provenance && <div className="virtual-result-toolbar"><div><ShieldCheck size={18} /><div><strong>{baselineOnly ? "公开证据细胞情境优先级基线" : baselineCount ? "疾病专属结果与细胞情境基线并行展示" : "结果使用了已锁定版本的供体级单细胞数据"}</strong><details><summary>数据与计算版本</summary><span>{first.provenance.snapshot_id} · {first.provenance.model_version}</span></details></div></div><span className="mode-pill">{baselineOnly ? "真实公共证据 · 模型推测" : "真实单细胞数据"}</span></div>}
      {baselineOnly && <div className="notice warning"><CircleAlert /> 当前疾病尚无通过质量门的专属病例/对照单细胞快照。本页先回答“应该在哪类细胞中优先验证”，不会把情境优先级包装成疾病状态恢复或药效预测。</div>}
      {benchmark && (baselineOnly ? <div className="quality-grid"><div><small>本次分析靶点</small><strong>{benchmark.candidate_count ?? results.length}</strong></div><div><small>疾病情境类型</small><strong>{benchmark.context_family ?? "通用疾病"}</strong></div><div><small>每个靶点比较的细胞类型</small><strong>{benchmark.context_template_count ?? "—"}</strong></div><div><small>干预方向覆盖率</small><strong>{typeof benchmark.direction_coverage === "number" ? `${Math.round(benchmark.direction_coverage * 100)}%` : "—"}</strong></div><div title="最小值 / 中位数 / 最大值"><small>情境优先级范围</small><strong>{benchmark.priority_distribution ? `${metric(benchmark.priority_distribution.minimum)} / ${metric(benchmark.priority_distribution.median)} / ${metric(benchmark.priority_distribution.maximum)}` : "—"}</strong></div><div><small>自动一致性检查</small><strong>{benchmark.quality_gates?.passed ? "通过" : "未通过"}</strong></div></div> : <div className="quality-grid"><div><small>通过质控的细胞</small><strong>{qc?.retained_cells?.toLocaleString("zh-CN") ?? "—"}</strong></div><div><small>病例 / 对照供体</small><strong>{qc?.case_donors ?? "—"} / {qc?.control_donors ?? "—"}</strong></div><div title={`${benchmark.evaluated_cell_type_count ?? "—"} 类参与检查，${benchmark.rejected_cell_type_count ?? "—"} 类因表现不足未发布`}><small>可可靠区分疾病的细胞类型</small><strong>{benchmark.cell_type_count ?? "—"} / {benchmark.evaluated_cell_type_count ?? "—"}</strong></div><div title="技术口径：留一供体 AUROC 中位数；只发布 AUROC≥0.65 且平衡准确率≥0.60 的细胞类型"><small>病例识别能力中位数</small><strong>{metric(benchmark.held_out_donor_auroc?.median)}</strong></div><div><small>候选在细胞类型中的覆盖率</small><strong>{typeof benchmark.target_context_coverage === "number" ? `${Math.round(benchmark.target_context_coverage * 100)}%` : "—"}</strong></div><div><small>自动质量检查</small><strong>{benchmark.quality_gates?.passed ? "通过" : "未通过"}</strong></div></div>)}
      {!!results.length && <details className="technical-details"><summary>查看分数和不确定范围的计算口径</summary><p>{baselineOnly ? "情境优先级综合上游疾病—靶点证据、因果线索、干预方向强度和疾病家族常见受累细胞。区间主动设得较宽，用来提示当前缺少疾病专属供体数据；分数只决定验证顺序。" : "预计恢复比例表示在明确干预假设下，疾病状态向健康样本中心移动的比例，不是实测药效。病例识别能力（AUROC）只检查状态模型；95% 区间反映供体抽样和上游证据带来的不确定性。"}</p></details>}
      {!!comparison.length && <section className="virtual-forest" aria-labelledby="virtual-forest-title">
        <header>
          <div><span>靶点横向比较</span><h3 id="virtual-forest-title">{baselineOnly ? "细胞情境验证优先级与不确定范围" : "预计恢复比例与不确定范围"}</h3><p>{baselineOnly ? "点越靠右，越建议优先进入对应细胞模型验证；横线越长，说明当前证据仍不足。" : "点越靠右，预计恢复越多；横线越短，不同供体重复计算得到的结果越稳定。"}</p></div>
          <div className="virtual-forest-legend" aria-label="最佳细胞类型图例">{comparisonCellTypes.map((cellType, index) => <span key={cellType}><i className={`context-${index % 4}`} />{cellType}</span>)}</div>
        </header>
        <div className="virtual-forest-axis" aria-hidden="true"><span /><div>{comparisonTicks.map((tick) => <b key={tick}>{tick.toFixed(2)}</b>)}</div><em>{baselineOnly ? "验证优先级" : "预计恢复比例"}</em></div>
        <div className="virtual-forest-rows">{comparison.map(({ row, value }) => {
          const symbol = value.symbol ?? row.intervention;
          const estimate = value.projected_reversal ?? 0;
          const low = clamp(value.projected_reversal_ci95?.[0] ?? estimate, 0, comparisonScaleMax);
          const high = clamp(value.projected_reversal_ci95?.[1] ?? estimate, low, comparisonScaleMax);
          const contextIndex = comparisonCellTypes.indexOf(value.best_cell_type ?? "未标注细胞类型") % 4;
          return <a key={row.id} className="virtual-forest-row" href={`#${virtualTargetId(symbol)}`} aria-label={`${symbol}：${baselineOnly ? "验证优先级" : "投影逆转"} ${metric(estimate)}，95% 区间 ${metric(low)} 至 ${metric(high)}，最佳细胞类型 ${value.best_cell_type ?? "未标注"}`}>
            <span className="virtual-forest-label"><b>{symbol}</b><small>第 {value.rank ?? "—"} 名</small></span>
            <span className="virtual-forest-track" aria-hidden="true"><i className="virtual-forest-ci" style={{ left: `${100 * low / comparisonScaleMax}%`, width: `${100 * (high - low) / comparisonScaleMax}%` }} /><i className={`virtual-forest-point context-${contextIndex}`} style={{ left: `${100 * estimate / comparisonScaleMax}%` }} /></span>
            <span className="virtual-forest-value"><b>{metric(estimate)}</b><small>置信度 {typeof value.confidence === "number" ? `${Math.round(value.confidence * 100)}%` : "—"}</small></span>
          </a>;
        })}</div>
        <footer><span><i className="forest-line" />95% 区间</span><span><i className="forest-dot" />点估计</span><p>点击靶点查看详情；用于安排验证顺序，不代表临床疗效。</p></footer>
      </section>}
      <div className="virtual-candidate-grid">{results.map((row) => {
        const value = row.result as VirtualCellPayload;
        const isBaseline = value.analysis_tier === "context_prior_baseline";
        const low = value.projected_reversal_ci95?.[0];
        const high = value.projected_reversal_ci95?.[1];
        const symbol = value.symbol ?? row.intervention;
        return <article id={virtualTargetId(symbol)} key={row.id} className="virtual-candidate-card">
          <header><span>第 {value.rank ?? "—"} 名</span><b>{symbol}</b><small>{value.approved_name ?? value.best_cell_type}</small></header>
          <div className="state-axis"><div><i style={{ width: "100%" }} /><span>{isBaseline ? "候选进入情境比较" : "干预前疾病状态 1.000"}</span></div><div className="after"><i style={{ width: `${isBaseline ? Math.max(0, 100 * (value.projected_reversal ?? 0)) : Math.max(0, 100 * (1 - (value.projected_reversal ?? 0)))}%` }} /><span>{isBaseline ? `优先验证指数 ${metric(value.projected_reversal)}` : `预计干预后 ${metric(1 - (value.projected_reversal ?? 0))}`}</span></div></div>
          <dl><div><dt>{isBaseline ? "细胞情境优先级" : "预计恢复比例"}</dt><dd>{metric(value.projected_reversal)}</dd></div><div><dt>不确定范围（95%）</dt><dd>{metric(low)}–{metric(high)}</dd></div><div><dt>{isBaseline ? "建议优先验证的细胞" : "预计最明显的细胞类型"}</dt><dd>{value.best_cell_type ?? "—"}</dd></div><div title={isBaseline ? "来自上游多源证据与方向一致性" : "技术指标：状态轴 AUROC"}><dt>{isBaseline ? "干预方向把握" : "病例识别能力"}</dt><dd>{isBaseline ? metric(value.evidence_factors?.direction_confidence) : metric(value.state_axis_auroc)}</dd></div><div><dt>比较的细胞类型</dt><dd>{value.cell_context_count ?? "—"}</dd></div><div><dt>综合可信度</dt><dd>{metric(value.confidence)}</dd></div></dl>
          {isBaseline && <p className="virtual-decision-summary">{value.decision_summary}</p>}
          <details><summary>{isBaseline ? "查看细胞类型依据与建议实验" : "查看各细胞类型和可能恢复的基因"}</summary>{(value.contexts ?? []).slice(0, 6).map((context) => <section className="virtual-context" key={context.cell_type}><header><b>{context.cell_type}</b><span>{isBaseline ? `情境优先级 ${metric(context.context_priority_score)} · 可信度 ${metric(context.context_confidence)}` : `${context.case_donors}/${context.control_donors} 位病例/对照供体 · ${context.cells?.toLocaleString("zh-CN")} 个细胞`}</span></header><p>{isBaseline ? context.rationale : `靶基因在疾病中的变化 ${metric(context.target_disease_log2fc)} · 干预方向${context.direction_consistent ? "与数据一致" : "与数据不完全一致，可信度已降低"} · 预计恢复 ${metric(context.projected_reversal)}`}</p>{isBaseline ? <div className="gene-chip-list">{(context.basis ?? []).map((basis) => <span key={basis}>{basis}</span>)}</div> : <div className="gene-chip-list">{(context.top_projected_restored_genes ?? []).slice(0, 8).map((gene) => <span key={gene.symbol} title={`疾病中的变化 ${metric(gene.disease_log2fc)}；预计干预变化 ${metric(gene.projected_change)}`}>{gene.symbol} <b>{metric(gene.projected_change)}</b></span>)}</div>}</section>)}{isBaseline && <><p><b>建议实验：</b>{(value.recommended_validation ?? []).join("；")}</p><p>{value.boundary}</p></>}</details>
        </article>;
      })}{!results.length && <div className="empty-state"><Sparkles /><h3>还没有虚拟细胞结果</h3><p>运行前系统会先检查疾病专属单细胞快照；没有专属数据时会运行透明的细胞情境优先级基线。</p></div>}</div>
      <div className="virtual-cell-footer"><div><CircleAlert size={18} /><p><strong>请把结果理解为“优先验证线索”</strong><span>{baselineOnly ? "当前结果用于选择先在哪类细胞中做实验，不是单细胞实测、药物处理后转录组或临床疗效。" : "当前版本是在疾病状态轴上的透明情景模拟，不是完整的药物处理后转录组，也不能当作临床疗效。"}</span></p></div><span className="maturity baseline">可解释产品基线</span></div>
    </>
  );
}
