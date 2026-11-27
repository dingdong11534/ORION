"""PipeWire / ALSA subprocess audio capture and playback adapters."""

import math
import shutil
import signal
import struct
import subprocess
import threading
import wave
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class WavAudioStats:
    """Metadata and signal amplitude summary for a captured WAV file."""

    sample_rate: int
    channels: int
    sample_width: int
    nframes: int
    duration_seconds: float
    rms: float
    peak: int
    file_size: int


def measure_wav_stats(wav_path: Path) -> WavAudioStats:
    """Validate WAV format and measure duration, RMS, and peak amplitude."""
    if not wav_path.is_file() or wav_path.stat().st_size <= 44:
        raise RuntimeError(f"Captured audio file is empty or missing: {wav_path}")
    with wave.open(str(wav_path), "rb") as w:
        rate = w.getframerate()
        ch = w.getnchannels()
        width = w.getsampwidth()
        nframes = w.getnframes()
        frames = w.readframes(nframes)
    if rate != 16000 or ch != 1 or width != 2:
        raise RuntimeError(
            f"Unexpected WAV format: rate={rate}, channels={ch}, sample_width={width}"
        )
    if nframes == 0 or len(frames) < 2:
        raise RuntimeError("Captured WAV contains 0 audio frames.")
    count = len(frames) // 2
    samples = struct.unpack(f"<{count}h", frames[: count * 2])
    rms = math.sqrt(sum(s * s for s in samples) / count)
    peak = max(abs(s) for s in samples)
    return WavAudioStats(
        sample_rate=rate,
        channels=ch,
        sample_width=width,
        nframes=nframes,
        duration_seconds=nframes / float(rate),
        rms=rms,
        peak=peak,
        file_size=wav_path.stat().st_size,
    )


def _voice_data_dir() -> Path:
    import os

    base = os.environ.get("XDG_DATA_HOME", "").strip()
    root = Path(base) if base else (Path.home() / ".local" / "share")
    return (root / "orion" / "voice").resolve()


class PipeWireAudioInput:
    """Microphone capture using pw-record (PipeWire) with arecord (ALSA) fallback."""

    def __init__(self) -> None:
        self._proc: subprocess.Popen[bytes] | None = None
        self._output_wav: Path | None = None
        self._lock = threading.Lock()

    def backend_name(self) -> str:
        if shutil.which("pw-record"):
            return "pipewire"
        if shutil.which("arecord"):
            return "alsa"
        return "none"

    def is_available(self) -> bool:
        if self.backend_name() == "none":
            return False
        if shutil.which("arecord"):
            try:
                res = subprocess.run(
                    ["arecord", "-l"],
                    capture_output=True,
                    text=True,
                    timeout=3,
                    check=False,
                )
                return "card " in res.stdout.lower()
            except Exception:  # noqa: BLE001
                return True
        return True

    def start_recording(self, output_wav: Path, device: str = "default") -> None:
        with self._lock:
            if self._proc is not None:
                raise RuntimeError("Recording already in progress.")
            backend = self.backend_name()
            if backend == "pipewire":
                cmd = [
                    "pw-record",
                    "--rate",
                    "16000",
                    "--channels",
                    "1",
                    "--format",
                    "s16",
                ]
                if device and device != "default":
                    cmd.extend(["--target", device])
                cmd.append(str(output_wav))
            elif backend == "alsa":
                cmd = ["arecord", "-q", "-f", "S16_LE", "-r", "16000", "-c", "1"]
                if device and device != "default":
                    cmd.extend(["-D", device])
                cmd.append(str(output_wav))
            else:
                raise RuntimeError("No audio capture backend (pw-record or arecord) found.")

            self._output_wav = output_wav
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )

    def stop_recording(self) -> Path:
        with self._lock:
            proc = self._proc
            wav = self._output_wav
            self._proc = None
            self._output_wav = None
        if proc is None or wav is None:
            raise RuntimeError("No active recording.")
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.terminate()
            proc.wait(timeout=2)
        stderr_text = ""
        if proc.stderr is not None:
            try:
                stderr_text = proc.stderr.read().decode("utf-8", errors="replace").strip()
                proc.stderr.close()
            except OSError:
                pass
        allowed_codes = {0, -signal.SIGINT, 130, -signal.SIGTERM, 143}
        if proc.returncode not in allowed_codes and (not wav.exists() or wav.stat().st_size <= 44):
            raise RuntimeError(
                f"Audio capture failed (exit={proc.returncode}): {stderr_text or 'no details'}"
            )
        stats = measure_wav_stats(wav)
        if stats.peak == 0:
            raise RuntimeError(
                "Microphone captured digital silence (peak=0). Check PipeWire input or mute state."
            )
        return wav

    def cancel_recording(self) -> None:
        with self._lock:
            proc = self._proc
            wav = self._output_wav
            self._proc = None
            self._output_wav = None
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
            if proc.stderr is not None:
                try:
                    proc.stderr.close()
                except OSError:
                    pass
        if wav is not None:
            wav.unlink(missing_ok=True)


class PipeWireAudioOutput:
    """WAV playback using pw-play (PipeWire) with aplay (ALSA) fallback."""

    def __init__(self) -> None:
        self._proc: subprocess.Popen[bytes] | None = None
        self._lock = threading.Lock()

    def is_playing(self) -> bool:
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    def play_wav(self, wav_path: Path) -> None:
        player = shutil.which("pw-play") or shutil.which("aplay")
        if not player:
            raise RuntimeError("No audio playback backend (pw-play or aplay) found.")
        cmd = [player, str(wav_path)] if "pw-play" in player else [player, "-q", str(wav_path)]
        with self._lock:
            self._proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            proc = self._proc
        try:
            proc.wait()
        finally:
            with self._lock:
                if self._proc is proc:
                    self._proc = None

    def stop_playback(self) -> None:
        with self._lock:
            proc = self._proc
            self._proc = None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
