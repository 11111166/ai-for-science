"""Project-scoped single-step process experiments and reproducible batch planning."""
import csv
import io
import itertools
import math
from datetime import datetime, timezone
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Response
from pydantic import BaseModel, Field, ConfigDict, model_validator
from sqlalchemy import ForeignKey, JSON, String, Integer, select, update
from sqlalchemy.orm import Mapped, Session, mapped_column
from .database import Base, get_db
from .models import new_id, AuditEvent
from .auth import UserContext, get_current_user, require_project_access

router = APIRouter(prefix='/api/v1/drug-discovery/projects/{project_id}/process')

class ProcessStudy(Base):
    __tablename__ = 'process_studies'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey('projects.id'), index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    data: Mapped[dict] = mapped_column(JSON)

class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False, str_strip_whitespace=True)

class Factor(Strict):
    name: str = Field(min_length=1, max_length=40)
    unit: str = Field(default='', max_length=20)
    kind: Literal['numeric','category'] = 'numeric'
    levels: list[str] = Field(min_length=2,max_length=7)
    @model_validator(mode='after')
    def check(self):
        self.levels=[v.strip() for v in self.levels]
        if any(not v or len(v)>80 for v in self.levels): raise ValueError('因素水平不能为空或超过80字')
        if self.kind=='numeric':
            try:
                values=[float(v) for v in self.levels]
                if not all(math.isfinite(v) and abs(v)<=1e9 for v in values): raise ValueError()
                self.levels=[format(v,'.12g') for v in sorted(values)]
            except ValueError: raise ValueError('数值因素须填写有限数字')
        if len(set(self.levels))!=len(self.levels): raise ValueError('因素水平不能重复')
        return self

class Objective(Strict):
    name: str = Field(min_length=1,max_length=40)
    unit: str = Field(min_length=1,max_length=20)
    direction: Literal['max','min'] = 'max'
    low: float
    high: float
    @model_validator(mode='after')
    def check(self):
        if not -1e9<=self.low<self.high<=1e9: raise ValueError('目标低值必须小于高值')
        return self

class StudyInput(Strict):
    name: str = Field(min_length=1,max_length=160)
    molecule: str = Field(min_length=1,max_length=180)
    target_state: Literal['solid','liquid','gas']
    target_amount: float = Field(gt=0,le=1e9)
    target_unit: str = Field(min_length=1,max_length=12)
    target_specification: str = Field(min_length=1,max_length=1000)
    process_system: Literal['均相液体','液固浆料','液体-树脂','气液反应'] = '均相液体'
    step: Literal['反应','树脂反应','后处理','纯化','结晶','蒸馏','气体收集'] = '反应'
    context: str = Field(min_length=1,max_length=3000)
    factors: list[Factor] = Field(min_length=1,max_length=5)
    objectives: list[Objective] = Field(min_length=1,max_length=3)
    experiment_budget: int = Field(default=24,ge=3,le=200)
    batch_size: int = Field(default=6,ge=1,le=12)
    confirmation_repeats: int = Field(default=2,ge=2,le=5)
    @model_validator(mode='after')
    def check(self):
        units={
            'solid':{'mg','g','kg','mmol','mol'},
            'liquid':{'mL','L','mg','g','kg','mmol','mol'},
            'gas':{'mL','L','NL','Nm³','mmol','mol','g','kg'},
        }
        if self.target_unit not in units[self.target_state]:
            raise ValueError('目标数量单位与目标物态不匹配')
        names=[v.name for v in self.factors+self.objectives]
        if len(names)!=len(set(names)) or set(names)&{'experiment_id','status','notes','batch'}: raise ValueError('因素和结果名称不能重复或使用保留字段')
        if math.prod(len(f.levels) for f in self.factors)>4096: raise ValueError('条件组合最多4096种，请减少水平')
        return self

class Experiment(Strict):
    id: str = Field(default='',max_length=36)
    conditions: dict[str,str]
    results: dict[str,float] = Field(default_factory=dict)
    status: Literal['planned','completed','failed'] = 'planned'
    notes: str = Field(default='',max_length=2000)
    batch: str = Field(default='',max_length=80)

class ExperimentRequest(Strict):
    revision: int = Field(ge=1)
    experiment: Experiment

class PlanRequest(Strict):
    revision: int = Field(ge=1)
    count: int = Field(default=6,ge=1,le=12)
    commit: bool = False


