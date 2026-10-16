#!/usr/bin/env python3
"""Isolated, offline VoxCPM2 worker for a dedicated Apple Silicon environment.

The stdout protocol is one JSON object. Model libraries log to stderr; callers
must not forward that stderr to public logs. This script never installs software,
fetches a model, creates alignment timestamps, or edits generated audio.
"""

import argparse
from contextlib import contextmanager, redirect_stdout
import importlib
import importlib.metadata
import inspect
import json
import math
import os
from pathlib import Path
import platform
import re
import socket
import sys
import tempfile
import wave

SUPPORTED_MLX_AUDIO_VERSION = "0.5.9"
REQUIRED_MODEL_FILES = (
    "model.safetensors", "config.json", "tokenizer.json",
    "tokenizer_config.json", "special_tokens_map.json",
)
GENERATE_PARAMETERS = (
    "text", "cfg_value", "inference_timesteps", "max_tokens", "ref_audio",
    "prompt_audio", "prompt_text", "instruct",
)
REQUEST_FIELDS = {
    "text", "language", "reference_audio", "prompt_audio", "prompt_text",
    "instruct", "seed", "cfg_value", "inference_timesteps", "max_tokens",
}
LANG_RE = re.compile(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*\Z")


class WorkerError(Exception):
    """Safe diagnostics only: never include library exceptions or request text."""


def enforce_offline():
    # Deliberately override caller values rather than using setdefault.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["DO_NOT_TRACK"] = "1"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"


@contextmanager
def no_network():
    """Fail closed even if an imported package ignores Hugging Face offline mode."""
    originals = [(socket, "create_connection", socket.create_connection),
                 (socket, "getaddrinfo", socket.getaddrinfo),
                 (socket.socket, "connect", socket.socket.connect),
                 (socket.socket, "connect_ex", socket.socket.connect_ex)]

    def blocked(*_args, **_kwargs):
        raise WorkerError("Network access is disabled in the local voice worker")

    try:
        for owner, name, _ in originals:
            setattr(owner, name, blocked)
        yield
    finally:
        for owner, name, original in originals:
            setattr(owner, name, original)


def read_json(path, limit, message):
    try:
        source = Path(path)
        if not source.is_file() or source.stat().st_size > limit:
            raise WorkerError(message)
        with source.open(encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, UnicodeError, ValueError, TypeError):
        raise WorkerError(message) from None


def validate_model_path(value):
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise WorkerError("An existing local model directory is required")
    try:
        path = Path(value).expanduser().resolve(strict=True)
        if not path.is_dir():
            raise WorkerError("An existing local model directory is required")
        for name in REQUIRED_MODEL_FILES:
            item = path / name
            if not item.is_file() or item.stat().st_size == 0:
                raise WorkerError("Model files are incomplete; download all required model files")
            with item.open("rb") as stream:
                if stream.read(64).startswith(b"version https://git-lfs.github.com/spec/"):
                    raise WorkerError("Model files contain a Git LFS pointer; actual weights are required")
        # VoxCPM2's optional legacy fallback can deserialize a PyTorch pickle.
        if (path / "audiovae.pth").exists():
            raise WorkerError("Only the complete safetensors model layout is supported")
    except (OSError, ValueError, RuntimeError):
        raise WorkerError("Cannot inspect the local model directory") from None
    config = read_json(path / "config.json", 2 * 1024 * 1024, "Model config.json is invalid")
    tokenizer = read_json(path / "tokenizer_config.json", 2 * 1024 * 1024, "Model tokenizer_config.json is invalid")
    if not isinstance(config, dict) or config.get("model_type") != "voxcpm2":
        raise WorkerError("The local model config must declare model_type voxcpm2")
    if not isinstance(tokenizer, dict) or config.get("auto_map") or tokenizer.get("auto_map"):
        raise WorkerError("Custom tokenizer or model remote code is unsupported")
    return path


def validate_signature(generate):
    try:
        params = inspect.signature(generate).parameters
    except (ValueError, TypeError):
        raise WorkerError("Cannot verify the VoxCPM2 generate API") from None
    if any(name not in params or params[name].kind == inspect.Parameter.POSITIONAL_ONLY
           for name in GENERATE_PARAMETERS):
        raise WorkerError("Installed VoxCPM2 API lacks required explicit generation controls")


def runtime_modules():
    """Import without loading weights. All calls occur under offline/log guards."""
    try:
        if importlib.metadata.version("mlx-audio") != SUPPORTED_MLX_AUDIO_VERSION:
            raise WorkerError("This worker requires mlx-audio 0.5.9 in its isolated environment")
    except importlib.metadata.PackageNotFoundError:
        raise WorkerError("mlx-audio 0.5.9 is missing from the isolated environment") from None
    try:
        mx = importlib.import_module("mlx.core")
        sf = importlib.import_module("soundfile")
        np = importlib.import_module("numpy")
        model_module = importlib.import_module("mlx_audio.tts.models.voxcpm2.voxcpm2")
        utils = importlib.import_module("mlx_audio.tts.utils")
        transformers = importlib.import_module("transformers")
        validate_signature(model_module.Model.generate)
        if not callable(getattr(utils, "load", None)) or not callable(getattr(sf, "write", None)):
            raise WorkerError("The isolated synthesis environment is incomplete")
        return mx, sf, np, utils, transformers
    except WorkerError:
        raise
    except Exception:
        raise WorkerError("Cannot import the required MLX, mlx-audio, soundfile, or tokenizer dependencies") from None


def supported_platform():
    return platform.system() == "Darwin" and platform.machine().lower() in ("arm64", "aarch64")


def doctor(model_path):
    enforce_offline()
    errors = []
    checks = {"apple_silicon_macos": supported_platform(), "model_files": False,
              "dependencies_and_api": False, "offline": True,
              "mlx_audio_required_version": SUPPORTED_MLX_AUDIO_VERSION}
    if not checks["apple_silicon_macos"]:
        errors.append("VoxCPM2 MLX requires an Apple Silicon Mac and native arm64 Python")
    try:
        validate_model_path(model_path)
        checks["model_files"] = True
    except WorkerError as exc:
        errors.append(str(exc))
    try:
        with redirect_stdout(sys.stderr), no_network():
            runtime_modules()
        checks["dependencies_and_api"] = True
    except WorkerError as exc:
        errors.append(str(exc))
    ok = not errors
    return {"ok": ok, "ready": ok, "status": "ready_for_load" if ok else "not_ready",
            "synthesis_passed": False, "checks": checks, "errors": errors}


def validate_request(request):
    if not isinstance(request, dict) or set(request) - REQUEST_FIELDS:
        raise WorkerError("Request must contain only supported local synthesis fields")
    result = dict(request)
    text = result.get("text")
    language = result.get("language")
    if not isinstance(text, str) or not text.strip() or len(text) > 12000 or "\x00" in text:
        raise WorkerError("text must be a nonempty paragraph of at most 12000 characters")
    if not isinstance(language, str) or not LANG_RE.fullmatch(language):
        raise WorkerError("language must be a language tag such as zh-CN or en")
    for name in ("prompt_text", "instruct"):
        value = result.get(name)
        if value is not None and (not isinstance(value, str) or not value.strip() or
                                  len(value) > 12000 or "\x00" in value):
            raise WorkerError("Optional voice descriptions and prompt transcripts must be nonempty text")
    for name in ("reference_audio", "prompt_audio"):
        value = result.get(name)
        if value is not None:
            if not isinstance(value, str) or not value.strip() or "://" in value:
                raise WorkerError("Reference and prompt audio must be existing local audio files")
            try:
                path = Path(value).expanduser().resolve(strict=True)
                if not path.is_file() or path.stat().st_size == 0:
                    raise WorkerError("Reference and prompt audio must be existing local audio files")
                result[name] = str(path)
            except (OSError, ValueError, RuntimeError):
                raise WorkerError("Reference and prompt audio must be existing local audio files") from None
    if (result.get("prompt_audio") is None) != (result.get("prompt_text") is None):
        raise WorkerError("Continuation requires both prompt_audio and its exact prompt_text")
    for name, default, minimum, maximum in (
            ("seed", 42, 0, 4294967295),
            ("inference_timesteps", 10, 1, 100),
            ("max_tokens", 2000, 1, 6500)):
        value = result.get(name, default)
        if type(value) is not int or not minimum <= value <= maximum:
            raise WorkerError(name + " is outside the supported integer range")
        result[name] = value
    cfg = result.get("cfg_value", 2.0)
    if type(cfg) not in (int, float) or not math.isfinite(cfg) or not 0 < cfg <= 10:
        raise WorkerError("cfg_value must be a finite number greater than 0 and at most 10")
    result["cfg_value"] = float(cfg)
    return result


@contextmanager
def local_tokenizer_only(transformers):
    """The v0.5.9 post-load hook otherwise omits these explicit safety flags."""
    owner = transformers.AutoTokenizer
    original = owner.from_pretrained

    def local_only(pretrained_model_name_or_path, *args, **kwargs):
        kwargs["local_files_only"] = True
        kwargs["trust_remote_code"] = False
        return original(pretrained_model_name_or_path, *args, **kwargs)

    owner.from_pretrained = local_only
    try:
        yield
    finally:
        owner.from_pretrained = original


def validate_reference_audio(request, sf):
    for name in ("reference_audio", "prompt_audio"):
        if request.get(name):
            try:
                data = sf.info(request[name])
                if data.frames <= 0 or data.samplerate <= 0 or data.channels < 1:
                    raise ValueError()
            except Exception:
                raise WorkerError("A reference or prompt audio file cannot be decoded") from None


def save_audio(result, destination, sf, np):
    """Preserve every sample; reject invalid/clipping/silent output, publish once."""
    try:
        rate = result.sample_rate
        if type(rate) is not int or rate != 48000:
            raise WorkerError("VoxCPM2 returned an unexpected sample rate; expected 48000 Hz")
        audio = np.asarray(result.audio, dtype=np.float32)
        if audio.ndim != 1 or audio.size == 0:
            raise WorkerError("VoxCPM2 returned empty or non-mono audio")
        if not np.isfinite(audio).all():
            raise WorkerError("VoxCPM2 returned nonfinite audio samples")
        peak = float(np.max(np.abs(audio)))
        if peak == 0:
            raise WorkerError("VoxCPM2 returned silent audio")
        if peak > 1.0:
            raise WorkerError("VoxCPM2 audio exceeds PCM range; refusing implicit clipping or normalization")
        samples = int(audio.size)
    except WorkerError:
        raise
    except Exception:
        raise WorkerError("VoxCPM2 returned invalid audio") from None
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".local-voice-", suffix=".wav", dir=str(destination.parent), delete=False) as handle:
            temporary = Path(handle.name)
        sf.write(str(temporary), audio, rate, format="WAV", subtype="PCM_16")
        with wave.open(str(temporary), "rb") as check:
            if (check.getnchannels(), check.getsampwidth(), check.getframerate(), check.getnframes()) != (1, 2, rate, samples):
                raise WorkerError("Generated WAV did not preserve the expected sample count and format")
        # Atomic, no-overwrite publication. Even a concurrently created symlink
        # cannot be followed or clobbered by link().
        os.link(str(temporary), str(destination))
    except FileExistsError:
        raise WorkerError("Output already exists; choose a new WAV output path") from None
    except WorkerError:
        raise
    except Exception:
        raise WorkerError("Cannot write the generated WAV output") from None
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
    return {"sample_rate": rate, "channels": 1, "samples": samples,
            "duration_seconds": samples / rate, "peak_amplitude": peak,
            "format": "WAV", "subtype": "PCM_16"}


