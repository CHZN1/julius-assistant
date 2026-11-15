"""The `pixel_art` tool: open the picture the user means, make pixel art from it, show a before/after card, return the facts for Julius to say."""

from __future__ import annotations

import io
import re
from typing import Any

from julius import log
from julius.art import faces, filters, pixel, source, sprite

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
    ref = files.put_memory(shown, "png", keep=16)
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
        "before": files.put_memory(_before_png(before), "png", keep=16),
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
            ref = files.put_memory(data, "png", keep=16)
            card = {
                "title": f"All five styles: {label}",
                "kind": "compare",
                "before": files.put_memory(_before_png(img), "png", keep=16),
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
    shown = files.put_memory(sp.png(k), "png", keep=16)
    card = {
        "title": f"Sprite: {label}",
        "kind": "sprite",
        "before": files.put_memory(_before_png(crop), "png", keep=16),
        "after": shown,
        "compare": files.put_memory(compared, "png", keep=16) if compared else "",
        "export": files.put_memory(sp.png(8), "png", keep=16),
        "small": files.put_memory(sp.png(1), "png", keep=16),
        "alt": f"{o.head} pixel head sprite of {label}",
        "summary": f"{o.head}×{o.head} head on a {sp.canvas}×{sp.canvas} canvas, {len(sp.palette)} colours, built from your face's measurements",
        "settings": [
            f"Head: {o.head} px on a {sp.canvas} px canvas",
            f"Palette: {o.palette}",
            f"Proportions: {'chibi' if o.chibi else 'normal'}",
            f"Big eyes: {'on' if o.big_eyes else 'off'}",
            f"Nose: {'on' if o.nose else 'off'}",
            "Outline: 1 px, darkest shade; background transparent",
            "Export: 1x, 4x and 8x nearest neighbour",
            note,
        ],
        "palette": sp.palette,
    }
    return panel.art(card, turn=turn_fn()) is not None


def sprite_style(
    source_name: str = "dropped",
    head: int = 0,
    palette: str = "",
    chibi: bool = False,
    big_eyes: bool = False,
    nose: bool = False,
) -> str:
    """A game sprite of the face in the user's picture: measured with Apple Vision on this Mac, built cell by cell (no generative model), shown next to the
    photo and next to the plain pixel-art filter, and held in the side panel until "save this card"."""
    try:
        img, label, fname = source.load(source_name)
        face, mask = faces.landmarks(img)
        if face is None:
            return "I couldn't find a face to build a sprite from. A clear, front-facing photo works best."
        opts = sprite.Options(head or sprite.DEFAULT_HEAD, palette, chibi, big_eyes, nose).clean()
        sp = sprite.build(img, face, mask, opts)
        crop, _found, focus = faces.portrait_crop(img)
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
            sp, crop, compared, label, res, "Pixel art filter shown for comparison"
        )
    except Exception as exc:
        log.event("sprite_card_failed", error=type(exc).__name__)
    o = sp.options
    log.event("sprite", head=o.head, palette=o.palette, chibi=o.chibi, big_eyes=o.big_eyes)
    return (
        f"Sprite of {fname}: a {o.head} by {o.head} pixel head on a {sp.canvas} by {sp.canvas} canvas, {o.palette} palette, "
        f"{'chibi' if o.chibi else 'normal'} proportions{', big eyes' if o.big_eyes else ''}{', with a nose' if o.nose else ''}, "
        "built from the measurements of the face, not generated. "
        + (
            "The photo, the sprite and the plain pixel-art version are side by side in the panel; say save this card to keep the 1x, 4x and 8x PNGs."
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
            (source_name or "").strip() or "dropped", head, palette, chibi, big_eyes, nose
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
