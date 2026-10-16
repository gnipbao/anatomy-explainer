#!/usr/bin/env python3
"""Independent, loopback-only voice service. Model execution stays in another Python.

This module uses the standard library only. It never downloads a model, installs a
package, changes a global TTS engine, or guesses subtitle timestamps.
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
import wave

DEFAULT_CONFIG = Path("~/.config/anatomy-voice-engine/config.json").expanduser()
MAX_BODY = 128 * 1024
MAX_TEXT = 12000
MAX_WAV = 128 * 1024 * 1024
WORKER = Path(__file__).resolve().with_name("local_voice_worker.py")
VOICE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
LANGUAGE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
GENERATION_FIELDS = {"seed", "cfg_value", "inference_timesteps", "max_tokens"}
VOICE_FIELDS = {"id", "reference_audio", "prompt_audio", "prompt_text", "instruct", "language"}


class EngineError(Exception):
    """Safe, actionable error; never contains worker stderr or narration."""


class BusyError(EngineError):
    pass


class NotReadyError(EngineError):
    pass


def _object(value, name):
    if not isinstance(value, dict):
        raise EngineError(f"{name} must be a JSON object")
    return value


def _integer(value, minimum, maximum, name):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise EngineError(f"{name} must be an integer from {minimum} to {maximum}")
    return value


def _number(value, minimum, maximum, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise EngineError(f"{name} must be a number from {minimum} to {maximum}")
    return value


def _text(value, name, maximum=MAX_TEXT, allow_empty=False):
    if not isinstance(value, str) or len(value) > maximum or (not allow_empty and not value.strip()):
        raise EngineError(f"{name} must be nonempty text of at most {maximum} characters")
    if "\x00" in value:
        raise EngineError(f"{name} cannot contain NUL characters")
    return value


def _path(value, name):
    return Path(_text(value, name, 4096)).expanduser().resolve()


def _loopback(host):
    # A literal address avoids host resolution and rebinding surprises.
    try:
        return ipaddress.ip_address(host).is_loopback
    except (ValueError, TypeError):
        return False


def validate_voice(value, default_id):
    voice = dict(_object(value, "voice"))
    if set(voice) - VOICE_FIELDS:
        raise EngineError("voice has unsupported configuration fields")
    voice_id = voice.get("id", default_id)
    if not isinstance(voice_id, str) or not VOICE_ID.fullmatch(voice_id):
        raise EngineError("voice id must be a safe identifier")
    voice["id"] = voice_id
    for name in ("instruct", "prompt_text"):
        if name in voice:
            _text(voice[name], name, 12000)
    for name in ("reference_audio", "prompt_audio"):
        if name in voice:
            voice[name] = str(_path(voice[name], name))
    if ("prompt_audio" in voice) != ("prompt_text" in voice):
        raise EngineError("Continuation requires both prompt_audio and its exact prompt_text")
    if "language" in voice:
        validate_language(voice["language"])
    return voice


def validate_language(value):
    if not isinstance(value, str) or not LANGUAGE.fullmatch(value):
        raise EngineError("language must be a BCP-47-like language code, for example zh-CN")
    return value


def load_config(path=DEFAULT_CONFIG):
    source = Path(path).expanduser().resolve()
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise EngineError("Voice engine config is missing; create it from the provided config example") from None
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise EngineError("Cannot read valid UTF-8 JSON voice engine config") from None
    raw = dict(_object(raw, "config"))
    allowed = {"schema_version", "host", "port", "backend", "python_path", "model_path", "voice", "voices", "generation", "timeout_seconds"}
    if set(raw) - allowed:
        raise EngineError("config has unsupported fields")
    if raw.get("schema_version") != 1 or isinstance(raw.get("schema_version"), bool):
        raise EngineError("config schema_version must be 1")
    host = raw.get("host", "127.0.0.1")
    if not _loopback(host):
        raise EngineError("host must be a literal loopback address; remote binding is disabled")
    backend = raw.get("backend", "voxcpm2_mlx")
    if backend not in BACKENDS:
        raise EngineError("Backend is not implemented; available backend: voxcpm2_mlx")
    for name in ("python_path", "model_path"):
        if name not in raw:
            raise EngineError(f"config requires {name}")
    # A venv Python is commonly a symlink. Resolving it runs the base interpreter
    # outside that environment and loses its MLX dependencies.
    raw["python_path"] = str(Path(_text(raw["python_path"], "python_path", 4096)).expanduser().absolute())
    raw["model_path"] = str(_path(raw["model_path"], "model_path"))
    raw["host"] = host
    raw["port"] = _integer(raw.get("port", 3931), 1, 65535, "port")
    raw["backend"] = backend
    raw["timeout_seconds"] = _integer(raw.get("timeout_seconds", 1800), 1, 7200, "timeout_seconds")
    primary = validate_voice(raw.get("voice", {"id": "default"}), "default")
    voices = {primary["id"]: primary}
    for name, value in _object(raw.get("voices", {}), "voices").items():
        if not isinstance(name, str) or not VOICE_ID.fullmatch(name):
            raise EngineError("voices keys must be safe identifiers")
        validated = validate_voice(value, name)
        if validated["id"] != name or name in voices:
            raise EngineError("voices must have unique keys matching their ids")
        voices[name] = validated
    generation = dict(_object(raw.get("generation", {}), "generation"))
    if set(generation) - GENERATION_FIELDS:
        raise EngineError("generation has unsupported fields")
    generation = {"seed": 42, "cfg_value": 2.0, "inference_timesteps": 10, "max_tokens": 2000, **generation}
    _integer(generation["seed"], 0, 2**32 - 1, "seed")
    _number(generation["cfg_value"], 0, 10, "cfg_value")
    if generation["cfg_value"] == 0:
        raise EngineError("cfg_value must be greater than 0")
    _integer(generation["inference_timesteps"], 1, 100, "inference_timesteps")
    _integer(generation["max_tokens"], 1, 6500, "max_tokens")
    raw["voice"] = primary
    raw["voices"] = voices
    raw["generation"] = generation
    raw["_config_path"] = str(source)
    return raw


def inspect_wav(path):
    """Read actual PCM frames; a header-only, truncated or silent file is not speech."""
    path = Path(path)
    try:
        if not path.is_file() or not 44 < path.stat().st_size <= MAX_WAV:
            raise EngineError("Worker did not produce a nonempty WAV within the size limit")
        with wave.open(str(path), "rb") as audio:
            frames, rate = audio.getnframes(), audio.getframerate()
            channels, width = audio.getnchannels(), audio.getsampwidth()
            if audio.getcomptype() != "NONE" or channels not in (1, 2) or width not in (1, 2, 3, 4) or not 8000 <= rate <= 192000 or frames < rate * 0.05:
                raise EngineError("Worker WAV has unsupported or empty PCM audio")
            payload = audio.readframes(frames)
            if len(payload) != frames * channels * width:
                raise EngineError("Worker WAV is truncated")
            silence = 128 if width == 1 else 0
            if not any(byte != silence for byte in payload):
                raise EngineError("Worker WAV contains only digital silence")
        return {"duration_seconds": frames / rate, "sample_rate": rate, "channels": channels, "frames": frames, "sample_width": width}
    except (OSError, EOFError, wave.Error):
        raise EngineError("Worker output is not a valid readable PCM WAV") from None


def model_manifest(path):
    """Fingerprint local filenames, sizes and mtimes without hashing GB of weights."""
    root = Path(path)
    entries = []
    if root.is_dir():
        for file in sorted(root.rglob("*")):
            if file.is_file():
                stat = file.stat()
                entries.append({"name": file.relative_to(root).as_posix(), "bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns})
                if len(entries) > 10000:
                    raise EngineError("Model directory has too many files; use a dedicated model directory")
    encoded = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()
    return {"fingerprint": hashlib.sha256(encoded).hexdigest(), "method": "relative-path-size-mtime-ns", "files": entries}


class VoxCPM2MLXBackend:
    """Backend contract: doctor(config)->safe status, generate(config,request,out)."""
    name = "voxcpm2_mlx"
    model_name = "voxcpm2"

    def call_worker(self, config, arguments, timeout):
        try:
            result = subprocess.run([config["python_path"], str(WORKER), *arguments], stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=timeout, check=False,
                                    env={**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"})
        except subprocess.TimeoutExpired:
            raise EngineError("Local voice worker timed out; shorten the semantic paragraph or review the local environment") from None
        except OSError:
            raise EngineError("Cannot start the configured isolated Python; create the dedicated environment first") from None
        if len(result.stdout) > 1024 * 1024:
            raise EngineError("Local voice worker returned an invalid oversized diagnostic")
        try:
            data = json.loads(result.stdout.decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError()
        except (UnicodeError, ValueError):
            raise EngineError("Local voice worker returned invalid diagnostic JSON") from None
        if result.returncode:
            # Do not expose arbitrary worker exception text, which can contain input or paths.
            code = data.get("code", "worker_failed")
            if not isinstance(code, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", code):
                code = "worker_failed"
            raise EngineError(f"Local voice worker failed ({code}); run doctor for environment checks")
        return data

    def doctor(self, config):
        if not Path(config["python_path"]).is_file():
            return {"ready": False, "reason": "python_missing", "message": "Create the isolated MLX Python environment first"}
        root = Path(config["model_path"])
        if not root.is_dir() or not (root / "config.json").is_file() or not any(root.glob("*.safetensors")):
            return {"ready": False, "reason": "model_missing", "message": "Download VoxCPM2-8bit into the configured model directory; no download is automatic"}
        if not WORKER.is_file():
            return {"ready": False, "reason": "worker_missing", "message": "The local voice worker is missing from this installation"}
        for voice in config["voices"].values():
            for key in ("reference_audio", "prompt_audio"):
                if key in voice and not Path(voice[key]).is_file():
                    return {"ready": False, "reason": "reference_missing", "message": "A configured local reference audio file is missing"}
        try:
            data = self.call_worker(config, ["--doctor", "--model-path", config["model_path"]], min(config["timeout_seconds"], 120))
        except EngineError as error:
            return {"ready": False, "reason": "environment_unavailable", "message": str(error)}
        ready = data.get("ready") is True or data.get("status") == "ready"
        return {"ready": ready, "reason": "ready" if ready else "worker_not_ready", "message": "Local model and environment checks passed" if ready else "Worker environment checks did not pass; run the worker doctor locally"}

    def generate(self, config, request, output):
        request_path = Path(output).parent / "request.json"
        request_path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
        request_path.chmod(0o600)
        self.call_worker(config, ["--request", str(request_path), "--output", str(output), "--model-path", config["model_path"]], config["timeout_seconds"])


# Explicit registry: adding another backend requires a reviewed implementation.
BACKENDS = {"voxcpm2_mlx": VoxCPM2MLXBackend}


class VoiceEngine:
    def __init__(self, config):
        self.config = config
        self.backend = BACKENDS[config["backend"]]()
        self.lock = threading.Lock()
        self.health_lock = threading.Lock()
        self.health_cache = None
        self.health_at = 0.0
        self.health_signature = None

    def environment_signature(self):
        model = Path(self.config["model_path"])
        paths = [Path(self.config["python_path"]), WORKER, model, model / "config.json", *sorted(model.glob("*.safetensors"))]
        paths.extend(Path(item[key]) for item in self.config["voices"].values() for key in ("reference_audio", "prompt_audio") if key in item)
        values = []
        for path in paths:
            try:
                stat = path.stat()
                values.append((str(path), stat.st_size, stat.st_mtime_ns))
            except OSError:
                values.append((str(path), None, None))
        return tuple(values)

    def health(self, refresh=False):
        with self.health_lock:
            signature = self.environment_signature()
            if refresh or self.health_cache is None or self.health_signature != signature or time.monotonic() - self.health_at > 30:
                diagnosis = self.backend.doctor(self.config)
                self.health_cache = {"status": "ready" if diagnosis["ready"] else "blocked", "ready": diagnosis["ready"],
                                     "backend": self.config["backend"], "model": self.backend.model_name,
                                     "reason": diagnosis["reason"], "message": diagnosis["message"], "synthesis_passed": False,
                                     "limitations": ["Apple Silicon MLX only", "No automatic model download", "No transcription or subtitle alignment", "Listening review required"]}
                self.health_at = time.monotonic()
                self.health_signature = signature
            return dict(self.health_cache)

    def validate_request(self, payload):
        payload = _object(payload, "speech request")
        allowed = {"model", "input", "voice", "response_format", "language"}
        if set(payload) - allowed:
            raise EngineError("Unsupported speech fields; speed, temperature, paths and generation overrides are not accepted")
        if payload.get("model", "voxcpm2") not in ("voxcpm2", "default"):
            raise EngineError("model must be voxcpm2 or default")
        if payload.get("response_format", "wav") != "wav":
            raise EngineError("Only response_format=wav is supported")
        text = _text(payload.get("input"), "input")
        voice_id = payload.get("voice", self.config["voice"]["id"])
        if not isinstance(voice_id, str) or voice_id not in self.config["voices"]:
            raise EngineError("voice must name a voice registered in the local configuration")
        voice = self.config["voices"][voice_id]
        language = validate_language(payload.get("language", voice.get("language", "zh-CN")))
        request = {"text": text, "language": language, **self.config["generation"]}
        request.update({key: value for key, value in voice.items() if key not in ("id", "language")})
        return request, voice_id

    def synthesize(self, payload):
        request, voice_id = self.validate_request(payload)
        if not self.lock.acquire(blocking=False):
            raise BusyError("Another synthesis is running; retry after it completes")
        try:
            status = self.health()
            if not status["ready"]:
                raise NotReadyError(f"Voice engine is blocked ({status['reason']}): {status['message']}")
            with tempfile.TemporaryDirectory(prefix="anatomy-voice-") as temporary:
                output = Path(temporary) / "speech.wav"
                self.backend.generate(self.config, request, output)
                measured = inspect_wav(output)
                data = output.read_bytes()
            metadata = {"schema_version": 1, "backend": self.config["backend"], "model": self.backend.model_name,
                        "voice": voice_id, "language": request["language"], "script_sha256": hashlib.sha256(request["text"].encode()).hexdigest(),
                        "model_manifest": model_manifest(self.config["model_path"]), "generation": dict(self.config["generation"]),
                        "audio_sha256": hashlib.sha256(data).hexdigest(), **measured,
                        "review": {"listening": "not_reviewed", "transcript": "not_reviewed", "alignment": "alignment_pending"}}
            # Reference/instruction fingerprints preserve reproducibility without publishing paths or prose.
            metadata["voice_configuration_sha256"] = hashlib.sha256(json.dumps(self.config["voices"][voice_id], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            metadata["reference_audio_sha256"] = {}
            for key in ("reference_audio", "prompt_audio"):
                if key in request:
                    digest = hashlib.sha256()
                    try:
                        with Path(request[key]).open("rb") as reference:
                            for chunk in iter(lambda: reference.read(1024 * 1024), b""):
                                digest.update(chunk)
                    except OSError:
                        raise EngineError("Configured reference audio became unreadable; no audio was published") from None
                    metadata["reference_audio_sha256"][key] = digest.hexdigest()
            return data, metadata
        finally:
            self.lock.release()


class VoiceHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, engine):
        import socket
        self.address_family = socket.AF_INET6 if ":" in address[0] else socket.AF_INET
        self.engine = engine
        super().__init__(address, VoiceHandler)


class VoiceHandler(BaseHTTPRequestHandler):
    server_version = "AnatomyVoice/1"
    sys_version = ""

    def log_message(self, *_):
        # HTTP logs intentionally omit request bodies, narration and filesystem paths.
        return

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def send_json(self, status, value):
        data = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def permitted(self):
        if not _loopback(self.client_address[0]) or self.headers.get("Origin"):
            self.send_json(403, {"error": "Only local non-browser clients may use this service; Origin requests are disabled"})
            return False
        host = self.headers.get("Host", "")
        try:
            parsed = urlsplit("//" + host)
            valid = parsed.hostname in ("localhost", "127.0.0.1", "::1", self.server.engine.config["host"]) and parsed.port == self.server.server_port
            valid = valid and not parsed.username and not parsed.password and not parsed.path and not parsed.query and not parsed.fragment
        except ValueError:
            valid = False
        if not valid:
            self.send_json(403, {"error": "Host must name this loopback service and port"})
            return False
        return True

    def do_GET(self):
        if not self.permitted():
            return
        if self.path == "/health":
            self.send_json(200, self.server.engine.health())
        elif self.path == "/engines/tts":
            engine = self.server.engine
            self.send_json(200, {"engines": [{"id": engine.backend.model_name, "backend": engine.config["backend"],
                                            "voices": list(engine.config["voices"])}], "active": engine.backend.model_name})
        else:
            self.send_json(404, {"error": "Unknown endpoint"})

    def do_OPTIONS(self):
        self.send_json(403, {"error": "Browser CORS access is disabled"})

    def do_POST(self):
        if not self.permitted():
            return
        if self.path != "/v1/audio/speech":
            self.send_json(404, {"error": "Unknown endpoint"})
            return
        if self.headers.get("Transfer-Encoding"):
            self.send_json(400, {"error": "Transfer-Encoding is not supported"})
            return
        if self.headers.get_content_type() != "application/json":
            self.send_json(415, {"error": "Content-Type must be application/json"})
            return
        try:
            size = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            size = -1
        if not 0 < size <= MAX_BODY:
            self.send_json(413 if size > MAX_BODY else 400, {"error": "A Content-Length from 1 to 131072 bytes is required"})
            return
        try:
            body = self.rfile.read(size)
            if len(body) != size:
                raise EngineError("Request body is incomplete")
            payload = json.loads(body.decode("utf-8"))
            data, metadata = self.server.engine.synthesize(payload)
        except (UnicodeError, json.JSONDecodeError):
            self.send_json(400, {"error": "Request must be valid UTF-8 JSON"})
            return
        except BusyError as error:
            self.send_json(409, {"error": str(error)})
            return
        except NotReadyError as error:
            self.send_json(503, {"error": str(error)})
            return
        except EngineError as error:
            self.send_json(400, {"error": str(error)})
            return
        except Exception:
            self.send_json(500, {"error": "Local synthesis failed; inspect local environment without sharing private inputs"})
            return
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Audio-Duration", f"{metadata['duration_seconds']:.9f}")
        self.send_header("X-Audio-Sample-Rate", str(metadata["sample_rate"]))
        self.send_header("X-Subtitle-Alignment", "pending")
        self.end_headers()
        self.wfile.write(data)


def publish_cli(data, metadata, destination, config, text_file, force=False):
    target = Path(destination).expanduser().resolve()
    source = Path(text_file).expanduser().resolve()
    sidecar = target.with_suffix(".voice.json")
    if target.suffix.lower() != ".wav":
        raise EngineError("Output must have a .wav suffix")
    protected = [source, Path(config["_config_path"]), Path(config["python_path"]).resolve(), WORKER]
    protected.extend(Path(item[key]).resolve() for item in config["voices"].values() for key in ("reference_audio", "prompt_audio") if key in item)
    model = Path(config["model_path"])
    if target in protected or sidecar in protected or target == model or model in target.parents or model in sidecar.parents:
        raise EngineError("Output cannot overwrite input, configuration, Python, worker or model files")
    if not target.parent.is_dir():
        raise EngineError("Output parent directory must already exist")
    if (target.exists() or sidecar.exists()) and not force:
        raise EngineError("Output or metadata already exists; choose a new file or use --force")
    # Same-directory publication prevents a failed metadata write from leaving mixed outputs.
    with tempfile.TemporaryDirectory(prefix=".anatomy-voice-output-", dir=target.parent) as temporary:
        staged = Path(temporary)
        (staged / "audio.wav").write_bytes(data)
        (staged / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        published, backups = [], []
        try:
            for index, (prepared, destination) in enumerate(((staged / "audio.wav", target), (staged / "metadata.json", sidecar))):
                if force and destination.exists():
                    backup = staged / f"backup-{index}"
                    os.replace(destination, backup)
                    backups.append((backup, destination))
                # Link rather than replace keeps default no-overwrite behavior even during a race.
                os.link(prepared, destination)
                published.append(destination)
        except OSError:
            for destination in reversed(published):
                destination.unlink(missing_ok=True)
            for backup, destination in reversed(backups):
                os.replace(backup, destination)
            raise EngineError("Could not publish audio and metadata together; previous outputs were preserved") from None
    return target, sidecar


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("doctor", "serve", "synthesize"):
        command = commands.add_parser(name)
        command.add_argument("--config", default=str(DEFAULT_CONFIG))
        if name == "synthesize":
            command.add_argument("--text-file", required=True)
            command.add_argument("--out", required=True)
            command.add_argument("--voice")
            command.add_argument("--language")
            command.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        engine = VoiceEngine(config)
        if args.command == "doctor":
            status = engine.health(refresh=True)
            print(json.dumps(status, ensure_ascii=False, indent=2))
            return 0 if status["ready"] else 2
        if args.command == "serve":
            engine.health(refresh=True)
            server = VoiceHTTPServer((config["host"], config["port"]), engine)
            url_host = f"[{config['host']}]" if ":" in config["host"] else config["host"]
            print(json.dumps({"service": "anatomy-voice-engine", "url": f"http://{url_host}:{config['port']}", "status": engine.health()["status"]}), flush=True)
            try:
                server.serve_forever(poll_interval=0.3)
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
            return 0
        try:
            source = Path(args.text_file).expanduser().resolve()
            if source.stat().st_size > MAX_BODY:
                raise EngineError("Text file exceeds the 128 KiB limit")
            text = source.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            raise EngineError("Cannot read UTF-8 narration text file") from None
        # Validate output before expensive synthesis; same checks run again before publishing.
        target = Path(args.out).expanduser().resolve()
        sidecar = target.with_suffix(".voice.json")
        if target.suffix.lower() != ".wav" or not target.parent.is_dir():
            raise EngineError("Choose a .wav output inside an existing directory")
        if (target.exists() or sidecar.exists()) and not args.force:
            raise EngineError("Output or metadata already exists; choose a new file or use --force")
        protected = [source, Path(config["_config_path"]), Path(config["python_path"]).resolve(), WORKER]
        protected.extend(Path(item[key]).resolve() for item in config["voices"].values() for key in ("reference_audio", "prompt_audio") if key in item)
        model = Path(config["model_path"])
        if target in protected or sidecar in protected or target == model or model in target.parents or model in sidecar.parents:
            raise EngineError("Output cannot overwrite input, configuration, Python, worker or model files")
        payload = {"input": text}
        if args.voice is not None:
            payload["voice"] = args.voice
        if args.language is not None:
            payload["language"] = args.language
        data, metadata = engine.synthesize(payload)
        target, sidecar = publish_cli(data, metadata, target, config, source, args.force)
        print(json.dumps({"output": str(target), "metadata": str(sidecar), "duration_seconds": metadata["duration_seconds"], "review": metadata["review"]}, ensure_ascii=False))
        return 0
    except (EngineError, OSError) as error:
        message = str(error) if isinstance(error, EngineError) else "Voice engine file or network operation failed; inspect local permissions and port availability"
        print(json.dumps({"error": message}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
