import { describe,it,expect } from 'vitest';
import type { Compound } from '@drug-discovery/contracts';
import { commonTargets, indicationRelation, phaseLabel, toggleComparison } from './drugDossier';
const c=(targets:string[]):Compound=>({id:'x',identifier:'CHEMBL1',name:'x',mechanism:'',match_score:0,demo:false,details:{target_matches:targets.map(target_symbol=>({target_symbol}))}});
describe('drug comparison evidence boundaries',()=>{
 it('compares only targets shared by every selected molecule',()=>{expect(commonTargets([c(['A','B']),c(['B','C']),c(['B'])])).toEqual(['B']);expect(commonTargets([c(['A']),c(['B'])])).toEqual([]);});
 it('caps at five and permits removal then replacement',()=>{let ids=['1','2','3','4','5'];expect(toggleComparison(ids,'6')).toEqual(ids);ids=toggleComparison(ids,'3');expect(toggleComparison(ids,'6')).toEqual(['1','2','4','5','6']);});
 it('never turns global approval or disease-name resemblance into indication approval',()=>{expect(phaseLabel(null)).toContain('未明');expect(indicationRelation({status:'available',warnings:[],max_phase:4,indications:[{efo_id:'EFO_0001',max_phase_for_ind:2}]},'MONDO_0001')).toContain('尚未确认');expect(indicationRelation({status:'available',warnings:[],indications:[{efo_id:'http://www.ebi.ac.uk/efo/EFO_0001'}]},'EFO:0001')).toContain('一致');});
});
