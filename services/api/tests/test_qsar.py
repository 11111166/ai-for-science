from types import SimpleNamespace
from app import qsar


def test_predictions_require_exact_batch_structure_and_passed_model(monkeypatch):
    def compound(smiles='CCO',demo=False):return SimpleNamespace(id='1',identifier='CHEMBL1',name='A',demo=demo,details={'canonical_smiles':smiles,'target_matches':[{'target_symbol':'ACE'}]})
    model={'symbol':'ACE','status':'validated','predictions':[{'identifier':'CHEMBL1','input_smiles':'CCO','predicted':7.1,'status':'available'}]}
    monkeypatch.setattr(qsar,'snapshot',lambda:{'version':'test','models':[model]})
    assert qsar.project_results([compound()])['models'][0]['predictions'][0]['predicted']==7.1
    assert qsar.project_results([compound('CCN')])['models'][0]['predictions'][0]['status']=='not_in_snapshot'
    assert qsar.project_results([compound(demo=True)])['models'][0]['predictions']==[]
    model['status']='review'
    row=qsar.project_results([compound()])['models'][0]['predictions'][0]
    assert row['predicted'] is None and row['status']=='withheld'
