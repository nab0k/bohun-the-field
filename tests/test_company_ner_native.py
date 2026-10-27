"""Process-boundary tests using a fake child, never a real model."""
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

from collectors.company_ner.native import Native
from tests.test_company_ner import row,answer


class NativeBoundaryTests(unittest.TestCase):
    def psutil(self,available=4*1024**3):
        process=types.SimpleNamespace(memory_info=lambda:types.SimpleNamespace(rss=1024),children=lambda recursive:[])
        return types.SimpleNamespace(virtual_memory=lambda:types.SimpleNamespace(available=available),
                                     Process=lambda pid:process,NoSuchProcess=ProcessLookupError)

    def test_child_has_no_database_secret_and_only_source_input(self):
        text=row()['content_capture']['text'];observed=[]
        def spawn(args,**kwargs):
            observed.append((args,kwargs))
            value=json.loads(Path(args[-2]).read_bytes())
            self.assertEqual(value,{'text':text})
            Path(args[-1]).write_text(json.dumps(answer(text)))
            return types.SimpleNamespace(pid=999999,returncode=0,poll=lambda:0,wait=lambda:0)
        with tempfile.TemporaryDirectory() as model,patch.dict('sys.modules',{'psutil':self.psutil()}), \
             patch.dict('os.environ',{'SUPABASE_DB_PASSWORD':'SENTINEL_NOT_A_REAL_SECRET'}), \
             patch('collectors.company_ner.native.subprocess.Popen',side_effect=spawn):
            result=Native('/existing/python',model)(text)
        self.assertEqual(len(observed),1);env=observed[0][1]['env']
        self.assertNotIn('SUPABASE_DB_PASSWORD',env)
        self.assertEqual(env['HF_HUB_OFFLINE'],'1');self.assertNotIn('PYTHONPATH',env)
        self.assertEqual(result,answer(text))

    def test_timeout_kills_child_without_second_attempt(self):
        child=types.SimpleNamespace(pid=999999,returncode=None,poll=lambda:None,wait=lambda:0)
        with tempfile.TemporaryDirectory() as model,patch.dict('sys.modules',{'psutil':self.psutil()}), \
             patch('collectors.company_ner.native.subprocess.Popen',return_value=child) as spawn, \
             patch('collectors.company_ner.native.time.monotonic',side_effect=[0,181]), \
             patch('collectors.company_ner.native.os.killpg') as kill:
            with self.assertRaisesRegex(RuntimeError,'NER_TIME_CAP'):Native('/existing/python',model)(row()['content_capture']['text'])
        self.assertEqual(spawn.call_count,1);self.assertEqual(kill.call_count,1)

    def test_insufficient_ram_prevents_dispatch(self):
        with tempfile.TemporaryDirectory() as model,patch.dict('sys.modules',{'psutil':self.psutil(1024)}), \
             patch('collectors.company_ner.native.subprocess.Popen') as spawn:
            with self.assertRaisesRegex(RuntimeError,'NER_RAM_PREFLIGHT'):Native('/existing/python',model)(row()['content_capture']['text'])
        spawn.assert_not_called()


if __name__=='__main__':unittest.main()
