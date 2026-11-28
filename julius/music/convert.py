"""Batch audio conversion (wav / aiff / mp3, sample rate, bit depth): always NEW copies, never touching the originals.

The conversion itself runs in a short subprocess (ffmpeg when installed, otherwise macOS afconvert, plus lame for mp3). It is planned, previewed, confirmed and undone by the file agent (`agents/file_plans.py`, `file_exec.py`), so a convert is a "convert" plan like a copy: the output goes to a subfolder of the Julius Audio Workspace, a file that already exists is never overwritten, and undo removes only the exact copies it made. Nothing here writes outside the workspace or onto an external drive."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from julius import config, platforms
from julius.music import policy

FORMATS = {"wav": ".wav", "aiff": ".aiff", "mp3": ".mp3"}
RATES = (22050, 32000, 44100, 48000, 88200, 96000, 176400, 192000)
DEPTHS = (16, 24, 32)
SOURCE_EXT = policy.AUDIO_EXT
DEFAULT_DEPTH = 24
WORKSPACE_DEFAULT = "~/Julius Audio Workspace"
TIMEOUT = 600


class ConvertError(Exception):
    pass


@dataclass(frozen=True)
class Spec:
    fmt: str
    rate: int | None = None
    depth: int | None = None

    def as_dict(self) -> dict:
        return {"fmt": self.fmt, "rate": self.rate, "depth": self.depth}

    @property
    def ext(self) -> str:
        return FORMATS[self.fmt]

    @property
    def folder(self) -> str:
        bits = [self.fmt]
        if self.rate:
            bits.append(f"{self.rate / 1000:g}k")
        if self.depth and self.fmt != "mp3":
            bits.append(f"{self.depth}bit")
        return "_".join(bits)


def workspace() -> Path:
    cfg = config.load_config().get("audio") or {}
    return Path(os.path.expanduser(str(cfg.get("workspace") or WORKSPACE_DEFAULT))).resolve()


def in_workspace(p: Path) -> bool:
    try:
        r, w = Path(p).resolve(), workspace()
    except OSError:
        return False
    return r == w or w in r.parents


def refuse_destination(dst: Path) -> str | None:
    """Why an output folder is not allowed: only the workspace (and never a drive under /Volumes)."""
    d = Path(os.path.abspath(dst))
    if str(d).startswith("/Volumes/") or str(Path(os.path.realpath(dst))).startswith("/Volumes/"):
        return "I never write to an external drive."
    if not in_workspace(d):
        return f"Converted copies go only into {workspace().name}."
    if policy.denied(d) is not None:
        return "That is a protected music location."
    return None


def backend() -> str | None:
    if shutil.which("ffmpeg"):
        return "ffmpeg"
    if platforms.current().audio.available():
        return platforms.current().audio.name
    return None


def make_spec(fmt: str, rate: int | None, depth: int | None) -> Spec:
    f = {"wave": "wav", "aif": "aiff"}.get(fmt.casefold(), fmt.casefold())
    if f not in FORMATS:
        raise ConvertError(f"I can convert to wav, aiff or mp3, not {fmt}.")
    if rate is not None and rate not in RATES:
        raise ConvertError(f"I can use these sample rates: {', '.join(str(r) for r in RATES)}.")
    if depth is not None and depth not in DEPTHS:
        raise ConvertError("Bit depth can be 16, 24 or 32.")
    if f != "mp3" and depth is None:
        depth = DEFAULT_DEPTH
    return Spec(f, rate, None if f == "mp3" else depth)


def command(src: Path, dst: str, spec: Spec, which: str) -> list[str]:
    """The argument list (never a shell string) that writes `dst`; a destination that exists is an error, never overwritten."""
    if which == "ffmpeg":
        cmd = [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-n",
            "-i",
            str(src),
            "-vn",
        ]
        if spec.rate:
            cmd += ["-ar", str(spec.rate)]
        if spec.fmt == "mp3":
            cmd += ["-c:a", "libmp3lame", "-b:a", "320k", "-f", "mp3"]
        else:
            order = "le" if spec.fmt == "wav" else "be"
            cmd += [
                "-c:a",
                f"pcm_s{spec.depth}{order}",
                "-f",
                spec.fmt if spec.fmt == "wav" else "aiff",
            ]
        return cmd + [dst]
    if spec.fmt == "mp3":
        raise ConvertError("mp3 needs ffmpeg (or lame) installed.")
    audio = platforms.current().audio
    return audio.pcm(audio.name, str(src), dst, spec.fmt, spec.depth, spec.rate)


def run(src: Path, dst: str, spec_dict: dict, runner=subprocess.run) -> None:
    """Write the converted copy to `dst` (a temporary file the file agent then publishes). Raises ConvertError."""
    spec = make_spec(spec_dict["fmt"], spec_dict.get("rate"), spec_dict.get("depth"))
    which = backend()
    if which is None:
        raise ConvertError("I have no audio converter: install ffmpeg, or use macOS afconvert.")
    if os.path.exists(dst) and os.path.getsize(dst) > 0:
        raise ConvertError("The output already exists; nothing is overwritten.")
    try:
        if which == platforms.current().audio.name and spec.fmt == "mp3":
            lame = shutil.which("lame")
            if not lame:
                raise ConvertError("mp3 needs ffmpeg or lame installed.")
            with tempfile.TemporaryDirectory() as td:
                mid = str(Path(td) / "mid.wav")
                r = runner(
                    command(src, mid, make_spec("wav", spec.rate, 24), which),
                    capture_output=True,
                    timeout=TIMEOUT,
                )
                if r.returncode != 0:
                    raise ConvertError("The converter could not read that file.")
                r = runner(
                    [lame, "--silent", "-b", "320", mid, dst], capture_output=True, timeout=TIMEOUT
                )
        else:
            r = runner(command(src, dst, spec, which), capture_output=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        raise ConvertError("The conversion took too long and was stopped.") from exc
    if r.returncode != 0 or not os.path.exists(dst) or os.path.getsize(dst) == 0:
        raise ConvertError("The converter could not make that copy.")


# --- what the user says --------------------------------------------------------------------------------------
_FMT = r"(wav|wave|aiff?|mp3)"
CONVERT = re.compile(
    r"^\s*(?:(?:please|hey julius|julius|okay|ok)[ ,]+)*(?:can you |could you )?(?:please )?"
    r"(?:batch[- ]convert|convert|resample|export)\s+(?P<what>.+?)\s+(?:in)?to\s+(?P<rest>.*?)\s*[.!?]*\s*$",
    re.I,
)
_RATE = re.compile(
    r"(?<![\d.])(\d{2,3}(?:\.\d)?)\s*(?:k|khz|kilohertz)\b|(?<!\d)(22050|32000|44100|48000|88200|96000|176400|192000)(?!\d)",
    re.I,
)
_DEPTH = re.compile(r"(?<!\d)(16|24|32)[ -]?bit", re.I)


def parse(text: str) -> dict | None:
    """ "convert kick.wav to mp3", "convert all the wav files in my workspace to aiff 48k 24 bit" -> {what, spec, error}. None when it is not a convert request."""
    m = CONVERT.match(text or "")
    if not m:
        return None
    rest = m.group("rest")
    fm = re.search(rf"\b{_FMT}\b", rest, re.I)
    if not fm:
        return None
    what = re.sub(r"\s+", " ", m.group("what")).strip()
    rate = None
    rm = _RATE.search(rest)
    if rm:
        rate = int(round(float(rm.group(1)) * 1000)) if rm.group(1) else int(rm.group(2))
    dm = _DEPTH.search(rest)
    depth = int(dm.group(1)) if dm else None
    try:
        spec = make_spec(fm.group(1), rate, depth)
        error = None
    except ConvertError as exc:
        spec, error = None, str(exc)
    return {"what": what, "spec": spec, "error": error}


_WORKSPACE_WORDS = re.compile(
    r"\b(?:in|from|inside)\s+(?:the |my )?(?:julius )?(?:audio )?workspace\b", re.I
)


def selection(what: str) -> tuple[str, str]:
    """-> (kind, pattern): ("all", "*.wav") for "all the wav files in the workspace", ("named", "kick.wav") for one file."""
    w = _WORKSPACE_WORDS.sub("", what).strip()
    am = re.match(
        rf"(?:all|every|each)\s+(?:of\s+)?(?:the\s+|my\s+)?(?:{_FMT}\s+)?(?:files?|audio|samples?|loops?|sounds?)?\s*$",
        w,
        re.I,
    )
    if am:
        f = am.group(1)
        if f:
            ext = {"wave": "wav", "aif": "aiff"}.get(f.casefold(), f.casefold())
            return "all", "*.aif*" if ext == "aiff" else f"*.{ext}"
        return "all", "*"
    return "named", w.strip("\"'")
