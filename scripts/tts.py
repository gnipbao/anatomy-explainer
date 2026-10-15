#!/usr/bin/env python3
"""Synthesize scene narration, normalize real audio, and measure scene timing.

Standard library only; actual synthesis/normalization requires ffmpeg + ffprobe.
No credentials, network, audio generation, or duration guesses in --dry-run.
"""

import argparse
import base64
import binascii
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import uuid
import wave

ENDPOINT = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"
DEFAULT_VOICE = "zh_female_vv_uranus_bigtts"
DEFAULT_RESOURCE = "seed-tts-2.0"
OUTPUTS = ("audio", "narration-full.wav", "narration.srt", "timing.json")
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
LANG_RE = re.compile(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*\Z")

# Also support loading this script by file path, as the offline test suite does.
_segments_spec = importlib.util.spec_from_file_location("anatomy_text_segments", Path(__file__).with_name("text_segments.py"))
_segments_module = importlib.util.module_from_spec(_segments_spec)
_segments_spec.loader.exec_module(_segments_module)
split_phrases = _segments_module.split_phrases


class TTSError(Exception):
    """A deliberately safe, user-visible diagnostic (no raw remote errors)."""


def validate_job(job):
    if not isinstance(job, dict) or type(job.get("schema_version")) is not int or job["schema_version"] != 1:
        raise TTSError("job.json requires schema_version: 1")
    if not isinstance(job.get("topic"), str) or not job["topic"].strip():
        raise TTSError("topic must be a nonempty string")
    language = job.get("language")
    if not isinstance(language, str) or not LANG_RE.fullmatch(language):
        raise TTSError("language must be a language tag such as zh-CN, en, or ja-JP")
    tts = job.get("tts", {})
    if not isinstance(tts, dict):
        raise TTSError("tts must be an object")
    provider = tts.get("provider", "doubao")
    if provider not in ("doubao", "say", "external"):
        raise TTSError("tts.provider must be doubao, say, or external")
    segment_mode = tts.get("segment_mode", "scene")
    if segment_mode not in ("scene", "phrases"):
        raise TTSError("tts.segment_mode must be scene or phrases")
    if provider == "external" and segment_mode == "phrases":
        raise TTSError("external audio cannot provide measured phrase alignment; set tts.segment_mode='scene' or use a separate workflow with reviewed manual cues")
    for key in ("voice", "resource_id"):
        value = tts.get(key)
        if value is not None and (not isinstance(value, str) or not value.strip() or any(ord(c) < 32 for c in value) or len(value) > 200):
            raise TTSError("tts voice/resource_id must be nonempty, single-line strings")
    if provider == "doubao" and language.split("-")[0].lower() != "zh" and not tts.get("voice"):
        raise TTSError("Non-Chinese Doubao narration requires an explicit tts.voice with verified language support")
    scenes = job.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise TTSError("scenes must be a nonempty array")
    ids = set()
    for scene in scenes:
        if not isinstance(scene, dict):
            raise TTSError("each scene must be an object")
        sid = scene.get("id")
        if not isinstance(sid, str) or not ID_RE.fullmatch(sid) or sid in ids:
            raise TTSError("scene ids must be unique, 1-64 ASCII letters/digits/underscores/hyphens, starting with a letter or digit")
        ids.add(sid)
        if not isinstance(scene.get("title"), str) or not scene["title"].strip():
            raise TTSError("each scene requires a nonempty title")
        if not isinstance(scene.get("narration"), str) or not scene["narration"].strip():
            raise TTSError("each scene requires nonempty narration")
        hold = scene.get("hold_seconds", 0)
        if type(hold) not in (int, float) or not math.isfinite(hold) or hold < 0:
            raise TTSError("hold_seconds must be a finite, nonnegative number")
    return job


def read_job(project):
    try:
        with (project / "job.json").open(encoding="utf-8") as stream:
            return validate_job(json.load(stream))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise TTSError("Cannot read a valid UTF-8 job.json") from None


def dry_run_config(project, job):
    """Only public configuration; does not access os.environ at all."""
    tts = job.get("tts", {})
    provider = tts.get("provider", "doubao")
    result = {"dry_run": True, "project": str(project), "provider": provider,
              "language": job["language"], "alignment": "scene",
              "scenes": [{"id": s["id"], "characters": len(s["narration"]), "hold_seconds": s.get("hold_seconds", 0)} for s in job["scenes"]],
              "duration": None, "voice": tts.get("voice")}
    if tts.get("segment_mode", "scene") == "phrases":
        result["captions"] = {"alignment": "measured-phrases", "durations": None,
                              "phrases": [{"scene_id": s["id"], "texts": split_phrases(s["narration"], job["language"])} for s in job["scenes"]]}
    if provider == "doubao":
        result.update(endpoint=ENDPOINT, voice=tts.get("voice", DEFAULT_VOICE),
                      resource_id=tts.get("resource_id", DEFAULT_RESOURCE),
                      credential_status="not inspected; required only for synthesis",
                      credential_env_names=["DOUBAO_API_KEY", "DOUBAO_APP_ID", "DOUBAO_ACCESS_KEY"])
    return result


def build_auth_headers(tts):
    """Read secrets only in a real request; callers must never log this return value."""
    headers = {"Content-Type": "application/json", "X-Api-Resource-Id": tts.get("resource_id", DEFAULT_RESOURCE),
               "X-Api-Request-Id": str(uuid.uuid4())}
    api_key = os.environ.get("DOUBAO_API_KEY")
    if api_key:
        headers["X-Api-Key"] = api_key
    else:
        app_id = os.environ.get("DOUBAO_APP_ID")
        access_key = (os.environ.get("DOUBAO_ACCESS_KEY") or os.environ.get("DOUBAO_TTS_TOKEN")
                      or os.environ.get("VOLCENGINE_ACCESS_TOKEN"))
        if not app_id or not access_key:
            raise TTSError("Set DOUBAO_API_KEY, or both DOUBAO_APP_ID and DOUBAO_ACCESS_KEY, outside job.json")
        headers.update({"X-Api-App-Id": app_id, "X-Api-Access-Key": access_key})
    return headers


def parse_ndjson(lines):
    """Require the documented 20000000 completion frame AND nonempty audio."""
    audio = bytearray()
    complete = False
    for line in lines:
        if len(line) > 4 * 1024 * 1024:
            raise TTSError("Doubao response frame exceeds the size limit")
        if not line.strip():
            continue
        try:
            frame = json.loads(line)
        except (ValueError, UnicodeError):
            raise TTSError("Doubao returned malformed NDJSON") from None
        if not isinstance(frame, dict) or type(frame.get("code")) is not int:
            raise TTSError("Doubao response is missing an integer status code")
        code = frame["code"]
        if code == 20000000:
            complete = True
            break
        if code != 0:
            # Remote messages may echo sensitive data. Never expose them.
            raise TTSError("Doubao synthesis failed (status %d); response details withheld" % code)
        data = frame.get("data")
        if data is not None:
            try:
                if not isinstance(data, str):
                    raise ValueError()
                audio.extend(base64.b64decode(data, validate=True))
            except (ValueError, binascii.Error):
                raise TTSError("Doubao returned invalid base64 audio") from None
            if len(audio) > 128 * 1024 * 1024:
                raise TTSError("Doubao audio exceeds the per-scene size limit")
    if not complete:
        raise TTSError("Doubao stream ended without its completion frame; partial audio discarded")
    if not audio:
        raise TTSError("Doubao completed without any audio")
    return bytes(audio)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward a credential-bearing request to another URL.


def synthesize_doubao(scene, tts, destination):
    payload = {"user": {"uid": "anatomy-explainer"}, "req_params": {
        "text": scene["narration"], "speaker": tts.get("voice", DEFAULT_VOICE),
        "audio_params": {"format": "mp3", "sample_rate": 24000}}}
    request = urllib.request.Request(ENDPOINT, data=json.dumps(payload).encode("utf-8"),
                                     headers=build_auth_headers(tts), method="POST")
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=120) as response:
            # readline bounds a hostile/accidental unbounded frame before decoding.
            chunks = iter(lambda: response.readline(4 * 1024 * 1024 + 1), b"")
            audio = parse_ndjson(chunks)
    except urllib.error.HTTPError as exc:
        raise TTSError("Doubao HTTP request failed (status %d); response details withheld" % exc.code) from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise TTSError("Doubao connection/authentication failed; connection details withheld") from None
    destination.write_bytes(audio)


