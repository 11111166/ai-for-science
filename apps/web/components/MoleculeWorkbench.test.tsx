// @vitest-environment jsdom
import React,{act} from 'react';
import {createRoot} from 'react-dom/client';
import {expect,it,vi} from 'vitest';
import {MoleculeWorkbench} from './MoleculeWorkbench';
import {api} from '@/lib/api';
vi.mock('@/lib/api',()=>({api:vi.fn(),apiBase:"/api"}));
it('shows persisted validation outcomes and restores the actual conditions for editing',async()=>{
 Object.assign(globalThis,{IS_REACT_ACT_ENVIRONMENT:true});
 vi.mocked(api).mockImplementation(async(path)=>path.endsWith('/tasks')?[{id:'t',molecule:'A',target:'T',endpoint:'IC50',unit:'nM',criterion:'<=',threshold:10,model:'独立实验',status:'completed',measured:5,predicted:8,notes:'复测',outcome:'达到本项阈值'}]:[]);
 const host=document.createElement('div');document.body.append(host);const root=createRoot(host);
 try{await act(async()=>root.render(<MoleculeWorkbench projectId="p" mode="tasks"/>));expect(host.textContent).toContain('达到本项阈值');expect(host.textContent).toContain('差值 -3.00');const edit=[...host.querySelectorAll('button')].find(b=>b.textContent==='回填 / 编辑')!;await act(async()=>edit.click());expect(host.querySelector<HTMLInputElement>('input[list]')!.value).toBe('A');expect(host.textContent).toContain('保存实验结果');expect(host.querySelector<HTMLAnchorElement>('a[download]')!.href).toContain('task-results-template');}finally{await act(async()=>root.unmount());host.remove();}
});
