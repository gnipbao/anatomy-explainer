import importlib.util
from pathlib import Path
import unittest
import tempfile
from types import SimpleNamespace
spec=importlib.util.spec_from_file_location('project',Path(__file__).resolve().parents[1]/'scripts/project.py')
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
class ProjectContract(unittest.TestCase):
    def test_draft_never_builds(self):
        job=p.read(p.ROOT/'examples/token-bucket/job.json');job['content_ready']=False
        with self.assertRaises(ValueError):p.validate(job)
    def test_sources_must_resolve(self):
        job=p.read(p.ROOT/'examples/token-bucket/job.json');job['scenes'][0]['source_ids']=['unknown']
        with self.assertRaises(ValueError):p.validate(job)
    def test_shipped_example_valid(self):
        job=p.validate(p.read(p.ROOT/'examples/token-bucket/job.json'))
        self.assertEqual(p.theme_for(job)['base'],'light')
    def test_stale_timing_is_rejected(self):
        job=p.read(p.ROOT/'examples/token-bucket/job.json')
        timing={'duration':3,'job_fingerprint':'obsolete','scenes':[{'id':s['id'],'start':i,'end':i+1,'duration':1} for i,s in enumerate(job['scenes'])]}
        with self.assertRaises(ValueError):p.timing_for(job,timing)
    def test_missing_fingerprint_is_rejected(self):
        job=p.read(p.ROOT/'examples/token-bucket/job.json')
        timing={'duration':3,'scenes':[{'id':s['id'],'start':i,'end':i+1,'duration':1} for i,s in enumerate(job['scenes'])]}
        with self.assertRaises(ValueError):p.timing_for(job,timing)
    def test_unknown_topic_pack_is_rejected(self):
        job=p.read(p.ROOT/'examples/token-bucket/job.json');job['topic_pack']='does-not-exist'
        with self.assertRaises(FileNotFoundError):p.topic_pack_for(job)
    def test_new_project_defaults_to_measured_phrase_synthesis(self):
        with tempfile.TemporaryDirectory() as d:
            target=Path(d)/'new'
            p.init(SimpleNamespace(project=str(target),example=None,topic='木牛流马',language='zh-CN',theme='anatomy-official-light'))
            job=p.read(target/'job.json')
            self.assertEqual(job['tts']['segment_mode'],'phrases')
            self.assertEqual(job['presentation']['captions']['mode'],'phrase')
            self.assertEqual(job['presentation']['captions']['style'],'cinema')
            self.assertEqual(job['presentation']['captions']['placement'],'bottom')
    def make_timing(self):
        job=p.read(p.ROOT/'examples/token-bucket/job.json')
        timing={'duration':3,'job_fingerprint':p.narration_fingerprint(job),'scenes':[{'id':s['id'],'start':i,'end':i+1,'duration':1} for i,s in enumerate(job['scenes'])],
                'captions':{'alignment':'measured-phrases','cues':[{'id':s['id']+'-p001','scene_id':s['id'],'start':i,'end':i+.7,'text':s['narration']} for i,s in enumerate(job['scenes'])]}}
        return job,timing
    def test_phrase_cue_may_not_escape_its_scene(self):
        job,timing=self.make_timing();timing['captions']['cues'][0]['end']=1.1
        with self.assertRaises(ValueError):p.timing_for(job,timing)
    def test_phrase_mode_cannot_silently_lose_measured_cues(self):
        job,timing=self.make_timing();del timing['captions']
        with self.assertRaises(ValueError):p.timing_for(job,timing)
    def test_manual_and_long_phrase_ids_and_legacy_scene_timing(self):
        job,timing=self.make_timing();timing['captions']['alignment']='manual';timing['captions']['cues'][0]['id']='a'*64+'-p001'
        self.assertIs(p.timing_for(job,timing),timing)
        job['tts'].pop('segment_mode');timing.pop('captions');timing['job_fingerprint']=p.narration_fingerprint(job)
        self.assertIs(p.timing_for(job,timing),timing)
    def test_invalid_motion_scale_is_rejected_before_build(self):
        job=p.read(p.ROOT/'examples/token-bucket/job.json');job['presentation']['zoom']=8
        with self.assertRaises(ValueError):p.validate(job)
    def test_cinema_build_blocks_overflow_and_exports_matching_srt_and_engine(self):
        with tempfile.TemporaryDirectory() as temporary:
            target=Path(temporary)
            job,timing=self.make_timing()
            timing['captions']['cues'][0]['text']='字'*40
            p.write(target/'job.json',job);p.write(target/'timing.json',timing)
            (target/'mechanism.mjs').write_text('export function mount(){return {frame(){}};}')
            args=SimpleNamespace(project=str(target),theme_file=None,topic_pack_file=None,force=False)
            with self.assertRaisesRegex(ValueError,'Cinema subtitle layout'):
                p.build(args)
            self.assertFalse((target/'index.html').exists())
            for cue in timing['captions']['cues']:cue['text']='测试字幕'
            p.write(target/'timing.json',timing)
            p.build(args)
            self.assertTrue((target/'subtitles.mjs').is_file())
            self.assertEqual((target/'cinematic.srt').read_text(),(target/'narration.srt').read_text())
            self.assertEqual(p.read(target/'subtitle-qc.json')['style'],'cinema')
if __name__=='__main__':unittest.main()
