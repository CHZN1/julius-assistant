"""Which images the art tools may open, and how they are opened.

Allowed: the first image dropped on the orb, or a file inside an approved folder (`[art] folders`). Refused: anything outside those folders, a folder named like an asset pack, a folder that ships a licence or EULA file (purchased and licensed packs do), the protected music locations, and anything that is not a sane raster image."""

from __future__ import annotations

import io
import os
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from julius import config
from julius.music import policy

IMAGE_EXT = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"})
DEFAULT_FOLDERS = ["~/Julius Art", "~/Pictures", "~/Desktop", "~/Downloads"]
MAX_BYTES = 25_000_000
MAX_PIXELS = 40_000_000
MAX_SIDE = 12_000
MAX_DEPTH = 4
MAX_VISITED = 20_000

# Folder names (whole words) that mark a purchased, licensed or store-bought asset pack. Plain "pack" is not here: the user's own packs are fine.
_PACK_NAME = re.compile(
    r"(?<![a-z])(?:asset|assets|asset[ _-]?packs?|purchased|bought|marketplace|unity|unreal|humble|itch|kenney|envato|creative[ _-]?market|"
    r"graphic[ _-]?river|stock|licensed|licen[cs]e|bundle|game[ _-]?dev[ _-]?market)(?![a-z])",
    re.I,
)
# Photo libraries are never read in place (their originals, edits and metadata are managed by Photos): export or drop the picture instead.
_PHOTO_LIBRARY = re.compile(
    r"\.(?:photoslibrary|photolibrary|migratedphotolibrary|aplibrary)$", re.I
)
_LICENCE_FILE = re.compile(
    r"^(?:licen[cs]e|eula|terms|read[ _-]?me[ _-]?licen[cs]e|copying)(?:[ ._-].*)?$", re.I
)


class ArtProblem(Exception):
    """An image that cannot or must not be used; the message is what Julius says."""


_drop_source: Callable[[], Any] | None = None


def set_drop_source(fn: Callable[[], Any] | None) -> None:
    """The service registers where dropped files are staged (a callable returning the pending drop, or None)."""
    global _drop_source
    _drop_source = fn


def _in_photo_library(p: Path) -> bool:
    return any(_PHOTO_LIBRARY.search(part) for part in p.parts)


def folders() -> list[Path]:
    names = (config.load_config().get("art") or {}).get("folders") or DEFAULT_FOLDERS
    out = []
    for n in names:
        p = Path(os.path.expanduser(str(n)))
        if p.is_dir() and policy.denied(p) is None and not _in_photo_library(p.resolve()):
            out.append(p.resolve())
    return out


def _pack_reason(path: Path, root: Path) -> str | None:
    """Why this file looks like part of a purchased or licensed pack (or None). Looks at the folder names below the approved root and at the files next to it."""
    try:
        rel_parts = path.relative_to(root).parts[:-1]
    except ValueError:
        rel_parts = ()
    for part in rel_parts:
        if _PACK_NAME.search(part):
            return f"the folder “{part}” looks like an asset pack"
    if (
        path.parent == root
    ):  # loose files in an approved folder (Downloads, Desktop): a stray licence file there says nothing about this picture
        return None
    try:
        for sib in path.parent.iterdir():
            if sib.is_file() and _LICENCE_FILE.match(sib.stem if sib.suffix else sib.name):
                return (
                    "its folder has a licence file, so it looks like a purchased or licensed pack"
                )
    except OSError:
        pass
    return None


def check_path(path: Path) -> Path:
    """The file to open, after every rule; raises ArtProblem with a sentence for the user."""
    p = path.expanduser()
    try:
        real = p.resolve(strict=True)
    except OSError:
        raise ArtProblem(f"I couldn't find {p.name}.") from None
    if _in_photo_library(p) or _in_photo_library(real):
        raise ArtProblem(
            f"I won't read {real.name} from inside a Photos library. Export it from Photos, or drop it on the orb."
        )
    why = policy.denied(real)
    if why:
        raise ArtProblem(f"I won't open {real.name}: {why}.")
    root = next((r for r in folders() if real == r or r in real.parents), None)
    if root is None:
        raise ArtProblem(
            f"{real.name} isn't in a folder I'm approved to use for art. Drop it on the orb, or put it in one of: "
            + ", ".join(str(r).replace(os.path.expanduser("~"), "~") for r in folders())
            + "."
        )
    reason = _pack_reason(real, root)
    if reason:
        raise ArtProblem(
            f"I won't use {real.name}: {reason}. I don't touch purchased asset packs. If the image is yours, drop it on the orb."
        )
    if real.suffix.casefold() not in IMAGE_EXT:
        raise ArtProblem(
            f"{real.name} isn't an image type I open (png, jpg, webp, gif, bmp, tiff)."
        )
    if not real.is_file():
        raise ArtProblem(f"I couldn't find {real.name}.")
    return real


# A photo taken with the camera is held here, in memory only, and works like a dropped image. It is replaced by the next photo, kept for the session
# (12 hours at most), and passed over when a different file has been dropped since (the newer of the two is "this").
HELD_TTL_S = 12 * 3600
_held: dict[str, Any] | None = None


# The current image of the art session: the picture the last art request used (the camera photo, or a file). "This" falls back to it after a drop
# has expired, so "make me an NES sprite" after a few edits still uses the same photo. Replaced by the next picture; cleared by forget_current().
_current: dict[str, Any] | None = None


def hold(jpeg: bytes, clock: Callable[[], float] = time.time) -> None:
    global _held, _current
    _held = {"jpeg": jpeg, "t": clock(), "drop": _dropped()}
    _current = {"kind": "camera", "label": CAMERA_NAME, "at": clock()}


