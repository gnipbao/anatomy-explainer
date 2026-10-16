import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import subprocess
import sys
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]


def module(name, filename):
    spec=importlib.util.spec_from_file_location(name,ROOT/'scripts'/filename)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result)
    return result


setup=module('voice_setup','setup_voice_engine.py')
checker=module('voice_model_check','check_voice_model.py')


class VoiceSetup(unittest.TestCase):
    def test_dry_run_creates_nothing_and_never_installs_or_downloads(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory)
            with mock.patch.object(setup.subprocess,'run',side_effect=AssertionError('installed')),mock.patch.object(setup.urllib.request,'urlopen',side_effect=AssertionError('downloaded')):
                report=setup.prepare(base/'runtime',base/'config.json',base/'model',True,True,True)
            self.assertTrue(report['dry_run'])
            self.assertFalse(report['weights_downloaded'])
            self.assertEqual(list(base.iterdir()),[])

    def test_preparation_preserves_existing_config_and_weights_and_backs_up_app(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);home=base/'runtime';config=base/'config.json';model=base/'model'
            model.mkdir();(model/'model.safetensors').write_bytes(b'user-owned-existing-weights')
            original='{"custom":"user configuration"}\n';config.write_text(original)
            with mock.patch.object(setup.subprocess,'run',side_effect=AssertionError('installed')),mock.patch.object(setup.urllib.request,'urlopen',side_effect=AssertionError('downloaded')):
                setup.prepare(home,config,model)
                setup.prepare(home,config,model)
            self.assertEqual(config.read_text(),original)
            self.assertEqual((model/'model.safetensors').read_bytes(),b'user-owned-existing-weights')
            self.assertTrue((home/'app/voice_engine.py').is_file())
            self.assertTrue((home/'app/local_voice_worker.py').is_file())
            self.assertEqual(len(list((home/'backups').iterdir())),1)

    def test_model_checker_verifies_bytes_and_detects_same_size_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);model=root/'model';model.mkdir()
            entries=[]
            for name in checker.NAMES:
                data=('fixture:'+name).encode();(model/name).write_bytes(data)
                entries.append({'name':name,'size':len(data),'sha256':hashlib.sha256(data).hexdigest()})
            manifest=root/'manifest.json';manifest.write_text(json.dumps({'model_id':'fixture-not-a-real-model','revision':'fixture','files':entries}))
            self.assertTrue(checker.check(model,manifest)['ok'])
            weight=model/'model.safetensors';raw=weight.read_bytes();weight.write_bytes(b'X'+raw[1:])
            result=checker.check(model,manifest)
            self.assertFalse(result['ok'])
            self.assertEqual(next(c for c in result['checks'] if c['name']=='model.safetensors')['status'],'sha256_mismatch')
            self.assertFalse(result['synthesis_passed'])

    def test_standalone_checker_finds_its_installed_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);home=base/'runtime';model=base/'model'
            setup.prepare(home,base/'config.json',model)
            result=subprocess.run([sys.executable,str(home/'app/check_voice_model.py'),
                                   '--model-dir',str(model)],capture_output=True,text=True)
            self.assertEqual(result.returncode,1)  # No weights in this fixture.
            report=json.loads(result.stdout)
            self.assertEqual(len(report['checks']),5)
            self.assertTrue(all(item['status']=='missing' for item in report['checks']))
            self.assertFalse(report['ok'])


if __name__=='__main__':unittest.main()
