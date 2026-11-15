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
    dither: bool = False  # ordered (Bayer) dithering
    retro: bool = False  # sprite look: speckle removed, flat tone areas, bold outline

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
        self.dither = bool(self.dither)
        self.retro = bool(self.retro)
        if self.retro:
            self.outline = True
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


BAYER4 = [[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]]
LOW_PALETTE = 8  # palettes of this many colours or fewer get auto-levels and a luminance mapping
STRONG_EDGE = (
    0.30  # an outline pixel needs a luminance step this big (0..1, after levels) to its neighbour
)


def _lum(rgb: Any) -> Any:
    return 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]


def _area(rgb_f: Any, a_f: Any, w: int, h: int) -> tuple[Any, Any]:
    """Area-average resize of colours weighted by opacity (so a transparent edge does not bleed black into the picture). Returns float rgb and alpha."""
    import numpy as np
    from PIL import Image

    small_a = np.asarray(Image.fromarray(a_f, "F").resize((w, h), Image.BOX))
    chans = [
        np.asarray(Image.fromarray(rgb_f[..., c] * a_f, "F").resize((w, h), Image.BOX))
        for c in range(3)
    ]
    safe = np.maximum(small_a, 1e-6)
    return np.stack([c / safe for c in chans], axis=-1), small_a


def _bilateral(rgb: Any, radius: int = 2, sigma_s: float = 1.6, sigma_r: float = 28.0) -> Any:
    """Edge-preserving smoothing: each pixel becomes the average of its neighbours, weighted down for neighbours that are far away or a very different colour,
    so skin and walls go smooth while the edges of eyes, hair and the jaw stay sharp."""
    import numpy as np

    h, w, _ = rgb.shape
    pad = np.pad(rgb, ((radius, radius), (radius, radius), (0, 0)), mode="edge")
    acc = np.zeros_like(rgb)
    wsum = np.zeros((h, w, 1), dtype=np.float32)
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            nb = pad[radius + dy : radius + dy + h, radius + dx : radius + dx + w]
            d2 = ((nb - rgb) ** 2).sum(axis=2, keepdims=True)
            wt = np.exp(-(dy * dy + dx * dx) / (2 * sigma_s**2) - d2 / (2 * sigma_r**2))
            acc += nb * wt
            wsum += wt
    return acc / wsum


def _levels(lum: Any, opaque: Any) -> tuple[float, float]:
    import numpy as np

    vals = lum[opaque]
    if vals.size < 4:
        return 0.0, 255.0
    lo, hi = (float(v) for v in np.percentile(vals, (2, 98)))
    return (lo, hi) if hi - lo >= 8 else (float(vals.min()), float(max(vals.max(), vals.min() + 8)))


def _tone(lum: Any, opaque: Any, focus: Any = None) -> Any:
    """Luminance 0..1 after auto-levels (the 2nd to 98th percentile stretched to the full range) and a half-strength histogram equalisation, so a flat, bright
    selfie still uses every tone the palette has."""
    import numpy as np

    ref = (
        opaque & focus if focus is not None else opaque
    )  # the levels come from the face when there is one, so the face gets the whole range
    lo, hi = _levels(lum, ref)
    t = np.clip((lum - lo) / (hi - lo), 0, 1)
    vals = np.sort(t[ref])
    eq = np.searchsorted(vals, t, side="right") / max(1, len(vals))
    return np.clip(0.5 * t + 0.5 * eq, 0, 1)


def _majority(index: Any, n: int, passes: int = 2) -> Any:
    """Each pixel takes the tone most of its 3x3 neighbourhood has (its own counts double): lone speckles vanish and tones become flat shapes, the way hand-drawn sprites are."""
    import numpy as np

    h, w = index.shape
    for _ in range(passes):
        pad = np.pad(index, 1, mode="edge")
        counts = np.zeros((n, h, w), dtype=np.int16)
        for dy in range(3):
            for dx in range(3):
                nb = pad[dy : dy + h, dx : dx + w]
                for k in range(n):
                    counts[k] += nb == k
        counts[index[None] == np.arange(n)[:, None, None]] += 1
        index = counts.argmax(axis=0).astype(index.dtype)
    return index


