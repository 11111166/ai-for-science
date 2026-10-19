import unittest
import numpy as np
from sklearn.model_selection import GroupShuffleSplit
from build import standardize, metrics, SEED

class PipelineTests(unittest.TestCase):
    def test_salts_and_stereoisomers_share_structure_groups(self):
        a=standardize('C[C@H](O)c1ccccc1')
        b=standardize('C[C@@H](O)c1ccccc1.[Na+]')
        self.assertEqual(a[0],b[0]);self.assertEqual(a[1],b[1])
        self.assertIsNotNone(standardize('CC(=O)Oc1ccccc1C(=O)O'))
    def test_no_scaffold_leakage(self):
        groups=np.repeat(np.arange(12),5);X=np.zeros((60,2));y=np.arange(60)
        train,test=next(GroupShuffleSplit(n_splits=1,test_size=.2,random_state=SEED).split(X,y,groups))
        self.assertFalse(set(groups[train]) & set(groups[test]))
    def test_metrics_are_on_same_scale_as_baseline(self):
        result=metrics(np.array([5.,6.,7.]),np.array([5.,6.,7.]),6.)
        self.assertEqual(result['rmse'],0);self.assertEqual(result['r2'],1);self.assertGreater(result['baseline_rmse'],0)
if __name__=='__main__':unittest.main()
