#!/usr/bin/env python3
"""Install this child Skill into an explicit new directory; never overwrites."""
import argparse
from pathlib import Path
import shutil
import tempfile
import sys
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--target',required=True)
p.add_argument('--dry-run',action='store_true')
a=p.parse_args()
source=Path(__file__).resolve().parents[1]
target=Path(a.target).expanduser().absolute()
try:
    if target.exists() or target.is_symlink(): raise ValueError('Target exists; preserve it and choose a new target. No files changed.')
    canonical=target.resolve()
    if canonical.is_relative_to(source) or source.is_relative_to(canonical): raise ValueError('Source/target overlap')
    if not (source/'SKILL.md').is_file() or not (source/'vendor/anatomy/LICENSE').is_file(): raise ValueError('Incomplete source package')
    if a.dry_run: print('Dry-run passed; target does not exist. No files changed.')
    else:
        target.parent.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.anatomy-install-',dir=target.parent) as temporary:
            staged=Path(temporary)/'anatomy-explainer'
            # Apply at every directory level, including when installed from a clone.
            # Repository metadata and local environment files are never Skill assets.
            shutil.copytree(source,staged,ignore=shutil.ignore_patterns(
                '.git','.github','.env','.env.*','__pycache__','*.pyc','.DS_Store'))
            # Refuse overwrite even if another process created the destination during staging.
            target.mkdir()
            try:
                for child in staged.iterdir(): shutil.move(str(child),str(target/child.name))
            except Exception:
                shutil.rmtree(target); raise
        print(f'Installed: {target}')
except (OSError,ValueError) as error:
    print(f'Error: {error}',file=sys.stderr); raise SystemExit(1)
