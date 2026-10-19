// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, expect, it, vi } from 'vitest';
import { MoleculeDetails } from './MoleculeDetails';
import type { Compound } from '@drug-discovery/contracts';
vi.mock('@/lib/moleculeRequest',()=>({moleculeRequest:vi.fn(async(path:string)=>path.endsWith('/identity') ? {status:'available',name:'STAUROSPORINE',molecular_formula:'C28H26N4O3',molecular_weight:466.54,smiles:'CC',cas_numbers:['62996-74-1']} : {status:'available',name:'STAUROSPORINE',max_phase:null,indications:[],mechanisms:[],warnings:[]})}));
const compound={id:'test',identifier:'CHEMBL388978',name:'STAUROSPORINE',mechanism:'',demo:false,match_score:0,details:{target_matches:[{target_symbol:'EGFR',activity_count:5,assay_count:3,document_count:2}]}} as Compound;
const container=document.createElement('div');document.body.append(container);
let root:ReturnType<typeof createRoot>;
afterEach(async()=>{await act(async()=>root?.unmount());});
async function toggle(el:HTMLDetailsElement,open:boolean){await act(async()=>{el.open=open;el.dispatchEvent(new Event('toggle',{bubbles:true}));});}
it('keeps one identity and the research dossier through repeated nested toggles',async()=>{
  Object.assign(globalThis,{IS_REACT_ACT_ENVIRONMENT:true});
  root=createRoot(container);
  await act(async()=>root.render(<MoleculeDetails compound={compound}/>));
  const outer=container.querySelector('details')!;
  await toggle(outer,true);
  for(let i=0;i<3;i++){
    expect(container.querySelectorAll('.compound-identity')).toHaveLength(1);
    const dossier=container.querySelector<HTMLDetailsElement>('.drug-dossier')!;
    expect(dossier).not.toBeNull();
    await toggle(dossier,true);
    expect(dossier.textContent).toContain('EGFR');
    expect(dossier.textContent).toContain('5 条活性记录');
    expect(dossier.textContent).toContain('研究化合物');
    await toggle(dossier,false);
    expect(outer.open).toBe(true);
    expect(container.querySelectorAll('.compound-identity')).toHaveLength(1);
    await toggle(outer,false);
    expect(container.querySelectorAll('.compound-identity')).toHaveLength(0);
    await toggle(outer,true);
  }
});

it('preserves additional indications and risk signals behind concise disclosures',async()=>{
  const {moleculeRequest}=await import('@/lib/moleculeRequest');
  vi.mocked(moleculeRequest).mockImplementation(async(path:string)=>path.endsWith('/identity') ? {status:'available',name:'test'} : {status:'available',name:'test',warnings:[],max_phase:3,black_box_warning:true,indications:Array.from({length:5},(_,i)=>({efo_term:`Condition ${i}`,max_phase_for_ind:2})),mechanisms:[]} as never);
  root=createRoot(container);
  await act(async()=>root.render(<MoleculeDetails compound={compound}/>));
  await toggle(container.querySelector('details')!,true);
  const dossier=container.querySelector<HTMLDetailsElement>('.drug-dossier')!;
  await toggle(dossier,true);
  expect(dossier.querySelector('.drug-indications')?.children).toHaveLength(3);
  const more=[...dossier.querySelectorAll('details')].find(d=>d.querySelector('summary')?.textContent?.includes('更多适应症'))!;
  expect(more.open).toBe(false);
  expect(dossier.textContent).toContain('黑框警告');
  await toggle(more,true);
  expect(more.querySelectorAll('li')).toHaveLength(2);
  expect(container.querySelectorAll('.compound-identity')).toHaveLength(1);
  expect(dossier.open).toBe(true);
});