def _bayer(h: int, w: int) -> Any:
    import numpy as np

    m = (np.array(BAYER4, dtype=np.float32) + 0.5) / 16.0
    return np.tile(m, (h // 4 + 1, w // 4 + 1))[:h, :w]


def _focus_mask(focus: Any, h: int, w: int) -> Any:
    import numpy as np

    m = np.zeros((h, w), dtype=bool)
    if focus:
        l, t, r, b = focus  # noqa: E741
        m[
            int(t * h) : max(int(t * h) + 1, int(b * h)),
            int(l * w) : max(int(l * w) + 1, int(r * w)),
        ] = True
    return m if m.sum() >= 16 else None


def render(img: Any, settings: Settings, focus: Any = None) -> Result:
    """`img` is a PIL image (any mode). Returns the pixel art at 1x; call .png(scale) for the export.

    Steps: area-average down to about three times the final size, edge-preserving smoothing, area-average to the final size; for palettes of 8 colours or
    fewer, auto-levels and a mapping by luminance (optionally ordered Bayer dithering); an outline only on strong edges. `focus` (left, top, right, bottom as fractions) is the face: the levels are taken from it."""
    import numpy as np
    from PIL import Image

    s = settings.clean()
    src = img.convert("RGBA")
    w, h = _fit(*src.size, s.size)
    a_full = np.asarray(src.getchannel("A"), dtype=np.float32) / 255.0
    rgb_full = np.asarray(src.convert("RGB"), dtype=np.float32)
    mw, mh = (
        min(src.size[0], w * 3),
        min(src.size[1], h * 3),
    )  # the smoothing works at about 3x the final size
    mid, mid_a = _area(rgb_full, a_full, mw, mh)
    mid = _bilateral(mid.astype(np.float32))
    rgb_f, small_a = _area(mid.astype(np.float32), mid_a.astype(np.float32), w, h)
    rgb_f = np.clip(rgb_f, 0, 255)
    alpha = (small_a >= 0.5).astype(np.uint8) * 255  # pixel art has no half-transparent pixels
    opaque = alpha > 0
    if not opaque.any():
        raise PixelProblem("That picture is fully transparent.")
    fill = rgb_f.copy()
    fill[~opaque] = rgb_f[opaque].mean(
        axis=0
    )  # transparent pixels must not pull the palette or the levels
    small_count = (len(PALETTES[s.palette]) if s.palette else s.colors) <= LOW_PALETTE
    fmask = _focus_mask(focus, h, w)
    tone = _tone(_lum(fill), opaque, fmask) if small_count else _lum(fill) / 255.0
    bayer = _bayer(h, w) - 0.5

    if s.palette:
        pal = [_rgb(c) for c in PALETTES[s.palette]]
    else:
        base = fill
        if small_count:  # stretch the contrast before choosing a few colours
            lo, hi = _levels(_lum(fill), opaque & fmask if fmask is not None else opaque)
            base = np.clip((fill - lo) / (hi - lo) * 255, 0, 255)
        pal = _median_cut(Image.fromarray(base.astype(np.uint8), "RGB"), s.colors)
        fill = base
    parr = pal_arr(pal)
    if (
        s.palette and small_count
    ):  # by luminance: darkest tone to the darkest colour, in as many steps as the palette has
        order = np.argsort(_lum(parr))
        n = len(pal)
        v = tone + (bayer / n if s.dither else 0)
        index = order[np.clip(np.floor(v * n), 0, n - 1).astype(np.int32)].astype(np.int32)
    else:
        px = fill + (bayer[..., None] * (64 if len(pal) <= 16 else 32) if s.dither else 0)
        snapped0 = _snap(np.clip(px, 0, 255).astype(np.uint8), pal)
        lut = {c: i for i, c in enumerate(pal)}
        index = np.array([lut[tuple(p)] for p in snapped0.reshape(-1, 3)], dtype=np.int32).reshape(
            h, w
        )
    if s.retro and not s.dither:
        index = _majority(index, len(pal))
    snapped = parr[index].astype(np.uint8)

    if s.transparent:
        bg = _background(index, alpha)
        if bg.all():
            bg[:] = False  # the whole picture is one colour: nothing to cut out
        alpha = np.where(bg, 0, alpha).astype(np.uint8)
        opaque = alpha > 0

    out_rgb, out_a = snapped.copy(), alpha.copy()
    if s.outline:
        lum = _lum(parr)
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
        else:  # a solid picture: ink the brighter side of each strong edge, one pixel wide
            mark = np.zeros((h, w), dtype=bool)
            if s.retro:  # edges of the cleaned tone shapes (two or more steps apart), so the line follows shapes and ignores texture
                rank = np.argsort(np.argsort(_lum(parr)))[index].astype(np.int16)
                step, edge_map = max(2, len(pal) // 2), rank
            else:
                step, edge_map = STRONG_EDGE, tone
            for dy, dx in ((0, 1), (1, 0)):
                a = edge_map[: h - dy, : w - dx]
                b = edge_map[dy:, dx:]
                mark[: h - dy, : w - dx] |= (a - b) >= step if s.retro else (a - b) > step
                mark[dy:, dx:] |= (b - a) >= step if s.retro else (b - a) > step
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
