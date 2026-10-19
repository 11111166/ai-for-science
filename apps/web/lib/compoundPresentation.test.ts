import {describe,it,expect} from 'vitest';
import {displayMoleculeName,diseaseLabel,identitySummary,mechanismLabel,isBiologic,moleculeTypeLabel} from './compoundPresentation';
describe('compound presentation evidence boundaries',()=>{
 it('recognizes antibody records and translates their exact labels',()=>{expect(isBiologic('Antibody')).toBe(true);expect(isBiologic('Small molecule')).toBe(false);expect(moleculeTypeLabel('Antibody')).toBe('抗体');expect(displayMoleculeName('CHEMBL3039540','ADUCANUMAB')).toContain('阿杜单抗');expect(diseaseLabel('Dementia')).toBe('痴呆 · Dementia');expect(mechanismLabel('Amyloid-beta A4 protein binding agent')).toContain('Aβ');});
 it('adds only verified identity names, never guesses by a similar name',()=>{expect(displayMoleculeName('CHEMBL289556','OMAPATRILAT')).toContain('奥马曲拉');expect(displayMoleculeName('CHEMBL9','OMAPATRILAT salt')).toBe('研发分子 · OMAPATRILAT salt');});
 it('keeps original indication and unknown mechanism available',()=>{expect(diseaseLabel('Renal insufficiency')).toBe('肾功能不全 · Renal insufficiency');expect(diseaseLabel('unknown condition')).toBe('unknown condition');expect(mechanismLabel('unmapped mechanism')).toBe('unmapped mechanism');});
 it('clinical stage is a research record, not approval or efficacy',()=>{const s=identitySummary({status:'available',warnings:[],max_phase:3,mechanisms:[{mechanism_of_action:'Beta-secretase 1 inhibitor'}]});expect(s).toContain('临床研究');expect(s).toContain('BACE1');expect(s).not.toContain('获批');expect(identitySummary({status:'available',warnings:[],max_phase:4})).toContain('获批记录');});
});
it('covers the reported molecules and exact preferred-name fallbacks',()=>{
 expect(displayMoleculeName('CHEMBL149082','TRAMIPROSATE')).toContain('高牛磺酸');
 expect(displayMoleculeName('CHEMBL1743004','CRENEZUMAB')).toContain('克瑞珠单抗');
 expect(displayMoleculeName('CHEMBL1743025','GANTENERUMAB')).toContain('甘特鲁单抗');
 expect(displayMoleculeName('CHEMBL1743072','SOLANEZUMAB')).toContain('索拉珠单抗');
 expect(displayMoleculeName('CHEMBL999','GEFITINIB')).toContain('吉非替尼');
 expect(displayMoleculeName('CHEMBL999','GEFITINIB DERIVATIVE')).not.toContain('吉非替尼');
 expect(displayMoleculeName('CHEMBL999','CHEMBL999')).toBe('研究化合物 · CHEMBL999');
});
