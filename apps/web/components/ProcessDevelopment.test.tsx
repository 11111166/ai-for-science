// @vitest-environment jsdom
import React,{act} from 'react';
import {createRoot} from 'react-dom/client';
import {expect,it,vi} from 'vitest';
import {ProcessDevelopment} from './ProcessDevelopment';
import {api} from '@/lib/api';
vi.mock('@/lib/api',()=>({api:vi.fn(),apiBase:'/api'}));
it('keeps proposals separate from saved experiments until explicitly confirmed',async()=>{
 Object.assign(globalThis,{IS_REACT_ACT_ENVIRONMENT:true});
 const s={id:'s',revision:1,spec:{name:'结晶研究',molecule:'A',target_state:'solid',target_amount:100,target_unit:'g',target_specification:'纯度≥98%',step:'结晶',context:'固定批次',factors:[{name:'温度',unit:'C',kind:'numeric',levels:['20','30']}],objectives:[{name:'收率',unit:'%',direction:'max',low:0,high:90}],experiment_budget:12,batch_size:4,confirmation_repeats:2},experiments:[],plans:[],analysis:{completed:0,unique:0,planned:0,failed:0,best:null,met:0,pareto_ids:[],repeats:[],factor_insights:[{factor:'温度',status:'覆盖不足',message:'暂无',best_level:null,effect:null,levels:[]}],guidance:{stage:'建立基线',decision:'开始首批覆盖实验',action:'生成4组',why:'暂无实测',defer:'暂缓判断',budget:12,used:0,committed:0,remaining:12,batch_size:4,confirmation_repeats:2,best_repeat:0,model_minimum:6},conclusion:'先建立基线',next_step:'生成首批'}};
 const suggestion={conditions:{温度:'20'},purpose:'补齐条件覆盖',reason:'未考察区域',prediction:null};
 vi.mocked(api).mockImplementation(async(path,init)=>{if(path.endsWith('/plan')){const body=JSON.parse(String(init?.body));return {proposal:{model:'空间覆盖设计',training_conditions:0,items:[suggestion],limitations:'未验证',remaining:1},study:body.commit?{...s,revision:2,experiments:[{id:'e',conditions:suggestion.conditions,results:{},status:'planned',notes:'',batch:'第1轮'}],analysis:{...s.analysis,planned:1}}:s};}return [s];});
 const host=document.createElement('div');document.body.append(host);const root=createRoot(host);
 const button=(name:string)=>[...host.querySelectorAll('button')].find(b=>b.textContent===name)!;
 try{await act(async()=>root.render(<ProcessDevelopment projectId="p"/>));expect(host.textContent).toContain('固体 · 100 g');expect(host.textContent).toContain('纯度≥98%');await act(async()=>button('生成下一批建议').click());expect(host.textContent).toContain('暂不预测数值');expect(host.textContent).toContain('已排待做 0 次');await act(async()=>button('确认保存 1 组待实验').click());expect(host.textContent).toContain('待执行 1');expect(host.textContent).toContain('回填 / 编辑 #1');expect(host.querySelector('a[download]')?.getAttribute('href')).toContain('/studies/s/csv');}finally{await act(async()=>root.unmount());host.remove();}
});

it('offers compatible target units and liquid/gas process steps',async()=>{
 Object.assign(globalThis,{IS_REACT_ACT_ENVIRONMENT:true});vi.mocked(api).mockResolvedValue([]);
 const host=document.createElement('div');document.body.append(host);const root=createRoot(host);
 try{
  await act(async()=>root.render(<ProcessDevelopment projectId="p"/>));
  await act(async()=>[...host.querySelectorAll('button')].find(x=>x.textContent==='新建工艺研究')!.click());
  expect(host.textContent).toContain('树脂反应');
  await act(async()=>[...host.querySelectorAll('button')].find(x=>x.textContent?.startsWith('树脂反应'))!.click());
  expect((host.querySelector('input[aria-label="因素1名称"]') as HTMLInputElement).value).toBe('树脂负载');
  const system=host.querySelector('select[aria-label="反应体系"]') as HTMLSelectElement;
  await act(async()=>{system.value='液体-树脂';system.dispatchEvent(new Event('change',{bubbles:true}));});
  expect(system.value).toBe('液体-树脂');
  const state=host.querySelector('select[aria-label="目标物态"]') as HTMLSelectElement;
  await act(async()=>{state.value='gas';state.dispatchEvent(new Event('change',{bubbles:true}));});
  const unit=host.querySelector('select[aria-label="目标数量单位"]') as HTMLSelectElement;
  expect([...unit.options].map(x=>x.value)).toEqual(expect.arrayContaining(['NL','Nm³','mol','kg']));
  await act(async()=>[...host.querySelectorAll('button')].find(x=>x.textContent?.startsWith('气体收集'))!.click());
  expect((host.querySelector('input[aria-label="因素3名称"]') as HTMLInputElement).value).toBe('载气流量');
  await act(async()=>{state.value='liquid';state.dispatchEvent(new Event('change',{bubbles:true}));});
  expect([...unit.options].map(x=>x.value)).toEqual(expect.arrayContaining(['mL','L','kg','mol']));
 }finally{await act(async()=>root.unmount());host.remove();}
});
