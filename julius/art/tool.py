"""The `pixel_art` tool: open the picture the user means, make pixel art from it, show a before/after card, return the facts for Julius to say."""

from __future__ import annotations

import io
import re
from typing import Any

from julius import log
from julius.art import faces, pixel, source

BEFORE_SIDE = 640  # the "before" picture on the card is a downsized copy, kept in memory only
PREVIEW_SIDE = 1024  # the card shows the art at this size at most; the saved file uses the zoom the user asked for
PORTRAIT_SIZE, PORTRAIT_MAX = (
    64,
    128,
)  # a portrait is 64 px wide unless asked otherwise, and never more than 128
DEFAULT_SIZE = 48
COMPARE_SIZES = (32, 48, 64)
TILE = 192  # each tile of the comparison grid is about this many screen pixels on its long side


def _before_png(img: Any) -> bytes:
    im = img.copy()
    im.thumbnail((BEFORE_SIDE, BEFORE_SIDE))
    out = io.BytesIO()
    im.save(out, "PNG", optimize=True)
    return out.getvalue()


def _card(res: pixel.Result, before: Any, label: str, scale: int, crop: str = "") -> bool:
    from julius.ui_bridge import charts

    if charts._panel is None:
        return False
    panel, files, turn_fn = charts._panel
    if files is None:
        return False
    k = scale
    while k > 1 and max(res.width, res.height) * k > PREVIEW_SIDE:
        k //= 2
    shown, _ = res.png(k)
    export, used = res.png(scale)
    s = res.settings
    card = {
        "title": f"Pixel art: {label}",
        "kind": "pixel",
        "before": files.put_memory(_before_png(before), "png", keep=16),
        "after": files.put_memory(shown, "png", keep=16),
        "export": files.put_memory(export, "png", keep=16),
        "small": files.put_memory(res.png(1)[0], "png", keep=16),
        "alt": f"{res.width} by {res.height} pixel art of {label}",
        "summary": f"{res.width}×{res.height} px, {len(res.palette)} colours, saved at {used}×",
        "settings": [
            f"Size: {s.size} px on the long side",
            f"Palette: {s.palette or str(s.colors) + ' colours from the picture'}",
            f"Outline: {'on' if s.outline else 'off'}",
            f"Dither: {'on' if s.dither else 'off'}",
            *([f"Crop: {crop}"] if crop else []),
            f"Background: {'transparent' if res.has_alpha else 'kept'}",
            f"Export: {used}× nearest neighbour",
        ],
        "palette": res.palette[:64],
    }
    return panel.art(card, turn=turn_fn()) is not None


