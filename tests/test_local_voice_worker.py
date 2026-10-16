"""Offline worker contract tests; fake MLX/model modules never synthesize speech."""
from contextlib import ExitStack, redirect_stderr, redirect_stdout
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import socket
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import wave

SPEC = importlib.util.spec_from_file_location(
    "anatomy_local_voice_worker", Path(__file__).resolve().parents[1] / "scripts" / "local_voice_worker.py")
worker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(worker)


class FakeArray:
    def __init__(self, data):
        self.data = list(data)
        self.ndim = 2 if self.data and isinstance(self.data[0], (list, tuple)) else 1
        self.size = len(self.data)

    def __iter__(self):
        return iter(self.data)


class FakeNumpy:
    float32 = "float32"

    @staticmethod
    def asarray(data, dtype):
        return FakeArray(data)

    @staticmethod
    def isfinite(data):
        return SimpleNamespace(all=lambda: all(math.isfinite(x) for x in data))

    @staticmethod
    def abs(data):
        return [abs(x) for x in data]

    max = staticmethod(max)


class FakeSoundFile:
    @staticmethod
    def info(path):
        with wave.open(str(path), "rb") as audio:
            return SimpleNamespace(frames=audio.getnframes(), channels=audio.getnchannels(), samplerate=audio.getframerate())

    @staticmethod
    def write(path, audio, samplerate, format, subtype):
        assert format == "WAV" and subtype == "PCM_16"
        with wave.open(str(path), "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(samplerate)
            stream.writeframes(b"".join(struct.pack("<h", max(-32768, min(32767, round(x * 32767)))) for x in audio))


def model_dir(root):
    path = root / "local-model"
    path.mkdir()
    for name in worker.REQUIRED_MODEL_FILES:
        (path / name).write_bytes(b"local fixture, not real weights")
    (path / "config.json").write_text(json.dumps({"model_type": "voxcpm2"}))
    (path / "tokenizer_config.json").write_text("{}")
    return path


def tone(path):
    # Only a fixture to exercise WAV I/O, never represented as model output.
    FakeSoundFile.write(str(path), [0.2, -0.1, 0.3], 48000, "WAV", "PCM_16")


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.model_path = model_dir(self.root)
        self.output = self.root / "out.wav"
        self.calls = {}
        self.audio = [0.0, 0.25, -0.25, 0.0]
        self.sample_rate = 48000
        self.results = 1
        parent = self

        class FakeModel:
            def generate(self, text, cfg_value=2.0, inference_timesteps=10, max_tokens=2000,
                         ref_audio=None, prompt_audio=None, prompt_text=None, instruct=None):
                parent.calls["generate"] = dict(text=text, cfg_value=cfg_value,
                    inference_timesteps=inference_timesteps, max_tokens=max_tokens, ref_audio=ref_audio,
                    prompt_audio=prompt_audio, prompt_text=prompt_text, instruct=instruct)
                print("private library generation diagnostic")
                for _ in range(parent.results):
                    yield SimpleNamespace(audio=parent.audio, sample_rate=parent.sample_rate)

        class AutoTokenizer:
            @staticmethod
            def from_pretrained(path, **kwargs):
                parent.calls["tokenizer"] = kwargs
                return object()

        self.transformers = SimpleNamespace(AutoTokenizer=AutoTokenizer)
        self.model = FakeModel()

        def load(path):
            parent.calls["load"] = path
            parent.calls["offline"] = (os.environ["HF_HUB_OFFLINE"], os.environ["TRANSFORMERS_OFFLINE"])
            with parent.assertRaises(worker.WorkerError):
                socket.create_connection(("example.invalid", 443))
            AutoTokenizer.from_pretrained(path)
            print("private library load diagnostic")
            return parent.model

        self.modules = {
            "mlx.core": SimpleNamespace(random=SimpleNamespace(seed=lambda seed: self.calls.update(seed=seed))),
            "soundfile": FakeSoundFile,
            "numpy": FakeNumpy,
            "mlx_audio.tts.models.voxcpm2.voxcpm2": SimpleNamespace(Model=FakeModel),
            "mlx_audio.tts.utils": SimpleNamespace(load=load),
            "transformers": self.transformers,
        }
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(mock.patch.object(worker.platform, "system", return_value="Darwin"))
        stack.enter_context(mock.patch.object(worker.platform, "machine", return_value="arm64"))
        stack.enter_context(mock.patch.object(worker.importlib.metadata, "version", return_value="0.5.9"))
        stack.enter_context(mock.patch.object(worker.importlib, "import_module", side_effect=lambda name: self.modules[name]))
        stack.enter_context(mock.patch.dict(os.environ, {"HF_HUB_OFFLINE": "0", "TRANSFORMERS_OFFLINE": "0"}))
        stack.enter_context(redirect_stderr(io.StringIO()))

    def request(self, **extra):
        return {"text": "声音来自物体的振动。", "language": "zh-CN", **extra}

    def test_doctor_ready_is_not_synthesis_proof_or_weight_load(self):
        result = worker.doctor(self.model_path)
        self.assertTrue(result["ok"])
        self.assertTrue(result["ready"])
        self.assertEqual(result["status"], "ready_for_load")
        self.assertFalse(result["synthesis_passed"])
        self.assertNotIn("load", self.calls)
        self.assertNotIn("generate", self.calls)

    def test_doctor_reports_missing_model_and_deps_safely(self):
        with mock.patch.object(worker.importlib.metadata, "version", side_effect=worker.importlib.metadata.PackageNotFoundError):
            result = worker.doctor(self.root / "absent")
        self.assertFalse(result["ok"])
        self.assertFalse(result["ready"])
        self.assertEqual(len(result["errors"]), 2)
        self.assertNotIn(str(self.root), json.dumps(result))

    def test_other_mlx_audio_versions_are_not_silently_supported(self):
        for version in ("0.5.8", "0.6.0", "0.5.9.dev0"):
            with self.subTest(version=version), mock.patch.object(worker.importlib.metadata, "version", return_value=version):
                result = worker.doctor(self.model_path)
            self.assertFalse(result["ok"])
            self.assertFalse(result["checks"]["dependencies_and_api"])

    def test_platform_requires_native_arm_macos(self):
        for system, machine in (("Windows", "arm64"), ("Darwin", "x86_64")):
            with self.subTest(system=system), mock.patch.object(worker.platform, "system", return_value=system), mock.patch.object(worker.platform, "machine", return_value=machine):
                self.assertFalse(worker.doctor(self.model_path)["ok"])
                with self.assertRaisesRegex(worker.WorkerError, "Apple Silicon"):
                    worker.generate(self.request(), self.output, self.model_path)

    def test_generate_uses_exact_controls_seed_and_local_tokenizer(self):
        reference = self.root / "reference.wav"
        prompt = self.root / "previous.wav"
        tone(reference)
        tone(prompt)
        result = worker.generate(self.request(reference_audio=str(reference), prompt_audio=str(prompt),
            prompt_text="此前的一句话。", instruct="温暖沉稳的女声", seed=12, cfg_value=1.5,
            inference_timesteps=15, max_tokens=300), self.output, self.model_path)
        self.assertEqual(self.calls["seed"], 12)
        self.assertEqual(self.calls["offline"], ("1", "1"))
        self.assertEqual(self.calls["load"], str(self.model_path.resolve()))
        self.assertEqual(self.calls["tokenizer"], {"trust_remote_code": False, "local_files_only": True})
        self.assertEqual(self.calls["generate"]["cfg_value"], 1.5)  # no cfg_scale alias clamp
        self.assertEqual(self.calls["generate"]["ref_audio"], str(reference.resolve()))
        self.assertEqual(self.calls["generate"]["prompt_audio"], str(prompt.resolve()))
        self.assertEqual(self.calls["generate"]["prompt_text"], "此前的一句话。")
        self.assertEqual(self.calls["generate"]["inference_timesteps"], 15)
        self.assertEqual(self.calls["generate"]["max_tokens"], 300)
        self.assertEqual(self.calls["generate"]["instruct"], "温暖沉稳的女声")
        self.assertEqual(result["sample_rate"], 48000)
        self.assertEqual(result["samples"], 4)
        self.assertEqual(result["duration_seconds"], 4 / 48000)
        self.assertIsNone(result["alignment"])
        self.assertFalse(result["audio_processed"])
        with wave.open(str(self.output), "rb") as audio:
            self.assertEqual((audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getnframes()), (1, 2, 48000, 4))
        self.assertEqual(list(self.root.glob(".local-voice-*")), [])

    def test_default_parameters_and_language_are_not_fake_model_controls(self):
        worker.generate(self.request(language="en"), self.output, self.model_path)
        self.assertEqual(self.calls["seed"], 42)
        params = self.calls["generate"]
        self.assertEqual(params["inference_timesteps"], 10)
        self.assertEqual(params["max_tokens"], 2000)
        self.assertEqual(params["cfg_value"], 2.0)
        self.assertNotIn("language", params)
        self.assertNotIn("lang_code", params)
        self.assertNotIn("speed", params)
        self.assertNotIn("ref_text", params)

    def test_invalid_requests_are_rejected_before_load(self):
        for request in ([], self.request(extra="bad"), self.request(text=" "), self.request(text="x" * 12001),
                        self.request(language="中文"), self.request(seed=True), self.request(seed=-1),
                        self.request(cfg_value=float("nan")), self.request(cfg_value=0),
                        self.request(inference_timesteps=0), self.request(max_tokens=6501),
                        self.request(prompt_text="仅有文本"), self.request(prompt_audio="https://example.com/audio.wav"),
                        self.request(reference_audio="https://example.com/audio.wav"), self.request(instruct="")):
            with self.subTest(request=request), self.assertRaises(worker.WorkerError):
                worker.generate(request, self.output, self.model_path)
        self.assertNotIn("load", self.calls)

    def test_missing_reference_and_undecodable_reference_rejected(self):
        with self.assertRaisesRegex(worker.WorkerError, "existing local"):
            worker.generate(self.request(reference_audio=str(self.root / "absent.wav")), self.output, self.model_path)
        bad = self.root / "bad.wav"
        bad.write_bytes(b"not audio")
        with self.assertRaisesRegex(worker.WorkerError, "cannot be decoded"):
            worker.generate(self.request(reference_audio=str(bad)), self.output, self.model_path)
        self.assertNotIn("load", self.calls)

    def test_model_must_be_complete_local_voxcpm2_without_remote_code(self):
        with self.assertRaises(worker.WorkerError):
            worker.validate_model_path("mlx-community/VoxCPM2-8bit")
        (self.model_path / "config.json").write_text('{"model_type":"qwen3_tts"}')
        with self.assertRaisesRegex(worker.WorkerError, "model_type"):
            worker.validate_model_path(self.model_path)
        (self.model_path / "config.json").write_text('{"model_type":"voxcpm2"}')
        (self.model_path / "tokenizer_config.json").write_text('{"auto_map":{"AutoTokenizer":"custom.Tokenizer"}}')
        with self.assertRaisesRegex(worker.WorkerError, "remote code"):
            worker.validate_model_path(self.model_path)
        (self.model_path / "tokenizer_config.json").write_text('{}')
        (self.model_path / "audiovae.pth").write_bytes(b"legacy pickle")
        with self.assertRaisesRegex(worker.WorkerError, "safetensors"):
            worker.validate_model_path(self.model_path)
        (self.model_path / "audiovae.pth").unlink()
        (self.model_path / "model.safetensors").write_text("version https://git-lfs.github.com/spec/v1\n")
        with self.assertRaisesRegex(worker.WorkerError, "LFS pointer"):
            worker.validate_model_path(self.model_path)
        (self.model_path / "model.safetensors").unlink()
        with self.assertRaisesRegex(worker.WorkerError, "incomplete"):
            worker.validate_model_path(self.model_path)

    def test_kwargs_only_api_does_not_prove_supported_controls(self):
        self.modules["mlx_audio.tts.models.voxcpm2.voxcpm2"] = SimpleNamespace(Model=SimpleNamespace(generate=lambda **kwargs: None))
        result = worker.doctor(self.model_path)
        self.assertFalse(result["ok"])
        self.assertIn("explicit generation controls", " ".join(result["errors"]))

    def test_invalid_generated_audio_does_not_publish_any_wav(self):
        for values in ([], [0.0, 0.0], [float("nan")], [float("inf")], [1.1], [[0.2, 0.1]]):
            self.audio = values
            with self.subTest(values=values), self.assertRaises(worker.WorkerError):
                worker.generate(self.request(), self.output, self.model_path)
            self.assertFalse(self.output.exists())
            self.assertEqual(list(self.root.glob(".local-voice-*")), [])

    def test_wrong_sample_rate_and_empty_or_multiple_results_rejected(self):
        self.sample_rate = 16000
        with self.assertRaisesRegex(worker.WorkerError, "sample rate"):
            worker.generate(self.request(), self.output, self.model_path)
        self.sample_rate = 48000
        for count in (0, 2):
            self.results = count
            with self.subTest(count=count), self.assertRaises(worker.WorkerError):
                worker.generate(self.request(), self.output, self.model_path)
        self.assertFalse(self.output.exists())

    def test_output_no_overwrite_including_broken_symlink(self):
        self.output.write_bytes(b"keep this file")
        with self.assertRaisesRegex(worker.WorkerError, "already exists"):
            worker.generate(self.request(), self.output, self.model_path)
        self.assertEqual(self.output.read_bytes(), b"keep this file")
        self.output.unlink()
        self.output.symlink_to(self.root / "missing-target")
        with self.assertRaisesRegex(worker.WorkerError, "already exists"):
            worker.generate(self.request(), self.output, self.model_path)
        self.assertTrue(self.output.is_symlink())
        self.assertNotIn("load", self.calls)

    def test_output_publication_race_does_not_clobber_and_cleans_temporary(self):
        original = worker.os.link

        def race(source, destination):
            Path(destination).write_bytes(b"other writer")
            return original(source, destination)

        with mock.patch.object(worker.os, "link", side_effect=race), self.assertRaisesRegex(worker.WorkerError, "already exists"):
            worker.generate(self.request(), self.output, self.model_path)
        self.assertEqual(self.output.read_bytes(), b"other writer")
        self.assertEqual(list(self.root.glob(".local-voice-*")), [])

    def test_main_stdout_only_json_and_errors_do_not_reveal_text_or_paths(self):
        request_file = self.root / "request.json"
        request_file.write_text(json.dumps(self.request()))
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = worker.main(["--request", str(request_file), "--output", str(self.output), "--model-path", str(self.model_path)])
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(stdout.getvalue())["ok"])
        self.assertNotIn("private", stdout.getvalue())
        self.assertNotIn(self.request()["text"], stdout.getvalue())
        self.assertNotIn(str(self.root), stdout.getvalue())

    def test_library_failure_is_sanitized_and_tokenizer_guard_is_restored(self):
        original = self.transformers.AutoTokenizer.from_pretrained
        self.modules["mlx_audio.tts.utils"].load = mock.Mock(side_effect=RuntimeError("secret-script-and-private-path"))
        with self.assertRaises(worker.WorkerError) as caught:
            worker.generate(self.request(), self.output, self.model_path)
        self.assertNotIn("secret", str(caught.exception))
        self.assertEqual(self.transformers.AutoTokenizer.from_pretrained, original)
        self.assertFalse(self.output.exists())

    def test_network_guard_blocks_and_restores_socket_functions(self):
        connection = socket.create_connection
        lookup = socket.getaddrinfo
        with worker.no_network():
            for method, args in ((socket.create_connection, (("example.invalid", 80),)),
                                 (socket.getaddrinfo, ("example.invalid", 80))):
                with self.assertRaises(worker.WorkerError):
                    method(*args)
        self.assertIs(socket.create_connection, connection)
        self.assertIs(socket.getaddrinfo, lookup)


if __name__ == "__main__":
    unittest.main()
