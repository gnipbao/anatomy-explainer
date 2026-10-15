"""Offline stream/security tests plus a real ffmpeg external-audio integration."""
import base64
import copy
import importlib.util
import io
import json
from pathlib import Path
import shutil
import struct
import tempfile
import unittest
from unittest import mock
import wave
from contextlib import redirect_stderr, redirect_stdout

SPEC = importlib.util.spec_from_file_location("anatomy_tts", Path(__file__).resolve().parents[1] / "scripts" / "tts.py")
tts = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tts)


def job(provider="external"):
    return {"schema_version": 1, "topic": "声音", "content_ready": True, "language": "zh-CN", "tts": {"provider": provider},
            "scenes": [{"id": "s01", "title": "声音", "narration": "声音来自振动。", "hold_seconds": 0.15}]}


def frame(code, data=None, **extra):
    return (json.dumps({"code": code, "data": data, **extra}) + "\n").encode()


def test_tone(path, seconds=0.1):
    """A fixture tone, never represented as synthesized narration."""
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(24000)
        output.writeframes(struct.pack("<h", 1000) * round(seconds * 24000))


class StreamTests(unittest.TestCase):
    def test_complete_audio_and_metadata_frames(self):
        chunks = [b"\n", frame(0, base64.b64encode(b"part1").decode()), frame(0, sentence={"text": "声音"}),
                  frame(0, base64.b64encode(b"part2").decode()), frame(20000000)]
        self.assertEqual(tts.parse_ndjson(chunks), b"part1part2")

    def test_partial_audio_without_completion_is_rejected(self):
        with self.assertRaisesRegex(tts.TTSError, "completion"):
            tts.parse_ndjson([frame(0, base64.b64encode(b"part").decode())])

    def test_success_without_audio_is_rejected(self):
        with self.assertRaisesRegex(tts.TTSError, "without any audio"):
            tts.parse_ndjson([frame(0), frame(20000000)])

    def test_remote_error_does_not_echo_sensitive_message(self):
        with self.assertRaises(tts.TTSError) as caught:
            tts.parse_ndjson([frame(45000000, message="sensitive-token-should-not-appear")])
        self.assertIn("45000000", str(caught.exception))
        self.assertNotIn("sensitive-token", str(caught.exception))

    def test_bad_json_base64_and_codes_are_rejected(self):
        for bad in (b"not json", frame(0, "%%%"), b'{"code": true}', b'[]'):
            with self.subTest(bad=bad), self.assertRaises(tts.TTSError):
                tts.parse_ndjson([bad, frame(20000000)])


