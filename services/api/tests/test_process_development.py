import copy
import pytest
from pydantic import ValidationError
from fastapi import HTTPException
from app.process_development import StudyInput,Experiment,checked_experiment,recommend,analysis,router

SPEC={'name':'步骤A','molecule':'测试分子','target_state':'solid','target_amount':100,'target_unit':'g','target_specification':'纯度≥98%','step':'反应','context':'固定规模和检测方法；全部组合已确认可执行','factors':[{'name':'温度','unit':'°C','kind':'numeric','levels':['20','30','40','50']},{'name':'介质','unit':'','kind':'category','levels':['A','B','C']}],'objectives':[{'name':'收率','unit':'%','direction':'max','low':0,'high':90},{'name':'杂质','unit':'%','direction':'min','low':1,'high':10}]}
def data():return {'spec':StudyInput(**SPEC).model_dump(),'experiments':[],'plans':[]}
def measured(d,n=8):
    from itertools import product
    for i,(t,s) in enumerate(list(product(['20','30','40','50'],['A','B','C']))[:n]):
        d['experiments'].append({'id':str(i),'conditions':{'温度':t,'介质':s},'results':{'收率':40+i*5,'杂质':8-i*.5},'status':'completed','notes':'','batch':'B'})

def test_validation_finite_levels_and_fixed_ranges():
    s=copy.deepcopy(SPEC);s['factors'][0]['levels']=['1','1.0']
    with pytest.raises(ValidationError):StudyInput(**s)
    s=copy.deepcopy(SPEC);s['objectives'][0]['high']=float('nan')
    with pytest.raises(ValidationError):StudyInput(**s)
    for conditions in [{'温度':'90','介质':'A'},{'温度':'20'}]:
        with pytest.raises(HTTPException):checked_experiment(data()['spec'],Experiment(conditions=conditions))
    with pytest.raises(HTTPException):checked_experiment(data()['spec'],Experiment(conditions={'温度':'20','介质':'A'},status='completed',results={'收率':80}))
    with pytest.raises(HTTPException):checked_experiment(data()['spec'],Experiment(conditions={'温度':'20','介质':'A'},status='failed'))

@pytest.mark.parametrize(('state','amount','unit'),[('solid',100,'g'),('liquid',2.5,'L'),('gas',500,'NL')])
def test_synthesis_target_supports_solid_liquid_and_gas(state,amount,unit):
    spec=copy.deepcopy(SPEC);spec.update(target_state=state,target_amount=amount,target_unit=unit)
    parsed=StudyInput(**spec)
    assert parsed.target_state==state and parsed.target_amount==amount and parsed.target_unit==unit

def test_synthesis_target_rejects_state_unit_mismatch():
    spec=copy.deepcopy(SPEC);spec.update(target_state='solid',target_unit='NL')
    with pytest.raises(ValidationError):StudyInput(**spec)

def test_process_system_supports_liquid_resin_and_resin_reaction():
    spec=copy.deepcopy(SPEC);spec.update(process_system='液体-树脂',step='树脂反应')
    parsed=StudyInput(**spec)
    assert parsed.process_system=='液体-树脂' and parsed.step=='树脂反应'

def test_cold_start_no_invented_predictions_no_duplicate_plans():
    d=data();p=recommend(d,6)
    assert len(p['items'])==6 and p['model']=='空间覆盖设计'
    assert all(r['prediction'] is None for r in p['items'])
    assert len({tuple(r['conditions'].items()) for r in p['items']})==6
    for i,r in enumerate(p['items']):d['experiments'].append({'id':str(i),'conditions':r['conditions'],'status':'planned','results':{},'notes':'','batch':'A'})
    nxt=recommend(d,6)
    assert not {tuple(r['conditions'].items()) for r in p['items']}&{tuple(r['conditions'].items()) for r in nxt['items']}
    a=analysis(d)
    assert a['guidance']['stage']=='批次执行' and a['guidance']['budget']==24