def run_command(args, label):
    try:
        return subprocess.run(args, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True).stdout
    except (OSError, subprocess.CalledProcessError):
        raise TTSError(label + " failed; command output withheld") from None


def choose_say_voice(language, explicit=None):
    if sys.platform != "darwin" or not shutil.which("say"):
        raise TTSError("provider say requires macOS and an installed speech voice; use external elsewhere")
    listing = run_command(["say", "-v", "?"], "Listing installed macOS voices")
    voices = []
    for line in listing.splitlines():
        match = re.match(r"^(.*?)\s+([a-z]{2,3}[_-][A-Za-z0-9_-]+)\s+#", line)
        if match:
            voices.append((match[1].strip(), match[2].replace("_", "-").lower()))
    wanted = language.lower()
    if explicit:
        matches = [(name, tag) for name, tag in voices if name == explicit]
        if not matches:
            raise TTSError("tts.voice is not installed; inspect available voices with: say -v '?' ")
        if all(tag.split("-")[0] != wanted.split("-")[0] for _, tag in matches):
            raise TTSError("Installed macOS voice language does not match job.language")
        return matches[0][0]
    exact = [name for name, tag in voices if tag == wanted]
    primary = [name for name, tag in voices if tag.split("-")[0] == wanted.split("-")[0]]
    if not (exact or primary):
        raise TTSError("No installed macOS voice matches job.language; install one or use external")
    return (exact or primary)[0]


