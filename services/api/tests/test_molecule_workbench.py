import pytest
from pydantic import ValidationError
from app.molecule_workbench import chemistry, parse_import, TaskInput, task_result, ValidationTask

def test_import_keeps_stereo_and_salts_and_flags_duplicates():
    rows = parse_import(b'name,smiles\none,CCO\ntwo,OCC\nsalt,CC(=O)[O-].[Na+]\ninvalid,XYZ\n', 'm.csv')
    assert len(rows) == 4
    assert rows[1]['duplicate']
    assert rows[2]['fragments'] == 2
    assert rows[3]['error']
    assert chemistry('C[C@H](O)F')[1]['smiles'] != chemistry('C[C@@H](O)F')[1]['smiles']

def test_scaffold_not_shared_for_acyclic_and_mass():
    assert chemistry('CCO')[1]['scaffold'] == ''
    assert chemistry('c1ccccc1O')[1]['scaffold'] == chemistry('c1ccccc1C')[1]['scaffold']
    assert chemistry('CCO')[1]['weight'] == 46.07

def test_completed_requires_measurement_and_valid_units():
    values=dict(molecule='A',target='T',endpoint='IC50',unit='nM',criterion='<=',threshold=10,model='assay A')
    with pytest.raises(ValidationError): TaskInput(**values,status='completed')
    with pytest.raises(ValidationError): TaskInput(**{**values,'unit':'%'})
    for value in [float('nan'),float('inf'),-1]:
        with pytest.raises(ValidationError): TaskInput(**values,measured=value)
    row=ValidationTask(id='x',project_id='p',data=TaskInput(**values,status='completed',measured=20).model_dump())
    assert '未达' in task_result(row)['outcome']
    row.data={**row.data,'measured':5}
    assert '达到' in task_result(row)['outcome']


def test_project_routes_persistence_permissions_and_search(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.database import Base, get_db
    from app.auth import get_current_user, UserContext
    from app.models import Project, ProjectMember
    from app.molecule_workbench import router
    engine=create_engine(f'sqlite:///{tmp_path}/test.db',connect_args={'check_same_thread':False})
    Base.metadata.create_all(engine)
    factory=sessionmaker(engine,expire_on_commit=False)
    with factory() as db:
        db.add_all([Project(id='p',name='P',disease='D',owner_id='owner'),Project(id='other',name='O',disease='D',owner_id='other')]);db.commit()
        db.add(ProjectMember(project_id='p',user_id='viewer',role='viewer'));db.commit()
    app=FastAPI();app.include_router(router)
    def connection():
        with factory() as db: yield db
    app.dependency_overrides[get_db]=connection
    app.dependency_overrides[get_current_user]=lambda:UserContext('owner','owner','researcher')
    client=TestClient(app);url='/api/v1/drug-discovery/projects/p/workbench'
    file={'file':('m.csv',b'name,smiles\nphenol,Oc1ccccc1\nethanol,CCO\n','text/csv')}
    assert client.post(url+'/import',files=file).json()['accepted']==2
    assert client.get(url+'/molecules').json()==[]
    assert client.post(url+'/import?commit=true',files=file).json()['saved']==2
    assert client.post(url+'/import?commit=true',files=file).json()['saved']==0
    rows=client.get(url+'/molecules').json();assert len(rows)==2
    result=client.post(url+'/search',json={'smiles':'Cc1ccccc1','mode':'scaffold'}).json()
    assert len(result['items'])==1 and result['items'][0]['name']=='phenol'
    assert '<svg' in rows[0]['svg']
    assert client.post(url+'/search',json={'smiles':'INVALID'}).status_code==422
    data=dict(molecule='phenol',target='T',endpoint='IC50',unit='nM',criterion='<=',threshold=10,model='test')
    task=client.post(url+'/tasks',json=data).json()
    template=client.get(url+'/task-results-template')
    assert task['id'] in template.text and 'attachment' in template.headers['content-disposition']
    saved=client.put(url+'/tasks/'+task['id'],json={**data,'status':'completed','measured':5})
    assert '达到' in saved.json()['outcome']
    assert client.get(url+'/tasks').json()[0]['measured']==5
    feedback={'file':('results.csv',('task_id,measured,notes\n'+task['id']+',15,repeat\nnot-ours,2,no\n').encode(),'text/csv')}
    assert client.post(url+'/task-results',files=feedback).json()['accepted']==1
    assert client.get(url+'/tasks').json()[0]['measured']==5
    assert client.post(url+'/task-results?commit=true',files=feedback).json()['saved']==1
    assert client.get(url+'/tasks').json()[0]['measured']==15
    # 要求 27：权限全面放开，任何账号都能访问任意项目，写操作也不再按角色拦截
    assert client.get(url.replace('/p/','/other/')+'/tasks').status_code==200
    app.dependency_overrides[get_current_user]=lambda:UserContext('viewer','viewer','researcher')
    assert client.get(url+'/molecules').status_code==200
    assert client.post(url+'/import?commit=true',files=file).status_code==200
    assert client.post(url+'/tasks',json=data).status_code==200
