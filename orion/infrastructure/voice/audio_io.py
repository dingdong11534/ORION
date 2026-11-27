"""PipeWire / ALSA subprocess audio capture and playback adapters."""

import shutil
import signal
import subprocess
import threading
from pathlib import Path


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