def generate(request, output, model_path):
    enforce_offline()
    request = validate_request(request)
    model_path = validate_model_path(model_path)
    try:
        destination = Path(output).expanduser().absolute()
        if destination.suffix.lower() != ".wav" or not destination.parent.is_dir():
            raise WorkerError("Output must be a WAV path inside an existing directory")
        if destination.exists() or destination.is_symlink():
            raise WorkerError("Output already exists; choose a new WAV output path")
    except (OSError, TypeError, ValueError):
        raise WorkerError("Output must be a WAV path inside an existing directory") from None
    if not supported_platform():
        raise WorkerError("VoxCPM2 MLX requires an Apple Silicon Mac and native arm64 Python")
    try:
        with redirect_stdout(sys.stderr), no_network():
            mx, sf, np, utils, transformers = runtime_modules()
            validate_reference_audio(request, sf)
            with local_tokenizer_only(transformers):
                # Passing a checked absolute existing path prevents the loader's
                # Hugging Face repository fallback. No remote model code is used.
                model = utils.load(str(model_path))
            validate_signature(model.generate)
            mx.random.seed(request["seed"])
            kwargs = {name: request[name] for name in
                      ("text", "cfg_value", "inference_timesteps", "max_tokens")}
            for source, target in (("reference_audio", "ref_audio"), ("prompt_audio", "prompt_audio"),
                                   ("prompt_text", "prompt_text"), ("instruct", "instruct")):
                if request.get(source) is not None:
                    kwargs[target] = request[source]
            iterator = iter(model.generate(**kwargs))
            try:
                result = next(iterator)
            except StopIteration:
                raise WorkerError("VoxCPM2 returned no audio result") from None
            if next(iterator, None) is not None:
                raise WorkerError("Unexpected streaming results from the pinned VoxCPM2 API")
            audio = save_audio(result, destination, sf, np)
    except WorkerError:
        raise
    except Exception:
        # Third-party exceptions can include the script or private file paths.
        raise WorkerError("Local VoxCPM2 synthesis failed; raw library details are withheld") from None
    return {"ok": True, "status": "synthesized", "provider": "voxcpm2-mlx",
            "mlx_audio_version": SUPPORTED_MLX_AUDIO_VERSION, "language": request["language"],
            "language_control": "text-only; no language parameter forwarded",
            "seed": request["seed"], "alignment": None, "audio_processed": False, **audio}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--doctor", action="store_true")
    mode.add_argument("--request", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--model-path", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.doctor:
            if args.output is not None:
                raise WorkerError("--output is only valid with --request")
            result = doctor(args.model_path)
        else:
            if args.output is None:
                raise WorkerError("--request requires --output")
            request = read_json(args.request, 256 * 1024, "Cannot read a valid local synthesis request JSON")
            result = generate(request, args.output, args.model_path)
    except WorkerError as exc:
        result = {"ok": False, "status": "error", "error": str(exc)}
    except Exception:
        result = {"ok": False, "status": "error", "error": "Local voice worker failed; raw details are withheld"}
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
