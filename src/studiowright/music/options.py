"""What the Flow Music compose panel accepts (measured on flowmusic.app, 2026-10-10)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from studiowright.flow.options import OptionError


class MusicModel(Enum):
    LYRIA_3_5 = "lyria-3.5"
    LYRIA_3_PRO = "lyria-3-pro"

    @property
    def label(self) -> str:
        return {MusicModel.LYRIA_3_5: "Lyria 3.5", MusicModel.LYRIA_3_PRO: "Lyria 3 Pro"}[self]


class AudioFormat(Enum):
    WAV = "wav"
    M4A = "m4a"


#: The panel rounds anything shorter up to one minute.
MIN_LENGTH_S = 60
MAX_LENGTH_S = 180
_LENGTH = re.compile(r"^\s*(?:(\d{1,2}):)?(\d{1,3})\s*$")


def parse_length(value: str | int | None) -> int | None:
    """Seconds from "1:30", "90" or 90; None means Flow decides."""
    if value is None or value == "":
        return None
    if isinstance(value, int):
        seconds = value
    else:
        match = _LENGTH.match(str(value))
        if not match:
            raise OptionError(f"length must look like 1:30 or 90, got {value!r}")
        minutes, rest = match.groups()
        seconds = int(minutes or 0) * 60 + int(rest)
    if not MIN_LENGTH_S <= seconds <= MAX_LENGTH_S:
        raise OptionError(f"length must be between 1:00 and 3:00, got {seconds} s")
    return seconds


def format_length(seconds: int) -> str:
    return f"{seconds // 60}:{seconds % 60:02d}"


@dataclass(frozen=True)
class MusicRequest:
    sound: str
    lyrics: str | None = None
    bpm: int | None = None
    length_s: int | None = None
    seed: int | None = None
    model: MusicModel = MusicModel.LYRIA_3_5
    title: str | None = None
    audio_format: AudioFormat = AudioFormat.WAV
    out_dir: Path | None = None

    @property
    def instrumental(self) -> bool:
        return not (self.lyrics and self.lyrics.strip())

    @classmethod
    def build(
        cls,
        sound: str,
        *,
        lyrics: str | None = None,
        bpm: int | None = None,
        length: str | int | None = None,
        seed: int | None = None,
        model: MusicModel | str = MusicModel.LYRIA_3_5,
        title: str | None = None,
        audio_format: AudioFormat | str = AudioFormat.WAV,
        out_dir: Path | None = None,
    ) -> MusicRequest:
        if not sound or not sound.strip():
            raise OptionError("describe the sound (style, instruments, mood)")
        if bpm is not None and not 1 <= bpm <= 999:
            raise OptionError(f"bpm must be between 1 and 999, got {bpm}")
        model_ = model if isinstance(model, MusicModel) else _member(MusicModel, model)
        fmt = (
            audio_format
            if isinstance(audio_format, AudioFormat)
            else _member(AudioFormat, audio_format)
        )
        return cls(
            sound.strip(), lyrics, bpm, parse_length(length), seed, model_, title, fmt, out_dir
        )


def _member[E: Enum](kind: type[E], value: str) -> E:
    text = str(value).strip().lower()
    for member in kind:
        if text in (str(member.value), member.name.lower()):
            return member
    choices = ", ".join(str(m.value) for m in kind)
    raise OptionError(f"unknown {kind.__name__} {value!r}; choose one of: {choices}")
