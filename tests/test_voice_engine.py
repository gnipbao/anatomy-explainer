"""Offline adapter tests. Fixture PCM is never claimed to be VoxCPM2 speech."""
import importlib.util
import io
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import wave
import venv

SPEC = importlib.util.spec_from_file_location("anatomy_voice_engine", Path(__file__).resolve().parents[1] / "scripts" / "voice_engine.py")
voice = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(voice)


def pcm(path, seconds=0.1, silent=False):
    """Fixture audio for transport and measurement tests, not a model output."""
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(struct.pack("<h", 0 if silent else 1100) * round(seconds * 24000))


def raw_config(root):
    return {"schema_version": 1, "host": "127.0.0.1", "port": 3931, "backend": "voxcpm2_mlx",
            "python_path": sys.executable, "model_path": str(root / "model"),
            "voice": {"id": "default", "instruct": "清晰自然科普旁白"},
            "generation": {"seed": 42, "cfg_value": 2, "inference_timesteps": 10, "max_tokens": 2000}}


def config(root, **updates):
    data = {**raw_config(root), **updates}
    path = root / "config.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return voice.load_config(path)


class ConfigTests(unittest.TestCase):
    def test_symlinked_python_stays_in_its_isolated_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment = root / "isolated"
            venv.EnvBuilder(with_pip=False, symlinks=True).create(environment)
            executable = environment / "bin/python"
            data = config(root, python_path=str(executable))
            # Resolve() here used to launch base Python, discarding the venv.
            result = subprocess.run([data["python_path"], "-c", "import sys;print(sys.prefix)"],
                                    capture_output=True, text=True, check=True)
            self.assertEqual(Path(result.stdout.strip()), environment.resolve())

    def test_missing_config_is_actionable(self):
        with self.assertRaisesRegex(voice.EngineError, "config is missing"):
            voice.load_config("/definitely/missing/voice-config.json")

    def test_defaults_and_multiple_registered_voices(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = config(root, voices={"calm": {"instruct": "平稳", "language": "en-US"}})
            self.assertEqual(list(data["voices"]), ["default", "calm"])
            self.assertEqual(data["voices"]["calm"]["id"], "calm")
            self.assertEqual(data["generation"]["seed"], 42)
            self.assertTrue(Path(data["python_path"]).is_absolute())

    def test_remote_hosts_unknown_backend_and_options_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for updates in ({"host": "0.0.0.0"}, {"host": "localhost"}, {"host": "192.168.1.4"},
                            {"backend": "qwen3"}, {"download": True}, {"port": True}, {"schema_version": True}):
                with self.subTest(updates=updates), self.assertRaises(voice.EngineError):
                    config(root, **updates)

    def test_generation_and_voice_validation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for updates in ({"generation": {"speed": 1}}, {"generation": {"cfg_value": float("nan")}},
                            {"generation": {"seed": True}}, {"voice": {"id": "../x"}},
                            {"voices": {"default": {"id": "default"}}}, {"voices": {"other": {"id": "mismatch"}}},
                            {"voice": {"prompt_text": "x"}}, {"voice": {"model_path": "/other"}}):
                with self.subTest(updates=updates), self.assertRaises(voice.EngineError):
                    config(root, **updates)

    def test_reference_and_continuation_can_be_combined_but_prompt_pair_is_required(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = {"id": "default", "reference_audio": str(root / "tone.wav"),
                        "prompt_audio": str(root / "continuation.wav"), "prompt_text": "准确转录。"}
            data = config(root, voice=settings)
            self.assertEqual(data["voice"]["prompt_text"], "准确转录。")
            for invalid in ({"reference_audio": str(root / "tone.wav"), "prompt_text": "x"},
                            {"prompt_audio": str(root / "tone.wav")}):
                with self.assertRaisesRegex(voice.EngineError, "both prompt_audio"):
                    config(root, voice=invalid)
            config(root, voice={"reference_audio": str(root / "tone.wav")})

    def test_worker_parameter_boundaries_are_shared(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for generation in ({"cfg_value": 0}, {"max_tokens": 6501}):
                with self.assertRaises(voice.EngineError):
                    config(root, generation=generation)
            engine = voice.VoiceEngine(config(root, generation={"max_tokens": 6500, "cfg_value": 0.01}))
            engine.validate_request({"input": "x" * 12000})
            with self.assertRaises(voice.EngineError):
                engine.validate_request({"input": "x" * 12001})

    def test_doctor_does_not_download_or_import_mlx_when_model_missing(self):
        with tempfile.TemporaryDirectory() as temporary:
            engine = voice.VoiceEngine(config(Path(temporary)))
            with mock.patch.object(voice.subprocess, "run", side_effect=AssertionError("worker must not start")):
                status = engine.health()
            self.assertEqual(status["reason"], "model_missing")
            self.assertFalse(status["ready"])
            self.assertNotIn(str(temporary), json.dumps(status))

    def test_new_model_files_refresh_blocked_health_without_restart(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            engine = voice.VoiceEngine(config(root))
            self.assertEqual(engine.health()["reason"], "model_missing")
            model = root / "model"
            model.mkdir()
            (model / "config.json").write_text("{}")
            (model / "model.safetensors").write_bytes(b"fixture")
            with mock.patch.object(voice, "WORKER", Path(__file__)), mock.patch.object(engine.backend, "call_worker", return_value={"ready": True}):
                self.assertTrue(engine.health()["ready"])

    def test_unavailable_isolated_python_is_clear(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            engine = voice.VoiceEngine(config(root, python_path=str(root / "missing-python")))
            self.assertEqual(engine.health()["reason"], "python_missing")


class WorkerTransportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.cfg = config(self.root)
        self.backend = voice.VoxCPM2MLXBackend()

    def tearDown(self):
        self.temporary.cleanup()

    def test_subprocess_is_argument_array_offline_and_stderr_not_forwarded(self):
        result = subprocess.CompletedProcess([], 0, b'{"ready":true}', b"private-path")
        with mock.patch.object(voice.subprocess, "run", return_value=result) as run:
            self.assertTrue(self.backend.call_worker(self.cfg, ["--doctor", "--model-path", self.cfg["model_path"]], 10)["ready"])
        args, kwargs = run.call_args
        self.assertIsInstance(args[0], list)
        self.assertEqual(args[0][0], self.cfg["python_path"])
        self.assertEqual(kwargs["env"]["HF_HUB_OFFLINE"], "1")
        self.assertEqual(kwargs["env"]["TRANSFORMERS_OFFLINE"], "1")
        self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
        self.assertNotIn("shell", kwargs)

    def test_worker_errors_never_echo_sensitive_stderr_or_error_text(self):
        result = subprocess.CompletedProcess([], 1, b'{"error":"secret narration", "code":"bad_model"}', b"secret path")
        with mock.patch.object(voice.subprocess, "run", return_value=result), self.assertRaises(voice.EngineError) as caught:
            self.backend.call_worker(self.cfg, [], 10)
        self.assertIn("bad_model", str(caught.exception))
        self.assertNotIn("secret", str(caught.exception))

    def test_timeout_and_invalid_json_are_clear(self):
        for response in (subprocess.TimeoutExpired([], 1), subprocess.CompletedProcess([], 0, b"library logs\n{}", b"")):
            with self.subTest(response=response), mock.patch.object(voice.subprocess, "run", **({"side_effect": response} if isinstance(response, Exception) else {"return_value": response})), self.assertRaises(voice.EngineError):
                self.backend.call_worker(self.cfg, [], 10)

    def test_worker_doctor_result_is_cached(self):
        model = self.root / "model"
        model.mkdir()
        (model / "config.json").write_text("{}")
        (model / "model.safetensors").write_bytes(b"fixture")
        engine = voice.VoiceEngine(self.cfg)
        with mock.patch.object(voice, "WORKER", Path(__file__)), mock.patch.object(engine.backend, "call_worker", return_value={"ready": True}) as call:
            self.assertTrue(engine.health()["ready"])
            self.assertTrue(engine.health()["ready"])
        self.assertEqual(call.call_count, 1)


class SynthesisTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.cfg = config(self.root, voices={"english": {"id": "english", "instruct": "Clear narration", "language": "en-US"}})
        self.engine = voice.VoiceEngine(self.cfg)
        self.engine.backend.doctor = mock.Mock(return_value={"ready": True, "reason": "ready", "message": "fixture environment"})
        self.requests = []
        def generate(_config, request, output):
            self.requests.append(dict(request))
            pcm(output)
        self.engine.backend.generate = mock.Mock(side_effect=generate)

    def tearDown(self):
        self.temporary.cleanup()

    def test_fixed_voice_config_measured_duration_and_pending_alignment(self):
        audio, metadata = self.engine.synthesize({"model": "voxcpm2", "input": "声音来自振动。", "voice": "default", "response_format": "wav"})
        self.assertTrue(audio.startswith(b"RIFF"))
        self.assertEqual(metadata["duration_seconds"], 0.1)
        self.assertEqual(metadata["sample_rate"], 24000)
        self.assertEqual(metadata["review"]["alignment"], "alignment_pending")
        self.assertEqual(metadata["review"]["listening"], "not_reviewed")
        self.assertEqual(self.requests[0]["instruct"], "清晰自然科普旁白")
        self.assertNotIn("声音来自振动", json.dumps(metadata, ensure_ascii=False))
        self.assertEqual(metadata["generation"], self.cfg["generation"])

    def test_registered_language_voice_and_explicit_language(self):
        _, metadata = self.engine.synthesize({"input": "Sound is vibration.", "voice": "english"})
        self.assertEqual(metadata["language"], "en-US")
        _, metadata = self.engine.synthesize({"input": "Sound is vibration.", "voice": "english", "language": "en-GB"})
        self.assertEqual(metadata["language"], "en-GB")

    def test_arbitrary_request_controls_paths_and_formats_are_rejected(self):
        for extra in ({"voice": "../else"}, {"model": "other"}, {"response_format": "mp3"}, {"speed": 1},
                      {"temperature": 0.7}, {"reference_audio": "/private/secret"}, {"python_path": "/bin/sh"},
                      {"language": "../../x"}, {"input": "x" * 20001}, {"input": ""}, {"input": 1}):
            with self.subTest(extra=extra), self.assertRaises(voice.EngineError):
                self.engine.synthesize({"input": "声音。", **extra})
        self.engine.backend.generate.assert_not_called()

    def test_blocked_and_busy_engines_do_not_generate(self):
        self.engine.backend.doctor.return_value = {"ready": False, "reason": "model_missing", "message": "Download locally"}
        with self.assertRaises(voice.NotReadyError):
            self.engine.synthesize({"input": "声音。"})
        self.engine.lock.acquire()
        try:
            with self.assertRaises(voice.BusyError):
                self.engine.synthesize({"input": "声音。"})
        finally:
            self.engine.lock.release()
        self.engine.backend.generate.assert_not_called()

    def test_failure_releases_lock_and_cleans_temporary_files(self):
        captured = []
        def failure(_config, _request, output):
            captured.append(Path(output).parent)
            raise voice.EngineError("worker failed")
        self.engine.backend.generate.side_effect = failure
        with self.assertRaises(voice.EngineError):
            self.engine.synthesize({"input": "声音。"})
        self.assertFalse(captured[0].exists())
        self.assertFalse(self.engine.lock.locked())

    def test_empty_silent_and_truncated_wav_are_rejected(self):
        for state in ("empty", "silent", "truncated"):
            output = self.root / "test.wav"
            if state == "empty":
                output.write_bytes(b"RIFF")
            else:
                pcm(output, silent=state == "silent")
                if state == "truncated":
                    output.write_bytes(output.read_bytes()[:-20])
            with self.subTest(state=state), self.assertRaises(voice.EngineError):
                voice.inspect_wav(output)

    def test_model_manifest_changes_without_hashing_weight_contents(self):
        root = self.root / "model"
        root.mkdir()
        (root / "model.safetensors").write_bytes(b"fixture")
        first = voice.model_manifest(root)
        (root / "model.safetensors").write_bytes(b"fixture-extended")
        second = voice.model_manifest(root)
        self.assertNotEqual(first["fingerprint"], second["fingerprint"])
        self.assertEqual(first["method"], "relative-path-size-mtime-ns")
        self.assertNotIn(str(root), json.dumps(first))

    def test_cli_sidecar_overwrite_and_protected_input(self):
        data, metadata = self.engine.synthesize({"input": "声音。"})
        script = self.root / "script.txt"
        script.write_text("声音。")
        output = self.root / "narration.wav"
        published, sidecar = voice.publish_cli(data, metadata, output, self.cfg, script)
        self.assertEqual(published.read_bytes(), data)
        self.assertEqual(json.loads(sidecar.read_text())["review"]["alignment"], "alignment_pending")
        with self.assertRaisesRegex(voice.EngineError, "already exists"):
            voice.publish_cli(data, metadata, output, self.cfg, script)
        voice.publish_cli(data, metadata, output, self.cfg, script, force=True)
        protected_script = self.root / "protected.wav"
        protected_script.write_text("must not overwrite")
        with self.assertRaisesRegex(voice.EngineError, "cannot overwrite"):
            voice.publish_cli(data, metadata, protected_script, self.cfg, protected_script, force=True)
        self.assertEqual(protected_script.read_text(), "must not overwrite")

    def test_reference_content_is_fingerprinted_without_path_or_prose(self):
        reference = self.root / "reference.wav"
        pcm(reference)
        self.engine.config["voices"]["default"]["reference_audio"] = str(reference)
        _, metadata = self.engine.synthesize({"input": "声音。"})
        first = metadata["reference_audio_sha256"]["reference_audio"]
        pcm(reference, seconds=0.2)
        _, metadata = self.engine.synthesize({"input": "声音。"})
        self.assertNotEqual(first, metadata["reference_audio_sha256"]["reference_audio"])
        self.assertNotIn(str(reference), json.dumps(metadata))
        with self.assertRaisesRegex(voice.EngineError, "cannot overwrite"):
            voice.publish_cli(b"fixture", {}, reference, self.cfg, self.root / "script.txt", force=True)

    def test_metadata_publication_failure_rolls_back_both_previous_files(self):
        data, metadata = self.engine.synthesize({"input": "声音。"})
        script = self.root / "script.txt"
        script.write_text("声音。")
        output = self.root / "speech.wav"
        sidecar = output.with_suffix(".voice.json")
        output.write_bytes(b"old-audio")
        sidecar.write_bytes(b"old-metadata")
        original = voice.os.link
        def fail_metadata(source, destination):
            if Path(source).name == "metadata.json":
                raise OSError("fixture failure")
            original(source, destination)
        with mock.patch.object(voice.os, "link", side_effect=fail_metadata), self.assertRaisesRegex(voice.EngineError, "previous outputs were preserved"):
            voice.publish_cli(data, metadata, output, self.cfg, script, force=True)
        self.assertEqual(output.read_bytes(), b"old-audio")
        self.assertEqual(sidecar.read_bytes(), b"old-metadata")

    def test_cli_fails_before_synthesis_for_existing_output(self):
        script = self.root / "script.txt"
        script.write_text("声音。")
        output = self.root / "existing.wav"
        output.write_text("keep")
        error = io.StringIO()
        with mock.patch.object(voice, "VoiceEngine", return_value=self.engine), redirect_stderr(error):
            self.assertEqual(voice.main(["synthesize", "--config", self.cfg["_config_path"], "--text-file", str(script), "--out", str(output)]), 1)
        self.engine.backend.generate.assert_not_called()
        self.assertEqual(output.read_text(), "keep")


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.engine = voice.VoiceEngine(config(self.root))
        self.engine.backend.doctor = mock.Mock(return_value={"ready": True, "reason": "ready", "message": "fixture"})
        self.engine.backend.generate = mock.Mock(side_effect=lambda _config, _request, output: pcm(output))
        try:
            self.server = voice.VoiceHTTPServer(("127.0.0.1", 0), self.engine)
        except (PermissionError, OSError) as error:
            self.temporary.cleanup()
            self.skipTest(f"Loopback server unavailable in this sandbox: {error.__class__.__name__}")
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        if hasattr(self, "server"):
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(2)
        self.temporary.cleanup()

    def request(self, path, payload=None, headers=None):
        request = Request(self.url + path, data=json.dumps(payload).encode() if payload is not None else None,
                          headers={"Content-Type": "application/json", **(headers or {})})
        try:
            return urlopen(request, timeout=5)
        except HTTPError as error:
            error.close()
            raise

    def test_health_and_engine_inventory_do_not_expose_paths(self):
        with self.request("/health") as response:
            body = json.loads(response.read())
            self.assertTrue(body["ready"])
            self.assertNotIn(str(self.root), json.dumps(body))
            self.assertIsNone(response.headers.get("Access-Control-Allow-Origin"))
        with self.request("/engines/tts") as response:
            self.assertEqual(json.loads(response.read())["active"], "voxcpm2")

    def test_speech_returns_real_wav_transport_and_measured_header(self):
        with self.request("/v1/audio/speech", {"model": "voxcpm2", "input": "声音。", "voice": "default", "response_format": "wav"}) as response:
            self.assertEqual(response.headers["Content-Type"], "audio/wav")
            self.assertAlmostEqual(float(response.headers["X-Audio-Duration"]), 0.1)
            self.assertEqual(response.headers["X-Subtitle-Alignment"], "pending")
            self.assertTrue(response.read().startswith(b"RIFF"))

    def test_browser_origin_and_remote_host_are_rejected(self):
        for headers in ({"Origin": "https://malicious.example"}, {"Origin": "http://localhost:3000"}, {"Host": "malicious.example"}, {"Host": "127.0.0.1:1"}):
            with self.subTest(headers=headers), self.assertRaises(HTTPError) as caught:
                self.request("/health", headers=headers)
            self.assertEqual(caught.exception.code, 403)

    def test_unready_service_stays_available_but_speech_is_503(self):
        self.engine.backend.doctor.return_value = {"ready": False, "reason": "model_missing", "message": "Download locally"}
        with self.request("/health") as response:
            self.assertEqual(json.loads(response.read())["status"], "blocked")
        with self.assertRaises(HTTPError) as caught:
            self.request("/v1/audio/speech", {"input": "声音。"})
        self.assertEqual(caught.exception.code, 503)

    def test_busy_and_invalid_controls_have_distinct_status(self):
        self.engine.lock.acquire()
        try:
            with self.assertRaises(HTTPError) as caught:
                self.request("/v1/audio/speech", {"input": "声音。"})
            self.assertEqual(caught.exception.code, 409)
        finally:
            self.engine.lock.release()
        with self.assertRaises(HTTPError) as caught:
            self.request("/v1/audio/speech", {"input": "声音。", "speed": 1})
        self.assertEqual(caught.exception.code, 400)

    def test_oversized_request_is_rejected_before_generation(self):
        with self.assertRaises(HTTPError) as caught:
            self.request("/v1/audio/speech", {"input": "x" * voice.MAX_BODY})
        self.assertEqual(caught.exception.code, 413)
        self.engine.backend.generate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
