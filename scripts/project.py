#!/usr/bin/env python3
"""Create and validate portable Anatomy video projects; never generates factual content."""
import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
_subtitle_spec = importlib.util.spec_from_file_location('anatomy_subtitle_tools', ROOT/'scripts/subtitle_tools.py')
_subtitle_module = importlib.util.module_from_spec(_subtitle_spec)
_subtitle_spec.loader.exec_module(_subtitle_module)
cinema_enabled = _subtitle_module.cinema_enabled
compile_subtitles = _subtitle_module.compile_subtitles
ID = re.compile(r'^[a-z][a-z0-9-]{0,63}$')
TOKEN = re.compile(r'^--anatomy-(page|card|text|strong|muted|paper|face|top|shade-[0-3]|lit-top|lit-shade|hi|mid|lo|faint|lit|halo|dot|red|green|blue)$')
COLOR = re.compile(r'^#[0-9a-fA-F]{6}$')
VOICE_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$')
TTS_PROVIDERS = ('doubao','local','say','external')

def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))

def write(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')

def number(value, label, low, high):
    if isinstance(value, bool) or not isinstance(value, (int,float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f'{label} must be a finite number in [{low}, {high}]')

def validate_presentation(options, scene=False):
    if not isinstance(options,dict): raise ValueError('presentation must be an object')
    allowed={'caption_placement','zoom','focus'} if scene else {'captions','reduced_motion','zoom','focus'}
    if set(options)-allowed: raise ValueError('unknown presentation field')
    placements={'auto','bottom','left','right','top'}
    if 'captions' in options:
        captions=options['captions']
        if not isinstance(captions,dict) or set(captions)-{'mode','placement','style','keep_together'}: raise ValueError('invalid captions options')
        if captions.get('mode','phrase') not in {'phrase','scene'}: raise ValueError('caption mode must be phrase or scene')
        if captions.get('style','cinema') not in {'cinema','kinetic'}: raise ValueError('caption style must be cinema or kinetic')
        if captions.get('placement','auto') not in placements: raise ValueError('invalid caption placement')
        terms=captions.get('keep_together',[])
        if not isinstance(terms,list) or len(terms)>64 or any(not isinstance(term,str) or not term.strip() or len(term)>100 or any(ord(c)<32 for c in term) for term in terms): raise ValueError('caption keep_together must contain short nonempty terms')
    if 'caption_placement' in options and options['caption_placement'] not in placements: raise ValueError('invalid scene caption placement')
    if 'reduced_motion' in options and not isinstance(options['reduced_motion'],bool): raise ValueError('reduced_motion must be boolean')
    if 'zoom' in options: number(options['zoom'],'zoom',1,1.12)
    if 'focus' in options:
        focus=options['focus']
        if not isinstance(focus,list) or len(focus)!=2: raise ValueError('focus must be [x,y]')
        for value in focus: number(value,'focus',0,1)
    return options

def validate(job):
    if job.get('schema_version') != 1: raise ValueError('schema_version must be 1')
    if not isinstance(job.get('topic'),str) or not job['topic'].strip(): raise ValueError('topic required')
    if not re.fullmatch(r'[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*',job.get('language','')): raise ValueError('language must be a BCP-47 style tag')
    v=job.get('video',{})
    for key in ['width','height']:
        number(v.get(key),key,240,3840)
        if not isinstance(v[key],int) or v[key]%2: raise ValueError(f'{key} must be an even integer')
    number(v.get('fps'),'fps',1,60)
    if not isinstance(v['fps'],int): raise ValueError('fps must be integer')
    if job.get('content_ready') is not True: raise ValueError('Research and author scenes/model before setting content_ready=true')
    validate_presentation(job.get('presentation',{}))
    sources=job.get('sources',[])
    if not sources or any(not isinstance(s,dict) or not ID.fullmatch(s.get('id','')) or not str(s.get('url','')).startswith(('https://','http://','local:')) for s in sources): raise ValueError('sources require ids and https/http/local references')
    source_ids={s['id'] for s in sources}
    if len(source_ids)!=len(sources): raise ValueError('duplicate source id')
    scenes=job.get('scenes',[])
    if not isinstance(scenes,list) or not scenes: raise ValueError('scenes required')
    ids=set()
    for s in scenes:
        ident=s.get('id','')
        if not ID.fullmatch(ident) or ident in ids: raise ValueError('scene ids must be unique safe lowercase ids')
        ids.add(ident)
        for key in ['title','narration','caption']:
            if not isinstance(s.get(key),str) or not s[key].strip(): raise ValueError(f'{ident}.{key} required')
        if not isinstance(s.get('source_ids'),list) or not s['source_ids'] or not set(s['source_ids'])<=source_ids: raise ValueError(f'{ident}.source_ids must resolve')
        number(s.get('hold_seconds',0),'hold_seconds',0,30)
        validate_presentation(s.get('presentation',{}),scene=True)
    return job

def theme_for(job, path=None):
    name=job.get('theme','anatomy-official-light')
    if not ID.fullmatch(name): raise ValueError('invalid theme id')
    t=read(path or ROOT/'themes'/f'{name}.json')
    if set(t)-{'schema_version','id','base','tokens'}: raise ValueError('theme contains unsupported fields')
    if t.get('schema_version')!=1 or t.get('base') not in ['light','dark'] or not ID.fullmatch(t.get('id','')): raise ValueError('invalid theme manifest')
    if t['id']!=name: raise ValueError('theme id differs from job.theme')
    if not isinstance(t.get('tokens'),dict) or any(not TOKEN.fullmatch(k) or not isinstance(v,str) or not COLOR.fullmatch(v) for k,v in t['tokens'].items()): raise ValueError('theme tokens must be allowed colors in #RRGGBB; no executable CSS')
    return t

def topic_pack_for(job, path=None, project=None):
    name=job.get('topic_pack','general-science')
    if not isinstance(name,str) or not ID.fullmatch(name): raise ValueError('invalid topic_pack id')
    cached=Path(project)/'topic-pack.json' if project else None
    candidate=path or (cached if cached and cached.is_file() else ROOT/'topic-packs'/f'{name}.json')
    pack=read(candidate)
    keys={'schema_version','id','audience','research_questions','explanation_pattern','avoid'}
    if set(pack)!=keys or pack['schema_version']!=1 or pack['id']!=name or not isinstance(pack['audience'],str) or not pack['audience'].strip(): raise ValueError('invalid topic pack manifest')
    for key in ['research_questions','explanation_pattern','avoid']:
        if not isinstance(pack[key],list) or not pack[key] or any(not isinstance(v,str) or not v.strip() for v in pack[key]): raise ValueError('topic pack arrays require nonempty strings')
    return pack

def timing_for(job, timing):
    number(timing.get('duration'),'duration',0.01,3600)
    rows=timing.get('scenes',[])
    if [s.get('id') for s in rows]!=[s['id'] for s in job['scenes']]: raise ValueError('timing scene ids/order differ; regenerate TTS')
    previous=0
    for r in rows:
        for k in ['start','end','duration']: number(r.get(k),k,0,3600)
        if abs(r['start']-previous)>0.002 or r['end']<=r['start'] or abs((r['end']-r['start'])-r['duration'])>0.002: raise ValueError('timing must be positive and contiguous')
        previous=r['end']
    if abs(previous-timing['duration'])>0.002: raise ValueError('timing duration differs from scenes')
    # Narration changes must never silently reuse an obsolete audio track.
    if timing.get('job_fingerprint')!=narration_fingerprint(job): raise ValueError('narration changed; regenerate TTS')
    if job.get('tts',{}).get('segment_mode')=='phrases' and 'captions' not in timing: raise ValueError('phrase synthesis requires measured caption cues; regenerate TTS')
    if 'captions' in timing:
        captions=timing['captions']
        if not isinstance(captions,dict) or captions.get('alignment') not in {'measured-phrases','manual'} or not isinstance(captions.get('cues'),list) or not captions['cues']:
            raise ValueError('captions need declared phrase alignment and cues')
        scene_tracks={row['id']:row for row in rows}
        seen=set();previous_end=0
        for cue in captions['cues']:
            if not isinstance(cue,dict) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,95}',cue.get('id','')) or cue['id'] in seen: raise ValueError('caption cue ids must be unique safe identifiers')
            seen.add(cue['id'])
            if cue.get('scene_id') not in scene_tracks: raise ValueError('caption scene_id must resolve')
            if not isinstance(cue.get('text'),str) or not cue['text'].strip() or len(cue['text'])>300: raise ValueError('caption cue requires short text')
            number(cue.get('start'),'caption.start',0,3600);number(cue.get('end'),'caption.end',0,3600)
            track=scene_tracks[cue['scene_id']]
            if cue['end']<=cue['start'] or cue['start']<previous_end-0.002 or cue['start']<track['start']-0.002 or cue['end']>track['end']+0.002: raise ValueError('caption cues must be ordered within their scene')
            previous_end=cue['end']
    return timing

def narration_fingerprint(job):
    data={'language':job['language'],'tts':job.get('tts',{}),'scenes':[{k:s.get(k,0) for k in ['id','narration','hold_seconds']} for s in job['scenes']]}
    return hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()

def default_preferences_path():
    return Path.home()/'.config/anatomy-explainer/preferences.json'

def project_preferences(args):
    """Read only portable TTS choices; credentials and local paths never enter jobs."""
    explicit=getattr(args,'preferences_file',None)
    if getattr(args,'ignore_preferences',False):
        if explicit: raise ValueError('--ignore-preferences and --preferences-file are mutually exclusive')
        return {}
    path=Path(explicit).expanduser() if explicit else default_preferences_path()
    if not path.exists():
        if explicit: raise ValueError(f'Preferences file does not exist: {path}')
        return {}
    try:
        if path.stat().st_size>16384: raise ValueError('preferences file exceeds 16 KiB')
        def unique_object(pairs):
            result={}
            for key,value in pairs:
                if key in result: raise ValueError('duplicate preferences field')
                result[key]=value
            return result
        data=json.loads(path.read_text(encoding='utf-8'),object_pairs_hook=unique_object)
        if not isinstance(data,dict) or set(data)!={'schema_version','tts'}:
            raise ValueError('preferences require only schema_version and tts')
        if type(data['schema_version']) is not int or data['schema_version']!=1:
            raise ValueError('preferences schema_version must be 1')
        tts=data['tts']
        if not isinstance(tts,dict) or set(tts)-{'provider','local_voice','segment_mode'}:
            raise ValueError('preferences tts allows only provider, local_voice and segment_mode')
        if 'provider' in tts and (not isinstance(tts['provider'],str) or tts['provider'] not in TTS_PROVIDERS):
            raise ValueError('preferences tts.provider must be doubao, local, say or external')
        if 'local_voice' in tts and (not isinstance(tts['local_voice'],str) or not VOICE_ID.fullmatch(tts['local_voice'])):
            raise ValueError('preferences tts.local_voice must be a registered voice name without paths')
        if 'segment_mode' in tts and (not isinstance(tts['segment_mode'],str) or tts['segment_mode'] not in ('phrases','scene')):
            raise ValueError('preferences tts.segment_mode must be phrases or scene')
        return dict(tts)
    except (ValueError,OSError,UnicodeError) as error:
        raise ValueError(f'Invalid preferences file: {error}') from None

def init(args):
    dest=Path(args.project).resolve()
    if dest.exists(): raise ValueError('project already exists; choose a new directory')
    preferences=project_preferences(args)
    provider=getattr(args,'tts_provider',None)
    if provider is not None and provider not in TTS_PROVIDERS: raise ValueError('invalid TTS provider override')
    if args.example:
        job=read(ROOT/'examples'/args.example/'job.json')
    else:
        job={'schema_version':1,'topic':args.topic,'language':args.language,'theme':args.theme,'topic_pack':'general-science','audience':'普通成年观众','content_ready':False,'video':{'width':1920,'height':1080,'fps':30},'tts':{'provider':'doubao','segment_mode':'phrases'},'presentation':{'captions':{'mode':'phrase','style':'cinema','placement':'bottom'}},'sources':[],'scenes':[]}
    job['tts'].update(preferences)
    if provider is not None: job['tts']['provider']=provider
    if job['tts'].get('provider')=='external': job['tts']['segment_mode']='scene'
    dest.parent.mkdir(parents=True,exist_ok=True)
    dest.mkdir()
    if args.example:
        for name in ['job.json','mechanism.mjs','model.mjs','learning-checks.json','brief.json']:
            source=ROOT/'examples'/(name if name in ['learning-checks.json','brief.json'] else f'token-bucket/{name}')
            shutil.copy2(source,dest/name)
    else:
        (dest/'mechanism.mjs').write_text("export function mount(){ throw new Error('Author a topic-specific mechanism and pure frame(t) first. See SKILL.md.'); }\n")
    write(dest/'job.json',job)
    print(f'Created project: {dest}')

def build(args):
    dest=Path(args.project).resolve()
    job=validate(read(dest/'job.json'))
    theme=theme_for(job,args.theme_file)
    pack=topic_pack_for(job,args.topic_pack_file,dest)
    timing=timing_for(job,read(dest/'timing.json'))
    if not (dest/'mechanism.mjs').is_file(): raise ValueError('mechanism.mjs missing')
    targets=[dest/p for p in ['index.html','data.json','runtime.mjs','presentation.mjs','subtitles.mjs','video.css','assets','topic-pack.json','build-manifest.json','cinematic.srt','subtitle-qc.json']]
    if not args.force and any(p.exists() for p in targets): raise ValueError('build output exists; use --force after reviewing changes')
    for p in targets:
        if p.is_symlink(): raise ValueError('refusing a symlink build target')
    if (dest/'narration.srt').is_symlink(): raise ValueError('refusing a symlink subtitle target')
    subtitle_bundle=compile_subtitles(job,timing) if cinema_enabled(job) and timing.get('captions',{}).get('cues') else None
    if subtitle_bundle and any(issue.get('severity')=='error' for issue in subtitle_bundle['track'].get('issues',[])):
        raise ValueError('Cinema subtitle layout failed: review long cues or protected terms; do not shrink text or invent speech timing.')
    assets=dest/'assets'
    assets.mkdir(exist_ok=True)
    kit_dest=assets/'kit'
    if kit_dest.exists():
        if kit_dest.is_symlink(): raise ValueError('refusing symlink kit target')
        shutil.rmtree(kit_dest)
    shutil.copytree(ROOT/'vendor/anatomy/kit',kit_dest)
    shutil.copy2(ROOT/'vendor/anatomy/LICENSE',assets/'ANATOMY-LICENSE')
    for name in ['runtime.mjs','presentation.mjs','subtitles.mjs','video.css']:
        shutil.copy2(ROOT/'scripts'/name,dest/name)
    if subtitle_bundle:
        for name in ['cinematic.srt','narration.srt']:
            (dest/name).write_text(subtitle_bundle['srt'],encoding='utf-8')
        write(dest/'subtitle-qc.json',subtitle_bundle['track'])
    write(dest/'topic-pack.json',pack)
    write(dest/'data.json',{'job':job,'timing':timing,'theme':theme})
    (dest/'index.html').write_text('<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Anatomy explainer</title><link rel="stylesheet" href="assets/kit/iso.css"><link rel="stylesheet" href="video.css"></head><body class="iso"><main id="film"><div id="eyebrow"></div><h1 id="title"></h1><section class="iso-plate"><div class="iso-plate-corners"><span class="iso-plate-corner" data-corner="fig" id="figure-no"></span><span class="iso-plate-corner" data-corner="title" id="topic"></span><span class="iso-plate-corner" data-corner="hint" id="hint"></span><span class="iso-plate-corner" data-corner="readout" id="readout"></span></div><div id="mechanism"></div></section><p id="caption"></p><p id="subtitle"></p><footer id="source"></footer><div id="progress"></div></main><script type="module" src="runtime.mjs"></script></body></html>\n',encoding='utf-8')
    write(dest/'build-manifest.json',{'schema_version':1,'inputs':{name:hashlib.sha256((dest/name).read_bytes()).hexdigest() for name in ['job.json','timing.json','data.json']}})
    print(f'Built: {dest / "index.html"}')

def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='command',required=True)
    a=sub.add_parser('init'); a.add_argument('--project',required=True); a.add_argument('--topic',default='请填写主题'); a.add_argument('--language',default='zh-CN'); a.add_argument('--theme',default='anatomy-official-light'); a.add_argument('--example',choices=['token-bucket'])
    a.add_argument('--tts-provider',choices=TTS_PROVIDERS,help='Override the TTS provider for this new project')
    preferences=a.add_mutually_exclusive_group()
    preferences.add_argument('--preferences-file',help='Read an explicit local preferences file; missing files are errors')
    preferences.add_argument('--ignore-preferences',action='store_true',help='Use portable defaults without reading local preferences')
    for command in ['build','check']:
        a=sub.add_parser(command); a.add_argument('--project',required=True); a.add_argument('--theme-file'); a.add_argument('--topic-pack-file'); a.add_argument('--force',action='store_true')
    args=p.parse_args()
    try:
        if args.command=='init': init(args)
        elif args.command=='build': build(args)
        else:
            d=Path(args.project).resolve(); job=validate(read(d/'job.json')); theme_for(job,args.theme_file); topic_pack_for(job,args.topic_pack_file,d)
            if (d/'timing.json').exists(): timing_for(job,read(d/'timing.json'))
            print('Project contract passed (fact truth and artwork require review).')
        return 0
    except (ValueError,KeyError,TypeError,OSError) as e:
        print(f'Error: {e}',file=sys.stderr); return 1

if __name__=='__main__': raise SystemExit(main())
