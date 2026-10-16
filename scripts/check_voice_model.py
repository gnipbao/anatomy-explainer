#!/usr/bin/env python3
"""Verify manually downloaded VoxCPM2 files; no imports of MLX or networking."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
# Source packages keep scripts/; the standalone runtime places this file in app/.
_ADJACENT_MANIFEST = Path(__file__).resolve().parent/'models/voxcpm2-8bit.manifest.json'
DEFAULT_MANIFEST = (_ADJACENT_MANIFEST if _ADJACENT_MANIFEST.is_file()
                    else ROOT/'models/voxcpm2-8bit.manifest.json')
DEFAULT_MODEL = Path('~/Models/AnatomyVoice/VoxCPM2-8bit').expanduser()
NAMES = {'config.json','tokenizer.json','tokenizer_config.json','special_tokens_map.json','model.safetensors'}


def check(model_dir, manifest_path=DEFAULT_MANIFEST):
    model_dir = Path(model_dir).expanduser().resolve()
    manifest = json.loads(Path(manifest_path).read_text())
    entries = manifest.get('files', [])
    if len(entries) != len(NAMES) or {e.get('name') for e in entries} != NAMES:
        raise ValueError('The manifest must contain exactly the five known model filenames.')
    checks = []
    for entry in entries:
        path = model_dir/entry['name']
        item = {'name':entry['name'],'expected_bytes':entry['size'],'ok':False}
        if not path.is_file():
            item['status']='missing'
        elif path.stat().st_size != entry['size']:
            item.update(status='size_mismatch',actual_bytes=path.stat().st_size)
        else:
            digest=hashlib.sha256()
            with path.open('rb') as stream:
                for chunk in iter(lambda:stream.read(8*1024*1024),b''):digest.update(chunk)
            item['ok']=digest.hexdigest()==entry['sha256']
            item['status']='verified' if item['ok'] else 'sha256_mismatch'
        checks.append(item)
    return {'ok':all(c['ok'] for c in checks),'model_id':manifest['model_id'],'revision':manifest['revision'],
            'checks':checks,'synthesis_passed':False,'downloads_performed':False}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-dir',type=Path,default=DEFAULT_MODEL)
    parser.add_argument('--manifest',type=Path,default=DEFAULT_MANIFEST)
    args=parser.parse_args(argv)
    try:
        result=check(args.model_dir,args.manifest)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0 if result['ok'] else 1
    except (OSError,ValueError,KeyError,TypeError):
        print('Cannot verify the local model files or manifest.',file=sys.stderr)
        return 1


if __name__=='__main__':raise SystemExit(main())
