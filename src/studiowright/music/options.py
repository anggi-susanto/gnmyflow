"""What Flow Music's panels accept (measured on flowmusic.app, 2026-10-10/11)."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
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
    MP3 = "mp3"


class EditMode(Enum):
    """The compose panel's edit modes for an existing song."""

    EXTEND = "extend"
    COVER = "cover"
    REPLACE = "replace"

    @property
    def label(self) -> str:
        return self.value.capitalize()


class VideoTemplate(Enum):
    """Download → Download video templates that render the song with its cover art."""

    SIMPLE = "simple"
    MODERN = "modern"
    PLAYER = "player"

    @property
    def label(self) -> str:
        return self.value.capitalize()


#: The panel rounds anything shorter up to one minute.
MIN_LENGTH_S = 60
MAX_LENGTH_S = 180
_LENGTH = re.compile(r"^\s*(?:(\d{1,2}):)?(\d{1,3})\s*$")
_TIME = re.compile(r"^\s*(?:(\d{1,2}):)?(\d{1,3}(?:\.\d+)?)\s*$")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


DEFAULT_UPLOAD_TIMEOUT_S = 1800


def validate_upload_timeout(timeout_s: float) -> None:
    """Upload waits must be finite and strictly positive (seconds)."""
    if not math.isfinite(timeout_s) or timeout_s <= 0:
        raise OptionError("upload timeout must be a finite, positive number of seconds")


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


def parse_time(value: str | float | None) -> float | None:
    """A point in a song, from "1:05", "1:05.5", "65" or 65."""
    if value is None or value == "":
        return None
    if isinstance(value, int | float):
        seconds = float(value)
    else:
        match = _TIME.match(str(value))
        if not match:
            raise OptionError(f"time must look like 1:05 or 65, got {value!r}")
        minutes, rest = match.groups()
        seconds = int(minutes or 0) * 60 + float(rest)
    if seconds < 0:
        raise OptionError(f"time must not be negative, got {value!r}")
    return seconds


def format_time(seconds: float) -> str:
    """The panel's own m:ss form, with tenths only when needed."""
    minutes, rest = divmod(round(seconds, 1), 60)
    whole = int(rest)
    tenths = round((rest - whole) * 10)
    return f"{int(minutes)}:{whole:02d}" + (f".{tenths}" if tenths else "")


def song_id(value: str) -> str:
    """A clip id from an id, an id prefix, or a flowmusic.app song URL (prefixes stay as-is)."""
    text = value.strip()
    match = _UUID.search(text.lower())
    if match:
        return match.group(0)
    if not re.fullmatch(r"[0-9a-fA-F-]{4,36}|last", text):
        raise OptionError(f"not a Flow Music song id or URL: {value!r}")
    return text.lower()


def _member[E: Enum](kind: type[E], value: str) -> E:
    text = str(value).strip().lower()
    for member in kind:
        if text in (str(member.value), member.name.lower()):
            return member
    choices = ", ".join(str(m.value) for m in kind)
    raise OptionError(f"unknown {kind.__name__} {value!r}; choose one of: {choices}")


def _format(value: AudioFormat | str) -> AudioFormat:
    return value if isinstance(value, AudioFormat) else _member(AudioFormat, value)


@dataclass(frozen=True)
class Cover:
    """Cover art for a new song: a local image, or a prompt for Flow Music to draw one."""

    image: Path | None = None
    prompt: str | None = None

    @classmethod
    def build(cls, image: str | Path | None, prompt: str | None) -> Cover | None:
        if image and prompt:
            raise OptionError("give either a cover image or a cover prompt, not both")
        if image:
            path = Path(image).expanduser()
            if not path.is_file():
                raise FileNotFoundError(f"cover image not found: {path}")
            return cls(image=path)
        if prompt and prompt.strip():
            return cls(prompt=prompt.strip())
        return None


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
    cover: Cover | None = None
    timeout_s: int | None = None
    single_submit: bool = False

    def __post_init__(self) -> None:
        if self.single_submit and self.cover and self.cover.prompt:
            raise OptionError("single-submit does not allow a cover prompt (extra paid generation)")

    @property
    def press_again(self) -> bool:
        return not self.single_submit

    @property
    def heuristic_recovery(self) -> bool:
        return not self.single_submit

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
        cover_image: str | Path | None = None,
        cover_prompt: str | None = None,
        timeout_s: int | None = None,
        single_submit: bool = False,
    ) -> MusicRequest:
        if not sound or not sound.strip():
            raise OptionError("describe the sound (style, instruments, mood)")
        if bpm is not None and not 1 <= bpm <= 999:
            raise OptionError(f"bpm must be between 1 and 999, got {bpm}")
        model_ = model if isinstance(model, MusicModel) else _member(MusicModel, model)
        return cls(
            sound.strip(),
            lyrics,
            bpm,
            parse_length(length),
            seed,
            model_,
            title,
            _format(audio_format),
            out_dir,
            Cover.build(cover_image, cover_prompt),
            timeout_s=timeout_s,
            single_submit=single_submit,
        )


