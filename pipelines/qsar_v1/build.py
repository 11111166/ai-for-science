"""Offline target-specific pIC50 QSAR; scaffold holdout, no production training."""
import argparse, hashlib, json, math, statistics, time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import httpx
import numpy as np
from rdkit import Chem, DataStructs, rdBase
from rdkit.Chem import rdFingerprintGenerator
from rdkit.Chem.MolStandardize import rdMolStandardize
from rdkit.Chem.Scaffolds import MurckoScaffold
from sklearn.ensemble import RandomForestRegressor
from sklearn.model_selection import GroupShuffleSplit, GroupKFold
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
import sklearn

BASE='https://www.ebi.ac.uk/chembl/api/data'
SEED=20260908
GEN=rdFingerprintGenerator.GetMorganGenerator(radius=2,fpSize=2048)

def standardize(smiles):
    mol=Chem.MolFromSmiles(smiles or '')
    if mol is None or mol.GetNumHeavyAtoms()<5:return None
    mol=rdMolStandardize.Cleanup(mol)
    mol=rdMolStandardize.FragmentParent(mol)
    mol=rdMolStandardize.Uncharger().uncharge(mol)
    Chem.SanitizeMol(mol)
    Chem.RemoveStereochemistry(mol)
    canonical=Chem.MolToSmiles(mol,isomericSmiles=False)
    group=MurckoScaffold.MurckoScaffoldSmiles(mol=mol) or 'ACYCLIC'
    fp=GEN.GetFingerprint(mol)
    return canonical,group,fp

def get(client,path,**params):
    for attempt in range(3):
        try:
            r=client.get(f'{BASE}/{path}.json',params=params);r.raise_for_status();return r.json()
        except (httpx.HTTPError,ValueError):
            if attempt==2:raise
            time.sleep(attempt+1)

def metrics(y,pred,base):
    return {'mae':float(mean_absolute_error(y,pred)),'rmse':float(math.sqrt(mean_squared_error(y,pred))),'r2':float(r2_score(y,pred)),'baseline_rmse':float(math.sqrt(mean_squared_error(y,np.full(len(y),base))))}

def forest():return RandomForestRegressor(n_estimators=160,min_samples_leaf=3,max_features=.33,n_jobs=2,random_state=SEED)

