"""Step C, style filters: cel-shade, comic ink with halftone, pencil sketch, pop art and watercolor-ish.

Plain image processing with Pillow and numpy, the same as the pixel-art core: no model, no new dependency (so no OpenCV or scikit-image licence question), and the same
input and settings always give the same picture. Each filter takes a picture of the user's own and returns a restyled copy; none creates anything from nothing.

Every filter has the same three knobs, each used where it makes sense: `colors` (flat colours), `line` (ink/pencil weight, 1 to 5) and `smooth` (how much detail is wiped
out first, 0 to 5)."""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any

from julius.art.pixel import PixelProblem, _bilateral, _lum

WORK_SIDE = 900  # filters work on a copy no larger than this; the result is that size
STYLES = ("cel_shade", "comic", "pencil", "pop_art", "watercolor")
LABELS = {
    "cel_shade": "Cel-shade",
    "comic": "Comic ink",
    "pencil": "Pencil sketch",
    "pop_art": "Pop art",
    "watercolor": "Watercolor",
}
DEFAULTS = {  # colors, line, smooth
    "cel_shade": (6, 2, 3),
    "comic": (4, 2, 2),
    "pencil": (0, 2, 1),
    "pop_art": (4, 3, 2),
    "watercolor": (14, 1, 4),
}
POP = [  # darkest to lightest: ink, deep blue, magenta, orange, yellow, cyan, white
    (20, 16, 40),
    (36, 60, 170),
    (226, 36, 130),
    (255, 130, 30),
    (255, 224, 40),
    (60, 200, 230),
    (255, 250, 240),
]


@dataclass
class FilterSettings:
    style: str = "cel_shade"
    colors: int = 0  # 0 = the style's default
    line: int = 0
    smooth: int = -1  # -1 = the style's default

    def clean(self) -> FilterSettings:
        if self.style not in STYLES:
            raise PixelProblem(f"I don't have a style called {self.style}.")
        dc, dl, ds = DEFAULTS[self.style]

        def num(v: Any, lo: int, hi: int, default: int) -> int:
            try:
                n = int(round(float(v)))
            except (TypeError, ValueError):
                return default
            return max(lo, min(hi, n))

        self.colors = num(self.colors or dc, 2, 32, dc) if dc else 0
        self.line = num(self.line or dl, 1, 5, dl)
        self.smooth = num(ds if self.smooth < 0 else self.smooth, 0, 5, ds)
        return self


def _np(img: Any) -> Any:
    import numpy as np

    return np.asarray(img.convert("RGB"), dtype=np.float32)


def _img(arr: Any) -> Any:
    import numpy as np
    from PIL import Image

    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")


def _smooth(rgb: Any, times: int) -> Any:
    for _ in range(times):
        rgb = _bilateral(rgb, radius=2, sigma_s=2.0, sigma_r=26.0)
    return rgb


def _blur(gray: Any, sigma: float) -> Any:
    import numpy as np
    from PIL import Image, ImageFilter

    im = Image.fromarray(np.clip(gray, 0, 255).astype(np.uint8), "L").filter(
        ImageFilter.GaussianBlur(sigma)
    )
    return np.asarray(im, dtype=np.float32)


