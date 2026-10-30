import http.client
import json
import os
import struct
import subprocess
import sys
import threading
import unittest
import wave
from unittest.mock import patch

from .grok_voice import LocalSpeech, VoiceConfig, VoiceError, VoiceService, chime_pcm, post_webhook
from .tokenserver import BoundedThreadingHTTPServer, Handler, _build_arg_parser


class ImmediateExecutor:
    def submit(self, fn, *args):
        fn(*args)

    def shutdown(self, **kwargs):
        return None


class Speech:
    def transcribe(self, pcm):
        return "Hej team, what needs my attention?"

    def synthesize(self, text):
        return b"\0\0" * 160


class SpeechEncodingTests(unittest.TestCase):
    def test_piper_receives_swedish_with_non_utf8_parent(self):
        original_run = subprocess.run
        received = []

        def fake_piper(command, **kwargs):
            # Exercise a real Python stdin decoder, as Piper uses sys.stdin.
            result = original_run([sys.executable, "-c",
                "import sys,json; print(json.dumps(sys.stdin.read(), ensure_ascii=True))"],
                **kwargs)
            received.append(json.loads(result.stdout))
            with wave.open(command[command.index("--output-file") + 1], "wb") as w:
                # Deliberately invalid TTS format exits before optional NumPy.
                w.setnchannels(2)
                w.setsampwidth(2)
                w.setframerate(16000)
                w.writeframes(b"\0" * 8)
            return result

        config = VoiceConfig("https://example.invalid", "unused", "unused", "unused",
                             piper_model="test-model.onnx")
        phrase = "Vilka uppgifter behöver min uppmärksamhet?"
        with patch.dict(os.environ, {"PYTHONIOENCODING": "cp1252"}), \
             patch("tools.tokenserver.grok_voice.subprocess.run", side_effect=fake_piper):
            with self.assertRaises(VoiceError):
                LocalSpeech(config).synthesize(phrase)
        self.assertEqual(received, [phrase])


class VoiceTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.sent = []
        self.config = VoiceConfig("https://example.invalid/routine", "test-webhook-key",
                                  "test-reply-token", "test-device-token")
        self.service = VoiceService(self.config, Speech(), self.sent.append,
                                    lambda: self.now, ImmediateExecutor())

    def tearDown(self):
        self.service.stop()

    def utterance(self, device="p4-7b"):
        sid = self.service.begin(device)["request_id"]
        voice = struct.pack("<h", 2000) * 1600
        for index in range(3):
            self.service.push(sid, voice, index)
        for index in range(3, 12):
            self.service.push(sid, b"\0\0" * 1600, index)
        return sid

    def test_end_to_end_callback_and_playback(self):
        sid = self.utterance()
        state = self.service.snapshot(sid)
        self.assertEqual(state["state"], "Thinking")
        self.assertEqual(self.sent, [{"request_id": sid, "text": Speech().transcribe(b""), "device": "p4-7b"}])
        self.assertEqual(self.service.playback(sid, 1), chime_pcm())
        self.service.reply({"request_id": sid, "spoken_text": "Två saker behöver dig.", "status": "completed"})
        self.assertEqual(self.service.snapshot(sid)["state"], "Speaking")
        self.assertTrue(self.service.playback(sid, 2))
        self.assertEqual(self.service.played(sid)["state"], "Done")
        self.assertFalse(self.service.get(sid).pcm)

    def test_timeout_spoken_once_and_late_reply_rejected(self):
        sid = self.utterance()
        self.now = 180
        self.service.tick()
        self.assertEqual(self.service.snapshot(sid)["status"], "reply_timeout")
        self.assertEqual(self.service.snapshot(sid)["state"], "Speaking")
        with self.assertRaises(VoiceError):
            self.service.reply({"request_id": sid, "spoken_text": "Late", "status": "completed"})

    def test_duplicate_callback_does_not_speak_twice(self):
        sid = self.utterance()
        reply = {"request_id": sid, "spoken_text": "Hello", "status": "completed"}
        self.service.reply(reply)
        with self.assertRaises(VoiceError) as error:
            self.service.reply(reply)
        self.assertEqual(error.exception.code, 409)

    def test_silence_never_sends_webhook(self):
        sid = self.service.begin("p4")["request_id"]
        for index in range(40):
            self.service.push(sid, b"\0" * 6400, index)
        self.assertEqual(self.service.snapshot(sid)["status"], "no_speech")
        self.assertFalse(self.sent)

    def test_rate_limit_and_hourly_recovery(self):
        self.config.limit = 1
        self.service.begin("p4")
        with self.assertRaises(VoiceError) as error:
            self.service.begin("p5")
        self.assertEqual(error.exception.code, 429)
        self.now = 3601
        self.service.tick()
        self.assertEqual(self.service.begin("p5")["state"], "Listening")

    def test_disconnected_capture_expires_and_is_purged(self):
        sid = self.service.begin("p4")["request_id"]
        self.now = 6
        self.service.tick()
        self.assertEqual(self.service.snapshot(sid)["status"], "capture_timeout")
        self.now = 67
        self.service.tick()
        self.assertNotIn(sid, self.service.sessions)

    def test_sequence_size_and_device_validation(self):
        sid = self.service.begin("p4")["request_id"]
        for data, sequence in ((b"\0", 0), (b"\0" * 6402, 0), (b"\0\0", 2)):
            with self.assertRaises(VoiceError):
                self.service.push(sid, data, sequence)
        with self.assertRaises(VoiceError):
            self.service.begin("../other")
        self.assertEqual(self.service.snapshot(sid)["next_sequence"], 0)

    def test_separate_tokens(self):
        self.assertTrue(self.service.authorized("Bearer test-device-token"))
        self.assertFalse(self.service.authorized("Bearer test-device-token", reply=True))
        self.assertFalse(self.service.authorized(None))

    def test_provider_failure_never_exposes_exception(self):
        def fail(payload):
            raise RuntimeError("sensitive-provider-key-and-transcript")
        self.service.post = fail
        sid = self.utterance()
        self.assertEqual(self.service.snapshot(sid)["status"], "request_failed")
        self.assertNotIn("sensitive", json.dumps(self.service.snapshot(sid)))

    def test_tts_failure_preserves_visible_reply(self):
        self.service.speech.synthesize = lambda text: b""
        sid = self.utterance()
        self.service.reply({"request_id": sid, "spoken_text": "Visible answer", "status": "completed"})
        self.assertEqual(self.service.snapshot(sid)["status"], "tts_failed")
        self.assertEqual(self.service.snapshot(sid)["spoken_text"], "Visible answer")

    def test_config_fails_closed_and_default_off(self):
        self.assertFalse(_build_arg_parser().parse_args([]).grok_voice)
        with self.assertRaises(ValueError):
            VoiceConfig.from_env({})
        env = {"GROKBOT_WEBHOOK_URL": "https://example.invalid/routine",
               "GROKBOT_WEBHOOK_KEY": "example-webhook-key", "VIBEPULSE_REPLY_TOKEN": "example-reply-token",
               "VIBEPULSE_VOICE_TOKEN": "example-device-token"}
        self.assertEqual(VoiceConfig.from_env(env).limit, 10)
        env["GROKBOT_WEBHOOK_URL"] = "http://example.invalid/"
        with self.assertRaises(ValueError):
            VoiceConfig.from_env(env)

    def test_webhook_shape_timeout_and_no_redirect_handler(self):
        with patch("tools.tokenserver.grok_voice.build_opener") as opener:
            response = opener.return_value.open.return_value.__enter__.return_value
            response.status = 202
            post_webhook(self.config, {"request_id": "test", "text": "hej", "device": "p4"})
            request = opener.return_value.open.call_args.args[0]
            self.assertEqual(json.loads(request.data)["text"], "hej")
            self.assertEqual(request.get_header("Authorization"), "Bearer test-webhook-key")
            self.assertEqual(opener.return_value.open.call_args.kwargs["timeout"], 15)


class VoiceHTTPTests(VoiceTests):
    # Inherit domain regressions and add real socket/Handler integration.
    def setUp(self):
        super().setUp()
        service = self.service
        class TestHandler(Handler):
            grok_voice = service
        self.handler = TestHandler
        self.server = BoundedThreadingHTTPServer(("127.0.0.1", 0), TestHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        super().tearDown()

    def request(self, path, payload, token="test-device-token", content_type="application/json"):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=3)
        data = json.dumps(payload) if content_type == "application/json" else payload
        conn.request("POST", path, data, {"Content-Type": content_type, "Authorization": "Bearer " + token})
        response = conn.getresponse()
        result = (response.status, json.loads(response.read()))
        conn.close()
        return result

    def test_http_disabled_and_bad_auth(self):
        self.assertEqual(self.request("/grokbot/session", {"device": "p4"}, "wrong")[0], 401)
        self.handler.grok_voice = None
        self.assertEqual(self.request("/grokbot/session", {"device": "p4"})[0], 404)

    def test_http_reply_requires_own_token(self):
        sid = self.utterance()
        reply = {"request_id": sid, "spoken_text": "Hej!", "status": "completed"}
        self.assertEqual(self.request("/grokbot/reply", reply)[0], 401)
        self.assertEqual(self.request("/grokbot/reply", reply, "test-reply-token")[0], 200)

    def test_http_pcm_and_unknown_request(self):
        code, state = self.request("/grokbot/session", {"device": "p4"})
        self.assertEqual(code, 200)
        sid = state["request_id"]
        self.assertEqual(self.request("/grokbot/audio/" + sid + "/0", b"\0" * 3200,
                                     content_type="application/octet-stream")[0], 200)
        self.assertEqual(self.request("/grokbot/audio/" + sid + "/1", b"\0",
                                     content_type="application/octet-stream")[0], 400)


if __name__ == "__main__":
    unittest.main()
