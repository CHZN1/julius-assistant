"""Editable sprites: a sprite as named layers with parameters, re-rendered from those parameters, plus per-layer pixel edits.

The builder (sprite.py) measures a face once. This module keeps what it measured as base layers on the sprite canvas (canvas coordinates, the head in the middle,
room above it for taller hair): the skull, the hair, every facial hair shape, the stubble, the eyes, brows, mouth and nose. Parameters change those layers
without the photo ("bigger beard", "taller afro", "add glasses", "close-set eyes"), accessories (glasses, earrings, hat) are drawn from the measured
geometry, and pixel edits from the editor window are stored per layer on top. The palette is locked: every pixel is an index into the sprite's palette.

A document is plain JSON (`.julius-sprite`), so it can be saved, reopened and edited again; a body added later keeps the same head.

Layers, bottom to top: outline, skin, stubble, beard, hair, hat, eyes, brows, mouth, nose, glasses, earrings. The outline is one pixel around the body
layers (skin, stubble, beard, hair, hat), recomputed after every change."""

from __future__ import annotations

import copy
import io
import json
import re
import secrets
import time
from typing import Any

import numpy as np
from PIL import Image

from julius.art import pixel

FORMAT, VERSION = "julius-sprite", 1
LAYERS = (
    "outline",
    "skin",
    "stubble",
    "beard",
    "hair",
    "hat",
    "eyes",
    "brows",
    "mouth",
    "nose",
    "glasses",
    "earrings",
)
BODY = ("skin", "stubble", "beard", "hair", "hat")
HAIR_STYLES = ("measured", "short", "afro", "long", "bald")
BEARDS = ("full", "goatee", "mustache", "stubble", "none")
GLASSES = ("none", "round", "square", "shades")
HATS = ("none", "cap", "beanie")
MOUTHS = ("measured", "smile", "neutral", "frown")
LIMITS = {
    "hair_height": (-4, 8),
    "beard_size": (-1, 3),
    "eye_spacing": (-2, 3),
    "brow_raise": (-1, 2),
    "mouth_width": (-2, 3),
}
MAX_HISTORY = 50
MAX_EDITS = 20000

ROLE_OF = {
    1: "skin",
    2: "hair",
    3: "line",
    4: "shade",
    5: "light",
    6: "brow",
}  # sprite.py cell kinds


class DocProblem(ValueError):
    """Something the user can be told in one sentence."""


# ---- building a document from the measured head ------------------------------------------------------
def _cells(mask: Any, off: int) -> list[list[int]]:
    ys, xs = np.nonzero(mask)
    return [[int(x) + off, int(y) + off] for y, x in zip(ys, xs, strict=True)]


def _rec(rec: dict, off: int) -> list[list]:
    return [[x + off, y + off, ROLE_OF[v]] for (x, y), v in sorted(rec.items())]