def get_study(db,project_id,study_id,user,write=False):
    require_project_access(db,project_id,user,'member' if write else 'viewer')
    study=db.get(ProcessStudy,study_id)
    if not study or study.project_id!=project_id: raise HTTPException(404,'工艺研究不存在')
    return study

def save_data(db,study,data,revision,user,action):
    data={**data,'updated_at':datetime.now(timezone.utc).isoformat()}
    changed=db.execute(update(ProcessStudy).where(ProcessStudy.id==study.id,ProcessStudy.revision==revision).values(data=data,revision=revision+1))
    if changed.rowcount!=1:
        db.rollback();raise HTTPException(409,'记录已更新，请刷新后重新检查')
    db.add(AuditEvent(project_id=study.project_id,actor_id=user.id,action=action,entity_type='process_study',entity_id=study.id,details={'revision':revision+1,'snapshot':data}))
    db.commit();db.refresh(study)

def checked_experiment(spec,experiment):
    e=experiment.model_dump()
    if set(e['conditions'])!={f['name'] for f in spec['factors']}: raise HTTPException(422,'实验条件须包含全部因素')
    for f in spec['factors']:
        value=e['conditions'][f['name']].strip()
        if f['kind']=='numeric':
            try: value=format(float(value),'.12g')
            except ValueError: raise HTTPException(422,f"{f['name']} 需要数值")
        if value not in f['levels']: raise HTTPException(422,f"{f['name']} 不在本研究预定水平中，请另建研究以改变范围")
        e['conditions'][f['name']]=value
    keys={o['name'] for o in spec['objectives']}
    if set(e['results'])-keys: raise HTTPException(422,'包含未定义结果指标')
    if e['status']=='completed' and set(e['results'])!=keys: raise HTTPException(422,'完成实验须填写所有结果指标')
    if e['status']=='failed' and not e['notes']: raise HTTPException(422,'失败实验请说明原因')
    if e['status']=='planned' and e['results']: raise HTTPException(422,'有实测结果时请选择已完成或失败')
    return e

def key_for(spec,c): return tuple(c[f['name']] for f in spec['factors'])

def desirability(spec,results):
    parts=[]
    for o in spec['objectives']:
        v=(results[o['name']]-o['low'])/(o['high']-o['low'])
        parts.append(max(0,min(1,v if o['direction']=='max' else 1-v)))
    return sum(parts)/len(parts)

def meets(spec,results):
    return all(results[o['name']]>=o['high'] if o['direction']=='max' else results[o['name']]<=o['low'] for o in spec['objectives'])

def spec_value(spec,key,default):
    """Keep studies created before process-planner-v2 readable without a data migration."""
    return spec.get(key,default)

def factor_insights(spec,measured):
    insights=[]
    for factor in spec['factors']:
        grouped={}
        for row in measured:
            grouped.setdefault(row['conditions'][factor['name']],[]).append(row)
        levels=[]
        for value,rows in grouped.items():
            levels.append({'value':value,'n':len(rows),'means':{
                o['name']:round(sum(r['results'][o['name']] for r in rows)/len(rows),6)
                for o in spec['objectives']}})
        if len(levels)<2:
            insights.append({'factor':factor['name'],'status':'覆盖不足','message':f"仅覆盖 {len(levels)} 个水平，暂不能比较。",'levels':levels,'best_level':None,'effect':None})
            continue
        def level_score(item): return desirability(spec,item['means'])
        best=max(levels,key=level_score)
        primary=spec['objectives'][0]
        observed=[v['means'][primary['name']] for v in levels]
        effect=round(max(observed)-min(observed),6)
        enough=all(v['n']>=2 for v in levels)
        if abs(effect)<1e-12:
            message=f"{primary['name']}在已覆盖水平间均值接近；综合差异主要来自其他指标。"
        else:
            message=f"按现有实测，{best['value']} {factor['unit']} 的综合表现较好；{primary['name']}的水平间均值差为 {effect:g} {primary['unit']}。"
        insights.append({'factor':factor['name'],'status':'初步信号' if enough else '样本较少',
            'message':message,
            'levels':levels,'best_level':best['value'],'effect':effect})
    return insights

