"""Real loopback HTTP and FFmpeg integration; fixture PCM is not synthesized speech."""
from contextlib import redirect_stdout
import errno
import importlib.util
import io
import json
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import threading
import unittest
from unittest import mock
import wave


ROOT = Path(__file__).resolve().parents[1]


def load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


voice = load_module("integration_voice_engine", "voice_engine.py")
tts = load_module("integration_tts", "tts.py")


class LocalVoiceIntegration(unittest.TestCase):
    def test_real_http_adapter_preserves_cloud_voice_and_measures_phrase_audio(self):
        # This exercises production HTTP, urllib, FFmpeg, publication and timing.
        # Only model generation is replaced by deliberately unequal fixture PCM.
        self.assertTrue(shutil.which("ffmpeg"), "FFmpeg is required for this integration regression")
        self.assertTrue(shutil.which("ffprobe"), "ffprobe is required for this integration regression")
        requests = []
        durations = [0.11, 0.27, 0.19]

        class FixtureBackend:
            model_name = "voxcpm2"  # The adapter's supported wire model identifier.

            def doctor(self, config):
                return {"ready": True, "reason": "fixture", "message": "Transport fixture; no model loaded"}

            def generate(self, config, request, output):
                index = len(requests)
                requests.append(dict(request))
                with wave.open(str(output), "wb") as audio:
                    audio.setnchannels(1)
                    audio.setsampwidth(2)
                    audio.setframerate(24000)
                    audio.writeframes(struct.pack("<h", 1100) * round(durations[index] * 24000))

        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(voice.BACKENDS, {"fixture_pcm": FixtureBackend}):
            root = Path(temporary)
            model = root / "model"
            model.mkdir()
            config_path = root / "engine.json"
            config_path.write_text(json.dumps({
                "schema_version": 1, "host": "127.0.0.1", "port": 3931,
                "backend": "fixture_pcm", "python_path": sys.executable, "model_path": str(model),
                "voice": {"id": "default", "language": "zh-CN", "instruct": "Fixture transport only"},
            }), encoding="utf-8")
            engine = voice.VoiceEngine(voice.load_config(config_path))
            try:
                server = voice.VoiceHTTPServer(("127.0.0.1", 0), engine)
            except OSError as error:
                if error.errno in (errno.EACCES, errno.EPERM):
                    self.skipTest("Sandbox denied loopback socket binding")
                raise
            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
            thread.start()
            try:
                project = root / "project"
                project.mkdir()
                document = {
                    "schema_version": 1, "topic": "声音", "content_ready": True, "language": "zh-CN",
                    "tts": {"provider": "local", "segment_mode": "phrases",
                            "endpoint": f"http://127.0.0.1:{server.server_port}/v1/audio/speech",
                            "voice": "preserved-doubao-cloud-voice"},
                    "presentation": {"captions": {"style": "kinetic"}},
                    "scenes": [
                        {"id": "s01", "title": "振动", "narration": "声音来自振动。振动推动空气。", "hold_seconds": 0.15},
                        {"id": "s02", "title": "传播", "narration": "空气继续传递振动。", "hold_seconds": 0.05},
                    ],
                }
                original = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
                (project / "job.json").write_text(original, encoding="utf-8")
                with redirect_stdout(io.StringIO()) as stdout, mock.patch.object(tts, "synthesize_doubao", side_effect=AssertionError("Cloud synthesis must not run")):
                    self.assertEqual(tts.main(["--project", str(project)]), 0)
                self.assertTrue(json.loads(stdout.getvalue())["ok"])
                self.assertEqual((project / "job.json").read_text(encoding="utf-8"), original)
                # Only the registered local default exists. Success proves the retained
                # cloud voice was not forwarded as the local service voice identifier.
                self.assertEqual(list(engine.config["voices"]), ["default"])
                self.assertEqual([r["text"] for r in requests], ["声音来自振动。", "振动推动空气。", "空气继续传递振动。"])
                self.assertEqual([r["language"] for r in requests], ["zh-CN"] * 3)
                timing = json.loads((project / "timing.json").read_text(encoding="utf-8"))
                self.assertEqual(timing["provider"], "local")
                self.assertEqual(timing["captions"]["alignment"], "measured-phrases")
                cues = timing["captions"]["cues"]
                for cue, expected in zip(cues, [(0, 0.11), (0.11, 0.38), (0.53, 0.72)]):
                    self.assertAlmostEqual(cue["start"], expected[0], delta=0.001)
                    self.assertAlmostEqual(cue["end"], expected[1], delta=0.001)
                self.assertEqual(len(cues), 3)
                self.assertAlmostEqual(timing["duration"], 0.77, delta=0.001)
                self.assertEqual([s["audio"] for s in timing["scenes"]], ["audio/s01.wav", "audio/s02.wav"])
                self.assertIn("00:00:00,110 --> 00:00:00,380\n振动推动空气。", (project / "narration.srt").read_text(encoding="utf-8"))
                with wave.open(str(project / "narration-full.wav"), "rb") as audio:
                    self.assertEqual((audio.getframerate(), audio.getnchannels(), audio.getsampwidth()), (48000, 2, 2))
                    self.assertAlmostEqual(audio.getnframes() / audio.getframerate(), timing["duration"], delta=0.001)
                self.assertFalse(list(project.glob(".tts-stage-*")))
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
            self.assertFalse(thread.is_alive())


if __name__ == "__main__":
    unittest.main()
