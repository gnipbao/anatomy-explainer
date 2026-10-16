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


def wav_bytes(seconds=0.1):
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(struct.pack("<h", 1000) * round(seconds * 24000))
    return output.getvalue()


class LocalResponse(io.BytesIO):
    def __init__(self, audio, headers=None, status=200):
        super().__init__(audio)
        self.headers = {"Content-Type": "audio/wav", "Content-Length": str(len(audio))} if headers is None else headers
        self.status = status

    def getcode(self):
        return self.status


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


class LocalAdapterTests(unittest.TestCase):
    def test_only_explicit_loopback_endpoint_is_accepted(self):
        for endpoint in (tts.LOCAL_ENDPOINT, "http://localhost:3931/v1/audio/speech", "http://[::1]:3931/v1/audio/speech"):
            document = job("local")
            document["tts"]["endpoint"] = endpoint
            self.assertIs(tts.validate_job(document), document)
        for endpoint in ("https://127.0.0.1:3931/v1/audio/speech", "http://example.com/v1/audio/speech",
                         "http://127.1:3931/v1/audio/speech", "http://2130706433:3931/v1/audio/speech",
                         "http://127.0.0.2:3931/v1/audio/speech", "http://localhost.evil/v1/audio/speech",
                         "http://user@127.0.0.1/v1/audio/speech", "http://localhost/v1/audio/speech?x=1",
                         "http://localhost/v1/audio/speech?", "http://localhost/v1/audio/speech#",
                         "http://localhost:0/v1/audio/speech", "http://localhost:99999/v1/audio/speech",
                         "http://localhost/other", "http://localhost/v1/audio/speech/",
                         "http://local\nhost/v1/audio/speech", "file:///v1/audio/speech", None):
            document = job("local")
            document["tts"]["endpoint"] = endpoint
            with self.subTest(endpoint=endpoint), self.assertRaisesRegex(tts.TTSError, "loopback"):
                tts.validate_job(document)

    def test_local_timeout_is_bounded_and_bool_is_rejected(self):
        for value in (29, 3601, True, "600", float("inf"), float("nan"), None):
            document = job("local")
            document["tts"]["timeout_seconds"] = value
            with self.subTest(value=value), self.assertRaisesRegex(tts.TTSError, "timeout_seconds"):
                tts.validate_job(document)
        for value in (30, 600, 3600):
            self.assertEqual(tts.local_timeout({"timeout_seconds": value}), value)

    def test_local_voice_is_validated_separately_from_cloud_voice(self):
        for value in (None, "", "   ", True, 4, "x" * 201, "name\nsecond-line", "name\t", "name\x7f"):
            document = job("local")
            document["tts"]["local_voice"] = value
            with self.subTest(value=value), self.assertRaisesRegex(tts.TTSError, "local_voice"):
                tts.validate_job(document)
        document = job("doubao")
        document["tts"]["voice"] = tts.DEFAULT_VOICE
        document["tts"]["local_voice"] = "narrator"
        self.assertIs(tts.validate_job(document), document)

    def test_switching_only_provider_preserves_cloud_voice_and_sends_local_default(self):
        document = job("doubao")
        document["tts"]["voice"] = tts.DEFAULT_VOICE
        document["tts"]["provider"] = "local"
        tts.validate_job(document)
        config = tts.dry_run_config(Path("/unused"), document)
        self.assertEqual(config["voice"], "default")
        for local_voice in (None, "narrator"):
            opener = mock.Mock()
            opener.open.return_value = LocalResponse(wav_bytes())
            if local_voice is not None:
                document["tts"]["local_voice"] = local_voice
            with self.subTest(local_voice=local_voice), tempfile.TemporaryDirectory() as temporary, mock.patch.object(tts.urllib.request, "build_opener", return_value=opener):
                tts.synthesize_local(document["scenes"][0], document["tts"], Path(temporary) / "result.wav", document["language"])
            self.assertEqual(json.loads(opener.open.call_args.args[0].data)["voice"], local_voice or "default")
        self.assertEqual(document["tts"]["voice"], tts.DEFAULT_VOICE)
        document["tts"]["provider"] = "doubao"
        self.assertEqual(tts.dry_run_config(Path("/unused"), document)["voice"], tts.DEFAULT_VOICE)

    def test_local_request_is_openai_compatible_and_disables_proxy_and_redirect(self):
        content = wav_bytes()
        opener = mock.Mock()
        opener.open.return_value = LocalResponse(content)
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(tts.urllib.request, "build_opener", return_value=opener) as build:
            destination = Path(temporary) / "result.wav"
            tts.synthesize_local(job("local")["scenes"][0], {"timeout_seconds": 900}, destination, "zh-CN")
            self.assertEqual(destination.read_bytes(), content)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, tts.LOCAL_ENDPOINT)
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(json.loads(request.data), {"model": "voxcpm2", "input": "声音来自振动。", "voice": "default", "response_format": "wav", "language": "zh-CN"})
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 900)
        self.assertNotIn("Authorization", request.headers)
        handlers = build.call_args.args
        self.assertTrue(any(isinstance(handler, tts.urllib.request.ProxyHandler) and handler.proxies == {} for handler in handlers))
        redirect = next(handler for handler in handlers if isinstance(handler, tts.NoRedirect))
        self.assertIsNone(redirect.redirect_request(request, None, 302, "Moved", {}, "http://example.com/"))

    def test_local_dry_run_does_not_contact_service_or_read_credentials(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            (project / "job.json").write_text(json.dumps(job("local")), encoding="utf-8")
            output = io.StringIO()
            with mock.patch.object(tts.urllib.request, "build_opener", side_effect=AssertionError("network")), mock.patch.object(tts, "build_auth_headers", side_effect=AssertionError("credentials")), redirect_stdout(output):
                self.assertEqual(tts.main(["--project", str(project), "--dry-run"]), 0)
            config = json.loads(output.getvalue())
            self.assertEqual(config["endpoint"], tts.LOCAL_ENDPOINT)
            self.assertEqual(config["voice"], "default")
            self.assertEqual(config["service_status"], "not inspected; required only for synthesis")
            self.assertIsNone(config["duration"])
            self.assertEqual(list(project.iterdir()), [project / "job.json"])

    def test_remote_endpoint_is_rejected_before_any_connection(self):
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(tts.urllib.request, "build_opener", side_effect=AssertionError("network")):
            destination = Path(temporary) / "result.wav"
            with self.assertRaisesRegex(tts.TTSError, "loopback"):
                tts.synthesize_local(job("local")["scenes"][0], {"endpoint": "http://example.com/v1/audio/speech"}, destination, "zh-CN")
            self.assertFalse(destination.exists())

    def test_local_response_rejects_json_empty_truncated_and_wrong_length_audio(self):
        content = wav_bytes()
        cases = [(b'{"error":"private-narration"}', {"Content-Type": "application/json"}),
                 (b"", {"Content-Type": "audio/wav"}),
                 (wav_bytes(0), None), (content[:-2], None),
                 (content, {"Content-Type": "audio/wav", "Content-Length": str(len(content) + 10)}),
                 (content, {"Content-Type": "audio/wav", "Content-Length": "-1"}),
                 (content, {"Content-Type": "audio/wav", "Content-Length": str(tts.MAX_LOCAL_AUDIO_BYTES + 1)})]
        for audio, headers in cases:
            with self.subTest(size=len(audio), headers=headers), tempfile.TemporaryDirectory() as temporary:
                destination = Path(temporary) / "result.wav"
                opener = mock.Mock()
                opener.open.return_value = LocalResponse(audio, headers)
                with mock.patch.object(tts.urllib.request, "build_opener", return_value=opener), self.assertRaises(tts.TTSError) as caught:
                    tts.synthesize_local(job("local")["scenes"][0], {}, destination, "zh-CN")
                self.assertNotIn("private-narration", str(caught.exception))
                self.assertFalse(destination.exists())

    def test_local_stream_size_is_bounded_without_content_length(self):
        opener = mock.Mock()
        opener.open.return_value = LocalResponse(b"a" * 65, {"Content-Type": "audio/wav"})
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(tts, "MAX_LOCAL_AUDIO_BYTES", 64), mock.patch.object(tts.urllib.request, "build_opener", return_value=opener):
            destination = Path(temporary) / "result.wav"
            with self.assertRaisesRegex(tts.TTSError, "size limit"):
                tts.synthesize_local(job("local")["scenes"][0], {}, destination, "zh-CN")
            self.assertFalse(destination.exists())

    def test_http_errors_are_safe_and_model_missing_is_actionable(self):
        for status in (302, 400, 500, 503):
            opener = mock.Mock()
            opener.open.side_effect = tts.urllib.error.HTTPError(tts.LOCAL_ENDPOINT, status, "sensitive-remote-message", {}, io.BytesIO(b"private-narration"))
            with self.subTest(status=status), tempfile.TemporaryDirectory() as temporary, mock.patch.object(tts.urllib.request, "build_opener", return_value=opener):
                destination = Path(temporary) / "result.wav"
                with self.assertRaises(tts.TTSError) as caught:
                    tts.synthesize_local(job("local")["scenes"][0], {}, destination, "zh-CN")
                message = str(caught.exception)
                self.assertIn(str(status), message)
                self.assertIn("doctor", message)
                self.assertNotIn("private-narration", message)
                self.assertNotIn("sensitive-remote-message", message)
                if status == 503:
                    self.assertIn("model path", message)
                self.assertFalse(destination.exists())

    def test_connection_failure_never_exposes_private_details(self):
        opener = mock.Mock()
        opener.open.side_effect = tts.urllib.error.URLError("private-connection-details")
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(tts.urllib.request, "build_opener", return_value=opener):
            with self.assertRaisesRegex(tts.TTSError, "connection failed") as caught:
                tts.synthesize_local(job("local")["scenes"][0], {}, Path(temporary) / "out.wav", "en-US")
            self.assertNotIn("private-connection-details", str(caught.exception))


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg and ffprobe are required")
class AudioIntegrationTests(unittest.TestCase):
    def test_local_full_scene_and_phrase_paths_keep_measured_audio_and_atomic_outputs(self):
        for segment_mode in ("scene", "phrases"):
            with self.subTest(segment_mode=segment_mode), tempfile.TemporaryDirectory() as temporary:
                project = Path(temporary)
                document = job("local")
                document["tts"]["segment_mode"] = segment_mode
                document["presentation"] = {"captions": {"style": "kinetic"}}
                document["scenes"][0]["narration"] = "声音来自振动。振动推动空气。"
                requested = []
                opener = mock.Mock()

                def local_open(request, timeout):
                    requested.append(json.loads(request.data))
                    return LocalResponse(wav_bytes(0.1 * len(requested)))

                opener.open.side_effect = local_open
                with mock.patch.object(tts.urllib.request, "build_opener", return_value=opener):
                    timing = tts.execute(project, document)
                self.assertEqual(timing["provider"], "local")
                self.assertEqual(set(path.name for path in project.iterdir()), set(tts.OUTPUTS))
                self.assertTrue(all(payload["language"] == "zh-CN" for payload in requested))
                if segment_mode == "scene":
                    self.assertEqual([payload["input"] for payload in requested], [document["scenes"][0]["narration"]])
                    self.assertNotIn("captions", timing)
                    self.assertAlmostEqual(timing["duration"], 0.25, delta=0.001)
                else:
                    self.assertEqual([payload["input"] for payload in requested], ["声音来自振动。", "振动推动空气。"])
                    self.assertEqual(timing["captions"]["alignment"], "measured-phrases")
                    self.assertEqual([(cue["start"], cue["end"]) for cue in timing["captions"]["cues"]], [(0.0, 0.1), (0.1, 0.3)])
                    self.assertAlmostEqual(timing["duration"], 0.45, delta=0.001)
                previous = {str(path.relative_to(project)): path.read_bytes() for path in project.rglob("*") if path.is_file()}
                opener.open.side_effect = lambda request, timeout: LocalResponse(wav_bytes()[:-2])
                with mock.patch.object(tts.urllib.request, "build_opener", return_value=opener), self.assertRaises(tts.TTSError):
                    tts.execute(project, document, force=True)
                actual = {str(path.relative_to(project)): path.read_bytes() for path in project.rglob("*") if path.is_file()}
                self.assertEqual(actual, previous)
                self.assertFalse(list(project.glob(".tts-stage-*")))

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
            document["presentation"] = {"captions": {"style": "kinetic"}}
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

    def test_cinema_srt_formats_display_without_changing_measured_audio_cues(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            document = job("doubao")
            document['video'] = {'width': 1920, 'height': 1080, 'fps': 24}
            document['tts']['segment_mode'] = 'phrases'
            document['scenes'][0].update(narration='声音来自振动。振动推动空气。', hold_seconds=0.2)
            with mock.patch.object(tts, 'synthesize_doubao', side_effect=lambda s, c, p: test_tone(p, 2)):
                timing = tts.execute(project, document)
            self.assertEqual([c['text'] for c in timing['captions']['cues']], ['声音来自振动。', '振动推动空气。'])
            self.assertAlmostEqual(timing['captions']['cues'][0]['end'], 2, places=3)
            srt = (project/'narration.srt').read_text()
            self.assertIn('00:00:00,000 --> 00:00:01,916', srt)
            self.assertIn('00:00:02,000 --> 00:00:04,000', srt)
            self.assertNotIn('。', srt)
            self.assertIn('声音来自振动\n', srt)

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