def _grid(img: Any, base: pixel.Settings, focus: Any = None) -> tuple[bytes, list[str]]:
    """The comparison grid: the same picture at 32, 48 and 64 px (columns); rows: smooth, ordered dither, retro sprite, each tile labelled. PNG bytes and the labels."""
    import copy

    from PIL import Image, ImageDraw, ImageFont

    try:
        font = ImageFont.load_default(size=13)
    except TypeError:  # an old Pillow
        font = ImageFont.load_default()
    pad, label_h = 10, 22
    tiles = []
    for name, dither, retro in (
        ("smooth", False, False),
        ("dither", True, False),
        ("retro sprite", False, True),
    ):
        for size in COMPARE_SIZES:
            st = copy.copy(base)
            st.size, st.dither, st.retro = size, dither, retro
            r = pixel.render(img, st, focus)
            k = max(1, TILE // max(r.width, r.height))
            im, _ = r.scaled(k)
            tiles.append((im.convert("RGB"), f"{size} px, {name}"))
    cell_w = max(t[0].width for t in tiles)
    cell_h = max(t[0].height for t in tiles) + label_h
    cols = len(COMPARE_SIZES)
    rows = len(tiles) // cols
    sheet = Image.new(
        "RGB", (cols * (cell_w + pad) + pad, rows * (cell_h + pad) + pad), (245, 245, 245)
    )
    draw = ImageDraw.Draw(sheet)
    for i, (im, label) in enumerate(tiles):
        x = pad + (i % cols) * (cell_w + pad)
        y = pad + (i // cols) * (cell_h + pad)
        sheet.paste(im, (x + (cell_w - im.width) // 2, y))
        draw.text((x + 2, y + im.height + 4), label, fill=(40, 40, 40), font=font)
    out = io.BytesIO()
    sheet.save(out, "PNG", optimize=True)
    return out.getvalue(), [t[1] for t in tiles]


def _compare_card(
    grid: bytes, before: Any, label: str, labels: list[str], st: pixel.Settings, crop: str
) -> bool:
    from julius.ui_bridge import charts

    if charts._panel is None:
        return False
    panel, files, turn_fn = charts._panel
    if files is None:
        return False
    ref = files.put_memory(grid, "png", keep=16)
    card = {
        "title": f"Pixel art options: {label}",
        "kind": "compare",
        "before": files.put_memory(_before_png(before), "png", keep=16),
        "after": ref,
        "export": ref,
        "small": ref,
        "alt": "Nine pixel art versions: 32, 48 and 64 pixels, smooth, dithered and retro sprite",
        "summary": "32 / 48 / 64 px; rows: smooth, dither, retro sprite (outline, flat tones)",
        "settings": [
            f"Palette: {st.palette or str(st.colors) + ' colours from the picture'}",
            f"Outline: {'on' if st.outline else 'off'}",
            f"Crop: {crop}",
        ],
        "palette": [c for c in (pixel.PALETTES.get(st.palette) or [])][:64],
    }
    return panel.art(card, turn=turn_fn()) is not None


def pixel_art(
    source_name: str = "",
    size: int = 0,
    colors: int = 16,
    palette: str = "",
    outline: bool = False,
    transparent: bool = False,
    scale: int = 4,
    dither: bool = False,
    full: bool = False,
    compare: bool = False,
    retro: bool = False,
) -> str:
    """size 0 means the default: 64 for a portrait (a face was found and the picture cropped to head and shoulders), 48 otherwise."""
    try:
        img, label, fname = source.load(source_name)
        portrait, crop, focus = False, "full photo" if full else "no face found, full photo", None
        work = img
        if not full:
            work, portrait, focus = faces.portrait_crop(img)
            if portrait:
                crop = "head and shoulders"
        if compare:
            base = pixel.Settings(
                64, colors, palette or "gameboy", outline, transparent, scale, dither
            ).clean()
            grid, labels = _grid(work, base, focus)
            shown = False
            try:
                shown = _compare_card(grid, work, label, labels, base, crop)
            except Exception as exc:
                log.event("pixel_art_card_failed", error=type(exc).__name__)
            log.event("pixel_art_compare", portrait=portrait)
            return (
                f"Comparison for {fname} ({crop}): {base.palette} palette at 32, 48 and 64 pixels, "
                "rows: smooth, dithered, and retro sprite with flat tones and an outline. "
                + (
                    "It's in the side panel. Tell me which one, for example 48 pixels, retro."
                    if shown
                    else "No side panel is open, so there is nothing to show."
                )
            )
        want = int(size or 0) or (PORTRAIT_SIZE if portrait else DEFAULT_SIZE)
        if portrait:
            want = min(want, PORTRAIT_MAX)
        res = pixel.render(
            work,
            pixel.Settings(want, colors, palette, outline, transparent, scale, dither, retro),
            focus,
        )
    except (source.ArtProblem, pixel.PixelProblem) as exc:
        return str(exc)
    except Exception as exc:
        log.event("pixel_art_failed", error=type(exc).__name__)
        return "I couldn't make pixel art from that."
    shown = False
    try:
        shown = _card(res, work, label, res.settings.scale, crop)
    except Exception as exc:
        log.event("pixel_art_card_failed", error=type(exc).__name__)
    log.event(
        "pixel_art",
        w=res.width,
        h=res.height,
        colors=len(res.palette),
        alpha=res.has_alpha,
        portrait=portrait,
    )
    s = res.settings
    bits = [
        f"Pixel art from {fname}"
        + (" (cropped to head and shoulders)" if portrait else "")
        + f": {res.width} by {res.height} pixels, {len(res.palette)} colours"
        + (f" ({s.palette} palette)" if s.palette else "")
        + (", outlined" if s.outline else "")
        + (", dithered" if s.dither else "")
        + (", transparent background" if res.has_alpha else "")
        + f", exported at {s.scale}× so it stays crisp."
    ]
    bits.append(
        "The before and after are in the side panel; say save this card to keep the PNG in your saved folder."
        if shown
        else "No side panel is open, so there is nothing to save yet."
    )
    return " ".join(bits)


_NUM = re.compile(r"(\d{1,3})\s*(?:px|pixels?|pixel)\b|\b(?:size|width)\D{0,3}(\d{1,3})\b")
_COLORS = re.compile(r"(\d{1,2})\s*(?:colou?rs?|shades)\b|\bcolou?rs?\D{0,3}(\d{1,2})\b")
_SCALE = re.compile(r"\b(\d)\s*(?:x|times)\b|\b(?:zoom|scale|export)\D{0,3}(\d)\b")


def parse_options(text: str) -> dict[str, Any]:
    """The wishes as words ("32 pixels, 8 colors, gameboy palette, outline, transparent, 4x") to pixel_art settings. Anything not mentioned keeps its default."""
    t = (text or "").casefold()
    out: dict[str, Any] = {}
    if m := _NUM.search(t):
        out["size"] = int(next(g for g in m.groups() if g))
    if m := _COLORS.search(t):
        out["colors"] = int(next(g for g in m.groups() if g))
    if m := _SCALE.search(t):
        out["scale"] = int(next(g for g in m.groups() if g))
    names = list(pixel.PALETTES) + list(pixel.ALIASES)
    for n in sorted(names, key=len, reverse=True):
        if re.search(rf"(?<![a-z0-9]){re.escape(n)}(?![a-z0-9])", t):
            out["palette"] = pixel.ALIASES.get(n, n)
            break
    out["outline"] = bool(re.search(r"outline|outlined|border", t)) and not re.search(
        r"no outline|without (?:an )?outline", t
    )
    out["dither"] = bool(re.search(r"\bdither(?:ed|ing)?\b(?! (?:off|no))", t)) and not re.search(
        r"no dither|without dither|dither(?:ing)? off", t
    )
    out["full"] = bool(
        re.search(
            r"\b(?:full|whole|entire) (?:photo|picture|image|frame)\b|\bno crop\b|\bdon'?t crop\b",
            t,
        )
    )
    out["retro"] = bool(
        re.search(r"\bretro\b|\bsprite\b|\bchunky\b|\bgame ?boy (?:game|sprite)\b", t)
    )
    out["compare"] = bool(re.search(r"\bcompar(?:e|ison|ing)\b|\bcomparison grid\b|\boptions\b", t))
    out["transparent"] = bool(
        re.search(r"transparen|no background|cut ?out|remove (?:the )?background", t)
    ) and not re.search(r"not transparent|opaque", t)
    return out


def pixel_art_from_words(source_name: str = "", options: str = "") -> str:
    return pixel_art(source_name, **parse_options(options))


STYLES = ("pixel_art",)  # step C adds the photo filters here


def style_name(text: str) -> str:
    """The style the model named, in whatever words ("pixel art", "8-bit", "retro sprite", "game boy"), as one of STYLES, or "" when there is no such style yet."""
    t = re.sub(r"[^a-z0-9]+", " ", (text or "pixel_art").casefold()).strip()
    if not t or re.search(r"pixel|\b(?:8|16)\s?bit\b|sprite|retro|game ?boy|\bgb\b", t):
        return "pixel_art"
    return ""


def image_style(
    source_name: str = "",
    style: str = "pixel_art",
    size: int = 0,
    colors: int = 16,
    palette: str = "",
    outline: bool = False,
    transparent: bool = False,
    zoom: int = 4,
    dither: bool = False,
    full_photo: bool = False,
    compare: bool = False,
    retro: bool = False,
) -> str:
    """Restyle the user's own picture. Never creates art from scratch: with no picture named it asks for one."""
    chosen = style_name(style)
    if not chosen:
        return (
            f"I can only do these styles so far: {', '.join(s.replace('_', ' ') for s in STYLES)}."
        )
    # no picture named: the one dropped on the orb, or a question asking for one (nothing is ever drawn from nothing)
    return pixel_art(
        (source_name or "").strip() or "dropped",
        size,
        colors,
        palette,
        outline,
        transparent,
        zoom,
        dither,
        full_photo,
        compare,
        retro,
    )
