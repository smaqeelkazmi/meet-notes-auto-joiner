"""Silent audio and a black frame for Chromium's fake media devices.

The browser is launched with fake capture devices pointed at these files so
Meet cannot open the laptop microphone or camera. The files are generated
once next to the profile. They are not secrets.
"""

from __future__ import annotations

import struct
import wave
from pathlib import Path


def ensure_blank_media(directory: Path) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    audio = directory / "silence.wav"
    video = directory / "black.y4m"
    if not audio.is_file() or audio.stat().st_size == 0:
        _write_silence(audio)
    if not video.is_file() or video.stat().st_size == 0:
        video.write_bytes(_black_y4m())
    return audio, video


def _write_silence(path: Path) -> None:
    """0.2 seconds of 8 kHz 16-bit mono silence. Chromium loops the file."""
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\x00\x00" * 1600)


def _black_y4m() -> bytes:
    """One 16x16 black 4:2:0 frame. Chromium loops the file."""
    width, height = 16, 16
    header = f"YUV4MPEG2 W{width} H{height} F15:1 Ip A1:1 C420\n".encode("ascii")
    y_size = width * height
    c_size = (width // 2) * (height // 2)
    frame = b"FRAME\n" + bytes(y_size) + bytes([128]) * c_size + bytes([128]) * c_size
    if len(frame) != len(b"FRAME\n") + y_size + c_size + c_size:
        raise RuntimeError("blank video frame has the wrong length")
    return header + frame


def wav_is_silent(path: Path) -> bool:
    with wave.open(str(path), "rb") as handle:
        frames = handle.readframes(handle.getnframes())
    if not frames:
        return False
    samples = struct.unpack("<" + "h" * (len(frames) // 2), frames)
    return all(sample == 0 for sample in samples)
