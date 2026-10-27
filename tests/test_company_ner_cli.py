"""CLI diagnostics must survive a connection error without leaking its text."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('company_ner_cli',ROOT/'scripts/company-ner.py')
CLI=importlib.util.module_from_spec(spec);spec.loader.exec_module(CLI)


class CLITests(unittest.TestCase):
    def test_real_entrypoint_emits_safe_tls_category_once(self):
        secret='SENTINEL_PASSWORD_MUST_NOT_APPEAR'
        class OperationalError(Exception):sqlstate=None
        attempts=[]
        def connect(*args,**kwargs):
            attempts.append(1)
            raise OperationalError('connection certificate verify failed password='+secret+' source=PRIVATE_SOURCE')
        driver=types.ModuleType('psycopg');driver.connect=connect
        conninfo=types.ModuleType('psycopg.conninfo');conninfo.conninfo_to_dict=lambda dsn:{'sslmode':'verify-full','sslrootcert':'public.crt'}
        rows=types.ModuleType('psycopg.rows');rows.dict_row=object()
        stderr,stdout=io.StringIO(),io.StringIO()
        with tempfile.TemporaryDirectory() as model, \
             patch.dict(sys.modules,{'psycopg':driver,'psycopg.conninfo':conninfo,'psycopg.rows':rows}), \
             patch.dict(os.environ,{'COMPANY_NER_DATABASE_URL':secret}), \
             patch.object(sys,'argv',['company-ner.py','--run','--model-python',sys.executable,'--model-dir',model]), \
             contextlib.redirect_stderr(stderr),contextlib.redirect_stdout(stdout):
            with self.assertRaises(SystemExit) as raised:
                runpy.run_path(str(ROOT/'scripts/company-ner.py'),run_name='__main__')
        self.assertEqual(str(raised.exception),'COMPANY_NER_STOP_NO_AUTOMATIC_RETRY')
        diagnostic=json.loads(stderr.getvalue())
        self.assertEqual((diagnostic['phase'],diagnostic['category']),('database_connect','TLS_CERTIFICATE'))
        self.assertEqual(len(attempts),1);self.assertFalse(stdout.getvalue())
        self.assertNotIn(secret,stderr.getvalue());self.assertNotIn('PRIVATE_SOURCE',stderr.getvalue())

    def test_only_bounded_sqlstate_and_known_phase_are_exposed(self):
        err=RuntimeError('PRIVATE_SOURCE credentials=SECRET');err.sqlstate='42501'
        value=CLI.safe_failure(err,'database_transaction')
        self.assertEqual(value['category'],'DATABASE_PERMISSION');self.assertEqual(value['sqlstate'],'42501')
        self.assertNotIn('SECRET',json.dumps(value))
        err.sqlstate='SECRET'*100
        value=CLI.safe_failure(err,'SECRET')
        self.assertIsNone(value['sqlstate']);self.assertEqual(value['phase'],'unknown')

    def test_official_public_ca_pin_and_workflow_verification(self):
        raw=(ROOT/'collectors/company_ner/supabase-prod-ca-2021.crt').read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(),'700723581420dd1ac98fd7e9ac529f0ef210eadcaf87fc868a3ad7d114c2f3b7')
        self.assertTrue(raw.startswith(b'-----BEGIN CERTIFICATE-----'));self.assertNotIn(b'PRIVATE KEY',raw)
        workflow=(ROOT/'.github/workflows/company-ner.yml').read_text()
        self.assertIn("sslmode='verify-full',sslrootcert=str(ca)",workflow)
        self.assertNotIn("sslmode='require'",workflow)


if __name__=='__main__':unittest.main()
