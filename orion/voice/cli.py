"""CLI harness for testing ORION offline voice (`python -m orion.voice.cli`)."""

import argparse
import sys
from pathlib import Path

from orion.application.voice.voice_service import VoiceService
from orion.infrastructure.voice.audio_io import PipeWireAudioInput, PipeWireAudioOutput
from orion.infrastructure.voice.piper_tts import PiperTtsAdapter
from orion.infrastructure.voice.whispercpp_stt import WhisperCppSttAdapter


def build_default_voice_service() -> VoiceService:
    return VoiceService(
        stt=WhisperCppSttAdapter(),
        tts=PiperTtsAdapter(),
        audio_in=PipeWireAudioInput(),
        audio_out=PipeWireAudioOutput(),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ORION offline voice CLI harness.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="Show offline voice model and audio backend status.")
    p_stt = sub.add_parser("stt", help="Transcribe a 16kHz mono WAV file offline.")
    p_stt.add_argument("wav", type=Path)
    p_tts = sub.add_parser("tts", help="Synthesize and play text offline.")
    p_tts.add_argument("text")

    args = parser.parse_args(argv)
    svc = build_default_voice_service()

    if args.cmd == "status":
        st = svc.get_asset_status()
        print(f"STT ready: {st.stt_ready} ({st.stt_model_path})")
        print(f"TTS ready: {st.tts_ready} ({st.tts_model_path})")
        print(f"Mic available: {st.mic_available} (backend={st.audio_backend})")
        if st.fix_hint:
            print(f"Fix hint: {st.fix_hint}")
        return 0
    if args.cmd == "stt":
        print(svc._stt.transcribe(args.wav))
        return 0
    if args.cmd == "tts":
        svc.speak_text(args.text)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
