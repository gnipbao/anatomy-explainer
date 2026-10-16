"""Bridge Python orchestration to the shared, dependency-free subtitle engine."""
import json
from pathlib import Path
import subprocess


def cinema_enabled(job):
    captions = job.get('presentation', {}).get('captions', {})
    return captions.get('style', 'cinema') == 'cinema' and captions.get('mode', 'phrase') != 'scene'


def compile_subtitles(job, timing):
    """Preserve measured cues; return frame-aligned display data and matching SRT."""
    script = Path(__file__).with_name('subtitles.mjs')
    command = '''import {pathToFileURL} from 'node:url';
const {buildCinemaTrack,subtitlesToSrt}=await import(pathToFileURL(process.argv[2]));
let input='';for await(const chunk of process.stdin)input+=chunk;
const {job,timing}=JSON.parse(input);const track=buildCinemaTrack(job,timing);
process.stdout.write(JSON.stringify({track,srt:subtitlesToSrt(track)}));'''
    try:
        result = subprocess.run(['node', '--input-type=module', '-e', command, 'anatomy-subtitle-bridge', str(script)],
                                input=json.dumps({'job': job, 'timing': timing}, ensure_ascii=False),
                                capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError('Cinema subtitle formatting needs Node.js and the bundled subtitle engine.') from error
    if result.returncode:
        raise ValueError('Cinema subtitle formatting failed; check the cue structure and video frame rate.')
    try:
        bundle = json.loads(result.stdout)
        if not isinstance(bundle['srt'], str) or not isinstance(bundle['track']['cues'], list):
            raise ValueError('Invalid subtitle result')
        return bundle
    except (ValueError, KeyError, TypeError) as error:
        raise ValueError('Cinema subtitle engine returned an invalid result.') from error
