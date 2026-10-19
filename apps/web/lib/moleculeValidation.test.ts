import {expect,it} from 'vitest';
import type {Compound} from '@drug-discovery/contracts';
import {moleculeValidation} from './moleculeValidation';
const base:Compound={id:'x',identifier:'CHEMBL1',name:'test',mechanism:'',match_score:.8,demo:false,details:{target_matches:[{target_symbol:'A',activity_count:5,document_count:2,intervention_direction:'inhibit'},{target_symbol:'B',activity_count:1,document_count:1,intervention_direction:'activate'}]}};
it('keeps upstream direction separate from demonstrated compound effect',()=>{
 const p=moleculeValidation(base,'A');expect(p.purpose).toBe('机制验证备选分子');expect(p.effect).toContain('上游建议抑制');expect(p.effect).toContain('仍需确认');expect(p.step).toContain('选择性');
 const b=moleculeValidation(base,'B');expect(b.targetName).toBe('B');expect(b.effect).toContain('激活');expect(b.step).toContain('复测');
});
it('does not call a phase or approval record proof of disease efficacy',()=>{
 const p=moleculeValidation({...base,details:{max_phase:4}});expect(p.purpose).toBe('药物参照');expect(p.roleBasis).toContain('不代表本病');expect(p.records).toBe(false);expect(p.step).toContain('核实');
 expect(moleculeValidation({...base,details:{max_phase:2}}).purpose).toBe('临床研究参照');
});
it('prioritizes regulatory review and does not interpret missing activity as support',()=>{
 const p=moleculeValidation({...base,details:{withdrawn_flag:true}});expect(p.flagged).toBe(true);expect(p.step).toContain('监管');
 const missing=moleculeValidation({...base,details:{target_matches:[{target_symbol:'A',activity_count:0}]}});expect(missing.purpose).toBe('待核实分子');expect(missing.effect).toContain('方向未确认');
 expect(moleculeValidation({...base,demo:true}).purpose).toBe('演示分子');
});
