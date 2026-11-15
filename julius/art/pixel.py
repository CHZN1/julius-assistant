"""Photo to pixel art: downscale, snap to a small palette, optional outline and transparent background, crisp nearest-neighbour export.

Plain image processing (Pillow and numpy): no model, nothing learned, the same input and settings always give the same pixels."""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any

MIN_SIZE, MAX_SIZE = 8, 256  # pixels on the longest side of the pixel art itself
MIN_COLORS, MAX_COLORS = 2, 64
SCALES = (1, 2, 4, 8)
MAX_EXPORT_SIDE = 4096

# Well-known small palettes (hex), by the names people use.
PALETTES: dict[str, list[str]] = {
    "gameboy": ["0F380F", "306230", "8BAC0F", "9BBC0F"],
    "pico8": [
        "000000",
        "1D2B53",
        "7E2553",
        "008751",
        "AB5236",
        "5F574F",
        "C2C3C7",
        "FFF1E8",
        "FF004D",
        "FFA300",
        "FFEC27",
        "00E436",
        "29ADFF",
        "83769C",
        "FF77A8",
        "FFCCAA",
    ],  # fmt: skip
    "c64": [
        "000000",
        "FFFFFF",
        "68372B",
        "70A4B2",
        "6F3D86",
        "588D43",
        "352879",
        "B8C76F",
        "6F4F25",
        "433900",
        "9A6759",
        "444444",
        "6C6C6C",
        "9AD284",
        "6C5EB5",
        "959595",
    ],  # fmt: skip
    "1bit": ["000000", "FFFFFF"],
}
ALIASES = {
    "game boy": "gameboy", "gb": "gameboy", "dmg": "gameboy",
    "pico-8": "pico8", "pico 8": "pico8", "pico": "pico8",
    "commodore": "c64", "commodore 64": "c64", "c 64": "c64",
    "1 bit": "1bit", "1-bit": "1bit", "one bit": "1bit", "black and white": "1bit", "monochrome": "1bit",
}  # fmt: skip


class PixelProblem(ValueError):
    """Settings that cannot be used; the message is what Julius says."""


def palette_name(text: str) -> str:
    t = (text or "").strip().casefold().replace("_", " ")
    if not t:
        return ""
    t = ALIASES.get(t, t)
    if t not in PALETTES:
        raise PixelProblem(
            f"I don't have a palette called {text}. I have: {', '.join(sorted(PALETTES))}."
        )
    return t


@dataclass
class Settings:
    size: int = 48  # longest side of the pixel art, in pixels
    colors: int = 16  # palette size when the palette comes from the picture
    palette: str = ""  # a fixed palette name, or "" for colours taken from the picture
    outline: bool = False
    transparent: bool = False  # make the background transparent (the colour that fills the border)
    scale: int = 4  # export zoom: 1, 2, 4 or 8 (nearest neighbour, so it stays crisp)

    def clean(self) -> Settings:
        def num(v: Any, lo: int, hi: int, default: int) -> int:
            try:
                n = int(round(float(v)))
            except (TypeError, ValueError):
                return default
            return max(lo, min(hi, n))

        self.size = num(self.size, MIN_SIZE, MAX_SIZE, 48)
        self.colors = num(self.colors, MIN_COLORS, MAX_COLORS, 16)
        self.palette = palette_name(self.palette)
        self.outline, self.transparent = bool(self.outline), bool(self.transparent)
        s = num(self.scale, 1, 8, 4)
        self.scale = min(SCALES, key=lambda c: abs(c - s))
        return self


@dataclass
class Result:
    small: Any  # PIL RGBA image at 1x
    width: int
    height: int
    palette: list[str] = field(default_factory=list)  # colours actually used, hex
    has_alpha: bool = False
    settings: Settings = field(default_factory=Settings)

    def scaled(self, scale: int | None = None) -> Any:
        """The export: nearest-neighbour upscale, so every art pixel is a crisp square. Never larger than MAX_EXPORT_SIDE."""
        from PIL import Image

        k = int(scale or self.settings.scale)
        while k > 1 and max(self.width, self.height) * k > MAX_EXPORT_SIDE:
            k //= 2
        return self.small.resize((self.width * k, self.height * k), Image.NEAREST), k

    def png(self, scale: int | None = None) -> tuple[bytes, int]:
        """PNG bytes at the given zoom (RGBA only when something is transparent) and the zoom actually used."""
        img, k = self.scaled(scale)
        out = io.BytesIO()
        (img if self.has_alpha else img.convert("RGB")).save(out, "PNG", optimize=True)
        return out.getvalue(), k


def _hex(rgb: Any) -> str:
    return "{:02X}{:02X}{:02X}".format(*(int(v) for v in rgb[:3]))


def _rgb(h: str) -> tuple[int, int, int]:
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _fit(w: int, h: int, size: int) -> tuple[int, int]:
    k = size / max(w, h)
    return max(1, round(w * k)), max(1, round(h * k))


def _snap(arr: Any, palette: list[tuple[int, int, int]]) -> Any:
    """Each pixel to its nearest palette colour (distance weighted for how the eye sees red, green and blue)."""
    import numpy as np

    pal = np.array(palette, dtype=np.float32)
    px = arr.reshape(-1, 3).astype(np.float32)
    wgt = np.array([0.30, 0.59, 0.11], dtype=np.float32) * 3
    d = (((px[:, None, :] - pal[None, :, :]) ** 2) * wgt).sum(axis=2)
    return pal[np.argmin(d, axis=1)].reshape(arr.shape).astype(np.uint8)