def external_audio(project, scene_id):
    project = project.resolve()
    root = project / "audio-input"
    candidates = [root / (scene_id + ext) for ext in (".wav", ".mp3", ".m4a")]
    found = [path for path in candidates if path.is_file()]
    if len(found) != 1:
        raise TTSError("external requires exactly one audio-input/<scene-id>.wav, .mp3, or .m4a for every scene")
    if not found[0].resolve().is_relative_to(project):
        raise TTSError("external audio must resolve inside the project")
    return found[0]


def probe_duration(path):
    output = run_command(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)], "Measuring audio with ffprobe")
    try:
        duration = float(output.strip())
    except ValueError:
        raise TTSError("ffprobe did not return an audio duration") from None
    if not math.isfinite(duration) or duration <= 0:
        raise TTSError("Audio has no positive, finite measured duration")
    return duration


def normalize(source, destination, hold):
    command = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source), "-map", "0:a:0", "-vn", "-sn", "-dn"]
    if hold:
        command += ["-af", "apad=pad_dur=%.9f" % hold]
    command += ["-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(destination)]
    run_command(command, "Normalizing scene audio")


def synthesize_source(scene, tts, provider, say_voice, workspace, stem):
    """Synthesize one real utterance and reject empty audio before any padding."""
    if provider == "doubao":
        source = workspace / (stem + ".mp3")
        synthesize_doubao(scene, tts, source)
    else:
        text_file = workspace / (stem + ".txt")
        text_file.write_text(scene["narration"], encoding="utf-8")
        source = workspace / (stem + ".aiff")
        run_command(["say", "-v", say_voice, "-o", str(source), "-f", str(text_file)], "macOS speech synthesis")
    require_source_audio(source, provider)
    return source


def require_source_audio(source, provider):
    # macOS say can exit 0 with an empty AIFF when its speech service is
    # sandboxed. A requested hold must never disguise an empty synthesis.
    try:
        return probe_duration(source)
    except TTSError:
        if provider == "say":
            raise TTSError("macOS say produced no usable audio. The speech service may be restricted by the sandbox; run local speech synthesis in an allowed environment or provide verified external audio") from None
        raise


def concatenate_wav(paths, output):
    with wave.open(str(output), "wb") as merged:
        merged.setnchannels(2)
        merged.setsampwidth(2)
        merged.setframerate(48000)
        for path in paths:
            with wave.open(str(path), "rb") as source:
                if (source.getnchannels(), source.getsampwidth(), source.getframerate(), source.getcomptype()) != (2, 2, 48000, "NONE"):
                    raise TTSError("Normalized audio has unexpected PCM parameters")
                if source.getnframes() <= 0:
                    raise TTSError("Normalized audio has no positive measured duration")
                while True:
                    chunk = source.readframes(65536)
                    if not chunk:
                        break
                    merged.writeframesraw(chunk)


def srt_time(seconds):
    milliseconds = round(seconds * 1000)
    hours, milliseconds = divmod(milliseconds, 3600000)
    minutes, milliseconds = divmod(milliseconds, 60000)
    whole, milliseconds = divmod(milliseconds, 1000)
    return "%02d:%02d:%02d,%03d" % (hours, minutes, whole, milliseconds)


def check_outputs(project, force):
    for name in OUTPUTS:
        path = project / name
        if path.is_symlink():
            raise TTSError("Refusing symlinked output paths")
        if path.exists() and not force:
            raise TTSError("Output already exists; use --force to replace a previous generated audio bundle")
        if path.exists() and ((name == "audio") != path.is_dir()):
            raise TTSError("Output path has the wrong file/directory type")


def publish_bundle(project, stage):
    """Timing is the last commit marker. Roll back all paths if a move fails."""
    backup = stage / ".previous"
    backup.mkdir()
    installed = []
    saved = []
    try:
        for name in OUTPUTS:
            target = project / name
            if target.exists():
                os.replace(target, backup / name)
                saved.append(name)
            os.replace(stage / name, target)
            installed.append(name)
    except OSError:
        for name in reversed(installed):
            target = project / name
            shutil.rmtree(target) if target.is_dir() else target.unlink(missing_ok=True)
        for name in saved:
            os.replace(backup / name, project / name)
        raise TTSError("Could not commit audio bundle; previous outputs restored") from None


def execute(project, job, force=False):
    project = project.expanduser().resolve()
    validate_job(job)
    if job.get("content_ready") is not True:
        raise TTSError("Set content_ready: true only after reviewing narration and scene content")
    check_outputs(project, force)
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise TTSError("Install ffmpeg and ffprobe before synthesizing audio")
    tts = job.get("tts", {})
    provider = tts.get("provider", "doubao")
    say_voice = choose_say_voice(job["language"], tts.get("voice")) if provider == "say" else None
    # Validate all external inputs before doing any conversion.
    external = {s["id"]: external_audio(project, s["id"]) for s in job["scenes"]} if provider == "external" else {}
    with tempfile.TemporaryDirectory(prefix=".tts-stage-", dir=project) as temporary:
        stage = Path(temporary)
        (stage / "audio").mkdir()
        measured = []
        subtitle_blocks = []
        caption_cues = []
        phrase_mode = tts.get("segment_mode", "scene") == "phrases"
        elapsed = 0.0
        paths = []
        for index, scene in enumerate(job["scenes"], 1):
            sid = scene["id"]
            scene_cues = []
            if phrase_mode:
                phrase_directory = stage / (sid + "-phrases")
                phrase_directory.mkdir()
                phrase_paths = []
                phrase_elapsed = 0.0
                for phrase_index, phrase in enumerate(split_phrases(scene["narration"], job["language"]), 1):
                    phrase_id = sid + "-p%03d" % phrase_index
                    utterance = dict(scene, narration=phrase)
                    source = synthesize_source(utterance, tts, provider, say_voice, phrase_directory, phrase_id)
                    phrase_path = phrase_directory / (phrase_id + ".wav")
                    normalize(source, phrase_path, 0)
                    phrase_duration = probe_duration(phrase_path)
                    scene_cues.append({"id": phrase_id, "scene_id": sid,
                                       "start": round(elapsed + phrase_elapsed, 6),
                                       "end": round(elapsed + phrase_elapsed + phrase_duration, 6),
                                       "text": " ".join(phrase.split())})
                    phrase_elapsed += phrase_duration
                    phrase_paths.append(phrase_path)
                if not phrase_paths:
                    raise TTSError("Narration produced no nonempty phrases")
                source = phrase_directory / "scene-unpadded.wav"
                concatenate_wav(phrase_paths, source)
                require_source_audio(source, provider)
            elif provider in ("doubao", "say"):
                source = synthesize_source(scene, tts, provider, say_voice, stage, sid)
            else:
                source = external[sid]
                require_source_audio(source, provider)
            destination = stage / "audio" / (sid + ".wav")
            normalize(source, destination, scene.get("hold_seconds", 0))
            duration = probe_duration(destination)
            end = elapsed + duration
            measured.append({"id": sid, "start": round(elapsed, 6), "end": round(end, 6),
                             "duration": round(duration, 6), "audio": "audio/" + sid + ".wav"})
            if phrase_mode:
                if abs(duration - phrase_elapsed - scene.get("hold_seconds", 0)) > max(0.001, len(scene_cues) / 48000):
                    raise TTSError("Measured scene audio duration disagrees with phrase audio and hold")
                for cue in scene_cues:
                    caption_cues.append(cue)
                    subtitle_blocks.append("%d\n%s --> %s\n%s\n" % (len(caption_cues), srt_time(cue["start"]), srt_time(cue["end"]), cue["text"]))
            else:
                text = " ".join(scene["narration"].split())
                subtitle_blocks.append("%d\n%s --> %s\n%s\n" % (index, srt_time(elapsed), srt_time(end), text))
            elapsed = end
            paths.append(destination)
        concatenate_wav(paths, stage / "narration-full.wav")
        full_duration = probe_duration(stage / "narration-full.wav")
        if abs(full_duration - elapsed) > max(0.001, len(paths) / 48000):
            raise TTSError("Measured full audio duration disagrees with scene audio")
        fingerprint_input = {"language": job["language"], "tts": job.get("tts", {}),
                             "scenes": [{k: s.get(k, 0) for k in ("id", "narration", "hold_seconds")} for s in job["scenes"]]}
        fingerprint = hashlib.sha256(json.dumps(fingerprint_input, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        timing = {"schema_version": 1, "provider": provider, "language": job["language"], "alignment": "scene", "job_fingerprint": fingerprint,
                  "duration": round(full_duration, 6), "scenes": measured}
        if phrase_mode:
            timing["captions"] = {"alignment": "measured-phrases", "cues": caption_cues}
        (stage / "narration.srt").write_text("\n".join(subtitle_blocks), encoding="utf-8")
        (stage / "timing.json").write_text(json.dumps(timing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        check_outputs(project, force)
        publish_bundle(project, stage)
    return timing


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true", help="Validate and print public configuration only")
    parser.add_argument("--force", action="store_true", help="Replace the entire previous generated audio bundle")
    args = parser.parse_args(argv)
    try:
        project = args.project.expanduser().resolve()
        job = read_job(project)
        if args.dry_run:
            print(json.dumps(dry_run_config(project, job), ensure_ascii=False, indent=2))
        else:
            timing = execute(project, job, args.force)
            summary = {"ok": True, "alignment": "scene", "duration": timing["duration"], "scenes": len(timing["scenes"])}
            if "captions" in timing:
                summary["captions"] = {"alignment": timing["captions"]["alignment"], "cues": len(timing["captions"]["cues"])}
            print(json.dumps(summary))
        return 0
    except TTSError as exc:
        print("TTS error: " + str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("TTS interrupted; incomplete staging files discarded", file=sys.stderr)
        return 130
    except Exception:
        # Unknown library/OS errors must never expose request headers or credentials.
        print("TTS failed; internal details withheld. Check job.json, dependencies, and file permissions.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
