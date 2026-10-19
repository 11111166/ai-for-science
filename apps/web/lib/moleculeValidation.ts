import type { Compound } from '@drug-discovery/contracts';
import { compoundDetails, developmentStage, finite, matchesFor } from './compoundInsights';

/** Evidence-based experimental triage; not a clinical or investment score. */
export function moleculeValidation(c:Compound,target='') {
 const d=compoundDetails(c),m=matchesFor(c,target)[0],stage=developmentStage(c);
 const targetName=m?.target_symbol || m?.target_chembl_id || '待确认靶点';
 const records=Boolean(m && ((m.activity_count ?? 0)>0 || (m.assay_count ?? 0)>0 || m.experimental_records?.length || finite(m.pchembl_median)));
 const flagged=Boolean(d.black_box_warning || d.withdrawn_flag);
 const direction=m?.intervention_direction==='inhibit'?'抑制':m?.intervention_direction==='activate'?'激活':null;
 const purpose=c.demo?'演示分子':stage.id==='approved'?'药物参照':stage.id==='clinical'?'临床研究参照':records?'机制验证备选分子':'待核实分子';
 const roleBasis=c.demo?'不参与真实研发判断':stage.id==='approved'?'有批准记录；不代表本病已获批或有效':stage.id==='clinical'?'有临床阶段记录；当前开发状态需核对':records?'有靶点活性记录；实验工具适用性仍需验证':'尚缺足够的直接靶点活性依据';
 const question=direction?`该分子能否通过${direction} ${targetName} 改变疾病相关表型？`:`该分子是否通过 ${targetName} 产生可重复的生物学效应？`;
 const effect=direction?`上游建议${direction} ${targetName}；仍需确认该分子的作用方向与细胞效应。`:'作用方向未确认，不能从结合活性推断疾病获益。';
 const step=c.demo?'先取得真实分子与实验数据':flagged?'先核对监管记录及适用分子形式':!records?'先核实分子身份与直接靶点活性':(m?.document_count ?? 0)<2?'优先复测直接活性，补充独立来源证据':'优先验证细胞内靶点作用与选择性';
 const blocker=flagged?'存在监管提示，先复核再安排开发评价':!records?'缺少直接活性依据': '选择性、有效暴露与疾病表型因果联系尚需实验确认';
 const experiment={
  model:m ? `与项目疾病相关、表达 ${targetName} 的细胞模型；原始活性实验用于复测参照。` : "先确认分子身份和直接靶点，再选择疾病相关模型。",
  controls:'溶剂对照、已验证的阳性对照（如可获得），以及独立遗传扰动或救援对照。',
  readouts:'浓度—效应、细胞内靶点作用、疾病相关表型和细胞活性；区分目标效应与非特异性损伤。',
  criteria:'靶点作用与表型变化可重复且方向一致，再评估选择性与暴露；仅在细胞受损时出现的效应先复核。具体阈值按项目预先确定。',
 };
 const reasons=[
  records ? `${targetName}：${m?.assay_count ?? '数量未提供'} 个实验、${m?.document_count ?? '数量未提供'} 篇文献记录` : '本批尚缺直接靶点活性记录',
  m?.activity_types?.length ? `实验终点：${m.activity_types.join(' / ')}${m.activity_types.length>1?'，需分别复核':''}` : roleBasis,
 ];
 const doubt=flagged?blocker:!records?'尚不能确定该分子能否作用于所选靶点':(m?.document_count ?? 0)<2?'缺少独立文献重复；单次结果可能受实验条件影响':'尚不能排除脱靶或细胞损伤造成的表型变化';
 const decision=flagged?'暂缓开发评价，先核对监管记录':!records?'补齐身份与直接活性后再评审':'优先补充验证，暂不据此确认疾病有效性';
 const stop='若效应不能独立重复、救援不支持靶点因果关系，或仅在细胞损伤时出现，暂缓推进。';
 const summary=[`定位：${purpose} · ${targetName}`,`依据：${reasons.join('；')}`,`疑点：${doubt}`,`下一步：${step}`,`推进条件：${experiment.criteria}`,`暂缓条件：${stop}`].join('\n');
 return {purpose,roleBasis,targetName,question,effect,step,blocker,experiment,records,flagged,reasons,doubt,decision,stop,summary};
}