def from_head(h: Any, cols: dict, o: Any, notes: list[str]) -> dict:
    """The document for a freshly built sprite (sprite.build): base layers from the measured head, parameters at their measured values."""
    head, canvas = o.head, o.head * 2
    off = (
        head // 2 + 1
    )  # the label grid sits inside the outline ring of the head grid, which sits in the middle of the canvas
    hexes = [c.upper() for c in pixel.PALETTES[o.palette]]
    index = {pixel._rgb(c): i for i, c in enumerate(hexes)}
    roles = {
        r: index[tuple(cols[r])] for r in ("line", "skin", "shade", "light", "brow") if r in cols
    }
    roles["hair"] = index[tuple(cols["hair"])] if "hair" in cols else roles["line"]
    roles["hat"] = _hat_colour(hexes, roles)
    rec = getattr(h, "rec", {})
    feat_off = head // 2  # features were drawn on the head grid (with its outline ring)
    eg = getattr(h, "eye_geom", {}) or {}
    mg = getattr(h, "mouth_geom", None)
    base = {
        "skull": _cells(h.parts["skull"], off),
        "silhouette": _cells(h.parts["silhouette"], off),
        "hair": _cells(h.parts["hair"], off),
        "stubble": _cells(h.parts["stubble"], off),
        "beard": _cells(h.parts["beard"], off),
        "beard_shapes": {
            k: _cells(v, off) for k, v in (getattr(h, "beard_shapes", {}) or {}).items()
        },
        "eye_l": _rec(rec.get("eye_l", {}), feat_off),
        "eye_r": _rec(rec.get("eye_r", {}), feat_off),
        "brows": _rec(rec.get("brows", {}), feat_off),
        "mouth": _rec(rec.get("mouth", {}), feat_off),
        "nose": _rec(rec.get("nose", {}), feat_off),
        "nose_at": [h.nose_at[0] + feat_off, h.nose_at[1] + feat_off]
        if getattr(h, "nose_at", None)
        else None,
        "eye": {"y": eg.get("y", head // 2) + feat_off, "u": eg.get("u", 1)},
        "mouth_geom": ({**mg, "x0": mg["x0"] + feat_off, "y": mg["y"] + feat_off} if mg else None),
        "beard_kind": h.beard_kind,
    }
    return {
        "format": FORMAT,
        "version": VERSION,
        "id": secrets.token_hex(4),
        "created": round(time.time(), 3),
        "label": "",
        "head": head,
        "canvas": canvas,
        "palette": o.palette,
        "colors": hexes,
        "roles": roles,
        "options": {"chibi": o.chibi, "big_eyes": o.big_eyes, "nose": o.nose, "beard": o.beard},
        "base": base,
        "params": default_params(base, o),
        "edits": {},
        "notes": list(notes),
    }


def _hat_colour(hexes: list[str], roles: dict) -> int:
    """A hat colour that is none of skin, hair or outline: the most colourful one left (pico8 red, nes orange; on a Game Boy the free shade)."""
    taken = {roles.get(r) for r in ("skin", "hair", "line")}
    best, score = roles.get("shade", 0), -1.0
    for i, c in enumerate(hexes):
        if i in taken:
            continue
        r, g, b = pixel._rgb(c)
        chroma = max(r, g, b) - min(r, g, b)
        if chroma > score:
            best, score = i, chroma
    return best


def default_params(base: dict, o: Any) -> dict:
    return {
        "hair_style": "measured",
        "hair_height": 0,
        "beard": base.get("beard_kind", "none"),
        "beard_size": 0,
        "eye_spacing": 0,
        "eye_size": "big" if o.big_eyes else "measured",
        "brows": True,
        "brow_raise": 0,
        "mouth": "measured",
        "mouth_width": 0,
        "nose": bool(o.nose),
        "glasses": "none",
        "earrings": False,
        "hat": "none",
        "skin_color": None,
        "hair_color": None,
        "hat_color": None,
    }


# ---- rendering from parameters -------------------------------------------------------------------------
def _set(cells: list) -> set[tuple[int, int]]:
    return {(int(c[0]), int(c[1])) for c in cells}


def _hair(doc: dict) -> set[tuple[int, int]]:
    p, b = doc["params"], doc["base"]
    hair = _set(b["hair"])
    sil = _set(b["silhouette"])
    eye_y = b["eye"]["y"]
    style = p["hair_style"]
    if style == "bald":
        return set()
    if (
        style == "short"
    ):  # a cap that hugs the skull: the top rows of the head, and the temples down to above the eyes
        skull = _set(b["skull"]) or sil
        u = b["eye"]["u"]
        out = set()
        for x in {c[0] for c in skull}:
            top = min(y for (xx, y) in skull if xx == x)
            out |= {(x, y) for y in range(top - u, top + 2 * u) if y < eye_y - 2 * u}
        rows: dict[int, list[int]] = {}
        for x, y in skull:
            rows.setdefault(y, []).append(x)
        for y, xs in rows.items():
            if y < eye_y - u:
                out |= {(min(xs) - 1, y), (min(xs), y), (max(xs), y), (max(xs) + 1, y)}
        hair = out
    elif style == "afro":  # rounder and bigger: one cell out, up and to the sides, above the eyes
        grow = {
            (x + dx, y + dy)
            for (x, y) in hair
            if y < eye_y
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (-1, -1), (1, -1))
        }
        hair |= {c for c in grow if c not in sil or c in hair}
    elif style == "long":  # down the sides to the chin
        chin = max((y for _, y in sil), default=eye_y)
        xs = [x for (x, y) in sil if abs(y - eye_y) <= 1]
        if xs:
            lo, hi = min(xs), max(xs)
            for y in range(eye_y, chin + 1):
                hair |= {(lo - 1, y), (lo, y), (hi, y), (hi + 1, y)}
    k = int(p["hair_height"])
    if k and hair:
        cols: dict[int, list[int]] = {}
        for x, y in hair:
            cols.setdefault(x, []).append(y)
        for x, ys in cols.items():
            top = min(ys)
            if k > 0:
                hair |= {(x, top - i) for i in range(1, k + 1)}
            else:
                keep = sorted(ys)
                for y in keep[: min(-k, max(0, len(keep) - 1))]:
                    hair.discard((x, y))
    c = doc["canvas"]
    return {(x, y) for x, y in hair if 0 <= x < c and 0 <= y < c}


def _beard(doc: dict) -> tuple[set, set]:
    """(beard cells, stubble cells) for the chosen kind and size."""
    p, b = doc["params"], doc["base"]
    kind = p["beard"]
    if kind == "none":
        return set(), set()
    shapes = b.get("beard_shapes") or {}
    if kind == b.get("beard_kind") and p["beard_size"] == 0:
        base = _set(b["stubble"] if kind == "stubble" else b["beard"])
    else:
        base = _set(shapes.get(kind, []))
    n = int(p["beard_size"])
    if n > 0:  # fuller: out to the sides and down
        for _ in range(n):
            base |= {(x + dx, y + dy) for (x, y) in base for dx, dy in ((-1, 0), (1, 0), (0, 1))}
    elif n < 0:  # trimmed: the cells with a beard neighbour on every side stay
        base = {
            (x, y) for (x, y) in base if {(x - 1, y), (x + 1, y), (x, y + 1), (x, y - 1)} <= base
        } or base
    return (set(), base) if kind == "stubble" else (base, set())


def _eyes(doc: dict) -> tuple[dict, list[tuple[int, int, int, int]]]:
    """The eye cells (role per cell) and each eye's box (x0, y0, x1, y1)."""
    p, b = doc["params"], doc["base"]
    u = b["eye"]["u"]
    out: dict = {}
    boxes = []
    d = int(p["eye_spacing"])
    for name, shift in (("eye_l", -d), ("eye_r", d)):
        cells = [(c[0] + shift, c[1], c[2]) for c in b[name]]
        if not cells:
            continue
        x0, y0 = min(c[0] for c in cells), min(c[1] for c in cells)
        x1, y1 = max(c[0] for c in cells), max(c[1] for c in cells)
        if p["eye_size"] == "big" and (x1 - x0 + 1 < 2 * u or y1 - y0 + 1 < 2 * u):
            x1, y1 = x0 + 2 * u - 1, y0 + 2 * u - 1
            cells = [(x, y, "line") for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]
            cells[0] = (x0, y0, "light")  # the glint
        elif p["eye_size"] == "normal":
            cells = [(x, y, "line") for x in range(x0, x1 + 1) for y in range(y0, y0 + u)]
            y1 = y0 + u - 1
        for x, y, r in cells:
            out[(x, y)] = r
        boxes.append((x0, y0, x1, y1))
    return out, boxes


def _mouth(doc: dict) -> dict:
    p, b = doc["params"], doc["base"]
    cells = {(c[0], c[1]): c[2] for c in b["mouth"]}
    g = b.get("mouth_geom")
    if not g or (p["mouth"] == "measured" and p["mouth_width"] == 0):
        return cells
    u, y, w = g["u"], g["y"], max(1, g["w"] + 2 * int(p["mouth_width"]))
    cx = g["x0"] + (g["w"] - 1) / 2
    x0 = int(round(cx - (w - 1) / 2))
    role = "skin" if g.get("lip") == 1 else "line"
    if p["mouth"] == "measured":  # wider or narrower, the rest as measured
        keep = {(x, yy): r for (x, yy), r in cells.items() if yy != y}
        return {**keep, **{(x0 + i, y + t): role for i in range(w) for t in range(u)}}
    out = {(x0 + i, y + t): role for i in range(w) for t in range(u)}
    if w >= 3 and p["mouth"] in ("smile", "frown"):
        dy = -u if p["mouth"] == "smile" else u
        out[(x0, y + dy)] = "line"
        out[(x0 + w - 1, y + dy)] = "line"
        out.pop((x0, y), None)
        out.pop((x0 + w - 1, y), None)
    return out


def _glasses(kind: str, boxes: list) -> dict:
    if kind == "none" or len(boxes) < 2:
        return {}
    out: dict = {}
    for x0, y0, x1, y1 in boxes:
        X0, Y0, X1, Y1 = x0 - 1, y0 - 1, x1 + 1, y1 + 1
        for x in range(X0, X1 + 1):
            for y in range(Y0, Y1 + 1):
                edge = x in (X0, X1) or y in (Y0, Y1)
                corner = x in (X0, X1) and y in (Y0, Y1)
                if kind == "shades" or edge and not (kind == "round" and corner):
                    out[(x, y)] = "line"
        if kind == "shades":
            out[(X0 + 1, Y0 + 1)] = "light"
    (a, b) = sorted(boxes)[:2]
    for x in range(a[2] + 2, b[0] - 1):  # the bridge
        out[(x, a[1] - 1)] = "line"
    return out


def _earrings(doc: dict, body: set) -> dict:
    if not doc["params"]["earrings"] or not body:
        return {}
    y = doc["base"]["eye"]["y"] + 2 * doc["base"]["eye"]["u"]
    row = [x for (x, yy) in body if yy == y]
    if not row:
        return {}
    return {(min(row) - 1, y + 1): "light", (max(row) + 1, y + 1): "light"}


def _hat(doc: dict, body: set) -> dict:
    kind = doc["params"]["hat"]
    if kind == "none" or not body:
        return {}
    top = min(y for _, y in body)
    u = doc["base"]["eye"]["u"]
    rows = 2 * u if kind == "cap" else 3 * u
    out = {}
    for x, y in body:
        if y < top + rows:
            out[(x, y)] = "hat"
    xs = [x for (x, y) in body if y == top + rows - 1]
    if kind == "cap" and xs:  # the brim, to the right
        for i in range(1, 3 * u + 1):
            out[(max(xs) + i, top + rows - 1)] = "hat"
    above = {(x, top - 1) for (x, y) in out if y == top}  # a beanie is a little taller and rounder
    if kind == "beanie":
        xs2 = sorted(x for x, _ in above)
        for x, y in above:
            if xs2 and xs2[0] < x < xs2[-1]:
                out[(x, y)] = "hat"
    return out


def layers(doc: dict) -> dict[str, dict[tuple[int, int], int]]:
    """Every layer as {(x, y): palette index}, parameters and pixel edits applied, outline recomputed. The order of LAYERS is the drawing order."""
    p, b = doc["params"], doc["base"]
    roles = dict(doc["roles"])
    for key, role in (("skin_color", "skin"), ("hair_color", "hair"), ("hat_color", "hat")):
        if p.get(key) is not None:
            roles[role] = int(p[key])
            if role == "hair" and doc["roles"].get("brow") == doc["roles"].get("hair"):
                roles["brow"] = int(p[key])
    hair = _hair(doc)
    beard, stubble = _beard(doc)
    sil = _set(b["silhouette"])
    skull = _set(b["skull"])
    base_hair, base_beard = _set(b["hair"]), _set(b["beard"]) | _set(b["stubble"])
    skin = (sil - base_hair - base_beard) | skull | (base_beard - beard - stubble)
    if p["hair_style"] == "bald" or p["hair_height"] < 0:
        skin -= (
            base_hair - skull
        )  # the hair that went leaves no skin behind (except over the skull)
    eyes, boxes = _eyes(doc)
    brows = (
        {} if not p["brows"] else {(c[0], c[1] - int(p["brow_raise"])): c[2] for c in b["brows"]}
    )
    nose: dict = {}
    if p["nose"]:
        nose = {(c[0], c[1]): c[2] for c in b["nose"]} or (
            {tuple(b["nose_at"]): "shade"} if b.get("nose_at") else {}
        )
    raw: dict[str, dict] = {
        "skin": {c: "skin" for c in skin},
        "stubble": {c: "shade" for c in stubble},
        "beard": {c: "hair" for c in beard},
        "hair": {c: "hair" for c in hair},
    }
    body_now = set().union(*[set(v) for v in raw.values()])
    raw["hat"] = _hat(doc, body_now)
    raw["eyes"] = eyes
    raw["brows"] = brows
    raw["mouth"] = _mouth(doc)
    raw["nose"] = nose
    raw["glasses"] = _glasses(p["glasses"], boxes)
    raw["earrings"] = _earrings(doc, body_now)
    c = doc["canvas"]
    out: dict[str, dict] = {}
    for name, cells in raw.items():
        m = {
            xy: roles.get(r, roles["line"])
            for xy, r in cells.items()
            if 0 <= xy[0] < c and 0 <= xy[1] < c
        }
        out[name] = _apply_edits(m, doc["edits"].get(name, {}))
    body = set().union(*[set(out[n]) for n in BODY])
    ring = set()
    for x, y in body:
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            q = (x + dx, y + dy)
            if q not in body and 0 <= q[0] < c and 0 <= q[1] < c:
                ring.add(q)
    out["outline"] = _apply_edits({q: roles["line"] for q in ring}, doc["edits"].get("outline", {}))
    return {n: out[n] for n in LAYERS}


def _apply_edits(m: dict, edits: dict) -> dict:
    for key, v in edits.items():
        x, y = (int(t) for t in key.split(","))
        if v is None or int(v) < 0:
            m.pop((x, y), None)
        else:
            m[(x, y)] = int(v)
    return m


def render(doc: dict, hidden: tuple[str, ...] = ()) -> Any:
    """The sprite as an RGBA picture, canvas x canvas, 1 pixel per sprite pixel, transparent background."""
    c = doc["canvas"]
    rgb = [pixel._rgb(h) for h in doc["colors"]]
    a = np.zeros((c, c, 4), dtype=np.uint8)
    for name, cells in layers(doc).items():
        if name in hidden:
            continue
        for (x, y), i in cells.items():
            r, g, bb = rgb[i]
            a[y, x] = (r, g, bb, 255)
    return Image.fromarray(a, "RGBA")


def png(doc: dict, k: int = 1) -> bytes:
    im = render(doc)
    if k > 1:
        im = im.resize((im.width * k, im.height * k), Image.NEAREST)
    out = io.BytesIO()
    im.save(out, "PNG", optimize=True)
    return out.getvalue()


def used_colors(doc: dict) -> list[str]:
    seen: list[int] = []
    for cells in layers(doc).values():
        for i in cells.values():
            if i not in seen:
                seen.append(i)
    return [doc["colors"][i] for i in seen]


# ---- edits in words ------------------------------------------------------------------------------------
def _clamp(key: str, v: int) -> int:
    lo, hi = LIMITS[key]
    return max(lo, min(hi, v))


_RULES: list[tuple[str, Any]] = []


def _rule(pattern: str):
    def deco(fn):
        _RULES.append((pattern, fn))
        return fn

    return deco


@_rule(
    r"\b(?:bigger|longer|fuller|thicker|more)\s+beard\b|\bbeard\s+(?:bigger|longer|fuller)\b|\bgrow (?:the |a )?beard\b"
)
def _w_beard_up(p, m):
    if p["beard"] in ("none", "stubble"):
        p["beard"] = "full"
        return "a full beard"
    p["beard_size"] = _clamp("beard_size", p["beard_size"] + 1)
    return "a bigger beard"


@_rule(
    r"\b(?:smaller|shorter|trim(?:med)?|thinner|less)\s+beard\b|\btrim (?:the )?beard\b|\bbeard\s+(?:smaller|shorter)\b"
)
def _w_beard_down(p, m):
    p["beard_size"] = _clamp("beard_size", p["beard_size"] - 1)
    return "a smaller beard"


@_rule(r"\bno beard\b|\bremove (?:the )?beard\b|\bshave\b|\bclean[- ]shaven\b|\bbeardless\b")
def _w_beard_none(p, m):
    p["beard"], p["beard_size"] = "none", 0
    return "no facial hair"


@_rule(r"\b(goatee|mo?ustache|stubble|full beard)\b")
def _w_beard_kind(p, m):
    w = m.group(1).lower()
    p["beard"] = "mustache" if "ustache" in w else "full" if "full" in w else w
    p["beard_size"] = 0
    return {"full": "a full beard"}.get(p["beard"], "a " + p["beard"])


@_rule(
    r"\b(?:taller|bigger|higher|larger|more)\s+(?:afro|hair)\b|\b(?:afro|hair)\s+(?:taller|bigger|higher)\b"
)
def _w_hair_up(p, m):
    if p["hair_style"] == "bald":
        p["hair_style"] = "measured"
    if "afro" in m.group(0).lower() and p["hair_style"] == "measured":
        p["hair_style"] = "afro"
        return "a bigger afro"
    p["hair_height"] = _clamp("hair_height", p["hair_height"] + 1)
    return "taller hair"


@_rule(
    r"\b(?:shorter|smaller|lower|less)\s+(?:afro|hair)\b|\b(?:afro|hair)\s+(?:shorter|smaller|lower)\b"
)
def _w_hair_down(p, m):
    p["hair_height"] = _clamp("hair_height", p["hair_height"] - 1)
    return "shorter hair"


@_rule(r"\b(bald|short hair|long hair|an afro|afro hair|hair as (?:measured|before))\b")
def _w_hair_style(p, m):
    w = m.group(1).lower()
    p["hair_style"] = (
        "bald"
        if "bald" in w
        else "short"
        if "short" in w
        else "long"
        if "long" in w
        else "afro"
        if "afro" in w
        else "measured"
    )
    p["hair_height"] = 0
    return {
        "bald": "bald",
        "short": "short hair",
        "long": "long hair",
        "afro": "an afro",
        "measured": "the measured hair",
    }[p["hair_style"]]


@_rule(
    r"\b(?:close[- ]?set|closer(?: together)?|narrower)\s+eyes\b|\beyes\s+(?:closer(?: together)?|close[- ]?set)\b"
)
def _w_eyes_in(p, m):
    p["eye_spacing"] = _clamp("eye_spacing", p["eye_spacing"] - 1)
    return "closer-set eyes"


@_rule(
    r"\b(?:wide[- ]?set|wider(?: apart)?|further apart)\s+eyes\b|\beyes\s+(?:wider apart|further apart|wide[- ]?set|wider)\b"
)
def _w_eyes_out(p, m):
    p["eye_spacing"] = _clamp("eye_spacing", p["eye_spacing"] + 1)
    return "wider-set eyes"


@_rule(r"\b(?:big|bigger|large|larger)\s+eyes\b|\beyes\s+(?:bigger|larger)\b")
def _w_eyes_big(p, m):
    p["eye_size"] = "big"
    return "big eyes"


@_rule(r"\b(?:small|smaller|normal)\s+eyes\b|\beyes\s+(?:smaller|normal)\b")
def _w_eyes_small(p, m):
    p["eye_size"] = "normal"
    return "smaller eyes"


@_rule(
    r"\b(?:remove|no|take off|without)\s+(?:the\s+)?(?:sun)?glasses\b|\bglasses off\b|\bno shades\b"
)
def _w_glasses_off(p, m):
    p["glasses"] = "none"
    return "no glasses"


@_rule(r"\b(?:sunglasses|shades)\b")
def _w_shades(p, m):
    p["glasses"] = "shades"
    return "sunglasses"


@_rule(r"\b(round|square)?\s*glasses\b")
def _w_glasses(p, m):
    if p["glasses"] == "none" or m.group(1):
        p["glasses"] = (m.group(1) or "square").lower()
    return f"{p['glasses']} glasses"


@_rule(r"\b(?:remove|no|take off|without)\s+(?:the\s+)?earrings?\b")
def _w_earrings_off(p, m):
    p["earrings"] = False
    return "no earrings"


@_rule(r"\bearrings?\b")
def _w_earrings(p, m):
    p["earrings"] = True
    return "earrings"


@_rule(r"\b(?:remove|no|take off|without)\s+(?:the\s+)?(?:hat|cap|beanie)\b")
def _w_hat_off(p, m):
    p["hat"] = "none"
    return "no hat"


@_rule(r"\b(cap|beanie|hat)\b")
def _w_hat(p, m):
    w = m.group(1).lower()
    p["hat"] = "beanie" if w == "beanie" else "cap"
    return "a " + p["hat"]


@_rule(r"\b(smile|smiling|frown|frowning|neutral mouth|straight mouth)\b")
def _w_mood(p, m):
    w = m.group(1).lower()
    p["mouth"] = (
        "smile" if w.startswith("smil") else "frown" if w.startswith("frown") else "neutral"
    )
    return {"smile": "a smile", "frown": "a frown", "neutral": "a straight mouth"}[p["mouth"]]


@_rule(r"\b(?:wider|bigger)\s+mouth\b|\bmouth\s+wider\b")
def _w_mouth_wide(p, m):
    p["mouth_width"] = _clamp("mouth_width", p["mouth_width"] + 1)
    return "a wider mouth"


@_rule(r"\b(?:narrower|smaller)\s+mouth\b|\bmouth\s+narrower\b")
def _w_mouth_narrow(p, m):
    p["mouth_width"] = _clamp("mouth_width", p["mouth_width"] - 1)
    return "a narrower mouth"


@_rule(r"\b(?:remove|no|without)\s+(?:the\s+)?nose\b")
def _w_nose_off(p, m):
    p["nose"] = False
    return "no nose"


@_rule(r"\b(?:add|with|show)\s+(?:a\s+|the\s+)?nose\b")
def _w_nose_on(p, m):
    p["nose"] = True
    return "a nose"


@_rule(r"\b(?:remove|no|without|hide)\s+(?:the\s+)?(?:eye)?brows\b")
def _w_brows_off(p, m):
    p["brows"] = False
    return "no brows"


@_rule(r"\b(?:raise|raised|higher)\s+(?:the\s+)?(?:eye)?brows\b")
def _w_brows_up(p, m):
    p["brows"] = True
    p["brow_raise"] = _clamp("brow_raise", p["brow_raise"] + 1)
    return "raised brows"


@_rule(r"\b(?:add|show|with)\s+(?:the\s+)?(?:eye)?brows\b")
def _w_brows_on(p, m):
    p["brows"] = True
    return "brows"


def _shift_role(doc: dict, key: str, role: str, step: int) -> str:
    """Hair or skin a shade lighter or darker, inside the locked palette (by lightness)."""
    cols = [pixel._rgb(h) for h in doc["colors"]]
    order = sorted(
        range(len(cols)), key=lambda i: 0.299 * cols[i][0] + 0.587 * cols[i][1] + 0.114 * cols[i][2]
    )
    cur = doc["params"].get(key)
    cur = doc["roles"][role] if cur is None else cur
    avoid = {
        doc["roles"]["line"],
        doc["roles"]["skin"] if role == "hair" else doc["roles"].get("hair"),
    }
    j = order.index(cur)
    while (
        0 <= j + step < len(order)
    ):  # the next shade that is not the outline or the other part's colour
        j += step
        if order[j] not in avoid:
            break
    else:
        return f"{role} is already as {'light' if step > 0 else 'dark'} as this palette allows"
    doc["params"][key] = order[j]
    return f"{role} a shade {'lighter' if step > 0 else 'darker'}"


def apply_words(doc: dict, text: str) -> list[str]:
    """Every edit the words ask for, applied to doc["params"] in place. Returns what changed, in words ([] when nothing matched)."""
    t = (text or "").lower()
    done: list[str] = []
    taken: list[tuple[int, int]] = []
    for pattern, fn in _RULES:
        for m in re.finditer(pattern, t):
            if any(a < m.end() and m.start() < b for a, b in taken):
                continue  # a longer phrase already used these words ("no glasses" is not also "glasses")
            taken.append((m.start(), m.end()))
            done.append(fn(doc["params"], m))
    for part, key, role in (("hair", "hair_color", "hair"), ("skin", "skin_color", "skin")):
        m = re.search(rf"\b(lighter|darker)\s+{part}\b|\b{part}\s+(lighter|darker)\b", t)
        if m:
            done.append(_shift_role(doc, key, role, 1 if "lighter" in m.group(0) else -1))
    return done


EDIT_HELP = (
    "bigger or smaller beard, goatee, mustache, stubble or no beard; taller or shorter hair, afro, short or long hair, bald; "
    "close-set or wider eyes, big eyes; add or remove glasses, sunglasses, earrings, a cap or a beanie; smile or frown; "
    "a nose; no brows; lighter or darker hair or skin; undo, redo"
)


# ---- the session: the current sprite, its card, undo and redo ------------------------------------------
class Studio:
    """The sprites of this session (in memory only, the last 20), which one is being edited, and its undo / redo history."""

    def __init__(self) -> None:
        self.docs: dict[str, dict] = {}
        self.cards: dict[str, str] = {}  # sprite id -> panel card id
        self.current = ""
        self.undo: dict[str, list] = {}
        self.redo: dict[str, list] = {}

    def add(self, doc: dict, card: str = "") -> None:
        self.docs[doc["id"]] = doc
        while len(self.docs) > 20:
            old = next(iter(self.docs))
            self.docs.pop(old)
            self.cards.pop(old, None)
        if card:
            self.cards[doc["id"]] = card
        self.current = doc["id"]

    def get(self, sid: str = "") -> dict | None:
        return self.docs.get(sid or self.current)

    @staticmethod
    def _snap(doc: dict) -> tuple[dict, dict]:
        return copy.deepcopy(doc["params"]), copy.deepcopy(doc["edits"])

    def checkpoint(self, doc: dict) -> None:
        h = self.undo.setdefault(doc["id"], [])
        h.append(self._snap(doc))
        del h[:-MAX_HISTORY]
        self.redo[doc["id"]] = []

    def step(self, doc: dict, back: bool) -> bool:
        src, dst = (self.undo, self.redo) if back else (self.redo, self.undo)
        stack = src.get(doc["id"]) or []
        if not stack:
            return False
        dst.setdefault(doc["id"], []).append(self._snap(doc))
        doc["params"], doc["edits"] = stack.pop()
        return True


STUDIO = Studio()


# ---- files ----------------------------------------------------------------------------------------------
def dumps(doc: dict) -> str:
    return json.dumps(doc, ensure_ascii=False, separators=(",", ":"))


def loads(text: str) -> dict:
    """A .julius-sprite file back into a document, checked: right format, known palette, cells inside the canvas, edits palette-locked."""
    try:
        doc = json.loads(text)
    except ValueError as e:
        raise DocProblem("That sprite file is not readable.") from e
    if (
        not isinstance(doc, dict)
        or doc.get("format") != FORMAT
        or int(doc.get("version", 0)) > VERSION
    ):
        raise DocProblem("That is not a Julius sprite file.")
    if doc.get("palette") not in pixel.PALETTES or doc.get("colors") != [
        c.upper() for c in pixel.PALETTES[doc["palette"]]
    ]:
        raise DocProblem("That sprite file uses a palette I don't have.")
    c = int(doc.get("canvas", 0))
    if c not in (32, 48, 64):
        raise DocProblem("That sprite file has an odd size.")
    n = len(doc["colors"])
    for key in ("skull", "silhouette", "hair", "stubble", "beard"):
        for cell in doc["base"].get(key, []):
            if not (0 <= cell[0] < c and 0 <= cell[1] < c):
                raise DocProblem("That sprite file has pixels outside its canvas.")
    doc["edits"] = clean_edits(doc.get("edits") or {}, c, n)
    defaults = default_params(doc["base"], type("O", (), {"big_eyes": False, "nose": False})())
    doc["params"] = {
        **defaults,
        **{k: v for k, v in (doc.get("params") or {}).items() if k in defaults},
    }
    doc.setdefault("id", secrets.token_hex(4))
    return doc


def clean_edits(edits: Any, canvas: int, colors: int) -> dict:
    """Pixel edits from the editor or a file, checked: known layers, cells inside the canvas, palette indices only (-1 = erase)."""
    if not isinstance(edits, dict):
        raise DocProblem("Those edits are not readable.")
    out: dict[str, dict] = {}
    total = 0
    for layer, cells in edits.items():
        if layer not in LAYERS or not isinstance(cells, dict):
            raise DocProblem(f"There is no layer called {str(layer)[:20]}.")
        m: dict[str, int] = {}
        for key, v in cells.items():
            mm = re.fullmatch(r"(\d{1,3}),(\d{1,3})", str(key))
            if not mm or not (0 <= int(mm.group(1)) < canvas and 0 <= int(mm.group(2)) < canvas):
                raise DocProblem("An edit is outside the canvas.")
            iv = int(v) if v is not None else -1
            if not -1 <= iv < colors:
                raise DocProblem("An edit uses a colour that is not in the palette.")
            m[f"{int(mm.group(1))},{int(mm.group(2))}"] = iv
        total += len(m)
        if m:
            out[layer] = m
    if total > MAX_EDITS:
        raise DocProblem("That is too many edits at once.")
    return out


def editor_view(doc: dict) -> dict:
    """What the editor window needs: the canvas, the locked palette, each layer as rendered from the parameters BEFORE its pixel edits, and the edits."""
    plain = copy.deepcopy(doc)
    plain["edits"] = {}
    lay = layers(plain)
    return {
        "id": doc["id"],
        "canvas": doc["canvas"],
        "palette": doc["colors"],
        "layers": [
            {"name": n, "cells": [[x, y, i] for (x, y), i in sorted(cells.items())]}
            for n, cells in lay.items()
        ],
        "edits": doc["edits"],
        "body": list(BODY),
        "line": doc["roles"]["line"],
    }
