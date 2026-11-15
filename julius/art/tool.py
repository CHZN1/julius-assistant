"""The `pixel_art` tool: open the picture the user means, make pixel art from it, show a before/after card, return the facts for Julius to say."""

from __future__ import annotations

import io
import re
from typing import Any

from julius import log
from julius.art import pixel, source

BEFORE_SIDE = 640  # the "before" picture on the card is a downsized copy, kept in memory only
PREVIEW_SIDE = 1024  # the card shows the art at this size at most; the saved file uses the zoom the user asked for


def _before_png(img: Any) -> bytes:
    im = img.copy()
    im.thumbnail((BEFORE_SIDE, BEFORE_SIDE))
    out = io.BytesIO()
    im.save(out, "PNG", optimize=True)
    return out.getvalue()


def _card(res: pixel.Result, before: Any, label: str, scale: int) -> bool:
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
            f"Background: {'transparent' if res.has_alpha else 'kept'}",
            f"Export: {used}× nearest neighbour",
        ],
        "palette": res.palette[:64],
    }
    return panel.art(card, turn=turn_fn()) is not None


def pixel_art(
    source_name: str = "",
    size: int = 48,
    colors: int = 16,
    palette: str = "",
    outline: bool = False,
    transparent: bool = False,
    scale: int = 4,
) -> str:
    try:
        path, _dropped = source.resolve(source_name)
        img = source.open_image(path)
        res = pixel.render(img, pixel.Settings(size, colors, palette, outline, transparent, scale))
    except (source.ArtProblem, pixel.PixelProblem) as exc:
        return str(exc)
    except Exception as exc:
        log.event("pixel_art_failed", error=type(exc).__name__)
        return "I couldn't make pixel art from that."
    shown = False
    try:
        shown = _card(res, img, path.stem, res.settings.scale)
    except Exception as exc:
        log.event("pixel_art_card_failed", error=type(exc).__name__)
    log.event("pixel_art", w=res.width, h=res.height, colors=len(res.palette), alpha=res.has_alpha)
    s = res.settings
    bits = [
        f"Pixel art from {path.name}: {res.width} by {res.height} pixels, {len(res.palette)} colours"
        + (f" ({s.palette} palette)" if s.palette else "")
        + (", outlined" if s.outline else "")
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
    out["transparent"] = bool(
        re.search(r"transparen|no background|cut ?out|remove (?:the )?background", t)
    ) and not re.search(r"not transparent|opaque", t)
    return out


def pixel_art_from_words(source_name: str = "", options: str = "") -> str:
    return pixel_art(source_name, **parse_options(options))
