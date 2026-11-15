"""Game sprite of a face, built from measurements, not from a model.

Apple Vision (visiond `landmarks`, on this Mac only) gives the face landmarks (jaw contour, eyes, brows, nose, mouth) and a person mask. This module turns those
numbers into a head a few pixels wide: the head shape comes from the jaw contour, the hair is one solid simplified mass cut from the person mask, the eyes are
one or two pixels, the brows and mouth are short lines, the nose is optional. Skin and hair colours are measured in the photo and each is mapped to the nearest
palette shade. The outline is one pixel in the darkest shade and the background is transparent. Same picture and options in, same sprite out (no randomness).

Head-only: the body belongs to the later sprite studio stage."""

from __future__ import annotations

import io
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from julius.art import pixel

HEADS = (16, 24, 32)
DEFAULT_HEAD = 16
DEFAULT_PALETTE = "gameboy"
SS = 8  # the head is built at 8x the sprite resolution, then reduced


class SpriteProblem(Exception):
    """Something the user can be told in one sentence."""


@dataclass
class Options:
    head: int = DEFAULT_HEAD  # head size in pixels (the canvas is twice as big)
    palette: str = DEFAULT_PALETTE
    chibi: bool = False  # big round head, low small features
    big_eyes: bool = False
    nose: bool = False

    def clean(self) -> Options:
        try:
            h = int(round(float(self.head or DEFAULT_HEAD)))
        except (TypeError, ValueError):
            h = DEFAULT_HEAD
        self.head = min(HEADS, key=lambda x: abs(x - h))
        self.palette = pixel.palette_name(self.palette or DEFAULT_PALETTE)
        return self


@dataclass
class Sprite:
    image: Any  # RGBA, canvas x canvas, 1 pixel per sprite pixel
    options: Options
    colors: dict[str, str] = field(default_factory=dict)  # role -> hex
    notes: list[str] = field(default_factory=list)

    @property
    def canvas(self) -> int:
        return self.image.width

    def scaled(self, k: int) -> Any:
        return self.image.resize((self.image.width * k, self.image.height * k), Image.NEAREST)

    def png(self, k: int = 1) -> bytes:
        out = io.BytesIO()
        (self.image if k == 1 else self.scaled(k)).save(out, "PNG", optimize=True)
        return out.getvalue()

    @property
    def palette(self) -> list[str]:
        seen: list[str] = []
        for v in self.colors.values():
            if v not in seen:
                seen.append(v)
        return seen


# ---- colour ----------------------------------------------------------------------------------------
def _lum(c: tuple[int, int, int]) -> float:
    return (0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]) / 255.0


def _lab(c: Any) -> Any:
    """sRGB (0-255, shape (..., 3)) to CIE Lab, so that "nearest colour" means nearest to the eye."""
    v = np.asarray(c, dtype=np.float64) / 255.0
    v = np.where(v > 0.04045, ((v + 0.055) / 1.055) ** 2.4, v / 12.92)
    m = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]])
    xyz = (v @ m.T) / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack(
        [116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])],
        axis=-1,
    )


def _near(a: Any, b: Any) -> Any:
    """Distance in Lab, lightness counted at 70% (a skin tone should keep its hue before its exact brightness)."""
    d = _lab(a) - _lab(b)
    return np.sqrt((0.7 * d[..., 0]) ** 2 + d[..., 1] ** 2 + d[..., 2] ** 2)


def _dist(a: Any, b: Any) -> Any:
    d = np.asarray(a, dtype=np.float32) - np.asarray(b, dtype=np.float32)
    w = np.array([0.30, 0.59, 0.11], dtype=np.float32)
    return np.sqrt((d * d * w).sum(axis=-1)) * 1.8