@dataclass(frozen=True)
class EditRequest:
    """Extend, cover or replace part of an existing song (5 credits, like a new song)."""

    song: str
    mode: EditMode
    prompt: str | None = None
    lyrics: str | None = None
    start_s: float | None = None
    end_s: float | None = None
    seed: int | None = None
    title: str | None = None
    audio_format: AudioFormat = AudioFormat.WAV
    out_dir: Path | None = None
    cover: Cover | None = None
    timeout_s: int | None = None

    @classmethod
    def build(
        cls,
        song: str,
        mode: EditMode | str,
        *,
        prompt: str | None = None,
        lyrics: str | None = None,
        start: str | float | None = None,
        end: str | float | None = None,
        seed: int | None = None,
        title: str | None = None,
        audio_format: AudioFormat | str = AudioFormat.WAV,
        out_dir: Path | None = None,
        cover_image: str | Path | None = None,
        cover_prompt: str | None = None,
        timeout_s: int | None = None,
    ) -> EditRequest:
        mode_ = mode if isinstance(mode, EditMode) else _member(EditMode, mode)
        start_s, end_s = parse_time(start), parse_time(end)
        if start_s is not None and end_s is not None and end_s <= start_s:
            raise OptionError("end must be after start")
        if mode_ is EditMode.COVER and (start_s is not None or end_s is not None):
            raise OptionError("a cover covers the whole song; start/end do not apply")
        if mode_ is not EditMode.EXTEND and not ((prompt and prompt.strip()) or lyrics):
            raise OptionError(f"{mode_.value} needs a prompt (what to change) or lyrics")
        return cls(
            song_id(song),
            mode_,
            prompt.strip() if prompt else None,
            lyrics,
            start_s,
            end_s,
            seed,
            title,
            _format(audio_format),
            out_dir,
            Cover.build(cover_image, cover_prompt),
            timeout_s=timeout_s,
        )


@dataclass(frozen=True)
class ChatRequest:
    """One message to Flow Music's Producer agent, optionally with audio or image files."""

    message: str
    session: str | None = None
    audio: Path | None = None
    images: tuple[Path, ...] = field(default_factory=tuple)
    confirm_rights: bool = False
    audio_format: AudioFormat = AudioFormat.WAV
    out_dir: Path | None = None
    timeout_s: float = 900
    upload_timeout_s: float = DEFAULT_UPLOAD_TIMEOUT_S

    def __post_init__(self) -> None:
        validate_upload_timeout(self.upload_timeout_s)

    @classmethod
    def build(
        cls,
        message: str,
        *,
        session: str | None = None,
        audio: str | Path | None = None,
        images: tuple[str | Path, ...] | list[str | Path] = (),
        confirm_rights: bool = False,
        audio_format: AudioFormat | str = AudioFormat.WAV,
        out_dir: Path | None = None,
        timeout_s: float = 900,
        upload_timeout_s: float = DEFAULT_UPLOAD_TIMEOUT_S,
    ) -> ChatRequest:
        if not message or not message.strip():
            raise OptionError("the message is empty")
        audio_path = Path(audio).expanduser() if audio else None
        if audio_path and not audio_path.is_file():
            raise FileNotFoundError(f"audio file not found: {audio_path}")
        image_paths = tuple(Path(i).expanduser() for i in images)
        for image in image_paths:
            if not image.is_file():
                raise FileNotFoundError(f"image not found: {image}")
        if (audio_path or image_paths) and not confirm_rights:
            raise OptionError(
                "Flow Music asks you to confirm you have the rights to uploaded files; "
                "pass confirm_rights / --confirm-rights if you do"
            )
        return cls(
            message.strip(),
            song_id(session) if session else None,
            audio_path,
            image_paths,
            confirm_rights,
            _format(audio_format),
            out_dir,
            timeout_s,
            upload_timeout_s,
        )


MUSIC_ACTIONS = ("create", "extend", "cover", "replace", "variation", "trim", "stems", "video")


@dataclass(frozen=True)
class MusicJob:
    """One music step of a batch: a new song, or an action on an existing one."""

    action: str
    request: MusicRequest | EditRequest | None = None
    song: str | None = None
    start: str | None = None
    end: str | None = None
    template: VideoTemplate = VideoTemplate.SIMPLE
    audio_format: AudioFormat = AudioFormat.WAV
    out_dir: Path | None = None
