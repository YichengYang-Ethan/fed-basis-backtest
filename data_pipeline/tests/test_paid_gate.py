"""Safety checks use a fake vendor; no live API or account is involved."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS=Path(__file__).resolve().parents[1]/'scripts'
sys.path.insert(0,str(SCRIPTS))
spec=importlib.util.spec_from_file_location('paid_gate',SCRIPTS/'databento_zq_pull.py')
gate=importlib.util.module_from_spec(spec);spec.loader.exec_module(gate)

class Fake:
    def __init__(self, cost=1):
        self.metadata=self;self.timeseries=self;self.cost=cost;self.downloads=0
    def get_cost(self,**kwargs):return self.cost
    def get_range(self,**kwargs):
        self.downloads+=1
        raise RuntimeError('simulated ambiguous vendor timeout')

class PaidGateTests(unittest.TestCase):
    def invoke(self,args,fake):
        return gate.main(['--start','2026-01-01','--end','2026-02-01']+args,client_factory=lambda _:fake)
    def test_quote_is_not_purchase(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(gate,'DATA_ROOT',Path(tmp)),patch.dict(os.environ,{'DATABENTO_API_KEY':'synthetic-test-value'}),contextlib.redirect_stdout(io.StringIO()):
            f=Fake();self.invoke([],f);self.assertEqual(f.downloads,0)
            self.assertFalse((Path(tmp)/'raw/cme/databento/purchase_reserve_ledger.json').exists())
    def test_budget_blocks_before_download(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(gate,'DATA_ROOT',Path(tmp)),patch.dict(os.environ,{'DATABENTO_API_KEY':'synthetic-test-value'}):
            f=Fake()
            with self.assertRaisesRegex(RuntimeError,'ceiling exceeded'):self.invoke(['--execute','--max-usd','1'],f)
            self.assertEqual(f.downloads,0)
    def test_uncertain_attempt_remains_reserved(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(gate,'DATA_ROOT',Path(tmp)),patch.dict(os.environ,{'DATABENTO_API_KEY':'synthetic-test-value'}):
            f=Fake()
            with self.assertRaisesRegex(RuntimeError,'simulated'):self.invoke(['--execute','--max-usd','2'],f)
            ledger=json.loads((Path(tmp)/'raw/cme/databento/purchase_reserve_ledger.json').read_text())
            self.assertEqual(ledger['attempts'][0]['reserve_usd'],'1.25')
            with self.assertRaisesRegex(RuntimeError,'ceiling exceeded'):self.invoke(['--execute','--max-usd','2'],f)
            self.assertEqual(f.downloads,1)
    def test_execution_requires_positive_budget(self):
        with patch.dict(os.environ,{'DATABENTO_API_KEY':'synthetic-test-value'}),contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):self.invoke(['--execute'],Fake())

if __name__=='__main__':unittest.main()
