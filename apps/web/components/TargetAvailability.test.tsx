// @vitest-environment jsdom
import React, {act} from 'react';
import {createRoot} from 'react-dom/client';
import {expect, it, vi} from 'vitest';
import {TargetsView} from './ProjectWorkspace';
import {api} from '@/lib/api';
vi.mock('@/lib/api', () => ({api: vi.fn(), apiBase: '/api'}));
it('blocks missing coverage, allows retry and only runs a supported disease', async () => {
  Object.assign(globalThis, {IS_REACT_ACT_ENVIRONMENT: true});
  vi.useFakeTimers();
  const host=document.createElement('div'); document.body.append(host); const root=createRoot(host);
  const run=vi.fn(), setStrategy=vi.fn();
  const props={projectDisease:'2 型糖尿病', diseaseId:'MONDO_0005148',targets:[],result:null,strategy:'novel' as const,setStrategy,busy:'',run,toggle:vi.fn(),selectTargets:vi.fn()};
  const button=(text:string)=>[...host.querySelectorAll('button')].find(b=>b.textContent?.includes(text))!;
  try {
    vi.mocked(api).mockResolvedValue({query:'MONDO_0005148',available:false,code:'NOVEL_TARGET_DISEASE_NOT_FOUND',message:'尚未接入多组学数据',diseases:[{id:'MONDO_0008383',name:'类风湿关节炎'}]});
    await act(async()=>root.render(<TargetsView {...props}/>));
    expect(button('检查数据中').disabled).toBe(true);
    await act(async()=>vi.advanceTimersByTimeAsync(160));
    expect(button('当前疾病暂未就绪').disabled).toBe(true);
    expect(host.textContent).toContain('当前支持：类风湿关节炎');
    await act(async()=>button('当前疾病暂未就绪').click());expect(run).not.toHaveBeenCalled();
    await act(async()=>button('转到已有证据靶点').click());expect(setStrategy).toHaveBeenCalledWith('established');
    vi.mocked(api).mockRejectedValueOnce(new Error('offline'));
    await act(async()=>button('重新检查').click());
    await act(async()=>vi.advanceTimersByTimeAsync(160));
    expect(host.textContent).toContain('暂时无法检查');
    vi.mocked(api).mockResolvedValue({query:'MONDO_0008383',available:true,code:'AVAILABLE',message:'已就绪',diseases:[]});
    await act(async()=>root.render(<TargetsView {...props} diseaseId="MONDO_0008383" projectDisease="类风湿关节炎"/>));
    expect(button('检查数据中').disabled).toBe(true);
    await act(async()=>vi.advanceTimersByTimeAsync(160));
    expect(button('运行靶点发现').disabled).toBe(false);
    await act(async()=>button('运行靶点发现').click());expect(run).toHaveBeenCalledOnce();
  } finally {await act(async()=>root.unmount());host.remove();vi.useRealTimers();}
});

it('labels public evidence exploration separately without claiming multimodal results', async () => {
  Object.assign(globalThis, {IS_REACT_ACT_ENVIRONMENT: true});
  vi.useFakeTimers();
  const host=document.createElement('div'); const root=createRoot(host);
  vi.mocked(api).mockResolvedValue({query:'MONDO_TEST',available:true,code:'AVAILABLE',message:'公共证据探索已就绪；未运行专属单细胞模型。',diseases:[]});
  const target={id:'t',job_id:'j',selected:false,note:'',demo:false,symbol:'GENE',name:'Gene',score:.36,derived_score:undefined,evidence_coverage:.2,direction:'not_available',evidence_breakdown:{registry_version:'v1',discovery_strategy:'novel' as const,exploration:{summary:'遗传证据支持',gaps:'方向未验证',next_step:'独立实验验证',support_count:1,channels:['人类遗传'],clinical_label:'未见本病临床记录'}}};
  try {
    await act(async()=>root.render(<TargetsView diseaseId="MONDO_TEST" projectDisease="测试疾病" targets={[target]} result={null} strategy="novel" setStrategy={vi.fn()} busy="" run={vi.fn()} toggle={vi.fn()} selectTargets={vi.fn()}/>));
    await act(async()=>vi.advanceTimersByTimeAsync(160));
    expect(host.textContent).toContain('公共证据探索已就绪');
    expect(host.textContent).toContain('待验证候选');
    expect(host.textContent).toContain('非药物证据分');
    expect(host.textContent).not.toContain('相对新颖程度');
    expect(host.textContent).toContain('方向未验证');
  } finally {await act(async()=>root.unmount());vi.useRealTimers();}
});