class ValidationTests(unittest.TestCase):
    def test_safe_ids_and_duplicates(self):
        for invalid in ("../secret", "a/b", ".", "..", "-option", "a.wav", "a\\b", "中文", "", "a" * 65):
            document = job()
            document["scenes"][0]["id"] = invalid
            with self.subTest(invalid=invalid), self.assertRaises(tts.TTSError):
                tts.validate_job(document)
        document = job()
        document["scenes"].append(copy.deepcopy(document["scenes"][0]))
        with self.assertRaises(tts.TTSError):
            tts.validate_job(document)

    def test_empty_narration_bad_language_and_holds(self):
        for value in (None, "", "   "):
            document = job()
            document["scenes"][0]["narration"] = value
            with self.assertRaises(tts.TTSError):
                tts.validate_job(document)
        for value in ("", "english", "../../en", "en\n", 3):
            document = job()
            document["language"] = value
            with self.assertRaises(tts.TTSError):
                tts.validate_job(document)
        for value in (-1, float("nan"), float("inf"), True, "1"):
            document = job()
            document["scenes"][0]["hold_seconds"] = value
            with self.assertRaises(tts.TTSError):
                tts.validate_job(document)

    def test_non_chinese_doubao_requires_explicit_voice(self):
        document = job("doubao")
        document["language"] = "en-US"
        with self.assertRaisesRegex(tts.TTSError, "explicit"):
            tts.validate_job(document)
        document["tts"]["voice"] = "verified-voice-id"
        self.assertIs(tts.validate_job(document), document)

    def test_segment_modes_and_external_phrase_alignment(self):
        for value in (None, "words", True, 1):
            document = job("doubao")
            document["tts"]["segment_mode"] = value
            with self.subTest(value=value), self.assertRaisesRegex(tts.TTSError, "segment_mode"):
                tts.validate_job(document)
        document = job()
        document["tts"]["segment_mode"] = "phrases"
        with self.assertRaisesRegex(tts.TTSError, "external.*segment_mode='scene'"):
            tts.validate_job(document)
        document["tts"]["segment_mode"] = "scene"
        self.assertIs(tts.validate_job(document), document)

    def test_phrase_dry_run_exposes_text_without_duration_estimates(self):
        document = job("doubao")
        document["tts"]["segment_mode"] = "phrases"
        document["scenes"][0]["narration"] = "声音来自振动。振动推动空气。"
        with mock.patch.object(tts.os, "environ", {}), mock.patch.object(tts, "synthesize_doubao", side_effect=AssertionError("synthesized")):
            config = tts.dry_run_config(Path("/unused"), document)
        self.assertEqual(config["captions"]["alignment"], "measured-phrases")
        self.assertEqual(config["captions"]["phrases"][0]["texts"], ["声音来自振动。", "振动推动空气。"])
        self.assertIsNone(config["captions"]["durations"])

    def test_dry_run_never_synthesizes_or_inspects_credentials(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            document = job("doubao")
            document["content_ready"] = False
            (project / "job.json").write_text(json.dumps(document), encoding="utf-8")
            output = io.StringIO()
            with mock.patch.object(tts, "build_auth_headers", side_effect=AssertionError("credentials read")), mock.patch.object(tts, "execute", side_effect=AssertionError("executed")), redirect_stdout(output):
                self.assertEqual(tts.main(["--project", str(project), "--dry-run"]), 0)
            config = json.loads(output.getvalue())
            self.assertIsNone(config["duration"])
            self.assertEqual(config["voice"], tts.DEFAULT_VOICE)
            self.assertEqual(list(project.iterdir()), [project / "job.json"])

    def test_unreviewed_content_does_not_execute(self):
        document = job()
        document["content_ready"] = False
        with self.assertRaisesRegex(tts.TTSError, "content_ready"):
            tts.execute(Path("/unused"), document)

    def test_permission_error_diagnostic_withholds_internal_details(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            (project / "job.json").write_text(json.dumps(job()), encoding="utf-8")
            error = io.StringIO()
            with mock.patch.object(tts, "execute", side_effect=PermissionError("sensitive-private-path")), redirect_stderr(error):
                self.assertEqual(tts.main(["--project", str(project)]), 1)
            self.assertIn("file permissions", error.getvalue())
            self.assertNotIn("sensitive-private-path", error.getvalue())


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg and ffprobe are required")
class AudioIntegrationTests(unittest.TestCase):
    def test_unspecified_segment_mode_keeps_scene_synthesis(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            document = job("doubao")
            document["scenes"][0]["narration"] = "声音来自振动。振动推动空气。"
            synthesized = []

            def fixture_synthesis(scene, config, destination):
                synthesized.append(scene["narration"])
                test_tone(destination, 0.2)

            with mock.patch.object(tts, "synthesize_doubao", side_effect=fixture_synthesis):
                timing = tts.execute(project, document)
            self.assertEqual(synthesized, [document["scenes"][0]["narration"]])
            self.assertNotIn("captions", timing)
            self.assertAlmostEqual(timing["duration"], 0.35, delta=0.001)
            self.assertIn("00:00:00,000 --> 00:00:00,350", (project / "narration.srt").read_text())

    def test_phrases_use_individual_measured_audio_and_scene_hold(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            document = job("doubao")
            document["tts"]["segment_mode"] = "phrases"
            document["scenes"][0].update(narration="声音来自振动。振动推动空气。", hold_seconds=0.15)
            document["scenes"].append({"id": "s02", "title": "传播", "narration": "空气继续传递振动。", "hold_seconds": 0.05})
            synthesized = []
            actual_durations = [0.1, 0.3, 0.2]

            def fixture_synthesis(scene, config, destination):
                synthesized.append(scene["narration"])
                test_tone(destination, actual_durations[len(synthesized) - 1])

            with mock.patch.object(tts, "synthesize_doubao", side_effect=fixture_synthesis):
                timing = tts.execute(project, document)
            self.assertEqual(synthesized, ["声音来自振动。", "振动推动空气。", "空气继续传递振动。"])
            self.assertEqual(timing["alignment"], "scene")
            self.assertEqual(timing["schema_version"], 1)
            self.assertEqual(timing["captions"]["alignment"], "measured-phrases")
            cues = timing["captions"]["cues"]
            self.assertEqual([c["id"] for c in cues], ["s01-p001", "s01-p002", "s02-p001"])
            self.assertEqual([c["scene_id"] for c in cues], ["s01", "s01", "s02"])
            for cue, (start, end) in zip(cues, [(0, 0.1), (0.1, 0.4), (0.55, 0.75)]):
                self.assertAlmostEqual(cue["start"], start, delta=0.001)
                self.assertAlmostEqual(cue["end"], end, delta=0.001)
            self.assertAlmostEqual(timing["scenes"][0]["end"], 0.55, delta=0.001)
            self.assertAlmostEqual(timing["duration"], 0.8, delta=0.001)
            # Equal-length Chinese phrases deliberately have unequal audio durations.
            self.assertNotEqual(cues[0]["end"] - cues[0]["start"], cues[1]["end"] - cues[1]["start"])
            srt = (project / "narration.srt").read_text(encoding="utf-8")
            self.assertIn("00:00:00,100 --> 00:00:00,400\n振动推动空气。", srt)
            self.assertIn("00:00:00,550 --> 00:00:00,750", srt)
            with wave.open(str(project / "audio" / "s01.wav"), "rb") as output:
                samples = output.readframes(output.getnframes())
                self.assertTrue(any(samples[:1000]))
                self.assertEqual(samples[-4800:], b"\0" * 4800)
            self.assertEqual(set(p.name for p in project.iterdir()), set(tts.OUTPUTS))
            self.assertEqual(sorted(p.name for p in (project / "audio").iterdir()), ["s01.wav", "s02.wav"])

    def test_failed_phrase_overwrite_preserves_previous_bundle(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            document = job("doubao")
            document["tts"]["segment_mode"] = "phrases"
            document["scenes"][0]["narration"] = "声音来自振动。振动推动空气。"
            with mock.patch.object(tts, "synthesize_doubao", side_effect=lambda s, c, p: test_tone(p)):
                tts.execute(project, document)
            previous = {name: (project / name).read_bytes() for name in tts.OUTPUTS if name != "audio"}
            with mock.patch.object(tts, "synthesize_doubao", side_effect=AssertionError("must not synthesize")):
                with self.assertRaisesRegex(tts.TTSError, "already exists"):
                    tts.execute(project, document)
            attempted = []

            def empty_second_phrase(scene, config, destination):
                attempted.append(scene["narration"])
                test_tone(destination, 0.1 if len(attempted) == 1 else 0)

            with mock.patch.object(tts, "synthesize_doubao", side_effect=empty_second_phrase):
                with self.assertRaises(tts.TTSError):
                    tts.execute(project, document, force=True)
            self.assertEqual(len(attempted), 2)
            for name, expected in previous.items():
                self.assertEqual((project / name).read_bytes(), expected)
            self.assertFalse(list(project.glob(".tts-stage-*")))

    def test_phrase_publish_permission_failure_rolls_back_bundle(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            document = job("doubao")
            document["tts"]["segment_mode"] = "phrases"
            with mock.patch.object(tts, "synthesize_doubao", side_effect=lambda s, c, p: test_tone(p, 0.1)):
                tts.execute(project, document)
            previous = {str(path.relative_to(project)): path.read_bytes() for path in project.rglob("*") if path.is_file()}
            original_replace = tts.os.replace

            def permission_failure(source, destination):
                if Path(source).name == "narration.srt" and Path(source).parent.name.startswith(".tts-stage-"):
                    raise PermissionError("fixture failure")
                return original_replace(source, destination)

            with mock.patch.object(tts, "synthesize_doubao", side_effect=lambda s, c, p: test_tone(p, 0.2)), mock.patch.object(tts.os, "replace", side_effect=permission_failure):
                with self.assertRaisesRegex(tts.TTSError, "previous outputs restored"):
                    tts.execute(project, document, force=True)
            actual = {str(path.relative_to(project)): path.read_bytes() for path in project.rglob("*") if path.is_file()}
            self.assertEqual(actual, previous)
            self.assertFalse(list(project.glob(".tts-stage-*")))

    def test_phrase_say_synthesizes_each_utterance_and_reports_empty_audio(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            document = job("say")
            document["tts"]["segment_mode"] = "phrases"
            document["scenes"][0]["narration"] = "声音来自振动。振动推动空气。"
            spoken = []
            original_run = tts.run_command

            def local_command(args, label):
                if args[0] == "say":
                    spoken.append(Path(args[args.index("-f") + 1]).read_text(encoding="utf-8"))
                    test_tone(Path(args[args.index("-o") + 1]), 0.12)
                    return ""
                return original_run(args, label)

            with mock.patch.object(tts, "choose_say_voice", return_value="fixture-voice"), mock.patch.object(tts, "run_command", side_effect=local_command):
                timing = tts.execute(project, document)
            self.assertEqual(spoken, ["声音来自振动。", "振动推动空气。"])
            self.assertEqual(len(timing["captions"]["cues"]), 2)
            with mock.patch.object(tts, "choose_say_voice", return_value="fixture-voice"), mock.patch.object(tts, "synthesize_source", wraps=tts.synthesize_source), mock.patch.object(tts, "run_command", side_effect=lambda args, label: test_tone(Path(args[args.index("-o") + 1]), 0) if args[0] == "say" else original_run(args, label)):
                with self.assertRaisesRegex(tts.TTSError, "macOS say produced no usable audio"):
                    tts.execute(project, document, force=True)

    def test_external_real_duration_silence_and_overwrite_guard(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            (project / "audio-input").mkdir()
            test_tone(project / "audio-input" / "s01.wav")
            document = job()
            timing = tts.execute(project, tts.validate_job(document))
            self.assertEqual(timing["alignment"], "scene")
            self.assertNotIn("captions", timing)
            self.assertAlmostEqual(timing["duration"], 0.25, delta=0.002)
            self.assertEqual(timing["scenes"][0]["audio"], "audio/s01.wav")
            self.assertEqual(len(timing["job_fingerprint"]), 64)
            with wave.open(str(project / "audio" / "s01.wav"), "rb") as output:
                self.assertEqual((output.getframerate(), output.getnchannels()), (48000, 2))
                frames = output.readframes(output.getnframes())
                self.assertTrue(any(frames[:1000]))
                self.assertEqual(frames[-4800:], b"\0" * 4800)
            self.assertIn("声音来自振动。", (project / "narration.srt").read_text())
            with self.assertRaisesRegex(tts.TTSError, "already exists"):
                tts.execute(project, document)
            previous = (project / "timing.json").read_bytes()
            (project / "audio-input" / "s01.wav").write_bytes(b"invalid audio")
            with self.assertRaises(tts.TTSError):
                tts.execute(project, document, force=True)
            self.assertEqual((project / "timing.json").read_bytes(), previous)
            self.assertFalse(list(project.glob(".tts-stage-*")))

    def test_failed_run_publishes_no_success_bundle(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            (project / "audio-input").mkdir()
            (project / "audio-input" / "s01.wav").write_bytes(b"invalid audio")
            with self.assertRaises(tts.TTSError):
                tts.execute(project, job())
            for output in tts.OUTPUTS:
                self.assertFalse((project / output).exists())
            self.assertFalse(list(project.glob(".tts-stage-*")))

    def test_empty_source_cannot_be_hidden_by_hold_silence(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            (project / "audio-input").mkdir()
            test_tone(project / "audio-input" / "s01.wav", seconds=0)
            document = job()
            document["scenes"][0]["hold_seconds"] = 2
            with self.assertRaises(tts.TTSError):
                tts.execute(project, document)
            for output in tts.OUTPUTS:
                self.assertFalse((project / output).exists())

    def test_external_escape_and_ambiguous_inputs_rejected(self):
        with tempfile.TemporaryDirectory() as temporary, tempfile.TemporaryDirectory() as outside:
            project = Path(temporary)
            (project / "audio-input").mkdir()
            test_tone(Path(outside) / "private.wav")
            (project / "audio-input" / "s01.wav").symlink_to(Path(outside) / "private.wav")
            with self.assertRaisesRegex(tts.TTSError, "inside"):
                tts.external_audio(project, "s01")
            test_tone(project / "audio-input" / "s01.mp3")
            with self.assertRaisesRegex(tts.TTSError, "exactly one"):
                tts.external_audio(project, "s01")


if __name__ == "__main__":
    unittest.main()