def _median_cut(rgb_img: Any, n: int) -> list[tuple[int, int, int]]:
    from PIL import Image

    q = rgb_img.quantize(colors=n, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    pal = q.getpalette() or []
    used = sorted({i for i in q.getdata()})
    return [(pal[3 * i], pal[3 * i + 1], pal[3 * i + 2]) for i in used]


def _background(index: Any, alpha: Any) -> Any:
    """Boolean mask of the background: the region of the most common border colour, connected to the border."""
    import numpy as np

    h, w = index.shape
    border = np.concatenate([index[0, :], index[-1, :], index[:, 0], index[:, -1]])
    vals, counts = np.unique(border, return_counts=True)
    target = vals[int(np.argmax(counts))]
    same = (index == target) & (alpha > 0)
    seen = np.zeros((h, w), dtype=bool)
    stack = [
        (y, x)
        for y in range(h)
        for x in range(w)
        if (y in (0, h - 1) or x in (0, w - 1)) and same[y, x]
    ]
    for y, x in stack:
        seen[y, x] = True
    while stack:
        y, x = stack.pop()
        for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
            if 0 <= ny < h and 0 <= nx < w and same[ny, nx] and not seen[ny, nx]:
                seen[ny, nx] = True
                stack.append((ny, nx))
    return seen


def render(img: Any, settings: Settings) -> Result:
    """`img` is a PIL image (any mode). Returns the pixel art at 1x; call .png(scale) for the export."""
    import numpy as np
    from PIL import Image

    s = settings.clean()
    src = img.convert("RGBA")
    w, h = _fit(*src.size, s.size)
    # area averaging, on colours weighted by opacity so a transparent edge does not bleed black into the picture
    a_full = np.asarray(src.getchannel("A"), dtype=np.float32) / 255.0
    rgb_full = np.asarray(src.convert("RGB"), dtype=np.float32) * a_full[..., None]
    small_a = np.asarray(Image.fromarray(a_full, "F").resize((w, h), Image.BOX))
    chans = [
        np.asarray(Image.fromarray(rgb_full[..., c], "F").resize((w, h), Image.BOX))
        for c in range(3)
    ]
    safe = np.maximum(small_a, 1e-6)
    rgb = np.clip(np.stack([c / safe for c in chans], axis=-1), 0, 255).astype(np.uint8)
    alpha = (small_a >= 0.5).astype(np.uint8) * 255  # pixel art has no half-transparent pixels
    opaque = alpha > 0
    if not opaque.any():
        raise PixelProblem("That picture is fully transparent.")

    if s.palette:
        pal = [_rgb(c) for c in PALETTES[s.palette]]
    else:
        fill = rgb.copy()
        fill[~opaque] = (
            rgb[opaque].mean(axis=0).astype(np.uint8)
        )  # transparent pixels must not pull the palette
        pal = _median_cut(Image.fromarray(fill, "RGB"), s.colors)
    snapped = _snap(rgb, pal)
    flat = snapped.reshape(-1, 3)
    lut = {c: i for i, c in enumerate(pal)}
    index = np.array([lut[tuple(p)] for p in flat], dtype=np.int32).reshape(h, w)

    if s.transparent:
        bg = _background(index, alpha)
        if bg.all():
            bg[:] = False  # the whole picture is one colour: nothing to cut out
        alpha = np.where(bg, 0, alpha).astype(np.uint8)
        opaque = alpha > 0

    out_rgb, out_a = snapped.copy(), alpha.copy()
    if s.outline:
        lum = 0.30 * pal_arr(pal)[:, 0] + 0.59 * pal_arr(pal)[:, 1] + 0.11 * pal_arr(pal)[:, 2]
        ink = (
            pal[int(np.argmin(lum))] if lum.min() < 90 else (0, 0, 0)
        )  # the palette's darkest colour, or black when none is dark
        if (
            ~opaque
        ).any():  # a silhouette outline, one pixel outside the shape (the canvas grows by one pixel each way)
            out_rgb = np.pad(out_rgb, ((1, 1), (1, 1), (0, 0)))
            out_a = np.pad(out_a, ((1, 1), (1, 1)))
            op = out_a > 0
            nb = np.zeros_like(op)
            nb[1:, :] |= op[:-1, :]
            nb[:-1, :] |= op[1:, :]
            nb[:, 1:] |= op[:, :-1]
            nb[:, :-1] |= op[:, 1:]
            edge = nb & ~op
            out_rgb[edge] = ink
            out_a[edge] = 255
        else:  # a solid picture: ink the brighter side of each strong edge
            lum_px = 0.30 * out_rgb[..., 0] + 0.59 * out_rgb[..., 1] + 0.11 * out_rgb[..., 2]
            mark = np.zeros((h, w), dtype=bool)
            for dy, dx in ((0, 1), (1, 0)):
                a = lum_px[: h - dy, : w - dx]
                b = lum_px[dy:, dx:]
                bright_a = (a - b) > 60
                bright_b = (b - a) > 60
                mark[: h - dy, : w - dx] |= bright_a
                mark[dy:, dx:] |= bright_b
            out_rgb[mark] = ink
    rgba = np.dstack([out_rgb, out_a]).astype(np.uint8)
    small = Image.fromarray(rgba, "RGBA")
    used = rgba[rgba[..., 3] > 0][:, :3]
    uniq = sorted({_hex(c) for c in used})
    return Result(
        small=small,
        width=rgba.shape[1],
        height=rgba.shape[0],
        palette=uniq,
        has_alpha=bool((rgba[..., 3] == 0).any()),
        settings=s,
    )


def pal_arr(pal: list[tuple[int, int, int]]) -> Any:
    import numpy as np

    return np.array(pal, dtype=np.float32)
