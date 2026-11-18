"""The `pixel_art` tool: open the picture the user means, make pixel art from it, show a before/after card, return the facts for Julius to say."""

from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any

from julius import log
from julius.art import faces, filters, pixel, source, sprite, sprite_doc

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


def _hexgrid(im: Any, w: int, h: int) -> str:
    """A picture as w*h pixels of 6 hex digits ("------" where it is clear)."""
    px = (
        im.convert("RGBA").resize((w, h), 0).getdata()
    )  # nearest neighbour: sprite pixels stay exact
    return "".join("------" if a < 128 else f"{r:02X}{g:02X}{b:02X}" for r, g, b, a in px)


def reveal(photo: Any | None, sprite_png: bytes) -> list | None:
    """The sprite card's sand reveal: the photo and the sprite as small grids, so the page can dissolve one into grains that settle as the other
    (the page cannot read pixels from the panel file server). None when the sprite is larger than the grid allows."""
    from PIL import Image

    try:
        spr = Image.open(io.BytesIO(sprite_png))
        w, h = spr.size
        if w > 64 or h > 64:
            return None
        canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        if photo is not None:
            ph = photo.copy().convert("RGB")
            ph.thumbnail((w, h))
            canvas.paste(ph, ((w - ph.width) // 2, (h - ph.height) // 2))
        return [w, h, _hexgrid(canvas, w, h), _hexgrid(spr, w, h)]
    except Exception:  # the reveal is decoration: a card without one is still a card
        return None


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
        "before": files.put_memory(_before_png(before), "png"),
        "after": files.put_memory(shown, "png"),
        "export": files.put_memory(export, "png"),
        "small": files.put_memory(res.png(1)[0], "png"),
        "alt": f"{res.width} by {res.height} pixel art of {label}",
        "summary": f"{res.width}×{res.height} px, {len(res.palette)} colours, saved at {used}×",
        "settings": [
            f"Size: {s.size} px on the long side",
            f"Palette: {s.palette or str(s.colors) + ' colours from the picture'}",
            f"Outline: {'on' if s.outline else 'off'}",
            f"Dither: {'on' if s.dither else 'off'}",
            f"Look: {'retro sprite' if s.retro else 'smooth'}",
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
    ref = files.put_memory(grid, "png")
    card = {
        "title": f"Pixel art options: {label}",
        "kind": "compare",
        "before": files.put_memory(_before_png(before), "png"),
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
    retro: bool | None = None,
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
        if (
            retro is None
        ):  # a portrait defaults to the retro sprite look (flat tones, despeckle, bold outline); "smooth" turns it off
            retro = portrait
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
    from julius.brain import session_state

    session_state.record("pixel_art", f"made pixel art from {label}")
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
        + (", retro sprite look" if s.retro else "")
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
    if re.search(r"\bsmooth\b|\bdetailed\b|\bno retro\b|\bnot retro\b|\bphoto ?like\b", t):
        out["retro"] = False  # asked for the plain look
    elif re.search(r"\bretro\b|\bsprite\b|\bchunky\b|\bgame ?boy (?:game|sprite)\b", t):
        out["retro"] = True  # otherwise left out: a portrait gets the retro look by default
    out["compare"] = bool(re.search(r"\bcompar(?:e|ison|ing)\b|\bcomparison grid\b|\boptions\b", t))
    out["transparent"] = bool(
        re.search(r"transparen|no background|cut ?out|remove (?:the )?background", t)
    ) and not re.search(r"not transparent|opaque", t)
    return out


def pixel_art_from_words(source_name: str = "", options: str = "") -> str:
    return pixel_art(source_name, **parse_options(options))


STYLES = ("pixel_art", "sprite", *filters.STYLES)

_STYLE_WORDS = (  # first match wins. "pixel art" and "pixelate" are the photo filter; a "sprite" or a game character is built from the face
    ("pixel_art", r"pixel"),
    (
        "sprite",
        r"sprite|\bavatar\b|(?:game ?boy|\bgb\b|retro|video ?game|\b(?:8|16)\s?bit|game)\s+(?:character|avatar|guy|hero|person)|\bcharacter\b",
    ),
    ("pixel_art", r"\b(?:8|16)\s?bit\b|retro|game ?boy|\bgb\b"),
    ("all_styles", r"\ball\b|\bevery\b|\beach\b|\bfive\b|side by side|\bstyles\b"),
    ("pop_art", r"pop ?art|warhol"),
    ("comic", r"comic|halftone|half tone|graphic novel|manga|\bink(?:ed)?\b"),
    ("pencil", r"pencil|sketch|charcoal|line drawing"),
    ("watercolor", r"water ?colou?r|gouache|\bwash\b"),
    ("cel_shade", r"\bcel\b|cel[- ]?shad|toon|cartoon|animated|anime"),
)


def style_name(text: str) -> str:
    """The style the model named, in whatever words ("pixel art", "8-bit", "comic book", "pencil sketch"), as one of STYLES, or "" when there is no such style."""
    t = re.sub(r"[^a-z0-9]+", " ", (text or "pixel_art").casefold()).strip()
    if not t:
        return "pixel_art"
    for name, pattern in _STYLE_WORDS:
        if re.search(pattern, t):
            return name
    return ""


def _filter_card(res: Any, before: Any, label: str, s: filters.FilterSettings) -> bool:
    from julius.ui_bridge import charts

    if charts._panel is None:
        return False
    panel, files, turn_fn = charts._panel
    if files is None:
        return False
    shown = filters.png(res)
    ref = files.put_memory(shown, "png")
    lines = [
        f"Style: {filters.LABELS[s.style]}",
        f"Line weight: {s.line}",
        f"Smoothing: {s.smooth}",
    ]
    if s.colors:
        lines.insert(1, f"Colours: {s.colors}")
    card = {
        "title": f"{filters.LABELS[s.style]}: {label}",
        "kind": "filter",
        "before": files.put_memory(_before_png(before), "png"),
        "after": ref,
        "export": ref,
        "small": ref,
        "alt": f"{filters.LABELS[s.style]} version of {label}",
        "summary": f"{res.width}×{res.height} px, {filters.LABELS[s.style].lower()}",
        "settings": lines,
        "palette": [],
    }
    return panel.art(card, turn=turn_fn()) is not None


def _style_grid(img: Any) -> tuple[bytes, Any]:
    """The original and all five styles at their defaults, each labelled, in one picture (3 columns, 2 rows)."""
    from PIL import Image, ImageDraw, ImageFont

    try:
        font = ImageFont.load_default(size=14)
    except TypeError:
        font = ImageFont.load_default()
    tile, pad, label_h = 330, 10, 24
    base = img.convert("RGBA")
    flat = Image.new("RGB", base.size, (255, 255, 255))
    flat.paste(base, mask=base.getchannel("A"))
    tiles = [(flat, "Original")]
    for st in filters.STYLES:
        res, _ = filters.apply(img, filters.FilterSettings(st))
        tiles.append((res, filters.LABELS[st]))
    cols = 3
    rows = (len(tiles) + cols - 1) // cols
    shrunk = []
    for im, name in tiles:
        im = im.copy()
        im.thumbnail((tile, tile))
        shrunk.append((im, name))
    cell_h = max(im.height for im, _ in shrunk) + label_h
    sheet = Image.new(
        "RGB", (cols * (tile + pad) + pad, rows * (cell_h + pad) + pad), (245, 245, 245)
    )
    draw = ImageDraw.Draw(sheet)
    for i, (im, name) in enumerate(shrunk):
        x = pad + (i % cols) * (tile + pad)
        y = pad + (i // cols) * (cell_h + pad)
        sheet.paste(im, (x + (tile - im.width) // 2, y))
        draw.text((x + 2, y + im.height + 4), name, fill=(40, 40, 40), font=font)
    return filters.png(sheet), sheet


def filter_compare(source_name: str = "dropped") -> str:
    """All five styles on one card, next to the original, so they can be judged in one look."""
    from julius.ui_bridge import charts

    try:
        img, label, fname = source.load(source_name)
        data, sheet = _style_grid(img)
    except (source.ArtProblem, pixel.PixelProblem) as exc:
        return str(exc)
    except Exception as exc:
        log.event("filter_compare_failed", error=type(exc).__name__)
        return "I couldn't restyle that."
    shown = False
    try:
        if charts._panel is not None and charts._panel[1] is not None:
            panel, files, turn_fn = charts._panel
            ref = files.put_memory(data, "png")
            card = {
                "title": f"All five styles: {label}",
                "kind": "compare",
                "before": files.put_memory(_before_png(img), "png"),
                "after": ref,
                "export": ref,
                "small": ref,
                "alt": "The original and five restyled versions: cel-shade, comic ink, pencil sketch, pop art, watercolor",
                "summary": "original, cel-shade, comic ink, pencil sketch, pop art, watercolor (each at its defaults)",
                "settings": ["Each style at its own default colours, line weight and smoothing"],
                "palette": [],
            }
            shown = panel.art(card, turn=turn_fn()) is not None
    except Exception as exc:
        log.event("filter_compare_card_failed", error=type(exc).__name__)
    log.event("image_filter_compare")
    return (
        f"All five styles for {fname}: cel-shade, comic ink, pencil sketch, pop art and watercolor, next to the original. "
        + (
            "It's in the side panel. Name one to make it bigger, e.g. the pencil sketch with thicker lines."
            if shown
            else "No side panel is open, so there is nothing to show."
        )
    )


def filter_style(
    source_name: str = "",
    style: str = "cel_shade",
    colors: int = 0,
    line: int = 0,
    smooth: int = -1,
) -> str:
    """One of the photo filters on the user's picture: loaded, restyled on this computer, shown as a before/after card."""
    try:
        img, label, fname = source.load(source_name)
        res, s = filters.apply(img, filters.FilterSettings(style, colors, line, smooth))
    except (source.ArtProblem, pixel.PixelProblem) as exc:
        return str(exc)
    except Exception as exc:
        log.event("filter_failed", error=type(exc).__name__)
        return "I couldn't restyle that."
    shown = False
    try:
        shown = _filter_card(res, img, label, s)
    except Exception as exc:
        log.event("filter_card_failed", error=type(exc).__name__)
    from julius.brain import session_state

    session_state.record("filter", f"made a {filters.LABELS[s.style].lower()} of {label}")
    log.event("image_filter", style=s.style, w=res.width, h=res.height)
    return (
        f"{filters.LABELS[s.style]} version of {fname}: {res.width} by {res.height} pixels"
        + (f", {s.colors} colours" if s.colors else "")
        + f", line weight {s.line}, smoothing {s.smooth}. "
        + (
            "The before and after are in the side panel; say save this card to keep the PNG in your saved folder."
            if shown
            else "No side panel is open, so there is nothing to save yet."
        )
    )


SPRITE_SHOW = (
    8  # the sprite is shown at this zoom (4x for a 32 pixel head: the card stays one size)
)


def _sprite_card(
    sp: Any, crop: Any, compared: bytes, label: str, st: pixel.Result | None, note: str
) -> bool:
    from julius.ui_bridge import charts

    if charts._panel is None:
        return False
    panel, files, turn_fn = charts._panel
    if files is None:
        return False
    k = max(1, min(SPRITE_SHOW, 256 // sp.canvas))
    o = sp.options
    shown = files.put_memory(sp.png(k), "png")
    card = {
        "title": f"Sprite: {label}",
        "kind": "sprite",
        "before": files.put_memory(_before_png(crop), "png"),
        "after": shown,
        "compare": files.put_memory(compared, "png") if compared else "",
        "export": files.put_memory(sp.png(8), "png"),
        "small": files.put_memory(sp.png(1), "png"),
        "alt": f"{o.head} pixel head sprite of {label}",
        "summary": f"{o.head}×{o.head} head on a {sp.canvas}×{sp.canvas} canvas, {len(sp.palette)} colours, built from your face's measurements",
        "settings": [
            f"Head: {o.head} px on a {sp.canvas} px canvas",
            f"Palette: {o.palette}",
            f"Proportions: {'chibi' if o.chibi else 'normal'}",
            f"Big eyes: {'on' if o.big_eyes else 'off'}",
            f"Nose: {'on' if o.nose else 'off'}",
            f"Facial hair: {sprite.BEARD_LABELS[sp.facial_hair]} ({'measured' if sp.facial_hair_measured else 'as asked'})",
            "Outline: 1 px, darkest shade; background transparent",
            "Export: 1x, 4x and 8x nearest neighbour",
            note,
        ],
        "palette": sp.palette,
        "editing": False,
        "sprite_id": sp.doc["id"] if sp.doc else "",
        "reveal": reveal(crop, sp.png(1)),
    }
    pid = panel.art(card, turn=turn_fn())
    if pid and sp.doc:
        sprite_doc.STUDIO.add(sp.doc, pid)
        _BEFORE[sp.doc["id"]] = card["before"]
    return pid is not None


_BEFORE: dict[str, str] = {}  # sprite id -> the photo thumbnail reference of its card


def sprite_style(
    source_name: str = "dropped",
    head: int = 0,
    palette: str = "",
    chibi: bool = False,
    big_eyes: bool = False,
    nose: bool = False,
    beard: str = "",
    compare: bool = False,
) -> str:
    """A game sprite of the face in the user's picture: measured with Apple Vision on this Mac, built cell by cell (no generative model), shown next to the
    photo and next to the plain pixel-art filter, and held in the side panel until "save this card"."""
    try:
        img, label, fname = source.load(source_name)
        face, mask = faces.landmarks(img)
        if face is None:
            return "I couldn't find a face to build a sprite from. A clear, front-facing photo works best."
        opts = sprite.Options(head or 0, palette, chibi, big_eyes, nose, beard).clean()
        sp = sprite.build(img, face, mask, opts)
        if sp.doc:
            sp.doc["label"] = label
            sp.doc["source"] = {
                k: v
                for k, v in (source.current() or {}).items()
                if k in ("kind", "label", "path", "at")
            }
        crop, _found, focus = faces.portrait_crop(img)
        res, compared = None, b""
        if compare:  # the plain pixel-art filter beside the sprite, only when asked ("compare with pixel art")
            res = pixel.render(
                crop,
                pixel.Settings(PORTRAIT_SIZE, 16, opts.palette, False, False, 4, False, True),
                focus,
            )
            k = max(1, 256 // max(res.width, res.height))
            compared = res.png(k)[0]
    except (source.ArtProblem, pixel.PixelProblem, sprite.SpriteProblem) as exc:
        return str(exc)
    except Exception as exc:
        log.event("sprite_failed", error=type(exc).__name__)
        return "I couldn't build a sprite from that."
    shown = False
    try:
        shown = _sprite_card(
            sp,
            crop,
            compared,
            label,
            res,
            "Pixel art filter shown beside it for comparison"
            if compare
            else "Say compare with pixel art to see the photo filter beside it",
        )
    except Exception as exc:
        log.event("sprite_card_failed", error=type(exc).__name__)
    if not shown and sp.doc:  # no panel: still editable by voice
        sprite_doc.STUDIO.add(sp.doc)
    o = sp.options
    from julius.brain import session_state

    session_state.record(
        "sprite",
        f"made a sprite ({o.palette}, {o.head} px{', chibi' if o.chibi else ''}) from {label}",
    )
    log.event("sprite", head=o.head, palette=o.palette, chibi=o.chibi, big_eyes=o.big_eyes)
    return (
        f"Sprite of {fname}: a {o.head} by {o.head} pixel head on a {sp.canvas} by {sp.canvas} canvas, {o.palette} palette, "
        f"{'chibi' if o.chibi else 'normal'} proportions{', big eyes' if o.big_eyes else ''}{', with a nose' if o.nose else ''}, "
        f"facial hair: {sprite.BEARD_LABELS[sp.facial_hair]}{'' if sp.facial_hair_measured else ' (as you asked)'}, "
        "built from the measurements of the face, not generated. "
        + (
            (
                "The photo, the sprite and the plain pixel-art version are side by side in the panel; "
                if compare
                else "The photo and the sprite are side by side in the panel; "
            )
            + "say save this card to keep the 1x, 4x and 8x PNGs."
            if shown
            else "No side panel is open, so there is nothing to save yet."
        )
    )


def image_style(
    source_name: str = "",
    style: str = "pixel_art",
    size: int = 0,
    colors: int = 0,
    palette: str = "",
    outline: bool = False,
    transparent: bool = False,
    zoom: int = 4,
    dither: bool = False,
    full_photo: bool = False,
    compare: bool = False,
    retro: bool | None = None,
    line: int = 0,
    smooth: int = -1,
    head: int = 0,
    chibi: bool = False,
    big_eyes: bool = False,
    nose: bool = False,
    beard: str = "",
) -> str:
    """Restyle the user's own picture. Never creates art from scratch: with no picture named it asks for one."""
    chosen = style_name(style)
    if not chosen:
        return (
            f"I can only do these styles so far: {', '.join(s.replace('_', ' ') for s in STYLES)}."
        )
    if chosen == "all_styles":
        return filter_compare((source_name or "").strip() or "dropped")
    if chosen == "sprite":
        return sprite_style(
            (source_name or "").strip() or "dropped",
            head,
            palette,
            chibi,
            big_eyes,
            nose,
            beard,
            compare,
        )
    if chosen != "pixel_art":
        return filter_style((source_name or "").strip() or "dropped", chosen, colors, line, smooth)
    # no picture named: the one dropped on the orb, or a question asking for one (nothing is ever drawn from nothing)
    return pixel_art(
        (source_name or "").strip() or "dropped",
        size,
        colors or 16,
        palette,
        outline,
        transparent,
        zoom,
        dither,
        full_photo,
        compare,
        retro,
    )


# ---- editing a sprite ---------------------------------------------------------------------------------
def _edit_settings(doc: dict) -> list[str]:
    p = doc["params"]

    def signed(v: int) -> str:
        return f" {v:+d}" if v else ""

    n_px = sum(len(v) for v in doc["edits"].values())
    return [
        f"Head: {doc['head']} px on a {doc['canvas']} px canvas, palette {doc['palette']} (locked)",
        f"Hair: {p['hair_style']}{signed(p['hair_height'])}",
        f"Facial hair: {sprite.BEARD_LABELS.get(p['beard'], p['beard'])}{signed(p['beard_size'])}",
        f"Eyes: {p['eye_size']}{', spacing' + signed(p['eye_spacing']) if p['eye_spacing'] else ''}; brows {'on' if p['brows'] else 'off'}",
        f"Mouth: {p['mouth']}{signed(p['mouth_width'])}; nose {'on' if p['nose'] else 'off'}",
        f"Glasses: {p['glasses']}; earrings {'on' if p['earrings'] else 'off'}; hat: {p['hat']}",
        f"Pixel edits: {n_px}",
        "Save this card writes the PNGs and a .julius-sprite file you can reopen",
    ]


def _refresh_card(doc: dict) -> bool:
    """The sprite's card after an edit: updated in place (sprite large, photo as a small reference). A new card when the old one is gone."""
    from julius.ui_bridge import charts

    if charts._panel is None:
        return False
    panel, files, turn_fn = charts._panel
    if files is None:
        return False
    k = max(1, min(SPRITE_SHOW, 256 // doc["canvas"]))
    fields = {
        "after": files.put_memory(sprite_doc.png(doc, k), "png"),
        "export": files.put_memory(sprite_doc.png(doc, 8), "png"),
        "small": files.put_memory(sprite_doc.png(doc, 1), "png"),
        "settings": _edit_settings(doc),
        "palette": sprite_doc.used_colors(doc),
        "summary": f"{doc['head']}×{doc['head']} head, being edited (palette locked)",
        "editing": True,
        "main": is_main(doc),
    }
    pid = sprite_doc.STUDIO.cards.get(doc["id"])
    if pid and panel.update_art(pid, fields):
        return True
    card = {
        "title": f"Sprite: {doc.get('name') or doc.get('label') or 'reopened'}",
        "kind": "sprite",
        "before": _BEFORE.get(doc["id"]) or fields["small"],
        "compare": "",
        "alt": f"{doc['head']} pixel head sprite",
        "sprite_id": doc["id"],
        "reveal": reveal(
            None, sprite_doc.png(doc, 1)
        ),  # reopened: no photo, so the sprite forms out of loose sand
        **fields,
    }
    pid = panel.art(card, turn=turn_fn())
    if pid:
        sprite_doc.STUDIO.cards[doc["id"]] = pid
    return pid is not None


def _saved_base() -> Path:
    from julius import config
    from julius.ui_bridge import panel_emit

    try:
        return panel_emit.saved_dir((config.load_config().get("ui") or {}).get("saved_dir"))
    except Exception:
        return Path.home() / "Julius Saved"


def saved_sprites() -> list[tuple[str, float, Path]]:
    """Every saved .julius-sprite in the saved-cards folder as (name, saved time, path), newest first. The name is its folder's name."""

    base = _saved_base()
    if not base.exists():
        return []
    out = []
    for p in base.glob("**/*.julius-sprite"):
        try:
            out.append((p.parent.name, p.stat().st_mtime, p))
        except OSError:
            continue
    return sorted(out, key=lambda r: r[1], reverse=True)


_NAME_SKIP = {
    "my",
    "the",
    "sprite",
    "sprites",
    "file",
    "open",
    "reopen",
    "a",
    "dropped",
    "this",
    "saved",
    "called",
    "named",
    "last",
    "latest",
    "newest",
    "most",
    "recent",
}


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if w not in _NAME_SKIP]


def matching_sprites(name: str) -> list[tuple[str, float, Path]]:
    """Saved sprites whose name has all the words (an exact name first)."""
    words = _words(name)
    rows = saved_sprites()
    if not words:
        return rows
    exact = [r for r in rows if r[0].lower() == " ".join(words)]
    return exact or [r for r in rows if all(w in r[0].lower() for w in words)]


def _when(t: float) -> str:
    import time as _time

    return _time.strftime("%b %d, %H:%M", _time.localtime(t)).replace(" 0", " ")


def _open_sprite_file(name: str) -> tuple[dict, str]:
    """(the document, a line about other recent ones): the .julius-sprite dropped on the orb, else the newest saved one whose name has the words."""
    pending = source._drop_source() if source._drop_source else None
    for item in (pending or {}).get("items", []):
        if item.get("kind") == "file" and str(item.get("path", "")).endswith(".julius-sprite"):
            p = Path(item["path"])
            return _load_saved(p.stem, p), ""
    hits = matching_sprites(name)
    if not hits:
        raise sprite_doc.DocProblem(
            "I couldn't find a saved sprite by that name. Drop the .julius-sprite file on the orb, or say open my sprite for the newest one."
        )
    chosen = hits[0]
    return _load_saved(chosen[0], chosen[2]), _others(name, hits, chosen)


def _load_saved(name: str, path: Path) -> dict:
    doc = sprite_doc.loads(path.read_text(encoding="utf-8"))
    doc["name"] = name
    sprite_doc.STUDIO.mark_saved(doc, str(path))
    return doc


def _others(name: str, hits: list, chosen: tuple) -> str:
    others = [r for r in (saved_sprites() if not _words(name) else hits)[1:4] if r[2] != chosen[2]][
        :3
    ]
    if not others:
        return ""
    return (
        " Other recent ones: "
        + "; ".join(f"{n} ({_when(t)})" for n, t, _ in others)
        + ". Say open and the name for one of those."
    )


_OPEN = re.compile(r"^\s*(?:re)?open\b|\bload\b", re.I)

# ---- the main sprite: one at a time, a pointer file next to the saves (never a copy) ------------------------
MAIN_FILE = ".julius-main-sprite.json"
MAIN_WORDS = re.compile(
    r"\s*(?:up\s+)?(?:my\s+|the\s+)?main(?:\s+(?:sprite|one|character))?[\s.!?]*", re.I
)
SET_MAIN = re.compile(
    r"^\W*(?:please\s+|can you\s+|could you\s+|now\s+)?(?:"
    r"(?:make|set|mark|use|pin|save)\s+(?:(?:this|it|that|this\s+one|this\s+sprite|that\s+sprite|the\s+current\s+sprite|him)\s+)?(?:as\s+|to\s+be\s+|to\s+)?(?:my\s+|the\s+)?main(?:\s+(?:sprite|one|character))?"
    r"|(?:this|it|that)(?:\s+one)?\s+is\s+(?:my|the)\s+(?:new\s+)?main(?:\s+(?:sprite|one|character))?"
    r")(?:,?\s+please)?[\s.!?]*$",
    re.I,
)


def _main_pointer() -> Path:
    return _saved_base() / MAIN_FILE


def main_sprite() -> tuple[str, Path] | None:
    """(name, path) of his main sprite, or None. Body, clothing and animation work start from this one unless he names another."""
    import json

    try:
        data = json.loads(_main_pointer().read_text(encoding="utf-8"))
        path = (_saved_base() / str(data["path"])).resolve()
        if _saved_base().resolve() not in path.parents or path.suffix != ".julius-sprite":
            return None
        return str(data.get("name") or path.parent.name), path
    except (OSError, ValueError, KeyError, TypeError):
        return None


def is_main(doc: dict) -> bool:
    main = main_sprite()
    hit = sprite_doc.STUDIO.saved.get(doc.get("id", ""))
    return bool(main and hit and Path(hit[0]).resolve() == main[1])


def set_main(doc: dict) -> str:
    """ "Make this my main sprite": point the main-sprite file at this sprite's .julius-sprite (saving it first when it has no file yet, or
    was edited since). One at a time; the reply says which one it replaced."""
    import json

    st = sprite_doc.STUDIO
    path = st.saved_path(doc)
    saved_now = ""
    if path is None:
        from julius.ui_bridge import charts, panel_emit

        card = None
        if charts._panel is not None:
            panel = charts._panel[0]
            with panel._lock:
                card = panel.cards.get(st.cards.get(doc["id"], ""))
        if card is None:
            return "I need to save this sprite before it can be your main one, and its card is gone. Reopen it, then say make this my main sprite."
        from julius import config

        said = panel_emit.save_art_card(
            card,
            charts._panel[1],
            (config.load_config().get("ui") or {}).get("saved_dir"),
            name=doc.get("name") or None,
        )
        path = st.saved_path(doc)
        if path is None:
            return said
        saved_now = f" I saved it first, as {Path(path).parent.name}."
    old = main_sprite()
    base = _saved_base()
    target = Path(path).resolve()
    if old and old[1] == target:
        return f"{target.parent.name} is already your main sprite."
    try:
        rel = target.relative_to(base.resolve())
    except ValueError:
        return "Your main sprite has to be one saved in your Julius Saved folder."
    tmp = _main_pointer().with_suffix(".tmp")
    tmp.write_text(
        json.dumps({"path": str(rel), "name": target.parent.name}, ensure_ascii=False),
        encoding="utf-8",
    )
    tmp.replace(_main_pointer())
    _badge(doc, old)
    from julius.brain import session_state

    session_state.record("main", f"made {target.parent.name} the main sprite")
    log.event("sprite_main_set", replaced=bool(old))
    replaced = f" It replaces {old[0]}." if old and old[1].exists() else ""
    return f"{target.parent.name} is now your main sprite.{replaced}{saved_now}"


def _badge(doc: dict, old: tuple[str, Path] | None) -> None:
    """The Main badge: on this sprite's card, off the card of the one it replaced."""
    from julius.ui_bridge import charts

    if charts._panel is None:
        return
    panel = charts._panel[0]
    st = sprite_doc.STUDIO
    for sid, (p, _state) in list(st.saved.items()):
        pid = st.cards.get(sid)
        if not pid:
            continue
        if sid == doc["id"]:
            panel.update_art(pid, {"main": True})
        elif old and Path(p).resolve() == old[1]:
            panel.update_art(pid, {"main": False})


def open_main() -> str:
    main = main_sprite()
    if main is None:
        return "You haven't picked a main sprite yet. Open or make one, then say make this my main sprite."
    name, path = main
    if not path.exists():
        return f"Your main sprite, {name}, is no longer in your Julius Saved folder. Open another and say make this my main sprite."
    doc = _load_saved(name, path)
    sprite_doc.STUDIO.add(doc)
    from julius.brain import session_state

    session_state.record("open", f"opened the main sprite {name}")
    shown = _refresh_card(doc)
    return (
        f"Opened your main sprite, {name}: {doc['head']} pixel head, {doc['palette']} palette. "
        + ("It's in the panel; say what to change." if shown else "Say what to change.")
    )


def sprite_edit(change: str = "", sprite_id: str = "") -> str:
    """Change the sprite being edited ("bigger beard", "taller afro", "add glasses", "close-set eyes"), undo / redo, or reopen a .julius-sprite file.
    Parameters re-render the sprite; the palette stays locked. The card updates in place."""
    text = (change or "").strip()
    st = sprite_doc.STUDIO
    try:
        if _OPEN.search(text):
            rest = _OPEN.sub("", text)
            if MAIN_WORDS.fullmatch(rest):
                return open_main()
            doc, extra = _open_sprite_file(rest)
            st.add(doc)
            src = doc.get("source") or {}
            if src.get(
                "path"
            ):  # a sprite made from a file: that file is the session's image again (a camera photo stays as it is)
                source.set_current_path(Path(src["path"]))
            from julius.brain import session_state

            session_state.record("open", f"opened the saved sprite {doc.get('name') or 'sprite'}")
            shown = _refresh_card(doc)
            title = doc.get("name") or (
                ("the sprite of " + doc["label"]) if doc.get("label") else "the sprite"
            )
            return (
                f"Opened {title}: {doc['head']} pixel head, {doc['palette']} palette. "
                + ("It's in the panel; say what to change." if shown else "Say what to change.")
                + extra
            )
        doc = st.get(sprite_id)
        if doc is None:
            if sprite_id and st.get():
                return "That is an older sprite card; I'm editing the newest one. Make or reopen the sprite you want first."
            return "There's no sprite to edit yet. Say make me a sprite, or reopen a saved one."
        low = text.lower()
        if SET_MAIN.match(text) or re.search(
            r"\bmain\s+(?:sprite|one|character)\b|\bthe\s+main\b", low
        ):
            return set_main(doc)
        pal = re.search(r"\b(nes|pico\s?-?8|game\s?boy|gameboy)\b", low)
        if pal and not re.search(r"\b(?:undo|redo)\b", low):
            # "make it NES", "palette nes": the same face in another palette is a new sprite from the current picture (its colours are measured)
            word = re.sub(r"[\s-]", "", pal.group(1))
            palette = "nes" if word == "nes" else "pico8" if word.startswith("pico") else "gameboy"
            return image_style("dropped", style="sprite", palette=palette)
        if re.search(r"\bredo\b", low) or re.search(r"\bundo\b", low):
            back = bool(re.search(r"\bundo\b", low))
            if not st.step(doc, back):
                return "Nothing to " + ("undo." if back else "redo.")
            _refresh_card(doc)
            return "Undone." if back else "Redone."
        if re.search(r"\breset\b|\bstart over\b|\bas (?:it was )?built\b", low):
            st.checkpoint(doc)
            doc["params"] = sprite_doc.default_params(doc["base"], type("O", (), doc["options"])())
            doc["edits"] = {}
            _refresh_card(doc)
            return "Back to the sprite as it was built. Say undo to go back."
        before = sprite_doc.dumps({"p": doc["params"]})
        st.checkpoint(doc)
        done = sprite_doc.apply_words(doc, text)
        if not done or sprite_doc.dumps({"p": doc["params"]}) == before:
            st.step(doc, True)  # nothing changed: drop the checkpoint
            st.redo[doc["id"]] = []
            return "I can change: " + sprite_doc.EDIT_HELP + "."
        shown = _refresh_card(doc)
    except (sprite_doc.DocProblem, OSError) as exc:
        return (
            str(exc)
            if isinstance(exc, sprite_doc.DocProblem)
            else "I couldn't read that sprite file."
        )
    except Exception as exc:
        log.event("sprite_edit_failed", error=type(exc).__name__)
        return "I couldn't change the sprite."
    from julius.brain import session_state

    session_state.record("edit", "edited the sprite: " + ", ".join(done))
    log.event("sprite_edit", changes=len(done))
    return (
        "Done: "
        + ", ".join(done)
        + ". "
        + ("The card is updated; say undo to go back." if shown else "Say undo to go back.")
    )


def sprite_pixels(edits: Any, sprite_id: str = "") -> tuple[bool, str]:
    """Pixel edits from the editor window (per layer, palette indices only), as one undo step."""
    st = sprite_doc.STUDIO
    doc = st.get(sprite_id)
    if doc is None:
        return False, "That sprite is no longer open."
    try:
        clean = sprite_doc.clean_edits(edits, doc["canvas"], len(doc["colors"]))
    except sprite_doc.DocProblem as exc:
        return False, str(exc)
    st.checkpoint(doc)
    doc["edits"] = clean
    _refresh_card(doc)
    log.event("sprite_pixels", layers=len(clean), cells=sum(len(v) for v in clean.values()))
    return True, "saved"


# ---- instant card edits (the card's buttons): no turn, no transcript, no speech, no model -------------------
CARD_OPS = {
    "smaller beard",
    "bigger beard",
    "shorter hair",
    "taller hair",
    "close-set eyes",
    "wider eyes",
    "glasses",
    "undo",
    "redo",
}
MAX_OPS = 20


def sprite_ops(sprite_id: str, ops: list) -> dict:
    """Apply the card's button presses (several when clicks came faster than the render: the latest state wins), render once, update the card in
    place, and hand the new picture straight back to the card. Returns {"ok", "after", "settings", "palette", "undo", "redo", "ms"} or {"ok": False, "error"}."""
    import time as _time

    t0 = _time.perf_counter()
    st = sprite_doc.STUDIO
    doc = st.get(sprite_id)
    if doc is None or (sprite_id and sprite_id != doc["id"]):
        return {"ok": False, "error": "That sprite is no longer open."}
    if (
        not isinstance(ops, list)
        or not ops
        or len(ops) > MAX_OPS
        or not all(o in CARD_OPS for o in ops)
    ):
        return {"ok": False, "error": "unknown edit"}
    changed = False
    for op in ops:
        if op in ("undo", "redo"):
            changed |= st.step(doc, op == "undo")
            continue
        before = sprite_doc.dumps(doc["params"])
        st.checkpoint(doc)
        if op == "glasses":  # a toggle on the card
            doc["params"]["glasses"] = "none" if doc["params"]["glasses"] != "none" else "square"
        else:
            sprite_doc.apply_words(doc, op)
        if sprite_doc.dumps(doc["params"]) == before:  # at a limit: no undo step for nothing
            st.step(doc, True)
            st.redo[doc["id"]] = []
        else:
            changed = True
    from julius.ui_bridge import charts

    out: dict = {
        "ok": True,
        "changed": changed,
        "undo": len(st.undo.get(doc["id"], [])),
        "redo": len(st.redo.get(doc["id"], [])),
    }
    if charts._panel is not None and charts._panel[1] is not None:
        panel, files, _turn = charts._panel
        k = max(1, min(SPRITE_SHOW, 256 // doc["canvas"]))
        shown = files.put_memory(sprite_doc.png(doc, k, fast=True), "png")
        fields = {
            "after": shown,
            "export": shown,  # the save writes 1x, 4x and 8x from "small"
            "small": files.put_memory(sprite_doc.png(doc, 1, fast=True), "png"),
            "settings": _edit_settings(doc),
            "palette": sprite_doc.used_colors(doc),
            "summary": f"{doc['head']}×{doc['head']} head, being edited (palette locked)",
            "editing": True,
        }
        pid = st.cards.get(doc["id"])
        if pid:
            panel.update_art(pid, fields)
        out.update({k: fields[k] for k in ("after", "settings", "palette")})
    out["ms"] = round((_time.perf_counter() - t0) * 1000, 1)
    if changed:
        from julius.brain import session_state

        session_state.record("edit", "edited the sprite on its card: " + ", ".join(ops))
    log.event("sprite_card_ops", ops=len(ops), ms=out["ms"])
    return out
