// @vitest-environment jsdom
import React,{act} from 'react';
import {createRoot} from 'react-dom/client';
import {expect,it} from 'vitest';
import type {Compound} from '@drug-discovery/contracts';
import {MoleculeValidation} from './MoleculeValidation';
it('switches the complete validation plan with its selected target',async()=>{
 Object.assign(globalThis,{IS_REACT_ACT_ENVIRONMENT:true});
 const host=document.createElement('div');document.body.append(host);const root=createRoot(host);
 const compound:Compound={id:'x',identifier:'CHEMBL1',name:'test',mechanism:'',match_score:0,demo:false,details:{target_matches:[{target_symbol:'A',activity_count:1,document_count:2,intervention_direction:'inhibit'},{target_symbol:'B',activity_count:2,document_count:1,intervention_direction:'activate'}]}};
 try {
  await act(async()=>root.render(<MoleculeValidation compound={compound}/>));
  const details=host.querySelector('details')!;
  await act(async()=>{details.open=true;details.dispatchEvent(new Event('toggle'));});
  expect(host.textContent).toContain('上游建议抑制 A');
  const select=host.querySelector('select')!;
  await act(async()=>{select.value='B';select.dispatchEvent(new Event('change',{bubbles:true}));});
  expect(host.textContent).toContain('上游建议激活 B');expect(host.textContent).not.toContain('上游建议抑制 A');expect(host.textContent).toContain('优先复测');
 } finally {await act(async()=>root.unmount());host.remove();}
});
