import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('project_preferences_tests',ROOT/'scripts/project.py')
project=importlib.util.module_from_spec(spec)
spec.loader.exec_module(project)


class ProjectPreferences(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory=Path(self.temp.name)
        self.preferences=self.directory/'preferences.json'
        patcher=mock.patch.object(project,'default_preferences_path',return_value=self.preferences)
        self.default_path=patcher.start()
        self.addCleanup(patcher.stop)

    def args(self,**overrides):
        fields=dict(project=str(self.directory/'new-project'),topic='木牛流马',language='zh-CN',
                    theme='anatomy-official-light',example=None,tts_provider=None,
                    preferences_file=None,ignore_preferences=False)
        fields.update(overrides)
        return SimpleNamespace(**fields)

    def save(self,tts=None):
        project.write(self.preferences,{'schema_version':1,'tts':tts or {'provider':'local','local_voice':'default','segment_mode':'phrases'}})

    def create(self,**options):
        args=self.args(**options)
        with contextlib.redirect_stdout(io.StringIO()): project.init(args)
        return project.read(Path(args.project)/'job.json')

    def test_missing_optional_preferences_keep_public_defaults(self):
        job=self.create()
        self.assertEqual(job['tts'],{'provider':'doubao','segment_mode':'phrases'})
        self.assertFalse(job['content_ready'])

    def test_local_preferences_apply_only_whitelisted_tts_choices(self):
        self.save()
        job=self.create()
        self.assertEqual(job['tts'],{'provider':'local','local_voice':'default','segment_mode':'phrases'})
        self.assertEqual(job['topic'],'木牛流马')
        self.assertNotIn('preferences_file',job)

    def test_each_explicit_provider_overrides_local_preferences(self):
        self.save()
        for provider in project.TTS_PROVIDERS:
            with self.subTest(provider=provider):
                job=self.create(project=str(self.directory/provider),tts_provider=provider)
                self.assertEqual(job['tts']['provider'],provider)
                self.assertEqual(job['tts']['segment_mode'],'scene' if provider=='external' else 'phrases')

    def test_missing_segment_mode_preserves_example_default(self):
        self.save({'provider':'local','local_voice':'default'})
        job=self.create(example='token-bucket')
        original=project.read(ROOT/'examples/token-bucket/job.json')
        self.assertEqual(job['tts']['segment_mode'],original['tts']['segment_mode'])
        self.assertEqual(job['tts']['provider'],'local')
        self.assertEqual(job['scenes'],original['scenes'])
        self.assertTrue((self.directory/'new-project/model.mjs').is_file())
        self.assertTrue(job['content_ready'])

    def test_example_honors_explicit_provider(self):
        self.save()
        self.assertEqual(self.create(example='token-bucket',tts_provider='say')['tts']['provider'],'say')

    def test_scene_preference_is_preserved(self):
        self.save({'provider':'local','segment_mode':'scene'})
        self.assertEqual(self.create()['tts']['segment_mode'],'scene')

    def test_ignore_skips_even_broken_preferences(self):
        self.preferences.write_text('broken json')
        job=self.create(ignore_preferences=True,tts_provider='say')
        self.assertEqual(job['tts'],{'provider':'say','segment_mode':'phrases'})
        self.default_path.assert_not_called()

    def test_explicit_file_is_used_without_reading_default(self):
        explicit=self.directory/'chosen.json'
        project.write(explicit,{'schema_version':1,'tts':{'provider':'local','local_voice':'Narrator_1'}})
        job=self.create(preferences_file=str(explicit))
        self.assertEqual(job['tts']['local_voice'],'Narrator_1')
        self.default_path.assert_not_called()

    def test_missing_explicit_file_fails_before_creating_parent(self):
        target=self.directory/'not-created'/'new'
        with self.assertRaisesRegex(ValueError,'Preferences file does not exist'):
            self.create(project=str(target),preferences_file=str(self.directory/'missing.json'))
        self.assertFalse(target.parent.exists())

    def test_conflicting_preference_options_fail_before_creation(self):
        with self.assertRaisesRegex(ValueError,'mutually exclusive'):
            self.create(ignore_preferences=True,preferences_file=str(self.preferences))
        self.assertFalse((self.directory/'new-project').exists())

    def test_bad_preferences_never_leave_half_created_project(self):
        documents=[
            '{not json',
            '[]',
            json.dumps({'schema_version':True,'tts':{}}),
            json.dumps({'schema_version':2,'tts':{}}),
            json.dumps({'schema_version':1,'tts':{},'token':'secret'}),
            json.dumps({'schema_version':1,'tts':[]}),
            json.dumps({'schema_version':1,'tts':{'provider':'unknown'}}),
            json.dumps({'schema_version':1,'tts':{'provider':['local']}}),
            json.dumps({'schema_version':1,'tts':{'provider':'local','api_key':'secret'}}),
            json.dumps({'schema_version':1,'tts':{'endpoint':'http://elsewhere/v1/audio/speech'}}),
            json.dumps({'schema_version':1,'tts':{'reference_audio':'/private/reference.wav'}}),
            json.dumps({'schema_version':1,'tts':{'local_voice':'../../reference.wav'}}),
            json.dumps({'schema_version':1,'tts':{'local_voice':'name\nother'}}),
            json.dumps({'schema_version':1,'tts':{'local_voice':'A'*65}}),
            json.dumps({'schema_version':1,'tts':{'segment_mode':None}}),
            '{"schema_version":1,"tts":{"provider":"doubao","provider":"local"}}',
        ]
        target=self.directory/'not-created'/'new'
        for document in documents:
            with self.subTest(document=document):
                self.preferences.write_text(document,encoding='utf-8')
                with self.assertRaisesRegex(ValueError,'Invalid preferences file'):
                    self.create(project=str(target))
                self.assertFalse(target.parent.exists())

    def test_oversized_or_non_utf8_file_is_rejected(self):
        for content in (b' '*16385,b'\xff\xfe'):
            with self.subTest(content=content[:10]):
                self.preferences.write_bytes(content)
                with self.assertRaisesRegex(ValueError,'Invalid preferences file'): self.create()
                self.assertFalse((self.directory/'new-project').exists())

    def test_existing_job_is_never_rewritten(self):
        target=self.directory/'existing'
        target.mkdir()
        saved=b'{"tts":{"provider":"doubao"}}\n'
        (target/'job.json').write_bytes(saved)
        self.save()
        with self.assertRaisesRegex(ValueError,'project already exists'): self.create(project=str(target))
        self.assertEqual((target/'job.json').read_bytes(),saved)

    def test_cli_supports_explicit_preferences_and_ignore_switch(self):
        self.save()
        for flags,provider in ((['--preferences-file',str(self.preferences)],'local'),
                               (['--ignore-preferences','--tts-provider','doubao'],'doubao')):
            target=self.directory/f'cli-{provider}'
            result=subprocess.run([sys.executable,str(ROOT/'scripts/project.py'),'init','--project',str(target),*flags],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(project.read(target/'job.json')['tts']['provider'],provider)


if __name__=='__main__': unittest.main()