def current() -> dict[str, Any] | None:
    """What the current image is (for the brain's context line and the sprite file): kind, label, time, and the path for a file. Never the pixels."""
    return dict(_current) if _current else None


def set_current_path(path: Path, clock: Callable[[], float] = time.time) -> None:
    """A reopened sprite's source file becomes the current image when there is none."""
    global _current
    if _current is None and Path(path).is_file():
        _current = {"kind": "file", "label": Path(path).stem, "path": str(path), "at": clock()}


def forget_current() -> None:
    global _current
    _current = None


def held(clock: Callable[[], float] = time.time) -> bytes | None:
    """The camera photo, when there is one that is still fresh and no other file was dropped after it."""
    global _held
    if _held is None:
        return None
    if clock() - _held["t"] > HELD_TTL_S:
        _held = None
        return None
    if _dropped() != _held["drop"]:
        return None  # something else was dropped since: that is the newer picture
    return _held["jpeg"]


def forget_held() -> None:
    global _held
    _held = None


def _dropped() -> Path | None:
    pending = _drop_source() if _drop_source else None
    for item in (pending or {}).get("items", []):
        if item.get("kind") == "file" and Path(item["path"]).suffix.casefold() in IMAGE_EXT:
            return Path(item["path"])
    return None


def find(name: str, roots: list[Path] | None = None) -> list[Path]:
    """Images whose name (or name without extension) matches, under the approved folders, not entering hidden, protected or pack-like folders."""
    want = name.strip().casefold()
    hits: list[Path] = []
    visited = 0
    for root in roots if roots is not None else folders():
        base = len(root.parts)
        for dirpath, dirs, files in os.walk(root):
            dirs[:] = [
                d
                for d in dirs
                if not d.startswith(".")
                and not _PACK_NAME.search(d)
                and not _PHOTO_LIBRARY.search(d)
                and policy.denied(Path(dirpath) / d) is None
                and len(Path(dirpath).parts) - base < MAX_DEPTH
            ]
            for f in files:
                visited += 1
                p = Path(dirpath) / f
                if p.suffix.casefold() in IMAGE_EXT and want in (f.casefold(), p.stem.casefold()):
                    hits.append(p)
            if visited > MAX_VISITED:
                return hits
    return hits


_THIS = (
    "dropped",
    "this",
    "this image",
    "this picture",
    "this photo",
    "the image",
    "that image",
    "dropped image",
    "dropped file",
    "my selfie",
    "the selfie",
    "this selfie",
    "the photo",
    "that photo",
    "the picture",
    "that picture",
    "camera photo",
    "my photo",
)


def resolve(source: str) -> tuple[Path, bool]:
    """(path, dropped): the image the user means. A dropped image is the user's own choice and skips the folder rules (it is still only read)."""
    raw = (source or "").strip().strip("\"'")
    if not raw or raw.casefold() in _THIS:
        found = _dropped()
        if found is None:
            raise ArtProblem("Which picture? Name it, or drop it on the orb first.")
        return found, True
    if raw.startswith(("/", "~")):
        return check_path(Path(raw)), False
    hits = find(raw)
    if not hits:
        raise ArtProblem(
            f"I couldn't find an image called {raw} in my approved art folders. Give me the full path or drop it on the orb."
        )
    if len(hits) > 1:
        shown = "; ".join(str(h.parent).replace(os.path.expanduser("~"), "~") for h in hits[:4])
        raise ArtProblem(
            f"AMBIGUOUS: {len(hits)} images are called {raw} ({shown}). Ask which folder."
        )
    return check_path(hits[0]), False


CAMERA_NAME = "camera photo"


def load(source: str) -> tuple[Any, str, str]:
    """(RGBA picture, label, file name to say): the camera photo held in memory when the user means "this", else the file `resolve` finds."""
    global _current
    raw = (source or "").strip().strip("\"'")
    if raw.casefold() in _THIS or not raw:
        jpeg = held()
        if jpeg is not None:
            return decode(jpeg, CAMERA_NAME), CAMERA_NAME, CAMERA_NAME
        if (
            _dropped() is None
            and _current
            and _current.get("kind") == "file"
            and Path(_current["path"]).is_file()
        ):
            p = Path(
                _current["path"]
            )  # the session's current picture: the drop has expired, the image has not changed
            return open_image(p), p.stem, p.name
    path, _dropped_flag = resolve(source)
    img = open_image(path)
    _current = {"kind": "file", "label": path.stem, "path": str(path), "at": time.time()}
    return img, path.stem, path.name


def decode(raw: bytes, name: str = "image") -> Any:
    from PIL import Image, ImageOps, UnidentifiedImageError

    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        with Image.open(io.BytesIO(raw)) as im:
            im.load()
            return ImageOps.exif_transpose(im.copy()).convert("RGBA")
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        raise ArtProblem(f"{name} isn't a readable image.") from None


def open_image(path: Path) -> Any:
    """The picture as an RGBA Pillow image in memory: orientation applied, animations reduced to their first frame, nothing written anywhere."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        if path.stat().st_size > MAX_BYTES:
            raise ArtProblem(f"{path.name} is too large to open (over 25 MB).")
        raw = path.read_bytes()
    except OSError:
        raise ArtProblem(f"I couldn't open {path.name}.") from None
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        with Image.open(io.BytesIO(raw)) as im:
            w, h = im.size
            if w < 1 or h < 1 or w > MAX_SIDE or h > MAX_SIDE or w * h > MAX_PIXELS:
                raise ArtProblem(f"{path.name} is too large to open.")
            im.seek(0)
            im.load()
            frame = ImageOps.exif_transpose(im.copy())
            return frame.convert("RGBA")
    except ArtProblem:
        raise
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        raise ArtProblem(f"{path.name} isn't a readable image.") from None
