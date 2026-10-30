"""Offline speech smoke test with a fixed synthetic sentence, no mic or webhook."""
import argparse
import json
from pathlib import Path
import re
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.tokenserver.grok_voice import LocalSpeech, VoiceConfig  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--piper-model", required=True, type=Path)
    parser.add_argument("--stt-model", required=True, type=Path)
    args = parser.parse_args()
    if not args.piper_model.is_file() or not args.stt_model.is_dir():
        parser.error("Download both models locally before running this offline test")
    config = VoiceConfig("", "", "", "", language="sv",
                         piper_model=str(args.piper_model.resolve()),
                         model=str(args.stt_model.resolve()))
    speech = LocalSpeech(config)
    phrase = "Hej! Vilka uppgifter behöver min uppmärksamhet idag?"
    start = time.monotonic()
    try:
        pcm = speech.synthesize(phrase)
        tts_seconds = time.monotonic() - start
        start = time.monotonic()
        transcript = speech.transcribe(pcm)
    except Exception:
        print(json.dumps({"ok": False, "error": "local_speech_failed"}))
        return 1
    normalize = lambda text: re.findall(r"\w+", text.casefold())
    ok = normalize(transcript) == normalize(phrase)
    print(json.dumps({"ok": ok, "synthetic_audio_seconds": round(len(pcm) / 32000, 2),
                      "tts_seconds": round(tts_seconds, 2),
                      "stt_seconds": round(time.monotonic() - start, 2)}))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
