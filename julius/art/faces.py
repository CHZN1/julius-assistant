"""Find the face in a picture and the head-and-shoulders crop around it, entirely on this Mac.

Apple's Vision framework does the detecting inside the one-shot helper (visiond/visiond.m, `faces`): the picture goes to the helper on stdin and only boxes
come back. Nothing is uploaded and nothing is written to disk. When there is no face, no helper or no answer, the caller uses the whole picture."""

from __future__ import annotations

import io
import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

SEND_SIDE = 1024  # the helper gets a copy no bigger than this; boxes come back as fractions, so the size does not matter
MIN_FACE = 0.06  # a face narrower than 6% of the picture is a face in a crowd, not a portrait
CROP_W, CROP_UP, CROP_DOWN = (
    3.0,
    0.8,
    1.2,
)  # crop width, and room above and below the face box, in face widths/heights


def detect(
    img: Any, runner: Callable = subprocess.run, binary: Path | None = None
) -> list[tuple[float, float, float, float]]:
    """Faces as (x, y, w, h) fractions of the picture, origin top-left, the largest first. [] when none or when the helper cannot run."""
    try:
        from julius.agents import vision

        im = img.convert("RGB")
        im.thumbnail((SEND_SIDE, SEND_SIDE))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=90)
        r = runner(
            [str(binary or vision.build()), "faces"],
            input=buf.getvalue(),
            capture_output=True,
            timeout=30,
        )
        line = (r.stdout or b"").decode("utf-8", "replace").strip().splitlines()[-1:] or [""]
        data = json.loads(line[0])
        out = [tuple(float(v) for v in f) for f in data.get("faces", []) if len(f) == 4]
    except Exception:
        return []
    return [f for f in out if f[2] >= MIN_FACE]  # type: ignore[misc]


def head_and_shoulders(
    size: tuple[int, int], face: tuple[float, float, float, float]
) -> tuple[int, int, int, int]:
    """The crop box (left, top, right, bottom) in pixels: the face with room for hair, neck and shoulders, kept inside the picture."""
    w, h = size
    fx, fy, fw, fh = face[0] * w, face[1] * h, face[2] * w, face[3] * h
    cw = fw * CROP_W
    top = fy - fh * CROP_UP
    bottom = fy + fh + fh * CROP_DOWN
    ch = bottom - top
    left = fx + fw / 2 - cw / 2
    cw, ch = min(cw, w), min(ch, h)
    left = max(0.0, min(left, w - cw))
    top = max(0.0, min(top, h - ch))
    return int(round(left)), int(round(top)), int(round(left + cw)), int(round(top + ch))


def portrait_crop(
    img: Any, runner: Callable = subprocess.run, binary: Path | None = None
) -> tuple[Any, bool, tuple[float, float, float, float] | None]:
    """(the picture cropped to the main face's head and shoulders, True, the face box as fractions of the crop) or (the picture unchanged, False, None)
    when no face is found."""
    found = detect(img, runner, binary)
    if not found:
        return img, False, None
    box = head_and_shoulders(img.size, found[0])
    cw, ch = box[2] - box[0], box[3] - box[1]
    if cw < 16 or ch < 16:
        return img, False, None
    fx, fy, fw, fh = found[0]
    w, h = img.size
    focus = (
        max(0.0, (fx * w - box[0]) / cw),
        max(0.0, (fy * h - box[1]) / ch),
        min(1.0, ((fx + fw) * w - box[0]) / cw),
        min(1.0, ((fy + fh) * h - box[1]) / ch),
    )
    return img.crop(box), True, focus
