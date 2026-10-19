import type { Compound } from '@drug-discovery/contracts';
import {compoundDetails,finite,matchesFor,predictionCount} from './compoundInsights';
import type {QsarResult} from '../components/QsarPanel';
export function interpretCompound(c:Compound,target='',qsar?:QsarResult|null) {
 const support:string[]=[],issues:string[]=[],next:string[]=[];const d=compoundDetails(c);const match=matchesFor(c,target)[0];const symbol=match?.target_symbol;
 if(c.demo)return {title:'演示记录',symbol,support:['仅用于展示流程'],issues:['不纳入真实候选判断'],next:['先取得真实分子与实验记录']};
 if(match){support.push(`${symbol || '关联靶点'}：${match.assay_count ?? '—'} 个实验、${match.document_count ?? '—'} 篇文献${finite(match.pchembl_median)?`；中位 pChEMBL ${match.pchembl_median.toFixed(2)}`:''}`);
 if((match.document_count??0)<2){issues.push('文献依据不足两篇，尚缺独立重复支持');next.push(`补充 ${symbol || '该靶点'} 的独立重复活性实验`);}
 const types=[...new Set(match.activity_types||[])];if(types.length>1){issues.push(`活性汇总混合 ${types.join(' / ')}，不能视为同一终点`);next.push('在统一终点和条件下测量浓度—效应曲线');}
 }else {issues.push('缺少可关联的靶点活性记录');next.push('先核实分子身份与直接靶点活性');}
 const model=qsar?.models.find(m=>m.symbol===symbol);const p=model?.status==='validated'?model.predictions.find(p=>p.compound_id===c.id&&p.identifier===c.identifier):undefined;
 if(finite(p?.predicted)){
 support.push(`${symbol} QSAR pIC50 ${p.predicted.toFixed(2)}（${p.prediction_kind==='scaffold_holdout'?'独立骨架测试':p.prediction_kind==='scaffold_cv'?'骨架交叉验证':'新结构预测'}）`);
 if(p?.domain==='outside'){issues.push(`QSAR 在结构适用域外${finite(p.similarity)?`，相似度 ${p.similarity.toFixed(2)}`:''}`);next.push('以实测活性为主，补充相近结构的验证数据');}
 if(finite(p?.observed)&&Math.abs(p.observed-p.predicted)>=1){issues.push(`同批 IC50 实测与预测相差 ${Math.abs(p.observed-p.predicted).toFixed(2)} pIC50；达到预设复核阈值 1`);next.push('核对原始 IC50 实验条件与结构后复测');}
 } else if(model?.status && model.status!=='validated'){issues.push(`${symbol} QSAR 未通过发布门槛，不用于支持该候选`);}
 if(d.black_box_warning||d.withdrawn_flag){issues.push('存在已记录的监管警示');next.unshift('复核原始监管资料及其适用的药物形式');}
 if(finite(d.descriptors?.structural_alert_count)&&d.descriptors.structural_alert_count>0){issues.push(`命中 ${d.descriptors.structural_alert_count} 条结构警示`);next.push('核查命中结构片段及其相关实验风险');}
 const n=predictionCount(c);if(n<5)issues.push(`完整 ADMET 尚缺 ${5-n} 项；现有性质评分不能确认安全性`);
 if(!finite(d.descriptors?.structural_alert_count))next.push('补齐结构警示计算与早期性质检查');
 if(!next.length)next.push(`在疾病相关体系验证 ${symbol || '靶点'} 的作用方向及选择性`);
 const title=d.black_box_warning||d.withdrawn_flag?'先复核监管风险':p?.domain==='outside'?'预测适用性需复核':match && (match.document_count??0)>=2?'有多篇文献记录，可安排活性复核':'先补独立活性依据';
 return {title,symbol,support,issues,next:[...new Set(next)]};
}