def process_guidance(spec,rows,measured,groups,best,met_count,repeats):
    planned=sum(r['status']=='planned' for r in rows)
    failed=sum(r['status']=='failed' for r in rows)
    used=len(measured)+failed
    budget=spec_value(spec,'experiment_budget',24)
    confirmation=spec_value(spec,'confirmation_repeats',2)
    best_repeat=0
    if best:
        best_repeat=len(groups.get(key_for(spec,best['conditions']),[]))
    model_min=max(6,2*len(spec['factors'])+2)
    if planned:
        stage='批次执行';decision='先回填本轮实验'
        action=f"完成或标记现有 {planned} 条待实验，再更新下一批建议。"
        why='未回填的建议不会进入模型；此时继续排新条件会增加积压。'
        defer='暂缓追加相似条件和扩大考察范围。'
    elif not measured:
        stage='建立基线';decision='开始首批覆盖实验'
        action=f"生成不超过 {spec_value(spec,'batch_size',6)} 组条件，先覆盖因素空间。"
        why='目前没有完整实测，无法比较条件或预测结果。'
        defer='暂缓判断最优条件和因素主效应。'
    elif met_count and best_repeat<confirmation:
        stage='结果确认';decision='复测达标条件'
        action=f"将当前达标条件补到至少 {confirmation} 次独立实验，并记录批次。"
        why='已有条件达到目标，但单次达标不足以证明可重复性。'
        defer='暂缓宣称稳健工艺窗口或进入放大。'
    elif met_count and best_repeat>=confirmation:
        stage='候选窗口';decision='考察边界与放大风险'
        action='围绕已确认条件考察相邻水平，并核对检测方法、物料批次和规模效应。'
        why=f"当前达标条件已有 {best_repeat} 次实测，可作为候选中心点。"
        defer='暂缓继续搜索远离候选中心且缺少机制依据的组合。'
    elif used>=budget:
        stage='范围复核';decision='停止盲目追加实验'
        action='复核目标线、因素范围和失败原因；确认后另建研究或调整工艺假设。'
        why=f"已执行 {used} 条实验，达到设定预算 {budget}，但仍无组合同时达标。"
        defer='暂缓由系统自动扩充同一条件网格。'
    elif len(groups)>=model_min:
        stage='模型优化';decision='做一批改善与降不确定性实验'
        action=f"生成不超过 {spec_value(spec,'batch_size',6)} 组建议，同时保留确认实验。"
        why=f"已有 {len(groups)} 组不同条件，达到本模型最低训练覆盖 {model_min} 组。"
        defer='暂缓只围绕单个最高值连续试验。'
    else:
        stage='补齐覆盖';decision='优先补关键条件'
        action=f"再完成至少 {model_min-len(groups)} 组不同条件，达到可建模的最低覆盖。"
        why=f"目前 {len(groups)} 组不同条件，尚不足以稳定比较多因素组合。"
        defer='暂缓解释因素因果关系或输出精确预测值。'
    return {'stage':stage,'decision':decision,'action':action,'why':why,'defer':defer,
        'budget':budget,'used':used,'committed':planned,'remaining':max(0,budget-used-planned),
        'batch_size':spec_value(spec,'batch_size',6),'confirmation_repeats':confirmation,
        'best_repeat':best_repeat,'model_minimum':model_min}

def analysis(data):
    spec=data['spec']; rows=data['experiments']; measured=[r for r in rows if r['status']=='completed']
    best=max(measured,key=lambda r:desirability(spec,r['results']),default=None)
    groups={}
    for r in measured: groups.setdefault(key_for(spec,r['conditions']),[]).append(r)
    repeats=[]
    for group in groups.values():
        if len(group)>1:
            repeats.append({'conditions':group[0]['conditions'],'n':len(group),'ranges':{o['name']:round(max(r['results'][o['name']] for r in group)-min(r['results'][o['name']] for r in group),6) for o in spec['objectives']}})
    pareto=[]
    def dominates(a,b):
        diffs=[(a['results'][o['name']]-b['results'][o['name']])*(1 if o['direction']=='max' else -1) for o in spec['objectives']]
        return all(d>=0 for d in diffs) and any(d>0 for d in diffs)
    for r in measured:
        if not any(dominates(other,r) for other in measured): pareto.append(r['id'])
    met_count=sum(meets(spec,r['results']) for r in measured)
    return {'completed':len(measured),'failed':sum(r['status']=='failed' for r in rows),'planned':sum(r['status']=='planned' for r in rows),'unique':len(groups),'best':best,'met':met_count,'pareto_ids':pareto,'repeats':repeats,
            'factor_insights':factor_insights(spec,measured),'guidance':process_guidance(spec,rows,measured,groups,best,met_count,repeats),
            'conclusion': '已有实测同时达到全部目标，优先确认重复性与批次稳定性。' if any(meets(spec,r['results']) for r in measured) else '尚无实测同时达到全部目标，继续比较条件并补齐证据。' if measured else '尚无完整实测；先用一批覆盖性实验建立基线。',
            'next_step':'已测条件不能直接视作稳健工艺窗口；确认重复性、检测方法及放大差异。' if measured else '确认全部条件组合可执行后，生成首批实验。'}