def _edges(gray: Any, line: int, strength: float = 0.5) -> Any:
    """Boolean ink mask: strong luminance edges (Sobel on a lightly blurred copy), thickened to `line` pixels."""
    import numpy as np
    from PIL import Image, ImageFilter

    g = _blur(gray, 1.2)
    p = np.pad(g, 1, mode="edge")
    gx = (p[:-2, 2:] + 2 * p[1:-1, 2:] + p[2:, 2:]) - (p[:-2, :-2] + 2 * p[1:-1, :-2] + p[2:, :-2])
    gy = (p[2:, :-2] + 2 * p[2:, 1:-1] + p[2:, 2:]) - (p[:-2, :-2] + 2 * p[:-2, 1:-1] + p[:-2, 2:])
    mag = np.hypot(gx, gy)
    thresh = max(24.0, float(np.percentile(mag, 100 - 12 * strength)))
    mask = mag > thresh
    if line > 1:
        im = Image.fromarray((mask * 255).astype(np.uint8), "L").filter(
            ImageFilter.MaxFilter(2 * (line // 2) + 1 if line % 2 else line + 1)
        )
        mask = np.asarray(im) > 127
    return mask


def _posterize(rgb: Any, colors: int) -> Any:
    from PIL import Image

    q = _img(rgb).quantize(colors=colors, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    return _np(q.convert("RGB"))


def cel_shade(rgb: Any, s: FilterSettings) -> Any:
    flat = _posterize(_smooth(rgb, s.smooth), s.colors)
    out = flat.copy()
    out[_edges(_lum(rgb), s.line)] = (18, 16, 24)
    return out


def _halftone(dark: Any, cell: int) -> Any:
    """Dots on a 45-degree grid whose size follows `dark` (0..1); True where a dot is."""
    import numpy as np

    h, w = dark.shape
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    u = (xx + yy) / 1.41421356
    v = (xx - yy) / 1.41421356
    fu = (u / cell) % 1.0 - 0.5
    fv = (v / cell) % 1.0 - 0.5
    dist = np.hypot(fu, fv)  # 0 at a dot centre, up to 0.707 in the cell corner
    return dist < np.sqrt(np.clip(dark, 0, 1)) * 0.62


def comic(rgb: Any, s: FilterSettings) -> Any:
    lum = _lum(rgb)
    flat = _posterize(_smooth(rgb, s.smooth), max(2, s.colors))
    out = np_lighten(flat, 0.18)
    dark = 1.0 - _blur(lum, 2.0) / 255.0
    dots = _halftone(dark * 1.25 - 0.18, cell=5 + s.line)
    out[dots] = out[dots] * 0.35
    out[_edges(lum, s.line)] = (14, 12, 20)
    return out


def np_lighten(rgb: Any, k: float) -> Any:
    return rgb + (255 - rgb) * k


def pencil(rgb: Any, s: FilterSettings) -> Any:
    import numpy as np

    gray = _lum(_smooth(rgb, s.smooth))
    inv = 255.0 - gray
    blur = _blur(inv, 2.0 + 2.2 * s.line)
    sketch = np.clip(gray * 255.0 / np.maximum(255.0 - blur, 1.0), 0, 255)  # colour dodge
    sketch = 255.0 * (sketch / 255.0) ** 2.4  # darker, more visible strokes
    rng = np.random.default_rng(7)
    grain = _blur(rng.normal(0, 1, gray.shape) * 40 + 128, 0.8)
    paper = np.clip(sketch * (0.92 + 0.08 * (grain - 128) / 40), 0, 255)
    tint = np.array([250.0, 246.0, 236.0]) / 255.0
    return paper[..., None] * tint


def pop_art(rgb: Any, s: FilterSettings) -> Any:
    import numpy as np

    lum = _lum(_smooth(rgb, s.smooth))
    n = max(2, min(s.colors, len(POP)))
    lo, hi = (float(v) for v in np.percentile(lum, (2, 98)))
    t = np.clip((lum - lo) / max(hi - lo, 1.0), 0, 0.9999)
    idx = np.floor(t * n).astype(int)
    pal = np.array([POP[round(i * (len(POP) - 1) / (n - 1))] for i in range(n)], dtype=np.float32)
    out = pal[idx]
    out[_edges(_lum(rgb), s.line)] = POP[0]
    return out


def watercolor(rgb: Any, s: FilterSettings) -> Any:
    import numpy as np

    wash = _smooth(rgb, max(2, s.smooth + 1))
    wash = _posterize(wash, s.colors) * 0.75 + wash * 0.25  # flat pools of colour, softened
    gray = _lum(rgb)
    edge = np.clip(_blur(np.hypot(*np.gradient(_blur(gray, 1.5))), 1.2) / 28.0, 0, 1)
    wash = wash * (1 - 0.35 * edge[..., None])  # pigment gathers at the edges
    mean = wash.mean(axis=2, keepdims=True)
    wash = mean + (wash - mean) * 1.15  # a little more saturation
    wash = np_lighten(wash, 0.12)
    rng = np.random.default_rng(11)
    tex = _blur(rng.normal(0, 1, gray.shape) * 30 + 128, 1.6)
    return wash * (0.94 + 0.06 * (tex - 128) / 30)[..., None]


_FILTERS = {
    "cel_shade": cel_shade,
    "comic": comic,
    "pencil": pencil,
    "pop_art": pop_art,
    "watercolor": watercolor,
}


def apply(img: Any, settings: FilterSettings) -> tuple[Any, FilterSettings]:
    """The restyled picture (RGB, at most WORK_SIDE on the long side) and the settings actually used."""
    s = settings.clean()
    im = img.convert("RGBA")
    flat = Image_new_white(im.size)
    flat.paste(im, mask=im.getchannel("A"))  # transparent areas become white paper
    flat.thumbnail((WORK_SIDE, WORK_SIDE))
    out = _FILTERS[s.style](_np(flat), s)
    return _img(out), s


def Image_new_white(size: tuple[int, int]) -> Any:  # noqa: N802
    from PIL import Image

    return Image.new("RGB", size, (255, 255, 255))


def png(img: Any) -> bytes:
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()
