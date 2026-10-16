#!/usr/bin/env python3
"""Prepare an independent local voice runtime. Model weights are never downloaded."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
DEFAULT_HOME=Path('~/.local/share/anatomy-voice-engine').expanduser()
DEFAULT_CONFIG=Path('~/.config/anatomy-voice-engine/config.json').expanduser()
DEFAULT_MODEL=Path('~/Models/AnatomyVoice/VoxCPM2-8bit').expanduser()
APP_FILES=['scripts/voice_engine.py','scripts/local_voice_worker.py','scripts/check_voice_model.py',
           'models/voxcpm2-8bit.manifest.json','requirements-voice-engine.txt','LICENSE','THIRD_PARTY_NOTICES.md','VERSION']


def prepare(home, config_path, model_dir, install_deps=False, metadata=False, dry_run=False):
    home,config_path,model_dir=(Path(p).expanduser().absolute() for p in (home,config_path,model_dir))
    if home.is_symlink() or config_path.is_symlink() or model_dir.is_symlink():
        raise ValueError('Choose ordinary runtime, config and model paths; existing symlinks are preserved.')
    for name in APP_FILES:
        if not (ROOT/name).is_file():raise ValueError('Source package is missing a voice engine resource.')
    python=home/'.venv/bin/python'
    manifest=json.loads((ROOT/'models/voxcpm2-8bit.manifest.json').read_text())
    result={'runtime':str(home),'config':str(config_path),'model_directory':str(model_dir),
            'python':str(python),'endpoint':'http://127.0.0.1:3931/v1/audio/speech',
            'dependency_install_requested':install_deps,'metadata_download_requested':metadata,
            'weights_downloaded':False,'weight_download':next(e['url'] for e in manifest['files'] if e['name']=='model.safetensors')}
    if dry_run:return {**result,'dry_run':True}
    if install_deps and (platform.system()!='Darwin' or platform.machine().lower()!='arm64'):
        raise ValueError('The optional MLX dependency environment requires native Apple Silicon macOS.')
    home.mkdir(parents=True,exist_ok=True);model_dir.mkdir(parents=True,exist_ok=True)
    app=home/'app'
    if app.is_symlink():raise ValueError('Refusing a symlink application directory.')
    if app.exists():
        # Backups are outside the discoverable Skill folders and never include model weights.
        tag=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        shutil.copytree(app,home/'backups'/tag)
    for name in APP_FILES:
        relative=Path(name).name if name.startswith('scripts/') else name
        target=app/relative;target.parent.mkdir(parents=True,exist_ok=True)
        if target.is_symlink():raise ValueError('Refusing a symlink application file.')
        shutil.copy2(ROOT/name,target)
    if config_path.exists():
        json.loads(config_path.read_text())
        result['configuration']='existing_preserved'
    else:
        config=json.loads((ROOT/'voice-engine.config.example.json').read_text())
        config['python_path']=str(python);config['model_path']=str(model_dir)
        config_path.parent.mkdir(parents=True,exist_ok=True)
        with config_path.open('x') as stream:json.dump(config,stream,ensure_ascii=False,indent=2);stream.write('\n')
        config_path.chmod(0o600);result['configuration']='created'
    if install_deps:
        uv=shutil.which('uv')
        if not uv:raise ValueError('Install uv first, or prepare the dedicated Python environment manually.')
        if not python.is_file():
            if (home/'.venv').exists():raise ValueError('Existing incomplete environment preserved; choose another runtime home.')
            subprocess.run([uv,'venv','--python','3.12',str(home/'.venv')],check=True)
        subprocess.run([uv,'pip','install','--python',str(python),'-r',str(ROOT/'requirements-voice-engine.txt')],check=True)
        result['dependencies']='installed_in_dedicated_environment'
    if metadata:
        for entry in manifest['files']:
            if entry['name']=='model.safetensors':continue
            destination=model_dir/entry['name']
            if destination.is_symlink():raise ValueError('Refusing a symlink model metadata file.')
            if destination.exists():raw=destination.read_bytes()
            else:
                with urllib.request.urlopen(entry['url'],timeout=60) as response:raw=response.read(8*1024*1024+1)
            if len(raw)!=entry['size'] or hashlib.sha256(raw).hexdigest()!=entry['sha256']:
                raise ValueError('Model metadata does not match the pinned official manifest; existing file preserved.')
            json.loads(raw)
            if not destination.exists():
                with destination.open('xb') as stream:stream.write(raw)
        result['model_metadata']='four_files_verified'
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--home',type=Path,default=DEFAULT_HOME)
    p.add_argument('--config',type=Path,default=DEFAULT_CONFIG)
    p.add_argument('--model-dir',type=Path,default=DEFAULT_MODEL)
    p.add_argument('--install-deps',action='store_true')
    p.add_argument('--prepare-metadata',action='store_true')
    p.add_argument('--dry-run',action='store_true')
    args=p.parse_args(argv)
    try:
        result=prepare(args.home,args.config,args.model_dir,args.install_deps,args.prepare_metadata,args.dry_run)
        print(json.dumps(result,ensure_ascii=False,indent=2));return 0
    except (OSError,ValueError,KeyError,subprocess.CalledProcessError):
        print('Local voice setup failed. Existing model weights and configuration are preserved; inspect paths and the dedicated dependency environment.',file=sys.stderr)
        return 1


if __name__=='__main__':raise SystemExit(main())