def recommend(data,count):
    # Imported only on demand; deployment supplies release-local numpy, no network service.
    import numpy as np
    spec=data['spec']; rows=data['experiments']
    count=min(count,max(0,spec_value(spec,'experiment_budget',24)-len(rows)))
    grid=[dict(zip([f['name'] for f in spec['factors']],v)) for v in itertools.product(*[f['levels'] for f in spec['factors']])]
    def encode(c):
        v=[]
        for f in spec['factors']:
            if f['kind']=='numeric':
                lo,hi=float(f['levels'][0]),float(f['levels'][-1]);v.append((float(c[f['name']])-lo)/(hi-lo))
            else: v.extend([float(c[f['name']]==level)/math.sqrt(2) for level in f['levels']])
        return v
    X=np.array([encode(c) for c in grid]); occupied={key_for(spec,r['conditions']) for r in rows}
    available=[i for i,c in enumerate(grid) if key_for(spec,c) not in occupied]
    completed=[r for r in rows if r['status']=='completed']; groups={}
    for r in completed: groups.setdefault(key_for(spec,r['conditions']),[]).append(r)
    model_ready=len(groups)>=max(6,2*len(spec['factors'])+2)
    prediction=None;uncertainty=None; model='空间覆盖设计'
    if model_ready:
        train=[g[0] for g in groups.values()];T=np.array([encode(r['conditions']) for r in train])
        Y=np.array([[sum(g['results'][o['name']] for g in group)/len(group) for o in spec['objectives']] for group in groups.values()])
        center=Y.mean(axis=0);scale=Y.std(axis=0);scale=np.where(scale<1e-8,1.,scale)
        def kernel(a,b):return np.exp(-((a[:,None,:]-b[None,:,:])**2).sum(axis=2)/(2*.35**2))
        K=kernel(T,T)+np.eye(len(T))*.05
        try:
            cross=kernel(X,T);L=np.linalg.cholesky(K)
            alpha=np.linalg.solve(L.T,np.linalg.solve(L,(Y-center)/scale))
            prediction=cross@alpha*scale+center
            solve=np.linalg.solve(L,cross.T)
            uncertainty=np.sqrt(np.maximum(1-(solve*solve).sum(axis=0),1e-8))[:,None]*scale
            model='高斯过程辅助优化'
        except np.linalg.LinAlgError: model_ready=False
    encoded_existing=[encode(r['conditions']) for r in rows]; selected=[]; origins=[]
    # One explicit confirmation repeat; never hide it as a novel condition.
    best=max(completed,key=lambda r:desirability(spec,r['results']),default=None)
    if best and count>=3 and len(completed)>=4 and not any(r['status']=='planned' and r['conditions']==best['conditions'] for r in rows):
        selected.append({'conditions':best['conditions'],'purpose':'确认重复性','reason':'复测当前综合表现较好的实测条件，确认结果能否重复。','prediction':None});origins.append(encode(best['conditions']))
    for slot in range(count-len(selected)):
        if not available: break
        reference=encoded_existing+origins
        distance=np.min(((X[:,None,:]-np.array(reference)[None,:,:])**2).sum(axis=2),axis=1) if reference else np.ones(len(grid))
        if not reference:
            distance=-((X-.5)**2).sum(axis=1)
        purpose='补齐条件覆盖';scores=distance.copy()
        if model_ready:
            mu=np.zeros(len(grid));sd=np.zeros(len(grid))
            for j,o in enumerate(spec['objectives']):
                v=(prediction[:,j]-o['low'])/(o['high']-o['low']);v=v if o['direction']=='max' else 1-v
                mu+=np.clip(v,0,1)/len(spec['objectives']);sd+=uncertainty[:,j]/(o['high']-o['low'])/len(spec['objectives'])
            explore=slot%3==2
            scores=(sd if explore else mu+.5*sd)+.08*np.sqrt(np.maximum(distance,0));purpose='降低模型不确定性' if explore else '改善多目标表现'
        idx=max(available,key=lambda i:(float(scores[i]),-i));available.remove(idx);origins.append(X[idx].tolist())
        pred={o['name']:{'mean':round(float(prediction[idx,j]),4),'sd':round(float(uncertainty[idx,j]),4)} for j,o in enumerate(spec['objectives'])} if model_ready else None
        selected.append({'conditions':grid[idx],'purpose':purpose,'reason':'在预定条件网格内平衡预测表现、模型不确定性与批次覆盖；仍需实验验证。' if model_ready else '优先覆盖尚未考察的条件区域，不以少量数据推断最优。','prediction':pred})
    return {'model':model,'training_conditions':len(groups),'remaining':len(available),'items':selected,'version':'process-planner-v1','limitations':'仅用于本步骤、固定条件与检测方法。模型标准差未经校准，不是置信区间；未证明稳健窗口或放大可行性。'}

