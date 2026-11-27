"""One-command offline voice model setup (`python -m orion.voice.setup`)."""

import argparse
import sys

from orion.infrastructure.voice.audio_io import _voice_data_dir
from orion.infrastructure.voice.piper_tts import PiperTtsAdapter
from orion.infrastructure.voice.whispercpp_stt import WhisperCppSttAdapter


def setup_voice_models(
    *, stt_model: str = "tiny.en", tts_voice: str = "en_US-lessac-medium"
) -> int:
    """Ensure local whisper.cpp and Piper models are downloaded and verified."""
    base = _voice_data_dir()
    stt_dir = base / "whispercpp"
    tts_dir = base / "piper"
    stt_dir.mkdir(parents=True, exist_ok=True)
    tts_dir.mkdir(parents=True, exist_ok=True)

    stt = WhisperCppSttAdapter(models_dir=stt_dir, model_name=stt_model)
    if stt.is_model_available():
        print(f"[STT] Ready: {stt.model_path()}")
    else:
        print(f"[STT] Downloading whisper.cpp '{stt_model}' to {stt_dir}...")
        from pywhispercpp.model import Model

        Model(stt_model, models_dir=str(stt_dir), print_realtime=False, print_progress=False)
        print(f"[STT] Ready: {stt.model_path()}")

    tts = PiperTtsAdapter(models_dir=tts_dir, voice_name=tts_voice)
    if tts.is_model_available():
        print(f"[TTS] Ready: {tts.model_path()}")
    else:
        print(f"[TTS] Downloading Piper '{tts_voice}' to {tts_dir}...")
        from piper.download_voices import download_voice

        download_voice(tts_voice, tts_dir)
        print(f"[TTS] Ready: {tts.model_path()}")

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Download and verify ORION offline voice models.")
    parser.add_argument("--stt-model", default="tiny.en")
    parser.add_argument("--tts-voice", default="en_US-lessac-medium")
    args = parser.parse_args(argv)
    return setup_voice_models(stt_model=args.stt_model, tts_voice=args.tts_voice)


if __name__ == "__main__":
    sys.exit(main())
