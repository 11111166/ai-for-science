"""Serve validated offline QSAR artifacts without training dependencies."""
from functools import lru_cache
import json
from pathlib import Path

@lru_cache(maxsize=1)
def snapshot():
    path=Path(__file__).parent/'data'/'qsar_v1.json'
    if not path.is_file():return {'version':'target-qsar-pic50-v1','models':[]}
    return json.loads(path.read_text())


def project_results(compounds):
    data=snapshot();results=[]
    symbols={m.get('target_symbol') for c in compounds for m in (c.details or {}).get('target_matches',[])}
    for model in data['models']:
        if model['symbol'] not in symbols:continue
        rows=[]
        for c in compounds:
            if c.demo:continue
            details=c.details or {}
            if not any(m.get('target_symbol')==model['symbol'] for m in details.get('target_matches',[])):continue
            p=next((p for p in model.get('predictions',[]) if p['identifier']==c.identifier and p['input_smiles']==details.get('canonical_smiles')),None)
            row={'compound_id':c.id,'identifier':c.identifier,'name':c.name,'status':'not_in_snapshot'}
            if p:
                row.update({k:v for k,v in p.items() if k not in {'input_smiles','name','identifier'}})
                if model['status']!='validated':row['predicted']=None;row['status']='withheld'
            rows.append(row)
        results.append({**{k:v for k,v in model.items() if k!='predictions'},'predictions':rows})
    return {'version':data['version'],'created_at':data.get('created_at'),'method':data.get('method'),'models':results}