def map_colors(
    skin: tuple[int, int, int], hair: tuple[int, int, int] | None, palette: str
) -> dict[str, tuple[int, int, int]]:
    """Skin, hair, line and shade colours chosen from the palette. A palette of four or fewer maps by lightness (the outline is always the darkest shade, the skin
    one of the lightest, the hair darker than the skin); a bigger palette takes the nearest colour."""
    pal = [pixel._rgb(c) for c in pixel.PALETTES[palette]]
    order = sorted(range(len(pal)), key=lambda i: _lum(pal[i]))
    pal = [pal[i] for i in order]
    n = len(pal)
    out: dict[str, tuple[int, int, int]] = {"line": pal[0]}
    if n <= 4:
        skin_i = max(min(n - 1, int(_lum(skin) * n)), n - 2 if n > 2 else 1)
        out["skin"] = pal[skin_i]
        if hair is not None:
            out["hair"] = pal[max(0, min(int(_lum(hair) * n), skin_i - 1))]
        out["shade"] = pal[max(0, skin_i - 1)]
        out["light"] = pal[min(n - 1, skin_i + 1)] if skin_i < n - 1 else pal[skin_i]
        return out
    near = sorted(range(n), key=lambda i: float(_near(pal[i], skin)))
    out["skin"] = pal[near[0]]
    if hair is not None:
        hnear = sorted(range(n), key=lambda i: float(_near(pal[i], hair)))
        pick = next((i for i in hnear if pal[i] != out["skin"]), hnear[0])
        out["hair"] = pal[pick]
    darker = [c for c in pal if _lum(c) < _lum(out["skin"]) - 0.12 and c != out["line"]]
    out["shade"] = (
        min(darker, key=lambda c: float(_near(c, np.asarray(out["skin"]) * 0.7)))
        if darker
        else out["line"]
    )
    lighter = [c for c in pal if _lum(c) > _lum(out["skin"]) + 0.05]
    out["light"] = lighter[-1] if lighter else out["skin"]
    return out


def _hex(c: tuple[int, int, int]) -> str:
    return f"{c[0]:02X}{c[1]:02X}{c[2]:02X}"


# ---- geometry --------------------------------------------------------------------------------------
def _pts(face: dict, name: str) -> np.ndarray:
    return np.asarray(face["points"].get(name) or [], dtype=np.float64).reshape(-1, 2)


def _face_polygon(contour: np.ndarray, forehead_top: float) -> np.ndarray:
    """The jaw contour closed over the forehead with a half ellipse: the head shape the sprite is cut from."""
    a, b = contour[0], contour[-1]
    cx, ye = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
    rx, ry = abs(a[0] - b[0]) / 2, max(1e-6, ye - forehead_top)
    ts = np.linspace(0, math.pi, 13)[1:-1]
    sign = 1.0 if b[0] >= a[0] else -1.0  # go from the end point b over the top to a
    arc = np.stack([cx + sign * rx * np.cos(ts), ye - ry * np.sin(ts)], axis=1)
    return np.vstack([contour, arc])


def _fill(poly: np.ndarray, size: int) -> Any:
    im = Image.new("L", (size, size), 0)
    ImageDraw.Draw(im).polygon([tuple(p) for p in poly], fill=255)
    return np.asarray(im) > 127


def _components(occ: Any) -> Any:
    """The largest 4-connected piece of an occupancy grid (stray specks go)."""
    h, w = occ.shape
    seen = np.zeros_like(occ, dtype=bool)
    best: list[tuple[int, int]] = []
    for y in range(h):
        for x in range(w):
            if occ[y, x] and not seen[y, x]:
                stack, comp = [(y, x)], []
                seen[y, x] = True
                while stack:
                    cy, cx = stack.pop()
                    comp.append((cy, cx))
                    for ny, nx in ((cy + 1, cx), (cy - 1, cx), (cy, cx + 1), (cy, cx - 1)):
                        if 0 <= ny < h and 0 <= nx < w and occ[ny, nx] and not seen[ny, nx]:
                            seen[ny, nx] = True
                            stack.append((ny, nx))
                if len(comp) > len(best):
                    best = comp
    out = np.zeros_like(occ, dtype=bool)
    for y, x in best:
        out[y, x] = True
    return out


def _fill_holes(occ: Any) -> Any:
    h, w = occ.shape
    pad = np.pad(~occ, 1, constant_values=True)
    seen = np.zeros_like(pad, dtype=bool)
    stack = [(0, 0)]
    seen[0, 0] = True
    while stack:
        y, x = stack.pop()
        for ny, nx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
            if 0 <= ny < h + 2 and 0 <= nx < w + 2 and pad[ny, nx] and not seen[ny, nx]:
                seen[ny, nx] = True
                stack.append((ny, nx))
    return ~seen[1:-1, 1:-1]