def output(study):
    # Studies created before target-definition-v1 remain readable and are clearly marked as unspecified.
    data={**study.data,'spec':{'experiment_budget':24,'batch_size':6,'confirmation_repeats':2,
        'target_state':None,'target_amount':None,'target_unit':'','target_specification':'历史记录未填写目标规格',
        'process_system':'均相液体',**study.data['spec']}}
    return {'id':study.id,'revision':study.revision,**data,'analysis':analysis(data)}

@router.get('/studies')
def list_studies(project_id:str,db:Session=Depends(get_db),user:UserContext=Depends(get_current_user)):
    require_project_access(db,project_id,user)
    return [output(s) for s in db.scalars(select(ProcessStudy).where(ProcessStudy.project_id==project_id)).all()]

@router.post('/studies')
def create_study(project_id:str,payload:StudyInput,db:Session=Depends(get_db),user:UserContext=Depends(get_current_user)):
    require_project_access(db,project_id,user,'member')
    if len(db.scalars(select(ProcessStudy.id).where(ProcessStudy.project_id==project_id)).all())>=50:raise HTTPException(422,'每个项目最多50个工艺步骤研究')
    study=ProcessStudy(project_id=project_id,data={'spec':payload.model_dump(),'experiments':[],'plans':[],'created_at':datetime.now(timezone.utc).isoformat()})
    db.add(study);db.flush();db.add(AuditEvent(project_id=project_id,actor_id=user.id,action='process.created',entity_type='process_study',entity_id=study.id,details=study.data));db.commit();db.refresh(study)
    return output(study)

@router.post('/studies/{study_id}/experiments')
def record(project_id:str,study_id:str,payload:ExperimentRequest,db:Session=Depends(get_db),user:UserContext=Depends(get_current_user)):
    study=get_study(db,project_id,study_id,user,True); e=checked_experiment(study.data['spec'],payload.experiment)
    rows=list(study.data['experiments']); old=next((r for r in rows if r['id']==e['id']),None)
    if e['id'] and not old:raise HTTPException(404,'实验记录不存在')
    if not old and len(rows)>=200:raise HTTPException(422,'每个步骤最多200条实验，请另建研究')
    e={**(old or {}),**e,'id':e['id'] or new_id(),'updated_at':datetime.now(timezone.utc).isoformat()}
    if old and old['conditions']!=e['conditions']: e.pop('recommendation',None)
    rows=[e if r['id']==e['id'] else r for r in rows] if old else rows+[e]
    save_data(db,study,{**study.data,'experiments':rows},payload.revision,user,'process.experiment_saved');return output(study)

@router.post('/studies/{study_id}/plan')
def plan(project_id:str,study_id:str,payload:PlanRequest,db:Session=Depends(get_db),user:UserContext=Depends(get_current_user)):
    study=get_study(db,project_id,study_id,user,True)
    if study.revision!=payload.revision:raise HTTPException(409,'记录已更新，请刷新后重新生成')
    proposal=recommend(study.data,payload.count)
    if payload.commit:
        if not proposal['items']:raise HTTPException(422,'没有新的可推荐条件；可手动添加确认实验')
        if len(study.data['experiments'])+len(proposal['items'])>200:raise HTTPException(422,'每个步骤最多200条实验')
        batch=f"第{len(study.data['plans'])+1}轮"
        items=[{'id':new_id(),'conditions':i['conditions'],'results':{},'status':'planned','notes':'','batch':batch,'recommendation':i} for i in proposal['items']]
        save_data(db,study,{**study.data,'experiments':study.data['experiments']+items,'plans':study.data['plans']+[{'batch':batch,'based_on_revision':payload.revision,**proposal}]},payload.revision,user,'process.plan_saved')
    return {'proposal':proposal,'study':output(study)}