def build(symbol,queries,raw_dir):
    with httpx.Client(timeout=40,follow_redirects=True) as client:
        search=get(client,'target/search',q=symbol,limit=20)
        targets=[t for t in search.get('targets',[]) if t.get('organism')=='Homo sapiens' and t.get('target_type')=='SINGLE PROTEIN' and any(s.get('syn_type')=='GENE_SYMBOL' and s.get('component_synonym','').upper()==symbol for c in t.get('target_components',[]) for s in c.get('target_component_synonyms',[]))]
        if not targets:return {'symbol':symbol,'status':'unavailable','reason':'未匹配人源单蛋白靶点'}
        tid=sorted(t['target_chembl_id'] for t in targets)[0]
        raw_path=raw_dir/f'{symbol}.json'
        if raw_path.exists():raw=json.loads(raw_path.read_text())
        else:
            rows=[];total=0
            for offset in range(0,2000,500):
                page=get(client,'activity',target_chembl_id=tid,target_organism='Homo sapiens',standard_type='IC50',assay_type='B',standard_relation='=',standard_flag=1,pchembl_value__isnull='false',data_validity_comment__isnull='true',potential_duplicate=0,limit=500,offset=offset,order_by='activity_id')
                rows+=page.get('activities',[]);total=page.get('page_meta',{}).get('total_count',0)
                if offset+500>=total:break
            raw={'target_id':tid,'total':total,'activities':rows,'retrieved_at':datetime.now(timezone.utc).isoformat()};raw_path.write_text(json.dumps(raw))
    grouped={};invalid=0
    for row in raw['activities']:
        try:
            value=float(row.get('pchembl_value'))
            if not math.isfinite(value) or not 0<=value<=12:raise ValueError()
            norm=standardize(row.get('canonical_smiles'))
            if not norm:raise ValueError()
        except Exception:invalid+=1;continue
        canonical,scaffold,fp=norm
        entry=grouped.setdefault(canonical,{'canonical':canonical,'scaffold':scaffold,'fp':fp,'values':[],'ids':set()})
        entry['values'].append(value);entry['ids'].add(row['molecule_chembl_id'])
    records=[r for _,r in sorted(grouped.items()) if max(r['values'])-min(r['values'])<=2]
    result={'symbol':symbol,'target_id':tid,'endpoint':'pIC50','status':'insufficient','reason':'有效结构或骨架数量不足','raw_count':len(raw['activities']),'source_total':raw['total'],'structure_count':len(records),'scaffold_count':len({r['scaffold'] for r in records}),'invalid_count':invalid,'discordant_structure_count':len(grouped)-len(records),'retrieved_at':raw['retrieved_at'],'data_sha256':hashlib.sha256(raw_path.read_bytes()).hexdigest(),'predictions':[]}
    if len(records)<60 or result['scaffold_count']<10:return result
    X=np.array([np.array(r['fp']) for r in records],dtype=np.uint8);y=np.array([statistics.median(r['values']) for r in records]);groups=np.array([r['scaffold'] for r in records])
    train,test=next(GroupShuffleSplit(n_splits=1,test_size=.2,random_state=SEED).split(X,y,groups))
    if len(test)<12 or len(train)<40 or len(set(groups[train]))<4:return result
    assert not set(groups[train]) & set(groups[test])
    model=forest().fit(X[train],y[train]);pred=model.predict(X[test]);validation=metrics(y[test],pred,float(np.median(y[train])))
    passed=validation['rmse']<1.2 and validation['rmse']<.9*validation['baseline_rmse'] and validation['r2']>0
    result.update(status='validated' if passed else 'review',reason='' if passed else '骨架留出测试未通过发布门槛，仅展示评测',train_count=len(train),test_count=len(test),metrics=validation,quality_gate={'passed':passed,'max_rmse':1.2,'baseline_ratio':.9,'min_r2':0},test_points=[{'id':sorted(records[i]['ids'])[0],'observed':float(y[i]),'predicted':float(p)} for i,p in zip(test,pred)],train_scaffolds=len(set(groups[train])),test_scaffolds=len(set(groups[test])))
    # Predictions for known molecules always exclude their entire scaffold.
    values={int(i):(float(p),'scaffold_holdout',max(DataStructs.BulkTanimotoSimilarity(records[i]['fp'],[records[j]['fp'] for j in train]))) for i,p in zip(test,pred)}
    for fit,valid in GroupKFold(n_splits=4).split(X[train],y[train],groups[train]):
        fit,valid=train[fit],train[valid];fold=forest().fit(X[fit],y[fit]);ps=fold.predict(X[valid])
        assert not set(groups[fit]) & set(groups[valid])
        for i,p in zip(valid,ps):values[int(i)]=(float(p),'scaffold_cv',max(DataStructs.BulkTanimotoSimilarity(records[i]['fp'],[records[j]['fp'] for j in fit])))
    by_structure={r['canonical']:i for i,r in enumerate(records)}
    final=forest().fit(X,y)
    for c in queries:
        norm=standardize(c.get('smiles'))
        if not norm:continue
        canonical,_,fp=norm
        if canonical in by_structure:
            idx=by_structure[canonical];v,kind,sim=values[idx];observed=float(y[idx])
        else:
            v=float(final.predict(np.array([np.array(fp)],dtype=np.uint8))[0]);kind='new_structure';sim=max(DataStructs.BulkTanimotoSimilarity(fp,[r['fp'] for r in records]));observed=None
        result['predictions'].append({'identifier':c['identifier'],'name':c['name'],'input_smiles':c['smiles'],'predicted':v if passed else None,'observed':observed,'prediction_kind':kind,'similarity':float(sim),'domain':'inside' if sim>=.5 else 'outside','status':'available' if passed else 'withheld'})
    return result

if __name__=='__main__':
    args=argparse.ArgumentParser();args.add_argument('--queries',required=True);args.add_argument('--output',required=True);args.add_argument('--raw',required=True);a=args.parse_args()
    query=json.loads(Path(a.queries).read_text());raw=Path(a.raw);raw.mkdir(parents=True,exist_ok=True)
    output={'version':'target-qsar-pic50-v1','created_at':datetime.now(timezone.utc).isoformat(),'method':'Morgan radius 2 / 2048 bits + RandomForest 160 trees; scaffold holdout 20%; training scaffold CV 4 folds','seed':SEED,'rdkit_version':rdBase.rdkitVersion,'sklearn_version':sklearn.__version__,'models':[]}
    def task(item):
        symbol,rows=item
        try:r=build(symbol,rows,raw)
        except Exception as e:r={'symbol':symbol,'status':'unavailable','reason':type(e).__name__,'predictions':[]}
        print(symbol,r['status'],r.get('structure_count'),r.get('metrics'),flush=True);return r
    with ThreadPoolExecutor(max_workers=2) as pool:output['models']=list(pool.map(task,query.items()))
    Path(a.output).write_text(json.dumps(output,ensure_ascii=False,allow_nan=False,indent=2))