def _despeckle(label: Any) -> Any:
    """A pixel with no neighbour of its own kind takes the most common neighbouring kind (0 = empty). Skin and hair edges stay put, single stray pixels go."""
    h, w = label.shape
    out = label.copy()
    for y in range(h):
        for x in range(w):
            v = label[y, x]
            nb = [
                label[ny, nx]
                for ny in range(max(0, y - 1), min(h, y + 2))
                for nx in range(max(0, x - 1), min(w, x + 2))
                if (ny, nx) != (y, x)
            ]
            if v and sum(1 for n in nb if n == v) == 0:
                vals = [n for n in nb if n]
                out[y, x] = max(set(vals), key=vals.count) if len(vals) >= 3 else 0
    return out


# ---- the build -------------------------------------------------------------------------------------
LINE, SKIN, HAIR, SHADE, LIGHT, BROW = (
    3,
    1,
    2,
    4,
    5,
    6,
)  # cell kinds on the sprite grid (0 is empty)


def _median(arr: Any, sel: Any) -> tuple[int, int, int] | None:
    px = arr[sel]
    if len(px) < 6:
        return None
    m = np.median(px.reshape(-1, 3), axis=0)
    return int(m[0]), int(m[1]), int(m[2])


class _Head:
    """One face on its way to a sprite: the measurements, the hi-res layers, then the cells."""

    def __init__(self, img: Any, face: dict, mask: Any, o: Options) -> None:
        self.o = o
        contour = _pts(face, "contour")
        if len(contour) < 5 or not len(_pts(face, "left_eye")) or not len(_pts(face, "right_eye")):
            raise SpriteProblem("I couldn't measure that face well enough to build a sprite.")
        self.im = img.convert("RGB")
        self.W, self.H = self.im.size
        self.pts = {k: self._px(_pts(face, k)) for k in face["points"] if len(face["points"][k])}
        self.contour = self._px(contour)
        self.mask = (
            None if mask is None else mask.convert("L").resize((self.W, self.H), Image.BILINEAR)
        )
        self._measure(face)
        self._frame()
        self._layers()

    def _px(self, p: Any) -> Any:
        return np.asarray(p, dtype=np.float64) * np.array([self.W, self.H], dtype=np.float64)

    # -- picture-space measurements and the square the head is cut from
    def _measure(self, face: dict) -> None:
        pts, bh = self.pts, face["box"][3]
        self.eye_l, self.eye_r = pts["left_eye"].mean(axis=0), pts["right_eye"].mean(axis=0)
        self.eye_y = (self.eye_l[1] + self.eye_r[1]) / 2
        self.brow_y = (
            min(pts["left_brow"][:, 1].min(), pts["right_brow"][:, 1].min())
            if "left_brow" in pts and "right_brow" in pts
            else self.eye_y - bh * self.H * 0.12
        )
        self.chin_y = float(self.contour[:, 1].max())
        self.forehead_top = self.brow_y - max(1.0, self.eye_y - self.brow_y) * 1.6
        self.poly = _face_polygon(self.contour, self.forehead_top)
        self.left, self.right = float(self.poly[:, 0].min()), float(self.poly[:, 0].max())
        self.fw = self.right - self.left

    def _frame(self) -> None:
        """The head box: the face plus the hair mass the person mask shows around the upper head, as a square."""
        top, lft, rgt = float(self.forehead_top), self.left, self.right
        if self.mask is not None:
            ma = np.asarray(self.mask) > 127
            fw = self.fw
            y0, y1 = int(max(0, top - fw * 0.95)), int(max(1, min(self.H, self.eye_y)))
            x0, x1 = int(max(0, lft - fw * 0.55)), int(min(self.W, rgt + fw * 0.55))
            rows = np.where(ma[y0:y1, x0:x1].sum(axis=1) > max(2, fw * 0.08))[0]
            if len(rows):
                top = min(top, y0 + float(rows[0]))
            low = int(max(1, self.chin_y - (self.chin_y - self.eye_y) * 0.4))
            cols = np.where(ma[int(max(0, top)) : low, x0:x1].sum(axis=0) > max(2, fw * 0.05))[0]
            if len(cols):
                lft, rgt = min(lft, x0 + float(cols[0])), max(rgt, x0 + float(cols[-1]))
        bottom = self.chin_y + self.fw * 0.02
        self.S = max(rgt - lft, bottom - top)
        self.G = self.o.head - 2  # the head without its outline
        self.N = self.G * SS
        self.scale = self.N / self.S
        self.ox, self.oy = (lft + rgt) / 2 - self.S / 2, (top + bottom) / 2 - self.S / 2

    def hi(self, p: Any) -> Any:
        return (np.asarray(p, dtype=np.float64) - np.array([self.ox, self.oy])) * self.scale

    def at(self, p: Any) -> tuple[float, float]:
        """A measured point (picture pixels) on the sprite grid."""
        q = self.hi(p) / SS + 1
        return float(q[0]), float(q[1])

    def _crop(self, im: Any) -> Any:
        box = tuple(int(round(v)) for v in (self.ox, self.oy, self.ox + self.S, self.oy + self.S))
        return im.crop(box).resize(
            (self.N, self.N), Image.BILINEAR
        )  # outside the picture: black / not person

    # -- the hi-res skin and hair layers
    def _layers(self) -> None:
        N = self.N
        arr = np.asarray(self._crop(self.im)).astype(np.float32)
        mc = (
            np.zeros((N, N), dtype=bool)
            if self.mask is None
            else np.asarray(self._crop(self.mask)) > 127
        )
        face = _fill(self.hi(self.poly), N)
        ys, xs = np.arange(N)[:, None] * np.ones((1, N)), np.arange(N)[None, :] * np.ones((N, 1))
        self.ys, self.xs = ys, xs
        eye_hi, self.brow_hi = self.hi([0, self.eye_y])[1], self.hi([0, self.brow_y])[1]
        chin_hi = self.hi([0, self.chin_y])[1]
        nose = self.pts.get("nose")
        self.nose_hi = (
            self.hi(nose)[:, 1].max() if nose is not None else eye_hi + (chin_hi - eye_hi) * 0.5
        )
        self.eye_hi, self.chin_hi, self.cx_hi = (
            eye_hi,
            chin_hi,
            self.hi([(self.left + self.right) / 2, 0])[0],
        )

        # colours measured in the photo
        mid = (ys > eye_hi + (self.nose_hi - eye_hi) * 0.2) & (ys < self.nose_hi)
        cheeks = face & mid & (np.abs(xs - self.cx_hi) < self.fw * self.scale * 0.35)
        self.skin_rgb = _median(arr, cheeks) or _median(arr, face) or (200, 160, 130)
        self.hair_rgb = None
        hair_sel = mc & ~face & (ys < self.brow_hi) & (_dist(arr, np.array(self.skin_rgb)) > 40)
        if self.mask is not None and hair_sel.sum() > 0.012 * N * N:
            self.hair_rgb = _median(arr, hair_sel)

        d_skin = _dist(arr, np.array(self.skin_rgb))
        d_hair = _dist(arr, np.array(self.hair_rgb if self.hair_rgb else (0, 0, 0)))
        lower = (
            eye_hi + chin_hi
        ) / 2  # hair beside the face stops at mid cheek; below that the mask is neck and clothes
        outside = mc & ~face & (ys < lower)
        hair, skin = np.zeros((N, N), dtype=bool), face.copy()
        if self.hair_rgb is not None:
            ears = (
                outside & (ys >= self.brow_hi) & (d_hair >= d_skin)
            )  # beside the face a skin-coloured patch is an ear; above the brows it is all hair
            hair, skin = outside & ~ears, skin | ears
            beard = (
                face & (ys > self.nose_hi) & (d_hair + 8 < d_skin) & (d_hair < 55)
            )  # dark facial hair inside the jaw line
            hair, skin = hair | beard, skin & ~beard
        else:
            skin |= outside
        if self.o.chibi:  # rounder cheeks: the face also fills an ellipse from forehead to chin; the bangs lift off it
            fy = self.hi([0, self.forehead_top])[1]
            ell = (np.abs((xs - self.cx_hi) / max(1.0, self.fw * self.scale / 2 * 1.02)) ** 3) + (
                np.abs((ys - (fy + chin_hi) / 2) / max(1.0, (chin_hi - fy) / 2)) ** 3
            ) <= 1.0  # a squarish ellipse: full cheeks
            lift_to = self.nose_hi + (chin_hi - self.nose_hi) * 0.3
            hair &= ~(ell & (ys > self.brow_hi - (eye_hi - self.brow_hi) * 1.5) & (ys < lift_to))
            skin |= ell & ~hair
        self.skin_hi, self.hair_hi = skin & ~hair, hair

    # -- the cells
    def cells(self) -> Any:
        G, head = self.G, self.o.head

        def cover(a: Any) -> Any:
            return a.astype(np.float32).reshape(G, SS, G, SS).mean(axis=(1, 3))

        cs, ch = cover(self.skin_hi), cover(self.hair_hi)
        occ = (cs + ch) >= 0.5
        label = np.where(occ, np.where(ch > cs, HAIR, SKIN), 0).astype(np.int8)
        occ = _fill_holes(_components(occ))
        label = _despeckle(np.where(occ, np.where(label == 0, SKIN, label), 0).astype(np.int8))
        if not label.any():
            raise SpriteProblem("I couldn't find a head shape to build a sprite from.")
        grid = np.zeros((head, head), dtype=np.int8)
        grid[1:-1, 1:-1] = label
        solid = grid > 0
        edge = np.zeros_like(solid)
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            edge |= np.roll(np.roll(solid, dy, axis=0), dx, axis=1)
        grid[edge & ~solid] = LINE  # one pixel in the darkest shade around the head
        self.grid = grid
        return grid

    def put(self, x: int, y: int, v: int, only: tuple[int, ...] = (SKIN, HAIR)) -> None:
        if 0 <= x < self.o.head and 0 <= y < self.o.head and self.grid[y, x] in only:
            self.grid[y, x] = v

    def mark(
        self, cx: float, y: int, w: int, h: int, v: int, only: tuple[int, ...] = (SKIN, HAIR)
    ) -> None:
        x0 = int(round(cx - (w - 1) / 2))
        for dy in range(h):
            for dx in range(w):
                self.put(x0 + dx, y + dy, v, only)

    def features(self, cols: dict) -> None:
        o, u = self.o, max(1, self.o.head // 16)
        (xl, yl), (xr, yr) = sorted((self.at(self.eye_l), self.at(self.eye_r)))
        ey, gap = (yl + yr) / 2, xr - xl
        if o.chibi:  # features low and wide-set on the big forehead
            ey, gap = 1.0 + (o.head - 2.0) * 0.60, max(gap, (o.head - 2.0) * 0.36)
            xl, xr = (xl + xr) / 2 - gap / 2, (xl + xr) / 2 + gap / 2
        if gap < 3 * u:
            xl, xr = (xl + xr) / 2 - 1.5 * u, (xl + xr) / 2 + 1.5 * u
        ew, eh = (
            max(1, min(2 * u, int(round(np.ptp(self.pts["left_eye"][:, 0]) * self.scale / SS)))),
            u,
        )
        if o.big_eyes or o.chibi:
            ew, eh = 2 * u, 2 * u
        eyes_y = int(round(ey - (eh - 1) / 2))
        for cx in (xl, xr):
            self.mark(cx, eyes_y, ew, eh, LINE)
            if o.big_eyes and eh >= 2:
                self.put(
                    int(round(cx - (ew - 1) / 2)), eyes_y, LIGHT, (LINE,)
                )  # the glint, top left
        cols["brow"] = (
            cols["hair"] if cols.get("hair", cols["skin"]) != cols["skin"] else cols["line"]
        )
        by = eyes_y - 2 * u - (eh - u)
        if "left_brow" in self.pts and "right_brow" in self.pts and not o.chibi:
            mid = (
                self.at(self.pts["left_brow"].mean(axis=0))[1]
                + self.at(self.pts["right_brow"].mean(axis=0))[1]
            ) / 2
            by = min(int(round(mid)), eyes_y - 2 * u)
        for cx in (xl, xr):
            self.mark(cx, by, max(2, int(round(1.2 * ew + 1))), u, BROW, (SKIN,))
        self._mouth(cols, eyes_y + eh, u)
        nose = self.pts.get("nose")
        if o.nose and nose is not None and len(nose):
            nx, ny = self.at(nose[np.argmax(nose[:, 1])])
            self.put(int(round(nx)), int(round(ny)), SHADE, (SKIN,))

    def _mouth(self, cols: dict, below_eyes: int, u: int) -> None:
        """A short line; the corners step up for a smile or down for a frown; a second row when the mouth is open."""
        mo = self.pts.get("mouth")
        if mo is None or not len(mo):
            return
        o = self.o
        mx, my0 = self.at(mo.mean(axis=0))
        wid = max(2 * u, min(int(round(np.ptp(mo[:, 0]) * self.scale / SS)), 6 * u))
        if o.chibi:
            my0, wid = 1.0 + (o.head - 2.0) * 0.82, max(2 * u, min(wid, 3 * u))
        my = max(int(round(my0)), below_eyes + u)
        # a mouth inside a beard of the line colour would vanish: show it in the skin tone
        lip = SKIN if cols.get("hair") == cols["line"] else LINE
        self.mark(mx, my, wid, u, lip, (SKIN, HAIR))
        x0 = int(round(mx - (wid - 1) / 2))
        corners = sorted(mo, key=lambda p: p[0])
        lift = (
            self.at(mo.mean(axis=0))[1] - (self.at(corners[0])[1] + self.at(corners[-1])[1]) / 2
        )  # > 0: corners above the middle
        if wid >= 4 * u and abs(lift) >= 0.45 and not o.chibi:
            for xx in (x0, x0 + wid - 1):
                self.put(xx, my - u if lift > 0 else my + u, LINE, (SKIN, HAIR))
        inner = self.pts.get("inner_mouth")
        if inner is not None and len(inner) and np.ptp(inner[:, 1]) * self.scale / SS >= 0.9:
            for dx in range(1, max(1, wid - 1)):
                self.put(x0 + dx, my + u, LINE, (SKIN, HAIR))


def build(img: Any, face: dict, mask: Any, options: Options | None = None) -> Sprite:
    """The sprite for one face. `face` is a Vision landmark record (fractions of the picture, origin top-left); `mask` the person mask (white = person) or None."""
    o = (options or Options()).clean()
    h = _Head(img, face, mask, o)
    grid = h.cells()
    cols = map_colors(h.skin_rgb, h.hair_rgb, o.palette)
    h.features(cols)
    lut = {
        SKIN: cols["skin"],
        HAIR: cols.get("hair", cols["line"]),
        LINE: cols["line"],
        SHADE: cols["shade"],
        LIGHT: cols["light"],
        BROW: cols["brow"],
    }
    canvas, off = o.head * 2, o.head // 2
    rgba = np.zeros((canvas, canvas, 4), dtype=np.uint8)
    for kind, c in lut.items():
        ys, xs = np.nonzero(grid == kind)
        rgba[ys + off, xs + off] = (c[0], c[1], c[2], 255)
    used = set(np.unique(grid).tolist())
    roles = {
        "line": cols["line"],
        "skin": cols["skin"],
        **({"hair": cols["hair"]} if "hair" in cols and HAIR in used else {}),
        **({"shade": cols["shade"]} if SHADE in used else {}),
        **({"light": cols["light"]} if LIGHT in used else {}),
    }
    notes = [
        f"{o.head}×{o.head} head on a {canvas}×{canvas} canvas",
        f"palette {o.palette}",
        "chibi proportions" if o.chibi else "normal proportions",
        *(["big eyes"] if o.big_eyes else []),
        *(["nose"] if o.nose else []),
        "no hair found" if h.hair_rgb is None else "hair from the person mask",
    ]
    return Sprite(Image.fromarray(rgba, "RGBA"), o, {k: _hex(v) for k, v in roles.items()}, notes)