@router.get('/studies/{study_id}/csv')
def export_csv(project_id:str,study_id:str,db:Session=Depends(get_db),user:UserContext=Depends(get_current_user)):
    study=get_study(db,project_id,study_id,user);spec=study.data['spec']
    columns=['experiment_id','status','batch']+[f['name'] for f in spec['factors']]+[o['name'] for o in spec['objectives']]+['notes']
    buf=io.StringIO();w=csv.writer(buf)
    def safe(v):
        s=str(v)
        return "'"+s if s.lstrip().startswith(('=','+','@','-')) and not _number(s) else s
    w.writerow([safe(c) for c in columns])
    for r in study.data['experiments']:
        values={'experiment_id':r['id'],'status':r['status'],'batch':r['batch'],**r['conditions'],**r['results'],'notes':r['notes']}
        w.writerow([safe(values.get(c,'')) for c in columns])
    return Response('\ufeff'+buf.getvalue(),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="process-experiments.csv"'})

def _number(v):
    try:return math.isfinite(float(v))
    except ValueError:return False

@router.post('/studies/{study_id}/import')
def import_csv(project_id:str,study_id:str,revision:int,commit:bool=False,file:UploadFile=File(...),db:Session=Depends(get_db),user:UserContext=Depends(get_current_user)):
    study=get_study(db,project_id,study_id,user,True)
    if revision!=study.revision:raise HTTPException(409,'记录已更新，请刷新后重新检查文件')
    raw=file.file.read(1_000_001)
    if len(raw)>1_000_000:raise HTTPException(413,'文件不超过1MB')
    try:
        reader=csv.DictReader(io.StringIO(raw.decode('utf-8-sig')))
        spec=study.data['spec'];needed=['experiment_id','status','batch','notes']+[f['name'] for f in spec['factors']]+[o['name'] for o in spec['objectives']]
        if not reader.fieldnames or len(set(reader.fieldnames))!=len(reader.fieldnames) or set(reader.fieldnames)!=set(needed):raise ValueError('列名须与下载模板完全一致')
        imported=list(reader)
        if not 1<=len(imported)<=200:raise ValueError('每次1–200条记录')
    except (UnicodeDecodeError,ValueError,csv.Error) as exc:raise HTTPException(422,f'请使用UTF-8 CSV模板：{exc}')
    rows=list(study.data['experiments']);known={r['id']:r for r in rows};seen=set();valid=[];preview=[]
    for n,r in enumerate(imported,2):
        try:
            if None in r or any(v is None for v in r.values()):raise ValueError('列数不正确')
            ident=r['experiment_id'].strip()
            if ident and (ident not in known or ident in seen):raise ValueError('实验编号无效或重复')
            if ident:seen.add(ident)
            e=Experiment(id=ident,conditions={f['name']:r[f['name']] for f in spec['factors']},results={o['name']:float(r[o['name']]) for o in spec['objectives'] if r[o['name']].strip()},status=r['status'],batch=r['batch'],notes=r['notes'])
            value=checked_experiment(spec,e);merged={**known.get(ident,{}),**value,'id':ident or new_id()}
            if ident and known[ident]['conditions']!=value['conditions']:merged.pop('recommendation',None)
            valid.append(merged);preview.append({'row':n,'status':'可保存'})
        except (ValueError,HTTPException) as exc:preview.append({'row':n,'error':str(exc.detail if isinstance(exc,HTTPException) else exc)[:300]})
    errors=any('error' in r for r in preview)
    if commit:
        if errors:raise HTTPException(422,'文件含错误，本次未保存；请修正后重新检查')
        merged=[r for r in rows if r['id'] not in seen]+valid
        if len(merged)>200:raise HTTPException(422,'每个步骤最多200条实验')
        save_data(db,study,{**study.data,'experiments':merged},revision,user,'process.csv_imported')
    return {'rows':preview,'accepted':len(valid),'can_save':not errors,'study':output(study)}
