"""Private worker for local STT; no remote inference or transcript logging."""
import json
import sys


def main():
    from faster_whisper import WhisperModel
    filename, model, language = sys.argv[1:]
    engine = WhisperModel(model, device="cpu", compute_type="int8")
    segments, _ = engine.transcribe(filename, language=None if language == "auto" else language,
                                    vad_filter=True, beam_size=1, condition_on_previous_text=False)
    text = " ".join(segment.text.strip() for segment in segments).strip()
    sys.stdout.write(json.dumps({"text": text}, ensure_ascii=True))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Captured by parent. Do not emit local paths, model errors or input text.
        sys.exit(1)