def test_gp_batch_repeats_pareto_and_failed_not_zero():
    d=data();measured(d)
    p=recommend(d,4)
    assert p['model']=='高斯过程辅助优化' and p['training_conditions']==8
    assert p['items'][0]['purpose']=='确认重复性'
    assert any(r['prediction'] for r in p['items'])
    d['experiments'].append({'id':'fail','conditions':{'温度':'50','介质':'C'},'results':{},'status':'failed','notes':'中止','batch':'B'})
    assert recommend(d,4)['training_conditions']==8
    a=analysis(d);assert a['failed']==1 and a['met']==0 and a['pareto_ids']==['7']
    assert a['guidance']['stage']=='模型优化'
    assert any(i['factor']=='温度' and i['levels'] for i in a['factor_insights'])
    repeat=copy.deepcopy(d['experiments'][7]);repeat['id']='repeat';repeat['results']['收率']=95;repeat['results']['杂质']=.5;d['experiments'].append(repeat)
    a=analysis(d);assert a['met']==1 and a['repeats'][0]['n']==2
    assert a['guidance']['stage']=='候选窗口'

def test_authorization_revision_csv_atomicity_and_persistence(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.database import Base,get_db
    from app.auth import get_current_user,UserContext
    from app.models import Project,ProjectMember
    engine=create_engine(f'sqlite:///{tmp_path}/test.db',connect_args={'check_same_thread':False});Base.metadata.create_all(engine)
    factory=sessionmaker(engine,expire_on_commit=False)
    with factory() as db:
        db.add_all([Project(id='p',name='P',disease='D',owner_id='owner'),Project(id='q',name='Q',disease='D',owner_id='other')]);db.commit();db.add(ProjectMember(project_id='p',user_id='viewer',role='viewer'));db.commit()
    app=FastAPI();app.include_router(router)
    def connection():
        with factory() as db:yield db
    app.dependency_overrides[get_db]=connection;app.dependency_overrides[get_current_user]=lambda:UserContext('owner','owner','researcher')
    c=TestClient(app);base='/api/v1/drug-discovery/projects/p/process';r=c.post(base+'/studies',json=SPEC);assert r.status_code==200,r.text
    s=r.json();url=base+'/studies/'+s['id'];assert c.get(base+'/studies').json()[0]['id']==s['id']
    assert s['spec']['experiment_budget']==24 and s['analysis']['guidance']['decision']=='开始首批覆盖实验'
    req={'revision':1,'count':4};assert len(c.post(url+'/plan',json=req).json()['proposal']['items'])==4
    assert c.get(base+'/studies').json()[0]['experiments']==[]
    s=c.post(url+'/plan',json={**req,'commit':True}).json()['study'];assert len(s['experiments'])==4
    assert c.post(url+'/plan',json={**req,'commit':True}).status_code==409
    e=s['experiments'][0];payload={k:e[k] for k in ['id','conditions','results','status','notes','batch']};payload.update(status='completed',results={'收率':90,'杂质':.8})
    s=c.post(url+'/experiments',json={'revision':s['revision'],'experiment':payload}).json();assert s['analysis']['met']==1
    template=c.get(url+'/csv');assert 'attachment' in template.headers['content-disposition']
    raw=template.content;assert c.post(url+'/import?revision=3',files={'file':('a.csv',raw)}).json()['can_save']
    bad=raw.replace(b'completed',b'invalid');preview=c.post(url+'/import?revision=3',files={'file':('a.csv',bad)});assert not preview.json()['can_save']
    assert c.post(url+'/import?revision=3&commit=true',files={'file':('a.csv',bad)}).status_code==422
    assert c.get(base+'/studies').json()[0]['revision']==3
    assert c.post(url+'/import?revision=3&commit=true',files={'file':('a.csv',raw)}).json()['study']['revision']==4
    other='/api/v1/drug-discovery/projects/q/process/studies/'+s['id']+'/csv';assert c.get(other).status_code in (403,404)
    app.dependency_overrides[get_current_user]=lambda:UserContext('viewer','viewer','researcher')
    assert c.get(base+'/studies').status_code==200
    # 要求 27：权限全面放开，只读角色也能写；这里改成断言放行
    assert c.post(url+'/plan',json={'revision':4,'count':3}).status_code==200
