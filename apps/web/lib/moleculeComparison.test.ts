import {expect,it} from 'vitest';
import type {Compound} from '@drug-discovery/contracts';
import {comparableExperiments} from './moleculeComparison';
const make=(value:string,units='nM',relation='=',assay='A',endpoint='IC50'):Compound=>({id:value,name:'x',identifier:'CHEMBL1',demo:false,mechanism:'',match_score:0,details:{target_matches:[{target_symbol:'T',experimental_records:[{standard_type:endpoint,standard_value:value,standard_units:units,standard_relation:relation,assay_chembl_id:assay}]}]}});
it('normalizes units only within an identical assay and endpoint',()=>{
 const groups=comparableExperiments([make('10'),make('0.02','uM'),make('30')],'T','IC50');
 expect(groups[0].values.map(x=>x.median)).toEqual([10,20,30]);
});
it('does not rank censored, different-assay, different-endpoint or missing records',()=>{
 for(const other of [make('10','nM','<'),make('10','nM','=','B'),make('10','nM','=','A','Kd'),make('0'),make('Infinity'),make('10','mg/mL')])expect(comparableExperiments([make('20'),other],'T','IC50')).toEqual([]);
 expect(comparableExperiments([make('10'),make('20')],'OTHER','IC50')).toEqual([]);
});
