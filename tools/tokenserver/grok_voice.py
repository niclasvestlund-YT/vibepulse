"""Opt-in voice workflow. Only the operator-configured routine webhook is used.

Audio/transcripts live in bounded memory and temporary local files, never logs.
No desktop automation, private Grok APIs, subscription tokens or call APIs.
"""
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import hashlib
import hmac
import io
import json
import math
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
import uuid
import wave

RATE = 16000
MAX_PCM = RATE * 2 * 30
MAX_TEXT = 2000


class VoiceError(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


def bounded_text(value, limit=MAX_TEXT):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise VoiceError(400, "invalid_text")
    if any(ord(c) < 32 and c not in "\n\t" for c in value):
        raise VoiceError(400, "invalid_text")
    return value.strip()


@dataclass(repr=False)
class VoiceConfig:
    webhook_url: str
    webhook_key: str
    reply_token: str
    device_token: str
    limit: int = 10
    timeout: float = 180
    model: str = "small"
    language: str = "auto"
    piper_model: str = ""
    tts: str = "piper"
    silence_ms: int = 900
    energy: int = 500

    @classmethod
    def from_env(cls, env=None):
        e = os.environ if env is None else env
        values = [e.get(k, "") for k in ("GROKBOT_WEBHOOK_URL", "GROKBOT_WEBHOOK_KEY",
                  "VIBEPULSE_REPLY_TOKEN", "VIBEPULSE_VOICE_TOKEN")]
        url = urlsplit(values[0])
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.fragment:
            raise ValueError("Voice requires an HTTPS routine webhook URL")
        if any(len(v) < 16 or any(c.isspace() for c in v) for v in values[1:]):
            raise ValueError("Voice requires three nonempty tokens of at least 16 characters")
        if len(set(values[1:])) != 3:
            raise ValueError("Use separate webhook, reply and device tokens")
        try:
            limit = int(e.get("VIBEPULSE_VOICE_REQUESTS_PER_HOUR", "10"))
            silence = int(e.get("VIBEPULSE_VOICE_SILENCE_MS", "900"))
            energy = int(e.get("VIBEPULSE_VOICE_ENERGY", "500"))
        except ValueError:
            raise ValueError("Invalid voice numeric configuration") from None
        language = e.get("VIBEPULSE_STT_LANGUAGE", "auto")
        tts = e.get("VIBEPULSE_TTS", "piper")
        if not 1 <= limit <= 100 or not 300 <= silence <= 3000 or not 50 <= energy <= 10000:
            raise ValueError("Voice configuration outside supported bounds")
        if language not in ("auto", "sv", "en") or tts not in ("piper", "elevenlabs"):
            raise ValueError("Unsupported voice language or TTS provider")
        return cls(*values, limit=limit, language=language, tts=tts, silence_ms=silence,
                   energy=energy, model=e.get("VIBEPULSE_STT_MODEL", "small"),
                   piper_model=e.get("VIBEPULSE_PIPER_MODEL", ""))


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward credentials to another endpoint.


def post_webhook(config, payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = Request(config.webhook_url, data=body, headers={
        "Authorization": "Bearer " + config.webhook_key, "Content-Type": "application/json"})
    with build_opener(NoRedirect()).open(req, timeout=15) as response:
        if not 200 <= response.status < 300:
            raise VoiceError(502, "webhook_failed")
        response.read(1024)  # Neither inspect nor log provider response bodies.


def wav_bytes(pcm):
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)
    return out.getvalue()


def chime_pcm():
    # Short acknowledgement generated locally; no cloud audio or microphone echo.
    return b"".join(struct.pack("<h", int(4500 * math.sin(2 * math.pi * 660 * i / RATE)
                      * math.sin(math.pi * i / 2400))) for i in range(2400))


class LocalSpeech:
    def __init__(self, config):
        self.config = config

    def transcribe(self, pcm):
        # A subprocess bounds inference time and keeps model libraries optional.
        with tempfile.TemporaryDirectory(prefix="vibepulse-stt-") as directory:
            path = Path(directory) / "input.wav"
            path.write_bytes(wav_bytes(pcm))
            command = [sys.executable, str(Path(__file__).with_name("grok_voice_stt.py")),
                       str(path), self.config.model, self.config.language]
            result = subprocess.run(command, capture_output=True, timeout=90, check=True)
            return bounded_text(json.loads(result.stdout)["text"])

    def synthesize(self, text):
        if self.config.tts == "elevenlabs":
            key = os.environ.get("ELEVENLABS_API_KEY", "")
            voice = os.environ.get("ELEVENLABS_VOICE_ID", "")
            if not key or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", voice):
                raise VoiceError(503, "tts_not_configured")
            req = Request("https://api.elevenlabs.io/v1/text-to-speech/" + voice
                          + "?output_format=pcm_16000", data=json.dumps({
                              "text": text, "model_id": "eleven_multilingual_v2"}).encode(),
                          headers={"xi-api-key": key, "Content-Type": "application/json"})
            with build_opener(NoRedirect()).open(req, timeout=25) as response:
                pcm = response.read(RATE * 2 * 120 + 1)
            if len(pcm) % 2 or len(pcm) > RATE * 2 * 120:
                raise VoiceError(502, "invalid_tts_audio")
            return pcm
        if not self.config.piper_model:
            raise VoiceError(503, "tts_not_configured")
        with tempfile.TemporaryDirectory(prefix="vibepulse-tts-") as directory:
            path = Path(directory) / "output.wav"
            subprocess.run([sys.executable, "-m", "piper", "--model",
                            self.config.piper_model, "--output-file", str(path)],
                           input=text.encode("utf-8"), capture_output=True, timeout=45, check=True)
            with wave.open(str(path), "rb") as w:
                if w.getnchannels() != 1 or w.getsampwidth() != 2 or w.getnframes() > w.getframerate() * 120:
                    raise VoiceError(502, "invalid_tts_audio")
                source_rate = w.getframerate()
                samples = w.readframes(w.getnframes())
            # Local CPU resampling, works on Python 3.12+ without removed audioop.
            import numpy as np
            data = np.frombuffer(samples, dtype="<i2")
            if not len(data):
                raise VoiceError(502, "empty_tts_audio")
            count = int(len(data) * RATE / source_rate)
            return np.interp(np.arange(count) * source_rate / RATE,
                             np.arange(len(data)), data).astype("<i2").tobytes()


@dataclass
class Session:
    request_id: str
    device: str
    created: float
    updated: float
    state: str = "Listening"
    pcm: bytearray = field(default_factory=bytearray, repr=False)
    speech_samples: int = 0
    silence_samples: int = 0
    sequence: int = 0
    transcript: str = ""
    spoken_text: str = ""
    status: str = "listening"
    deadline: float = 0
    audio: dict = field(default_factory=dict, repr=False)
    audio_version: int = 0
    reply_received: bool = False
    webhook_sent: bool = False


class VoiceService:
    def __init__(self, config, speech=None, post=None, clock=time.monotonic, executor=None):
        self.config = config
        self.speech = speech or LocalSpeech(config)
        self.post = post or (lambda p: post_webhook(config, p))
        self.clock = clock
        self.lock = threading.RLock()
        self.sessions = {}
        self.requests = deque()
        self.executor = executor or ThreadPoolExecutor(max_workers=2, thread_name_prefix="grok-voice")
        self.stop_event = threading.Event()
        self.timer = None

    def start(self):
        def run():
            while not self.stop_event.wait(0.5):
                self.tick()
        self.timer = threading.Thread(target=run, daemon=True, name="grok-voice-timeouts")
        self.timer.start()

    def stop(self):
        self.stop_event.set()
        if self.timer:
            self.timer.join(timeout=2)
        self.executor.shutdown(wait=False, cancel_futures=True)
        with self.lock:
            self.sessions.clear()

    def authorized(self, header, reply=False):
        token = self.config.reply_token if reply else self.config.device_token
        return isinstance(header, str) and hmac.compare_digest(
            hashlib.sha256(header.encode()).digest(),
            hashlib.sha256(("Bearer " + token).encode()).digest())

    def begin(self, device):
        device = bounded_text(device, 64)
        if not re.fullmatch(r"[A-Za-z0-9_-]+", device):
            raise VoiceError(400, "invalid_device")
        with self.lock:
            now = self.clock()
            while self.requests and now - self.requests[0] >= 3600:
                self.requests.popleft()
            if len(self.requests) >= self.config.limit:
                raise VoiceError(429, "hourly_limit")
            if any(s.device == device and s.state not in ("Done", "Error") for s in self.sessions.values()):
                raise VoiceError(409, "device_busy")
            # At most four retained sessions, two active inference/reply jobs.
            if sum(s.state not in ("Done", "Error") for s in self.sessions.values()) >= 2:
                raise VoiceError(503, "voice_busy")
            if len(self.sessions) >= 4:
                terminal = [k for k, s in self.sessions.items() if s.state in ("Done", "Error")]
                if not terminal:
                    raise VoiceError(503, "voice_busy")
                del self.sessions[terminal[0]]
            sid = str(uuid.uuid4())
            self.sessions[sid] = Session(sid, device, now, now)
            self.requests.append(now)  # Count attempts too, to bound local STT work.
            return self.snapshot(sid)

    def get(self, sid):
        if sid not in self.sessions:
            raise VoiceError(404, "unknown_request")
        return self.sessions[sid]

    def snapshot(self, sid):
        with self.lock:
            s = self.get(sid)
            return {"request_id": s.request_id, "state": s.state, "status": s.status,
                    "transcript": s.transcript, "spoken_text": s.spoken_text,
                    "audio_version": s.audio_version, "next_sequence": s.sequence,
                    "sample_rate": RATE}

    def push(self, sid, pcm, sequence):
        if not pcm or len(pcm) > 6400 or len(pcm) % 2:
            raise VoiceError(400, "invalid_pcm")
        with self.lock:
            s = self.get(sid)
            if s.state != "Listening":
                return self.snapshot(sid)
            if sequence != s.sequence:
                raise VoiceError(409, "sequence_mismatch")
            if len(s.pcm) + len(pcm) > MAX_PCM:
                raise VoiceError(413, "audio_limit")
            s.sequence += 1
            s.updated = self.clock()
            s.pcm.extend(pcm)
            # Evaluate 20 ms windows, not packet-level energy; transport chunking
            # must not change the trailing-silence decision.
            for offset in range(0, len(pcm), 640):
                part = pcm[offset:offset + 640]
                values = struct.unpack("<" + "h" * (len(part) // 2), part)
                rms = math.sqrt(sum(x*x for x in values) / len(values))
                if rms >= self.config.energy:
                    s.speech_samples += len(values)
                    s.silence_samples = 0
                else:
                    s.silence_samples += len(values)
            speech = s.speech_samples >= RATE // 5
            ended = speech and s.silence_samples >= RATE * self.config.silence_ms // 1000
            empty = not speech and len(s.pcm) >= RATE * 2 * 8
            if empty:
                s.pcm.clear()
                s.state, s.status = "Error", "no_speech"
            elif ended or len(s.pcm) >= MAX_PCM:
                s.state, s.status = "Thinking", "transcribing"
                s.deadline = self.clock() + self.config.timeout
                s.audio[1] = chime_pcm()
                s.audio_version = 1
                audio = bytes(s.pcm)
                s.pcm.clear()
                self.executor.submit(self._transcribe_send, sid, audio)
            return self.snapshot(sid)

    def _transcribe_send(self, sid, pcm):
        try:
            transcript = self.speech.transcribe(pcm)
            with self.lock:
                s = self.get(sid)
                if s.state != "Thinking" or s.reply_received or self.clock() >= s.deadline:
                    return
                s.transcript = bounded_text(transcript)
                s.status = "waiting_for_team"
                s.webhook_sent = True  # Callback may arrive before POST returns.
                payload = {"request_id": sid, "text": s.transcript, "device": s.device}
            self.post(payload)  # No retries: avoid duplicate routine execution.
        except Exception:
            # Provider/process exceptions may contain credentials or transcript.
            # Expose only a fixed code; never log exception text or payload.
            self._fallback(sid, "request_failed")

    def reply(self, payload):
        if not isinstance(payload, dict):
            raise VoiceError(400, "invalid_reply")
        sid = payload.get("request_id")
        if not isinstance(sid, str):
            raise VoiceError(400, "invalid_request_id")
        text = bounded_text(payload.get("spoken_text"))
        status = payload.get("status")
        if status not in ("completed", "error"):
            raise VoiceError(400, "invalid_reply_status")
        with self.lock:
            s = self.get(sid)
            if s.reply_received:
                raise VoiceError(409, "reply_already_received")
            if s.state != "Thinking" or not s.webhook_sent or self.clock() >= s.deadline:
                raise VoiceError(410, "request_expired")
            s.reply_received = True
            s.spoken_text, s.status = text, status
            self.executor.submit(self._speak, sid, text)
        return {"ok": True, "request_id": sid}

    def _fallback(self, sid, code):
        with self.lock:
            if sid not in self.sessions:
                return
            s = self.sessions[sid]
            if s.reply_received or s.state != "Thinking":
                return
            s.reply_received = True
            text = ("Jag fick inget svar i tid. Försök igen senare." if self.config.language != "en"
                    else "I did not get a reply in time. Please try again later.")
            s.spoken_text, s.status = text, code
        self._speak(sid, text)

    def _speak(self, sid, text):
        try:
            pcm = self.speech.synthesize(text)
            if not pcm or len(pcm) % 2 or len(pcm) > RATE * 2 * 120:
                raise ValueError("Invalid speech audio")
            with self.lock:
                if sid not in self.sessions:
                    return
                s = self.sessions[sid]
                if s.state != "Thinking":
                    return
                s.audio[2] = pcm
                s.audio_version = 2
                s.state = "Speaking"
                s.updated = self.clock()
        except Exception:
            with self.lock:
                if sid in self.sessions:
                    s = self.sessions[sid]
                    s.state, s.status = "Error", "tts_failed"
                    s.updated = self.clock()

    def playback(self, sid, version):
        with self.lock:
            s = self.get(sid)
            if version not in s.audio:
                raise VoiceError(404, "audio_not_ready")
            return s.audio[version]

    def played(self, sid):
        with self.lock:
            s = self.get(sid)
            if s.state != "Speaking":
                raise VoiceError(409, "not_speaking")
            s.state = "Done"
            s.audio.clear()
            s.updated = self.clock()
            return self.snapshot(sid)

    def tick(self):
        timed_out = []
        with self.lock:
            now = self.clock()
            for sid, s in list(self.sessions.items()):
                if s.state == "Listening" and (now - s.updated > 5 or now - s.created > 40):
                    s.pcm.clear()
                    s.state, s.status, s.updated = "Error", "capture_timeout", now
                elif (s.state == "Thinking" and not s.reply_received and now >= s.deadline
                      and s.status != "timeout_pending"):
                    s.status = "timeout_pending"
                    timed_out.append(sid)
                elif s.state == "Speaking" and now - s.updated > 150:
                    s.state, s.status, s.updated = "Error", "playback_timeout", now
                    s.audio.clear()
                elif s.state in ("Done", "Error") and now - s.updated > 60:
                    del self.sessions[sid]
        for sid in timed_out:
            # Timeout synthesis must not block the HTTP server or timer thread.
            self.executor.submit(self._fallback, sid, "reply_timeout")
