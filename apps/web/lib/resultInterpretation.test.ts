import {describe,it,expect} from 'vitest';
import type {Compound} from '@drug-discovery/contracts';
import type {QsarResult} from '../components/QsarPanel';
import {interpretCompound} from './resultInterpretation';
import {buildCandidateDecisionSupport} from './decisionSupport';
const c:Compound={id:'c',identifier:'CHEMBL1',name:'C',mechanism:'',match_score:.9,demo:false,details:{target_matches:[{target_symbol:'ACE',document_count:3,assay_count:4,pchembl_median:10,activity_types:['IC50','Ki']}]},evaluation:{efficacy:.9,safety:.9,developability:.9,admet:{}}};
const q=(status='validated'):QsarResult=>({version:'v',models:[{symbol:'ACE',status,predictions:[{compound_id:'c',identifier:'CHEMBL1',name:'C',status:'available',predicted:7,observed:9,domain:'outside',similarity:.4}]}]});
describe('data-grounded result interpretation',()=>{
 it('reports mixed endpoints, same-endpoint discrepancy and domain without using pooled activity as IC50',()=>{const r=interpretCompound(c,'ACE',q());expect(r.issues.join()).toContain('IC50 / Ki');expect(r.issues.join()).toContain('2.00 pIC50');expect(r.issues.join()).not.toContain('3.00 pIC50');expect(r.issues.join()).toContain('适用域外');});
 it('never uses failed models or a different molecule result',()=>{expect(interpretCompound(c,'ACE',q('review')).support.join()).not.toContain('QSAR pIC50');const wrong=q();wrong.models[0].predictions[0].compound_id='other';expect(interpretCompound(c,'ACE',wrong).support.join()).not.toContain('QSAR pIC50');});
 it('does not treat four proxy scores as complete property assessment',()=>{const rank={id:'r',rank:1,total_score:.95,decision:'pending' as const,rationale:'',weights:{},compound:c};expect(buildCandidateDecisionSupport(rank,[rank]).action).toBe('hold');expect(buildCandidateDecisionSupport(rank,[rank]).missing).toContain('完整 ADMET / 结构警示');});
 it('keeps demo out of scientific interpretation',()=>{expect(interpretCompound({...c,demo:true},'ACE',q()).support).toEqual(['仅用于展示流程']);});
});
