# ORION Milestone 4.12 — Offline Voice Engine Evaluation & Decision

**Date:** 2026-11-27  
**Target Machine:** Ubuntu Linux (Python 3.12), 8 GB RAM, Intel i3-1115G4, `qwen3:1.7b` loaded in Ollama (`num_ctx=2048`).

---

## 1. Speech-to-Text (STT) Evaluation

Measured in isolated subprocesses on this machine against 16 kHz mono PCM WAV utterances (4.55 s short command and 11.16 s multi-sentence command) while `qwen3:1.7b` was loaded in Ollama (`1.6 GB`):

| Engine | Upstream & Status | Software Licence | Model & Licence | Python 3.12 / Ubuntu | Install Method | Model Size | Cached Load | 4.55s Latency | 11.16s Latency | Peak RSS (`VmHWM`) | Network Off |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **whisper.cpp (`pywhispercpp 1.5.1`)** | `ggml-org/whisper.cpp` (active, `v1.9.x` 2026) | MIT | `ggml-tiny.en.bin` (MIT) | Yes (wheel) | `uv pip install pywhispercpp` | 74.1 MB | **0.081 s** | 0.921 s | 1.218 s | **186.5 MB** | Yes |
| **faster-whisper (`1.2.1`)** | `SYSTRAN/faster-whisper` (active, `v1.2.1` Oct 2025) | MIT | `Systran/faster-whisper-tiny.en` int8 (MIT) | Partial (`av 19.0.2` breaks file path `decode_audio`) | `uv pip install faster-whisper` | ~40 MB | 0.310 s | 0.502 s | 0.596 s | 258.7 MB | Yes (`local_files_only=True`) |
| **Vosk (`0.3.45`)** | `alphacep/vosk-api` (active `v0.3.5x`) | Apache-2.0 | `vosk-model-small-en-us-0.15` (Apache-2.0) | Yes (wheel) | `uv pip install vosk` | 39.3 MB | 0.289 s | 0.822 s | 1.501 s | 189.0 MB | Yes |

### Accuracy Notes (Observed)
- **whisper.cpp (`tiny.en`)**: Preserved punctuation and capitalization (`"Show me my tasks and habits for today. I completed my reading habit today, and I want to check my weekly streak. Also remind me to review operating systems chapter 3 before 5 p.m. tomorrow."`).
- **faster-whisper (`tiny.en` int8)**: Similar accuracy, but `faster-whisper==1.2.1` raises `TypeError: open() got an unexpected keyword argument 'metadata_errors'` with `av==19.0.2` unless raw numpy arrays are decoded manually, and uses `258.7 MB` `VmHWM`.
- **Vosk (`small-en-us-0.15`)**: No punctuation/capitalization, misrecognized `"DBMS"` as `"be a mess"` and `"Show"` as `"though"`.

---

## 2. Text-to-Speech (TTS) Evaluation

| Engine | Upstream & Status | Software Licence | Voice Model & Licence | Python 3.12 / Ubuntu | Install Method | Model Size | Cached Load | 4.55s Synth | 11.16s Synth | Peak RSS (`VmHWM`) | Network Off |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **Piper (`piper-tts 1.8.0`)** | `OHF-Voice/piper1-gpl` (relocated from archived `rhasspy/piper`) | GPL-3.0-or-later | `en_US-lessac-medium.onnx` (MIT) | Yes (wheel) | `uv pip install piper-tts` | ~63 MB | **0.362 s** | **0.379 s** | **0.767 s** | **162.1 MB** | Yes |
| **espeak-ng** | `espeak-ng/espeak-ng` (`1.52.0`) | GPL-3.0-or-later | Built-in formant | Not pre-installed (`apt` available) | `apt install espeak-ng` | < 5 MB | — | — | — | — | Yes |

*(Note: `/usr/bin/piper` on Ubuntu is the GTK mouse configuration utility, not Piper TTS; `piper-tts` Python package provides the neural TTS engine.)*

---

## 3. Decision

- **STT:** **`pywhispercpp` (`ggml-tiny.en.bin`, `n_threads=2`)**
  - Fastest cold load (`0.081 s`), lowest `VmHWM` (`186.5 MB`, well under the 600 MB target), ~1 s transcription for 5–11 s utterances (well under the 10 s target), MIT licensed, clean punctuation, and zero `av` compatibility issues.
- **TTS:** **`piper-tts` (`en_US-lessac-medium`)**
  - First-sentence synthesis in `0.379 s` (well under the 2 s target), `162.1 MB` `VmHWM`, natural offline neural speech, 100% local.
- **Audio I/O:** **PipeWire (`pw-record` / `pw-play`)** with **ALSA (`arecord` / `aplay`)** fallback — avoids `PySide6.QtMultimedia` (not installed) and native PortAudio build issues.
- **Memory Management:** Models are loaded lazily on demand, guarded by `HeavyWorkGate` so STT/TTS never contend with active LLM streaming or indexing, and automatically unloaded after an idle timeout (default 120 s) so RSS returns to baseline.
